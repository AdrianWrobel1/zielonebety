"""
Production Scan Orchestrator

The authoritative entry point for executing one complete, deterministic, failure-safe scan cycle.
Connects:
    Acquisition (Superbet / Betclic via ExecutionEngine)
        ↓
    Event Selection Policy (DefaultEventSelectionPolicy)
        ↓
    Normalization (NormalizationEngine)
        ↓
    Entity & Event Matching (CrossBookmakerValidationPipeline)
        ↓
    Surebet Detection (SurebetDetectorEngine)
        ↓
    Opportunity Lifecycle (OpportunityLifecycleManager)
        ↓
    Alert Policy (DefaultOpportunityAlertPolicy)
        ↓
    Opportunity Dispatcher (OpportunityDispatcher)
        ↓
    Persistent Delivery & Reliability (DeliveryRepository + DeliveryReconciliationService)
        ↓
    Resource Safety Guard & Telemetry
        ↓
    ScanCycleResult
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import concurrent.futures
import logging
import time
import tracemalloc
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple, Union
import uuid

from database.connection import DatabaseManager
from database.repositories.delivery_repository import DeliveryRepository
from database.repositories.opportunity_repository import OpportunityRepository
from normalization.alert_policy import DefaultOpportunityAlertPolicy, OpportunityAlertPolicy
from normalization.quality_policy import (
    DefaultOpportunityQualityPolicy,
    OpportunityQualityConfig,
    OpportunityQualityEvaluation,
    OpportunityQualityPolicy,
    OpportunityRankingEngine,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.delivery_reliability import (
    DeliveryReconciliationService,
    DeliveryRetryConfig,
    ReconciliationSummary,
)
from normalization.dispatcher import (
    BatchDispatchResult,
    OpportunityConsumer,
    OpportunityDispatcher,
)
from normalization.engine import NormalizationEngine, NormalizationResult
from normalization.market_scope import is_allowed_market_family, get_market_family_name
from normalization.lifecycle import (
    LifecycleDispatchSummary,
    OpportunityLifecycleManager,
)
from normalization.surebet import (
    SurebetDetectionMetrics,
    SurebetDetectionResult,
    SurebetDetectorEngine,
    SurebetStatus,
)
from reference_odds.provider import ReferenceOddsProvider, TheOddsApiReferenceProvider
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine
from valuebets.lifecycle import (
    ValuebetLifecycleBatch,
    ValuebetLifecycleManager,
    generate_valuebet_fingerprint,
)
from valuebets.models import ValueBetDetectionResult
from valuebets.quality_policy import ValuebetQualityConfig, ValuebetQualityPolicy

from normalization.validation_pipeline import (
    CrossBookmakerValidationPipeline,
    CrossBookmakerValidationResult,
)
from orchestration.event_selection import (
    DefaultEventSelectionPolicy,
    EventSelectionPolicy,
)
from orchestration.exceptions import CriticalPipelineFailure, ScanConfigurationError
from orchestration.models import (
    CycleStatus,
    EvaluationExclusionReason,
    MarketEvaluationState,
    MatchedMarketEvaluationRecord,
    ResourceBudget,
    ResourceMetrics,
    ScanConfig,
    ScanCycleResult,
    StageTiming,
)
from providers.base.base_provider import BaseProvider
from providers.base.execution_engine import ExecutionEngine
from providers.base.provider_factory import ProviderFactory
from providers.base.provider_manager import ProviderManager
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.betclic.config import BetclicConfig
from providers.betclic.provider import BetclicProvider
from providers.superbet.config import SuperbetConfig
from providers.superbet.provider import SuperbetProvider

logger = logging.getLogger("zielonebety.orchestration.scanner")


from normalization.market_identity import classify_market_category
from orchestration.detail_planning import CoordinatedDetailSelectionPlanner


def _categorize_market_type(mkt_type_or_name: Any, key_or_mkt: Any = None) -> str:
    """Categorizes raw market name, canonical market type, or CanonicalMarketKey into target market families.

    Delegates authoritatively to normalization.market_identity.classify_market_category.
    """
    return classify_market_category(mkt_type_or_name, key_or_mkt)


MARKET_FAMILY_TELEMETRY_FAMILIES: Tuple[str, ...] = (
    "1X2",
    "BTTS",
    "TOTALS",
    "DOUBLE_CHANCE",
    "DRAW_NO_BET",
    "HALF_TIME_RESULT",
    "HANDICAP",
    "PLAYER_PROPS",
)


def count_market_families(graphs: Sequence[NormalizedGraph]) -> Dict[str, int]:
    """Single-pass census of normalized markets per telemetry family.

    Replaces N full scans (one per family) with exactly one categorization
    call per market. Output is bit-for-bit identical to the legacy per-family
    comprehension: families outside MARKET_FAMILY_TELEMETRY_FAMILIES are ignored.
    """
    counts: Dict[str, int] = {fam: 0 for fam in MARKET_FAMILY_TELEMETRY_FAMILIES}
    for g in graphs or []:
        for m in getattr(g, "markets", None) or []:
            fam = _categorize_market_type(getattr(m, "market_type", ""))
            if fam in counts:
                counts[fam] += 1
    return counts


def index_parsed_by_provider_id(parsed_objects: Sequence[Any], id_attr: str = "provider_event_id") -> Dict[str, Any]:
    """Builds a first-wins index of parsed provider objects by provider event id.

    Replaces O(C x P) linear scans (next(...)) per canonical event with a
    single O(P) indexing pass. First duplicate wins, matching next() semantics.
    """
    index: Dict[str, Any] = {}
    for obj in parsed_objects or []:
        eid = str(getattr(obj, id_attr, "") or "")
        if eid and eid not in index:
            index[eid] = obj
    return index


class ProductionScanOrchestrator:
    """Production orchestrator executing one discrete, deterministic scan cycle."""

    def __init__(
        self,
        config: Optional[ScanConfig] = None,
        execution_engine: Optional[ExecutionEngine] = None,
        provider_manager: Optional[ProviderManager] = None,
        normalization_engine: Optional[NormalizationEngine] = None,
        validation_pipeline: Optional[CrossBookmakerValidationPipeline] = None,
        surebet_detector: Optional[SurebetDetectorEngine] = None,
        opportunity_repository: Optional[OpportunityRepository] = None,
        delivery_repository: Optional[DeliveryRepository] = None,
        lifecycle_manager: Optional[OpportunityLifecycleManager] = None,
        alert_policy: Optional[OpportunityAlertPolicy] = None,
        dispatcher: Optional[OpportunityDispatcher] = None,
        reconciliation_service: Optional[DeliveryReconciliationService] = None,
        db_manager: Optional[DatabaseManager] = None,
        event_selection_policy: Optional[EventSelectionPolicy] = None,
        quality_policy: Optional[OpportunityQualityPolicy] = None,
        reference_provider: Optional[ReferenceOddsProvider] = None,
        valuebet_engine: Optional[ValuebetEngine] = None,
        valuebet_quality_policy: Optional[ValuebetQualityPolicy] = None,
        valuebet_lifecycle_manager: Optional[ValuebetLifecycleManager] = None,
    ) -> None:
        self.config = config or ScanConfig()
        self.execution_engine = execution_engine or ExecutionEngine()
        self.provider_manager = provider_manager or ProviderManager(engine=self.execution_engine)
        self.normalization_engine = normalization_engine or NormalizationEngine()
        self.validation_pipeline = validation_pipeline or CrossBookmakerValidationPipeline()
        self.surebet_detector = surebet_detector or SurebetDetectorEngine()
        self.event_selection_policy = event_selection_policy or DefaultEventSelectionPolicy()

        # Reference Odds & Valuebet Subsystem
        self.reference_provider = reference_provider or TheOddsApiReferenceProvider()
        self.valuebet_engine = valuebet_engine or ValuebetEngine(
            config=ValuebetConfig(min_value_percent=self.config.min_value_percent)
        )
        self.valuebet_quality_policy = valuebet_quality_policy or ValuebetQualityPolicy(
            config=ValuebetQualityConfig(min_value_percent=self.config.min_value_percent)
        )

        # Database and persistence setup
        self.db_manager = db_manager
        self.opportunity_repository = opportunity_repository
        self.delivery_repository = delivery_repository

        # If db_manager is provided but repositories are not, initialize from session
        if self.db_manager is not None and (self.opportunity_repository is None or self.delivery_repository is None):
            session = self.db_manager.get_session()
            if self.opportunity_repository is None:
                self.opportunity_repository = OpportunityRepository(session)
            if self.delivery_repository is None:
                self.delivery_repository = DeliveryRepository(session)

        # Alert Policy, Quality Policy, Ranking Engine and Dispatcher
        self.alert_policy = alert_policy or DefaultOpportunityAlertPolicy()
        self.quality_policy = quality_policy or DefaultOpportunityQualityPolicy(
            event_selection_policy=self.event_selection_policy if isinstance(self.event_selection_policy, DefaultEventSelectionPolicy) else None
        )
        self.ranking_engine = OpportunityRankingEngine(quality_policy=self.quality_policy)
        self.dispatcher = dispatcher or OpportunityDispatcher()
        self.detail_planner = CoordinatedDetailSelectionPlanner(
            event_selection_policy=self.event_selection_policy,
            normalization_engine=self.normalization_engine,
            validation_pipeline=self.validation_pipeline,
        )

        # Surebet Lifecycle Manager
        if lifecycle_manager is not None:
            self.lifecycle_manager: Optional[OpportunityLifecycleManager] = lifecycle_manager
        elif self.opportunity_repository is not None:
            self.lifecycle_manager = OpportunityLifecycleManager(
                repository=self.opportunity_repository,
                alert_policy=self.alert_policy,
                delivery_repository=self.delivery_repository,
                quality_policy=self.quality_policy,
            )
        else:
            self.lifecycle_manager = None

        # Valuebet Lifecycle Manager
        if valuebet_lifecycle_manager is not None:
            self.valuebet_lifecycle_manager: Optional[ValuebetLifecycleManager] = valuebet_lifecycle_manager
        elif self.opportunity_repository is not None:
            self.valuebet_lifecycle_manager = ValuebetLifecycleManager(
                repository=self.opportunity_repository,
                quality_policy=self.valuebet_quality_policy,
                max_alerts_per_scan=self.config.max_valuebet_alerts_per_scan,
            )
        else:
            self.valuebet_lifecycle_manager = None

        # Delivery Reconciliation Service
        if reconciliation_service is not None:
            self.reconciliation_service: Optional[DeliveryReconciliationService] = reconciliation_service
        elif self.delivery_repository is not None and self.opportunity_repository is not None:
            self.reconciliation_service = DeliveryReconciliationService(
                delivery_repository=self.delivery_repository,
                opportunity_repository=self.opportunity_repository,
                dispatcher=self.dispatcher,
            )
        else:
            self.reconciliation_service = None

    def register_consumer(self, consumer: OpportunityConsumer) -> None:
        """Register a notification consumer into the opportunity dispatcher."""
        self.dispatcher.register_consumer(consumer)

    def _create_provider_instance(self, p_name: str) -> BaseProvider:
        """Creates and configures a provider instance according to ScanConfig."""
        if p_name == "superbet":
            sb_cfg = SuperbetConfig(
                selection_mode=self.config.selection_mode,
                request_timeout=self.config.request_timeout,
                rate_limit_per_sec=self.config.rate_limit_per_sec,
                max_detail_requests=self.config.effective_max_detail_requests,
                preferred_competitions=self.config.preferred_competitions,
                hours_ahead=self.config.hours_ahead,
                detail_workers=self.config.detail_workers,
            )
            return SuperbetProvider(config=sb_cfg)
        elif p_name == "betclic":
            bc_cfg = BetclicConfig(
                selection_mode=self.config.selection_mode,
                timeout_seconds=self.config.request_timeout,
                rate_limit_per_sec=self.config.rate_limit_per_sec,
                max_detail_requests=self.config.effective_max_detail_requests,
                preferred_competitions=self.config.preferred_competitions,
                hours_ahead=self.config.hours_ahead,
                detail_workers=self.config.detail_workers,
            )
            return BetclicProvider(config=bc_cfg)
        elif p_name in ("odds_api", "bet365", "unibet"):
            from providers.odds_api.config import OddsApiConfig
            from providers.odds_api.provider import OddsApiProvider
            eff_limit = self.config.event_limit if (isinstance(self.config.event_limit, int) and self.config.event_limit > 0) else 50
            oapi_cfg = OddsApiConfig(
                request_timeout=self.config.request_timeout,
                rate_limit_per_sec=self.config.rate_limit_per_sec,
                preferred_competitions=self.config.preferred_competitions,
                event_limit=eff_limit,
            )
            return OddsApiProvider(config=oapi_cfg)
        else:
            return ProviderFactory.create_provider(name=p_name)

    def run_scan_cycle(
        self,
        providers: Optional[Dict[str, BaseProvider]] = None,
        evaluation_time: Optional[datetime] = None,
    ) -> ScanCycleResult:
        """Executes one complete, discrete scan cycle.

        Args:
            providers: Optional explicit dict of provider instances {provider_name: provider}.
                       If not supplied, instances are created via ProviderFactory / configured providers.
            evaluation_time: Optional explicit timestamp for deterministic testing.

        Returns:
            ScanCycleResult containing complete structured diagnostic telemetry.
        """
        if not tracemalloc.is_tracing():
            tracemalloc.start()

        cycle_t0 = time.perf_counter()
        now_dt = evaluation_time or datetime.now(timezone.utc)
        started_at = now_dt.isoformat()
        execution_id = f"scan_{now_dt.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        from orchestration.profiler import (
            ScanExecutionProfiler,
            set_current_scan_profiler,
        )
        scan_mode_str = str(getattr(self.config, "scan_mode", "NORMAL")).upper()
        profiler = ScanExecutionProfiler(execution_id=execution_id, scan_mode=scan_mode_str)
        set_current_scan_profiler(profiler)

        stage_timings = StageTiming()
        resource_metrics = ResourceMetrics()
        errors: List[str] = []
        warnings: List[str] = []
        diagnostics: Dict[str, Any] = {"execution_id": execution_id}

        provider_results: Dict[str, ProviderResult] = {}
        normalization_results: Dict[str, NormalizationResult] = {}
        validation_result: Optional[CrossBookmakerValidationResult] = None
        detection_result: Optional[SurebetDetectionResult] = None
        lifecycle_summary: Optional[LifecycleDispatchSummary] = None
        reconciliation_summary: Optional[ReconciliationSummary] = None
        valuebet_result: Any = None
        valuebet_lifecycle_batch: Any = None
        nearest_opp_dict: Optional[Dict[str, Any]] = None

        total_discovered = 0
        total_parsed = 0
        total_normalized = 0
        total_norm_failed = 0

        market_breakdown: Dict[str, Dict[str, Any]] = {
            "1X2": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "BTTS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TOTALS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_GOALS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "CARDS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_CARDS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "CARD_POINTS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_CARD_POINTS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "CORNERS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_CORNERS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "OFFSIDES": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_OFFSIDES": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "FOULS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_FOULS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "SHOTS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_SHOTS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "SHOTS_ON_TARGET": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "TEAM_SHOTS_ON_TARGET": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "DOUBLE_CHANCE": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "DRAW_NO_BET": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "HALF_TIME_RESULT": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "HANDICAP": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "PLAYER_PROPS": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
            "OTHER": {"discovered": 0, "acquired": 0, "parsed": 0, "normalized": 0, "matched": 0, "candidates": 0, "excluded": 0, "evaluated": 0, "coverage": "0.0%"},
        }

        cycle_status = CycleStatus.SUCCESS
        provider_graphs: Dict[str, List[NormalizedGraph]] = {}
        provider_instances: Dict[str, BaseProvider] = {}

        try:
            # ──────────────────────────────────────────────────────────────────
            # 1. STAGE 1: PROVIDER ACQUISITION
            # ──────────────────────────────────────────────────────────────────
            t_acq_0 = time.perf_counter()
            if providers is not None:
                target_provider_names = list(providers.keys())
            else:
                target_provider_names = list(self.config.providers)
                try:
                    from providers.odds_api.config import OddsApiConfig
                    oapi_cfg = OddsApiConfig()
                    if oapi_cfg.enabled and "odds_api" not in target_provider_names:
                        target_provider_names.append("odds_api")
                except Exception as e:
                    logger.debug("Odds API not auto-activated: %s", e)

            # Order providers so source/comparison providers (e.g. Superbet) execute before detail-dependent targets (e.g. Betclic)
            if "betclic" in target_provider_names and len(target_provider_names) > 1:
                ordered_names = [p for p in target_provider_names if p != "betclic"] + ["betclic"]
            else:
                ordered_names = target_provider_names

            provider_instances: Dict[str, BaseProvider] = {}
            for p_name in ordered_names:
                if providers and p_name in providers:
                    p_inst = providers[p_name]
                else:
                    p_inst = self._create_provider_instance(p_name)
                provider_instances[p_name] = p_inst

            overlap_sb_ids: Set[str] = set()
            overlap_bc_ids: Set[str] = set()

            # Coordinated detail prioritization for Superbet & Betclic if multi-provider scan
            sb_inst = provider_instances.get("superbet")
            bc_inst = provider_instances.get("betclic")

            if sb_inst and bc_inst and isinstance(sb_inst, SuperbetProvider) and isinstance(bc_inst, BetclicProvider):
                sb_cfg = getattr(sb_inst, "superbet_config", None)
                bc_cfg = getattr(bc_inst, "betclic_config", None)
                try:
                    with profiler.trace_phase("pre_discovery", counters={"providers": ["superbet", "betclic"]}):
                        # 1. Run Superbet & Betclic discovery concurrently
                        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as disc_pool:
                            sb_future = disc_pool.submit(sb_inst.discover)
                            bc_future = disc_pool.submit(bc_inst.discover)
                            disc_timeout = min(self.config.effective_provider_timeout, 45.0)
                            sb_discovered = sb_future.result(timeout=disc_timeout)
                            bc_discovered = bc_future.result(timeout=disc_timeout)

                    with profiler.trace_phase("detail_selection", counters={"budget": self.config.effective_max_detail_requests}):
                        plan = self.detail_planner.create_plan(
                            sb_discovered=sb_discovered,
                            bc_discovered=bc_discovered,
                            sb_parser=getattr(sb_inst, "parser", None),
                            bc_parser=getattr(bc_inst, "parser", None),
                            config=self.config,
                            evaluation_time=now_dt,
                        )

                        bc_inst.configure_full_market_acquisition(event_ids=plan.selected_event_ids_betclic)
                        sb_inst.configure_full_market_acquisition(event_ids=plan.selected_event_ids_superbet)

                        sb_inst.set_discovered_items(sb_discovered)
                        bc_inst.set_discovered_items(bc_discovered)

                    overlap_sb_ids = plan.overlap_event_ids_superbet
                    overlap_bc_ids = plan.overlap_event_ids_betclic

                    for k, v in plan.resource_metrics_patch.items():
                        setattr(resource_metrics, k, v)

                    diagnostics["detail_prioritization"] = plan.diagnostics

                    # Stage 22C: Discovery diagnostics
                    discovery_diag: Dict[str, Any] = {
                        "requested_hours_ahead": self.config.hours_ahead,
                        "superbet": {
                            "hours_ahead": getattr(sb_inst.superbet_config, 'hours_ahead', None),
                            "events_discovered": len(sb_discovered),
                        },
                    }
                    if hasattr(bc_inst, 'discovery') and bc_inst.discovery is not None and hasattr(bc_inst.discovery, 'stats'):
                        discovery_diag["betclic"] = dict(bc_inst.discovery.stats)
                    else:
                        discovery_diag["betclic"] = {
                            "hours_ahead": getattr(bc_inst.betclic_config, 'hours_ahead', None),
                            "events_discovered": len(bc_discovered),
                        }
                    diagnostics["discovery"] = discovery_diag

                    prio_bc = plan.prioritization_result_betclic
                    if prio_bc:
                        logger.info(
                            "Stage 25 Matched Detail Prioritization [%s]: Matched Overlap=%d, Budget=%d | Tier 0 (Sel/Avail): %d/%d | Tier 1 (Sel/Avail): %d/%d | Tier 2 (Sel/Avail): %d/%d | Samples: %s",
                            plan.diagnostics.get("scan_mode"),
                            len(overlap_bc_ids),
                            self.config.effective_max_detail_requests,
                            prio_bc.tier_0_selected,
                            prio_bc.tier_0_available,
                            prio_bc.tier_1_selected,
                            prio_bc.tier_1_available,
                            prio_bc.tier_2_selected,
                            prio_bc.tier_2_available,
                            prio_bc.tier_samples,
                        )
                except Exception as exc:
                    logger.warning("Error during coordinated Superbet & Betclic detail pre-discovery: %s", exc, exc_info=True)

            # Fallback single provider detail configuration if either provider lacks selected_event_ids
            for p_name in ordered_names:
                p_inst = provider_instances[p_name]
                bc_cfg = getattr(p_inst, "betclic_config", None)
                sb_cfg = getattr(p_inst, "superbet_config", None)
                if p_name == "betclic" and isinstance(p_inst, BetclicProvider) and bc_cfg is not None and not getattr(bc_cfg, "selected_event_ids", None):
                    try:
                        bc_discovered = p_inst.discover()
                        max_reqs = self.config.effective_max_detail_requests or getattr(bc_cfg, "max_detail_requests", 15) or 15
                        prio_result = self.event_selection_policy.prioritize_detail_events(
                            discovered_items=bc_discovered,
                            overlap_event_ids=set(),
                            max_detail_requests=max_reqs,
                            preferred_competitions=self.config.preferred_competitions,
                            hours_ahead=self.config.hours_ahead,
                            current_time=now_dt,
                        )
                        p_inst.configure_full_market_acquisition(event_ids=prio_result.selected_event_ids)
                        p_inst._discovered_items_cache = bc_discovered
                    except Exception as bc_disc_err:
                        logger.warning("Error during single Betclic detail configuration: %s", bc_disc_err)
                elif p_name == "superbet" and isinstance(p_inst, SuperbetProvider) and sb_cfg is not None and not getattr(sb_cfg, "selected_event_ids", None):
                    try:
                        sb_discovered = p_inst.discover()
                        max_reqs = self.config.effective_max_detail_requests or getattr(sb_cfg, "max_detail_requests", 15) or 15
                        prio_result = self.event_selection_policy.prioritize_detail_events(
                            discovered_items=sb_discovered,
                            overlap_event_ids=set(),
                            max_detail_requests=max_reqs,
                            preferred_competitions=self.config.preferred_competitions,
                            hours_ahead=self.config.hours_ahead,
                            current_time=now_dt,
                        )
                        p_inst.configure_full_market_acquisition(event_ids=prio_result.selected_event_ids)
                        p_inst._discovered_items_cache = sb_discovered
                    except Exception as sb_disc_err:
                        logger.warning("Error during single Superbet detail configuration: %s", sb_disc_err)

            # Stage 23A & Stage 25C: Execute providers concurrently via bounded ThreadPoolExecutor
            max_workers = min(
                max(1, len(ordered_names)),
                getattr(self.config, "max_provider_workers", 4) or 4,
            )
            worker_timeout = getattr(self.config, "effective_provider_timeout", 60.0) or 60.0

            # Stage 25C: Explicit runtime telemetry and sanity assertion before acquisition
            cur_scan_mode = str(getattr(self.config, "scan_mode", "NORMAL")).upper()
            cur_provider_timeout = float(getattr(self.config, "provider_timeout", 60.0))
            eff_provider_timeout = float(self.config.effective_provider_timeout)
            eff_max_details = int(self.config.effective_max_detail_requests)

            logger.info(
                "Stage 25C Concurrent Acquisition Init: scan_mode=%s, provider_timeout=%.1fs, effective_provider_timeout=%.1fs, effective_max_detail_requests=%d, max_workers=%d",
                cur_scan_mode,
                cur_provider_timeout,
                eff_provider_timeout,
                eff_max_details,
                max_workers,
            )

            if cur_scan_mode == "DEEP" and worker_timeout < 120.0:
                logger.error(
                    "CRITICAL: DEEP scan mode resolved worker_timeout=%.1fs (< 120.0s). Enforcing 120.0s.",
                    worker_timeout,
                )
                worker_timeout = 120.0

            def _run_single_provider(name: str, instance: BaseProvider) -> ProviderResult:
                return self.execution_engine.execute(instance)

            future_to_provider: Dict[concurrent.futures.Future, str] = {}
            with profiler.trace_phase("acquisition", counters={"providers": ordered_names}):
                with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
                    for p_name in ordered_names:
                        p_inst = provider_instances[p_name]
                        fut = pool.submit(_run_single_provider, p_name, p_inst)
                        future_to_provider[fut] = p_name

                    logger.info(
                        "Stage 25C concurrent.futures.wait(): waiting for %d provider(s) with worker_timeout=%.1fs (return_when=ALL_COMPLETED)",
                        len(future_to_provider),
                        worker_timeout,
                    )

                    # Wait for all provider tasks to complete or timeout
                    done, not_done = concurrent.futures.wait(
                        future_to_provider.keys(),
                        timeout=worker_timeout,
                        return_when=concurrent.futures.ALL_COMPLETED,
                    )

                # Post-teardown inspection: after exiting the ThreadPoolExecutor context manager,
                # all running futures have naturally finished executing (or were aborted).
                for fut in future_to_provider:
                    p_name = future_to_provider[fut]
                    p_res = None
                    try:
                        if fut.done():
                            p_res = fut.result()
                    except Exception as exc:
                        err_msg = f"Unexpected exception executing provider '{p_name}': {exc}"
                        logger.error(err_msg, exc_info=True)
                        errors.append(err_msg)
                        provider_results[p_name] = ProviderResult(
                            provider_name=p_name,
                            status=ProviderState.FAILED,
                            execution_duration=0.0,
                            errors=[str(exc)],
                        )
                        continue

                    if p_res is not None and getattr(p_res, "status", None) != ProviderState.FAILED:
                        if fut in not_done:
                            logger.info(
                                "Provider '%s' finished acquisition during graceful teardown (%d parsed, %d discovered); using completed payload",
                                p_name,
                                len(p_res.parsed_objects),
                                len(p_res.discovered_objects),
                            )
                        provider_results[p_name] = p_res
                    else:
                        err_msg = f"Provider '{p_name}' timed out after {worker_timeout:.1f}s during concurrent acquisition"
                        logger.error(err_msg)
                        errors.append(err_msg)
                        provider_results[p_name] = ProviderResult(
                            provider_name=p_name,
                            status=ProviderState.FAILED,
                            execution_duration=worker_timeout,
                            errors=[err_msg],
                        )

            # Stage 50 Telemetry Counters (profiled separately: runs after the
            # provider wait, over all parsed markets, on the scan critical path)
            profiler.start_phase("acquisition_telemetry")
            raw_markets_received_total = 0
            allowed_markets_total = 0
            discarded_markets_total = 0
            discarded_families_counter: Dict[str, int] = {}
            per_provider_counts: Dict[str, Dict[str, Any]] = {}
            events_in_scope_total = 0

            # Preserve deterministic provider result ordering and aggregate telemetry in the main orchestrator thread
            for p_name in ordered_names:
                p_result = provider_results.get(p_name)
                if p_result is None:
                    continue

                p_inst = provider_instances.get(p_name)
                total_discovered += len(p_result.discovered_objects)
                total_parsed += len(p_result.parsed_objects)

                prov_raw_mkts = 0
                prov_allowed_mkts = 0
                prov_discarded_mkts = 0
                prov_events_disc = len(p_result.discovered_objects)
                prov_events_in_scope = 0

                # Compute discovered popular events and raw markets
                for item in p_result.discovered_objects:
                    comp = self.event_selection_policy._extract_item_competition(item)
                    tier = self.event_selection_policy.calculate_competition_tier(comp, self.config.preferred_competitions)
                    if tier < 2:
                        resource_metrics.popular_events_discovered += 1
                        events_in_scope_total += 1
                        prov_events_in_scope += 1

                for p_obj in p_result.parsed_objects:
                    markets = getattr(p_obj, "markets", [])
                    raw_markets_received_total += len(markets)
                    prov_raw_mkts += len(markets)
                    resource_metrics.markets_discovered += len(markets)
                    for m in markets:
                        m_name = getattr(m, "name", "") or getattr(m, "market_type_code", "") or getattr(m, "market_type_id", "") or ""
                        cat = _categorize_market_type(str(m_name))
                        market_breakdown[cat]["discovered"] += 1
                        market_breakdown[cat]["acquired"] += 1
                        market_breakdown[cat]["parsed"] += 1

                        # Evaluate raw market against allowlist
                        is_allowed = False
                        if hasattr(m, "market_type_id") and m.market_type_id:
                            is_allowed = is_allowed_market_family(m.market_type_id, raw_name=m_name)
                        if not is_allowed:
                            is_allowed = is_allowed_market_family(cat, raw_name=m_name)
                        if not is_allowed and m_name:
                            # Direct check against Polish raw market names
                            lower_name = str(m_name).lower()
                            if any(k in lower_name for k in ("mecz", "wynik", "btts", "obie dru", "gole", "powy", "poni", "handicap", "podw", "szansa", "remis", "zawodnik", "strzel", "asyst", "kartk", "faul", "strza", "odbior", "ron", "rozn")):
                                is_allowed = True
                        if not is_allowed and cat != "OTHER":
                            is_allowed = True

                        if is_allowed:
                            allowed_markets_total += 1
                            prov_allowed_mkts += 1
                        else:
                            discarded_markets_total += 1
                            prov_discarded_mkts += 1
                            fam_name = get_market_family_name(cat, raw_name=str(m_name))
                            discarded_families_counter[fam_name] = discarded_families_counter.get(fam_name, 0) + 1

                per_provider_counts[p_name] = {
                    "events_discovered": prov_events_disc,
                    "events_in_scope": prov_events_in_scope,
                    "raw_markets": prov_raw_mkts,
                    "allowed_markets": prov_allowed_mkts,
                    "discarded_markets": prov_discarded_mkts,
                }

                # Extract Acquisition 2.0 explicit accounting object
                if getattr(p_result, "accounting", None) is not None:
                    acct_dict = p_result.accounting.to_dict()
                    resource_metrics.per_provider_accounting[p_name] = acct_dict
                elif p_inst and hasattr(p_inst, "get_accounting") and callable(p_inst.get_accounting):
                    try:
                        acct_obj = p_inst.get_accounting()
                        if acct_obj:
                            resource_metrics.per_provider_accounting[p_name] = acct_obj.to_dict()
                    except Exception:
                        pass

                # Extract provider HTTP and detail acquisition telemetry
                if p_inst and hasattr(p_inst, "fetcher") and hasattr(p_inst.fetcher, "stats"):
                    f_stats = p_inst.fetcher.stats
                    detail_reqs = f_stats.get("detail_requests_attempted", 0)
                    resource_metrics.detail_http_requests += detail_reqs
                    resource_metrics.total_http_requests += (1 + detail_reqs)
                    resource_metrics.detail_requests_total += detail_reqs
                    resource_metrics.detail_requests_success += f_stats.get("detail_requests_successful", 0)
                    resource_metrics.detail_requests_failed += f_stats.get("detail_requests_failed", 0)
                    resource_metrics.detail_requests_retried += f_stats.get("detail_requests_retried", 0)
                    resource_metrics.detail_requests_timeout += f_stats.get("detail_requests_timeout", 0)
                    resource_metrics.detail_queue_wait_ms += f_stats.get("detail_queue_wait_ms", 0.0)
                    max_conc = f_stats.get("max_concurrent_details", 1)
                    if isinstance(max_conc, (int, float)):
                        resource_metrics.max_concurrent_details = max(int(resource_metrics.max_concurrent_details or 1), int(max_conc))
                elif p_inst and hasattr(p_inst, "acquisition_metrics") and isinstance(p_inst.acquisition_metrics, dict):
                    detail_reqs = p_inst.acquisition_metrics.get("detail_requests_attempted", 0)
                    resource_metrics.detail_http_requests += detail_reqs
                    resource_metrics.total_http_requests += (1 + detail_reqs)
                else:
                    resource_metrics.total_http_requests += 1

                # Parse duration
                if hasattr(p_result, "metrics") and hasattr(p_result.metrics, "stage_durations"):
                    parse_dur = p_result.metrics.stage_durations.get("parse", 0.0)
                    resource_metrics.detail_parse_ms += parse_dur * 1000.0

                if p_result.status == ProviderState.FAILED:
                    err_str = f"Provider '{p_name}' failed acquisition (status={p_result.status.value})"
                    errors.append(err_str)
                    if p_result.errors:
                        errors.extend([f"[{p_name}] {e}" for e in p_result.errors])
                elif p_result.status == ProviderState.DEGRADED:
                    degraded_detail = ""
                    if p_result.validation_report and p_result.validation_report.rejection_reasons:
                        sample_rej = p_result.validation_report.rejection_reasons[0].get("reasons", [])
                        degraded_detail = f" ({p_result.validation_report.invalid_objects} of {p_result.validation_report.total_objects} events invalid: {', '.join(sample_rej)})"
                    warnings.append(f"Provider '{p_name}' completed in DEGRADED state{degraded_detail}")

                if p_result.warnings:
                    warnings.extend([f"[{p_name}] {w}" for w in p_result.warnings])

            profiler.finish_phase("acquisition_telemetry")
            stage_timings.acquisition_seconds = time.perf_counter() - t_acq_0

            # ──────────────────────────────────────────────────────────────────
            # 2. STAGE 2: NORMALIZATION & EVENT SELECTION FILTERING
            # ──────────────────────────────────────────────────────────────────
            t_norm_0 = time.perf_counter()
            provider_graphs: Dict[str, List[NormalizedGraph]] = {}


            with profiler.trace_phase("normalization", counters={"providers": target_provider_names}):
                for p_name in target_provider_names:
                    p_res = provider_results.get(p_name)
                    if not p_res or p_res.status == ProviderState.FAILED:
                        provider_graphs[p_name] = []
                        continue

                    try:
                        norm_res = self.normalization_engine.normalize(
                            provider_name=p_name,
                            parsed_objects=p_res.parsed_objects,
                        )
                        normalization_results[p_name] = norm_res

                        # Apply Event Selection Policy filtering/ranking on graphs favoring cross-provider overlap
                        prov_overlap = overlap_sb_ids if p_name == "superbet" else (overlap_bc_ids if p_name == "betclic" else set())
                        filtered_graphs = self.event_selection_policy.filter_normalized_graphs(
                            graphs=norm_res.graphs,
                            limit=self.config.event_limit,
                            preferred_competitions=self.config.preferred_competitions,
                            overlap_event_ids=prov_overlap,
                        )

                        provider_graphs[p_name] = filtered_graphs
                        total_normalized += len(filtered_graphs)
                        total_norm_failed += norm_res.failed_count

                        prov_norm_mkts = 0
                        for g in filtered_graphs:
                            comp = g.competition.name if g.competition else ""
                            if self.event_selection_policy.calculate_competition_tier(comp, self.config.preferred_competitions) < 2:
                                resource_metrics.popular_events_selected += 1
                            resource_metrics.markets_normalized += len(g.markets)
                            prov_norm_mkts += len(g.markets)
                            for m in g.markets:
                                cat = _categorize_market_type(m.market_type, m)
                                market_breakdown[cat]["normalized"] += 1

                        if p_name in per_provider_counts:
                            per_provider_counts[p_name]["normalized_markets"] = prov_norm_mkts

                        if norm_res.errors:
                            warnings.extend([f"[{p_name} Normalization] {e}" for e in norm_res.errors])

                    except Exception as exc:
                        err_msg = f"Unexpected exception normalizing data for provider '{p_name}': {exc}"
                        logger.error(err_msg, exc_info=True)
                        errors.append(err_msg)
                        provider_graphs[p_name] = []

            stage_timings.normalization_seconds = time.perf_counter() - t_norm_0
            resource_metrics.detail_normalization_ms = stage_timings.normalization_seconds * 1000.0
            resource_metrics.detail_acquisition_seconds = stage_timings.acquisition_seconds
            resource_metrics.markets_per_selected_event = (
                round(resource_metrics.markets_discovered / resource_metrics.detail_events_selected, 2)
                if resource_metrics.detail_events_selected > 0
                else 0.0
            )
            resource_metrics.events_discovered = total_discovered
            resource_metrics.events_selected = total_normalized
            resource_metrics.events_parsed = total_parsed
            resource_metrics.normalized_graphs = total_normalized

            # Stage 50: Assign telemetry fields to resource_metrics
            top_20_discarded = sorted(discarded_families_counter.items(), key=lambda x: x[1], reverse=True)[:20]
            resource_metrics.events_in_competition_scope = events_in_scope_total
            resource_metrics.raw_markets_received = raw_markets_received_total
            resource_metrics.allowed_markets = allowed_markets_total
            resource_metrics.discarded_markets = discarded_markets_total
            resource_metrics.normalized_allowed_markets = resource_metrics.markets_normalized
            resource_metrics.discarded_market_families_top_20 = top_20_discarded
            resource_metrics.per_provider_counts = per_provider_counts

            diagnostics["stage50_scope_telemetry"] = {
                "events_discovered": total_discovered,
                "events_in_competition_scope": events_in_scope_total,
                "events_selected_for_detail": resource_metrics.detail_events_selected,
                "raw_markets_received": raw_markets_received_total,
                "allowed_markets": allowed_markets_total,
                "discarded_markets": discarded_markets_total,
                "normalized_allowed_markets": resource_metrics.markets_normalized,
                "discarded_market_families_top_20": top_20_discarded,
                "per_provider_counts": per_provider_counts,
            }

            # ──────────────────────────────────────────────────────────────────
            # 3. MULTI-PROVIDER AVAILABILITY & PARTIAL FAILURE GATE
            # Stage 11: N-way matching — no single provider is required as anchor.
            # Matching proceeds if ANY 2+ providers have valid normalized graphs.
            # ──────────────────────────────────────────────────────────────────
            # Collect all providers with valid data
            providers_with_data = [
                p_name for p_name in target_provider_names
                if len(provider_graphs.get(p_name, [])) > 0
            ]
            failed_providers = [
                p_name for p_name in target_provider_names
                if p_name not in providers_with_data
            ]

            # Legacy compatibility: still compute src/tgt for telemetry
            src_p = self.config.source_provider
            tgt_p = self.config.target_provider
            src_graphs = provider_graphs.get(src_p, [])
            tgt_graphs = provider_graphs.get(tgt_p, [])

            if len(providers_with_data) == 0:
                # Check if all providers explicitly failed or if they completed cleanly with 0 data
                all_explicitly_failed = all(
                    provider_results.get(p_name) and provider_results[p_name].status == ProviderState.FAILED
                    for p_name in target_provider_names
                ) if target_provider_names else True
                if all_explicitly_failed:
                    cycle_status = CycleStatus.FAILED
                    warnings.append("All configured providers failed; aborting downstream stages.")
                    diagnostics["data_availability"] = "ZERO_EVENTS_ALL_PROVIDERS"
                else:
                    cycle_status = CycleStatus.SUCCESS
                    warnings.append("All configured providers completed cleanly with 0 events; aborting downstream stages.")
                    diagnostics["data_availability"] = "ZERO_EVENTS_BOTH_PROVIDERS" if len(target_provider_names) == 2 else "ZERO_EVENTS_ALL_PROVIDERS"
            elif len(providers_with_data) == 1:
                # Only one provider has data — cannot cross-match
                cycle_status = CycleStatus.PARTIAL
                sole_provider = providers_with_data[0]
                warnings.append(
                    f"Only provider '{sole_provider}' has valid data ({len(provider_graphs[sole_provider])} graphs). "
                    f"Cross-bookmaker matching requires 2+ providers. Matching skipped."
                )
                if failed_providers:
                    warnings.append(f"Failed/empty providers: {', '.join(failed_providers)}")
                diagnostics["data_availability"] = "SINGLE_PROVIDER_ONLY"
            else:
                # 2+ providers have data — proceed to N-way matching
                if failed_providers:
                    cycle_status = CycleStatus.PARTIAL
                    warnings.append(
                        f"Provider(s) {', '.join(failed_providers)} failed/empty; "
                        f"matching proceeds with {', '.join(providers_with_data)}."
                    )
                diagnostics["data_availability"] = "MULTI_PROVIDER"

            # Aggregate all graphs from providers with data for N-way matching
            all_matchable_graphs: List[NormalizedGraph] = []
            for p_name in providers_with_data:
                all_matchable_graphs.extend(provider_graphs[p_name])

            # Also compute legacy all_target_graphs for backward-compatible telemetry
            all_target_graphs: List[NormalizedGraph] = []
            for p_name, g_list in provider_graphs.items():
                if p_name != src_p:
                    all_target_graphs.extend(g_list)

            # ──────────────────────────────────────────────────────────────────
            # ──────────────────────────────────────────────────────────────────
            # 4. STAGE 3: ENTITY & EVENT MATCHING (Cross-Bookmaker Validation)
            # Stage 11: N-way matching — pass all provider graphs. The validation
            # pipeline handles pairwise matching between all provider pairs internally.
            # ──────────────────────────────────────────────────────────────────
            if len(providers_with_data) >= 2:
                t_match_0 = time.perf_counter()
                try:
                    with profiler.trace_phase("matching", counters={"matchable_graphs": len(all_matchable_graphs)}):
                        # Stage 12.3: Full N-way matching across all available normalized provider graphs
                        validation_result = self.validation_pipeline.run_n_way(
                            all_items=all_matchable_graphs,
                        )
                    if validation_result.errors:
                        warnings.extend([f"[Validation Pipeline] {e}" for e in validation_result.errors])

                    all_matched_lineages: List[Any] = []
                    canonical_candidates_map: Dict[Tuple[str, str], List[Any]] = {}
                    category_candidates: Dict[str, Set[Tuple[str, str]]] = {cat: set() for cat in market_breakdown}

                    if validation_result.event_validation_records:
                        for record in validation_result.event_validation_records:
                            for m_lineage in record.matched_markets:
                                all_matched_lineages.append(m_lineage)
                                cat = _categorize_market_type(m_lineage.canonical_market_key)
                                market_breakdown[cat]["matched"] += 1
                                
                                cand_key = (record.canonical_event.canonical_event_id, m_lineage.canonical_market_key.to_key_string())
                                canonical_candidates_map.setdefault(cand_key, []).append(m_lineage)
                                category_candidates[cat].add(cand_key)

                    for cat, cand_set in category_candidates.items():
                        market_breakdown[cat]["candidates"] = len(cand_set)

                    evaluation_candidates_total = len(canonical_candidates_map)
                    resource_metrics.markets_matched = validation_result.metrics.matched_market_count
                    resource_metrics.matched_markets_total = validation_result.metrics.matched_market_count
                    resource_metrics.evaluation_candidates_total = evaluation_candidates_total

                    # Structured matching diagnostic (actionable matches between execution bookmakers)
                    cand_cnt = len(validation_result.event_candidates)
                    actionable_matched_cnt = sum(
                        1 for ce in validation_result.canonical_events
                        if ("superbet" in ce.sources and "betclic" in ce.sources)
                        or (not any(b in ce.sources for b in ("superbet", "betclic", "bet365", "unibet")) and len(ce.sources) >= 2)
                    )
                    rej_breakdown = dict(validation_result.rejection_reasons_breakdown)
                    
                    if actionable_matched_cnt == 0:
                        if cand_cnt == 0:
                            expl = "Provider coverage did not overlap (0 candidate pairs generated)."
                        else:
                            expl = f"{cand_cnt} candidate pair(s) generated but rejected by safety rules / low matching score."
                    else:
                        expl = f"{actionable_matched_cnt} event(s) successfully matched across execution bookmakers."

                    # Standard 6-pair coverage summary
                    known_pairs = [
                        ("betclic", "superbet"),
                        ("bet365", "superbet"),
                        ("superbet", "unibet"),
                        ("bet365", "betclic"),
                        ("betclic", "unibet"),
                        ("bet365", "unibet"),
                    ]
                    pair_coverage_diag: Dict[str, Any] = {}
                    for p1, p2 in known_pairs:
                        p_key = f"{min(p1, p2)}:{max(p1, p2)}"
                        if p_key in validation_result.provider_pair_coverage:
                            pair_coverage_diag[p_key] = validation_result.provider_pair_coverage[p_key]
                        else:
                            # Check if both providers were present with data
                            p1_has_data = any(g for p_name, g_list in provider_graphs.items() for g in g_list if (p_name == p1 or (p_name == "odds_api" and g.event.metadata.get("odds_api", {}).get("bookmaker") == p1)))
                            p2_has_data = any(g for p_name, g_list in provider_graphs.items() for g in g_list if (p_name == p2 or (p_name == "odds_api" and g.event.metadata.get("odds_api", {}).get("bookmaker") == p2)))
                            if p1_has_data and p2_has_data:
                                pair_coverage_diag[p_key] = {
                                    "status": "EVALUATED",
                                    "candidate_count": 0,
                                    "matched_count": 0,
                                    "ambiguous_count": 0,
                                    "team_identity_mismatch_count": 0,
                                    "suffix_veto_count": 0,
                                    "low_match_score_count": 0,
                                }
                            else:
                                pair_coverage_diag[p_key] = {"status": "NOT_AVAILABLE"}

                    diagnostics["matching_diagnostic"] = {
                        "candidates_generated": cand_cnt,
                        "matched_events": actionable_matched_cnt,
                        "rejected_candidates": len(validation_result.event_decisions) - actionable_matched_cnt,
                        "rejection_reasons_breakdown": rej_breakdown,
                        "provider_pair_coverage": pair_coverage_diag,
                        "explanation": expl,
                        "sample_rejected_pairs": [
                            {
                                "source_name": d.evidence.get("source_name"),
                                "target_name": d.evidence.get("target_name"),
                                "score": float(d.total_score),
                                "rejection_reason": d.rejection_reason_code or "LOW_MATCH_SCORE",
                                "vetoes": list(d.veto_reasons),
                            }
                            for d in validation_result.event_decisions
                            if d.decision.value != "MATCHED"
                        ][:5],
                    }

                except Exception as exc:
                    err_msg = f"Unexpected exception in cross-bookmaker validation pipeline: {exc}"
                    logger.error(err_msg, exc_info=True)
                    errors.append(err_msg)
                    cycle_status = CycleStatus.FAILED

                stage_timings.matching_seconds = time.perf_counter() - t_match_0

            # ──────────────────────────────────────────────────────────────────
            # 5. STAGE 4: SUREBET DETECTION & NEAREST OPPORTUNITY EXTRACTION
            # ──────────────────────────────────────────────────────────────────
            nearest_opp_dict: Optional[Dict[str, Any]] = None
            if validation_result is not None:
                t_det_0 = time.perf_counter()
                try:
                    with profiler.trace_phase("detection"):
                        detection_result = self.surebet_detector.detect(validation_result)
                    if detection_result.errors:
                        warnings.extend([f"[Surebet Detector] {e}" for e in detection_result.errors])

                    eval_map: Dict[Tuple[str, str], Any] = {}
                    if detection_result.evaluations:
                        for eval_item in detection_result.evaluations:
                            k = (eval_item.canonical_event_id, eval_item.canonical_market_key.to_key_string())
                            eval_map[k] = eval_item

                    eval_records: List[MatchedMarketEvaluationRecord] = []
                    exclusion_breakdown: Dict[str, int] = {r.value: 0 for r in EvaluationExclusionReason}

                    evaluated_complete_count = 0
                    valid_surebets_count = 0
                    rejected_count = 0
                    not_evaluated_count = 0

                    for (ev_id, mkt_key_str), lineages in canonical_candidates_map.items():
                        primary_lineage = lineages[0]
                        mkt_type = getattr(primary_lineage.canonical_market_key, "market_type", "")
                        cat = _categorize_market_type(primary_lineage.canonical_market_key)

                        # Determine source and target providers
                        src_prov_ids = getattr(primary_lineage.source_market, "provider_ids", {}) or {}
                        tgt_prov_ids = getattr(primary_lineage.target_market, "provider_ids", {}) or {}
                        src_p = list(src_prov_ids.keys())[0] if src_prov_ids else None
                        tgt_p = list(tgt_prov_ids.keys())[0] if tgt_prov_ids else None

                        # Evaluate primary lineage
                        if len(primary_lineage.comparable_selections) == 0:
                            state = MarketEvaluationState.NOT_EVALUATED
                            reason = EvaluationExclusionReason.ZERO_COMPARABLE_SELECTIONS
                            opp_id = None
                            details = {"reason": "Zero comparable selections between providers"}
                        elif (ev_id, mkt_key_str) in eval_map:
                            cand_eval = eval_map[(ev_id, mkt_key_str)]
                            if cand_eval.status == SurebetStatus.SUREBET:
                                state = MarketEvaluationState.OPPORTUNITY
                                reason = None
                                opp_id = cand_eval.opportunity.opportunity_id if cand_eval.opportunity else None
                                details = {
                                    "arbitrage_margin": float(cand_eval.arbitrage_margin) if cand_eval.arbitrage_margin else None,
                                    "implied_probability_sum": float(cand_eval.implied_probability_sum) if cand_eval.implied_probability_sum else None,
                                }
                            elif cand_eval.status == SurebetStatus.NO_SUREBET:
                                state = MarketEvaluationState.EVALUATED
                                reason = None
                                opp_id = None
                                details = {
                                    "arbitrage_margin": float(cand_eval.arbitrage_margin) if cand_eval.arbitrage_margin else None,
                                    "implied_probability_sum": float(cand_eval.implied_probability_sum) if cand_eval.implied_probability_sum else None,
                                }
                            elif cand_eval.status == SurebetStatus.INCOMPLETE_MARKET:
                                state = MarketEvaluationState.REJECTED
                                reason = EvaluationExclusionReason.INCOMPLETE_SELECTIONS
                                opp_id = None
                                details = {"rejection_reason": cand_eval.rejection_reason}
                            elif cand_eval.status == SurebetStatus.INVALID_MARKET:
                                state = MarketEvaluationState.REJECTED
                                code = getattr(cand_eval, "exclusion_reason_code", None)
                                if code == "LINE_INVALID":
                                    reason = EvaluationExclusionReason.LINE_INVALID
                                elif code == "INCOMPLETE_EVENT_IDENTITY":
                                    reason = EvaluationExclusionReason.INCOMPLETE_EVENT_IDENTITY
                                elif code == "INVALID_ODDS":
                                    reason = EvaluationExclusionReason.INVALID_ODDS
                                elif code == "INVALID_MARKET_IDENTITY":
                                    reason = EvaluationExclusionReason.INVALID_MARKET_IDENTITY
                                elif code == "CROSS_MARKET_CONTAMINATION":
                                    reason = EvaluationExclusionReason.CROSS_MARKET_CONTAMINATION
                                else:
                                    reason = EvaluationExclusionReason.LINE_INVALID
                                opp_id = None
                                details = {"rejection_reason": cand_eval.rejection_reason, "exclusion_reason_code": code}
                            elif cand_eval.status == SurebetStatus.UNSUPPORTED_MARKET:
                                state = MarketEvaluationState.NOT_EVALUATED
                                reason = EvaluationExclusionReason.UNSUPPORTED_MARKET
                                opp_id = None
                                details = {"rejection_reason": cand_eval.rejection_reason}
                            else:
                                state = MarketEvaluationState.NOT_EVALUATED
                                reason = EvaluationExclusionReason.OTHER
                                opp_id = None
                                details = {}
                        else:
                            state = MarketEvaluationState.NOT_EVALUATED
                            reason = EvaluationExclusionReason.UNSUPPORTED_MARKET
                            opp_id = None
                            details = {}

                        rec = MatchedMarketEvaluationRecord(
                            canonical_event_id=ev_id,
                            canonical_market_key=mkt_key_str,
                            state=state,
                            reason=reason,
                            market_type=mkt_type,
                            source_provider=src_p,
                            target_provider=tgt_p,
                            opportunity_id=opp_id,
                            details=details,
                        )
                        eval_records.append(rec)

                        if state in (MarketEvaluationState.EVALUATED, MarketEvaluationState.OPPORTUNITY):
                            evaluated_complete_count += 1
                            market_breakdown[cat]["evaluated"] += 1
                            if state == MarketEvaluationState.OPPORTUNITY:
                                valid_surebets_count += 1
                        elif state == MarketEvaluationState.REJECTED:
                            rejected_count += 1
                            if reason:
                                exclusion_breakdown[reason.value] = exclusion_breakdown.get(reason.value, 0) + 1
                        elif state == MarketEvaluationState.NOT_EVALUATED:
                            not_evaluated_count += 1
                            if reason:
                                exclusion_breakdown[reason.value] = exclusion_breakdown.get(reason.value, 0) + 1

                        # Process any duplicate lineages collapsed into this canonical candidate
                        for dup_lineage in lineages[1:]:
                            dup_rec = MatchedMarketEvaluationRecord(
                                canonical_event_id=ev_id,
                                canonical_market_key=mkt_key_str,
                                state=MarketEvaluationState.NOT_EVALUATED,
                                reason=EvaluationExclusionReason.DUPLICATE_CANONICAL_MARKET,
                                market_type=mkt_type,
                                source_provider=src_p,
                                target_provider=tgt_p,
                                details={"duplicate_of": mkt_key_str},
                            )
                            eval_records.append(dup_rec)
                            not_evaluated_count += 1
                            exclusion_breakdown[EvaluationExclusionReason.DUPLICATE_CANONICAL_MARKET.value] = (
                                exclusion_breakdown.get(EvaluationExclusionReason.DUPLICATE_CANONICAL_MARKET.value, 0) + 1
                            )

                    # 4. Calculate per-category excluded, coverage, and zero-match reasons
                    cand_cnt = len(validation_result.event_candidates)
                    matched_cnt = len(validation_result.canonical_events)

                    for cat, data in market_breakdown.items():
                        cands = data.get("candidates", 0)
                        evals = data.get("evaluated", 0)
                        mtch = data.get("matched", 0)
                        disc = data.get("discovered", 0)
                        data["excluded"] = max(0, cands - evals)
                        if cands > 0:
                            data["coverage"] = f"{(evals / cands * 100):.1f}%"
                        else:
                            data["coverage"] = "0.0%"

                        if mtch == 0:
                            if matched_cnt == 0:
                                if cand_cnt == 0:
                                    data["zero_match_reason"] = "NO_PROVIDER_OVERLAP"
                                else:
                                    data["zero_match_reason"] = "EVENT_IDENTITY_MISMATCH"
                            else:
                                if disc == 0 or cands == 0:
                                    data["zero_match_reason"] = "MARKET_NOT_PRESENT"
                                else:
                                    data["zero_match_reason"] = "SELECTION_MISMATCH"
                        else:
                            data["zero_match_reason"] = None

                    active_breakdown = {k: v for k, v in exclusion_breakdown.items() if v > 0}
                    total_excluded = rejected_count + not_evaluated_count
                    coverage_rate = (evaluated_complete_count / evaluation_candidates_total) if evaluation_candidates_total > 0 else 0.0

                    resource_metrics.markets_evaluated = evaluated_complete_count
                    resource_metrics.matched_markets_total = resource_metrics.markets_matched
                    resource_metrics.evaluation_candidates_total = evaluation_candidates_total
                    resource_metrics.evaluated_markets_total = evaluated_complete_count
                    resource_metrics.evaluation_excluded_total = total_excluded
                    resource_metrics.evaluation_exclusion_breakdown = active_breakdown
                    resource_metrics.evaluation_coverage_rate = float(coverage_rate)
                    resource_metrics.market_coverage_breakdown = market_breakdown

                    # Stage 13 Explicit Reconciled Telemetry
                    resource_metrics.surebet_candidates = evaluation_candidates_total
                    resource_metrics.valid_surebets = valid_surebets_count
                    resource_metrics.rejected_markets_total = rejected_count
                    resource_metrics.not_evaluated_markets_total = not_evaluated_count
                    resource_metrics.rejection_reasons_breakdown = active_breakdown
                    resource_metrics.market_evaluation_records = eval_records


                    # Telemetry and diagnostics for zero-surebet interpretation
                    if len(detection_result.opportunities) == 0 and detection_result.no_surebet_evaluations:
                        valid_no_surebets = [
                            e for e in detection_result.no_surebet_evaluations
                            if e.implied_probability_sum is not None
                        ]
                        if valid_no_surebets:
                            best_eval = min(valid_no_surebets, key=lambda e: e.implied_probability_sum)
                            S = best_eval.implied_probability_sum
                            margin = best_eval.arbitrage_margin
                            distance = S - Decimal("1.0")
                            legs_data = [
                                {
                                    "selection_type": str(l.selection_type),
                                    "provider": str(l.provider),
                                    "odds": float(l.odds),
                                }
                                for l in best_eval.best_legs
                            ]
                            nearest_opp_dict = {
                                "canonical_event_id": best_eval.canonical_event_id,
                                "canonical_market_key": best_eval.canonical_market_key.to_key_string(),
                                "market_type": str(best_eval.canonical_market_key.market_type),
                                "line": str(best_eval.canonical_market_key.line) if best_eval.canonical_market_key.line is not None else None,
                                "implied_probability_sum": float(S),
                                "arbitrage_margin": float(margin) if margin is not None else None,
                                "distance_to_surebet": float(distance),
                                "best_legs": legs_data,
                            }
                            diagnostics["nearest_opportunity"] = {
                                "event": best_eval.canonical_event_id,
                                "market": best_eval.canonical_market_key.to_key_string(),
                                "implied_probability_sum": str(S),
                                "margin_pct": f"{float(margin * 100):.2f}" if margin is not None else "0.00",
                                "distance_to_arbitrage": str(distance),
                            }

                except Exception as exc:
                    err_msg = f"Unexpected exception in surebet detection engine: {exc}"
                    logger.error(err_msg, exc_info=True)
                    errors.append(err_msg)
                    cycle_status = CycleStatus.FAILED

                stage_timings.detection_seconds = time.perf_counter() - t_det_0

            # ──────────────────────────────────────────────────────────────────
            # 6. STAGE 5: OPPORTUNITY LIFECYCLE, ALERT POLICY & DISPATCH
            # ──────────────────────────────────────────────────────────────────
            if detection_result is not None and self.lifecycle_manager is not None:
                t_life_0 = time.perf_counter()
                try:
                    with profiler.trace_phase("lifecycle", counters={"opportunities": len(detection_result.opportunities)}):
                        lifecycle_summary = self.lifecycle_manager.process_and_dispatch(
                            detection_result=detection_result,
                            dispatcher=self.dispatcher,
                            max_misses=self.config.max_misses_for_expiration,
                            evaluation_time=now_dt,
                        )
                except Exception as exc:
                    err_msg = f"Unexpected exception in opportunity lifecycle manager: {exc}"
                    logger.error(err_msg, exc_info=True)
                    errors.append(err_msg)
                    cycle_status = CycleStatus.FAILED

                stage_timings.lifecycle_seconds = time.perf_counter() - t_life_0

            # ──────────────────────────────────────────────────────────────────
            # 7. STAGE 5.5: REFERENCE ODDS ACQUISITION & VALUEBET DETECTION
            # ──────────────────────────────────────────────────────────────────
            valuebet_result: Optional[ValueBetDetectionResult] = None
            valuebet_lifecycle_batch: Optional[ValuebetLifecycleBatch] = None
            t_val_0 = time.perf_counter()

            if self.config.enable_valuebets and len(provider_graphs) > 0:
                try:
                    with profiler.trace_phase("valuebets"):
                        all_graphs = []
                        for p_graphs in provider_graphs.values():
                            all_graphs.extend(p_graphs)

                        if all_graphs:
                            ref_events = self.reference_provider.fetch_reference_events(sport="soccer")
                            valuebet_result = self.valuebet_engine.detect_valuebets(
                                bookmaker_graphs=all_graphs,
                                reference_events=ref_events,
                            )

                            if self.valuebet_lifecycle_manager is not None and valuebet_result.candidates:
                                valuebet_lifecycle_batch = self.valuebet_lifecycle_manager.evaluate_candidates(
                                    candidates=valuebet_result.candidates,
                                    evaluation_time=now_dt,
                                )

                                # Dispatch qualified valuebets within budget
                                if valuebet_lifecycle_batch.to_dispatch:
                                    for val_cand in valuebet_lifecycle_batch.to_dispatch:
                                        disp_res = self.dispatcher.dispatch(val_cand)
                                        fp = generate_valuebet_fingerprint(val_cand)
                                        is_delivered = disp_res.status.value == "DELIVERED"
                                        if self.opportunity_repository is not None:
                                            self.opportunity_repository.record_delivery_result(
                                                fingerprint=fp,
                                                success=is_delivered,
                                                delivery_status=disp_res.status.value,
                                                alert_time=now_dt,
                                            )

                                # Expire missing active valuebets
                                evaluated_fps = {generate_valuebet_fingerprint(c) for c in valuebet_result.candidates}
                                expired_recs = self.valuebet_lifecycle_manager.expire_missing_candidates(
                                    evaluated_fingerprints=evaluated_fps,
                                    evaluation_time=now_dt,
                                    max_misses=self.config.max_misses_for_expiration,
                                )
                                if expired_recs:
                                    valuebet_lifecycle_batch.expired_count = len(expired_recs)

                            # Extract Valuebet rejection breakdown
                            if valuebet_result and hasattr(valuebet_result, "metrics"):
                                vm = valuebet_result.metrics
                                val_rej_breakdown: Dict[str, int] = {}
                                if getattr(vm, "markets_rejected_incomplete", 0) > 0:
                                    val_rej_breakdown["INCOMPLETE_REFERENCE_MARKET"] = vm.markets_rejected_incomplete
                                if getattr(vm, "markets_rejected_stale", 0) > 0:
                                    val_rej_breakdown["STALE_REFERENCE_DATA"] = vm.markets_rejected_stale
                                if getattr(vm, "markets_rejected_invalid_odds", 0) > 0:
                                    val_rej_breakdown["INVALID_ODDS"] = vm.markets_rejected_invalid_odds
                                if getattr(vm, "markets_rejected_unsupported", 0) > 0:
                                    val_rej_breakdown["UNSUPPORTED_MARKET_TYPE"] = vm.markets_rejected_unsupported
                                # P1-004: previously silent exits, now counted.
                                if getattr(vm, "markets_rejected_market_key", 0) > 0:
                                    val_rej_breakdown["UNSUPPORTED_MARKET_KEY"] = vm.markets_rejected_market_key
                                if getattr(vm, "markets_rejected_reference_missing", 0) > 0:
                                    val_rej_breakdown["REFERENCE_MARKET_MISSING"] = vm.markets_rejected_reference_missing
                                if getattr(vm, "selections_rejected_key_missing", 0) > 0:
                                    val_rej_breakdown["SELECTION_KEY_MISSING"] = vm.selections_rejected_key_missing
                                if getattr(vm, "selections_rejected_reference_missing", 0) > 0:
                                    val_rej_breakdown["REFERENCE_SELECTION_MISSING"] = vm.selections_rejected_reference_missing
                                if getattr(vm, "selections_rejected_invalid_odds", 0) > 0:
                                    val_rej_breakdown["INVALID_EXECUTION_ODDS"] = vm.selections_rejected_invalid_odds
                                if getattr(vm, "events_unmatched", 0) > 0:
                                    val_rej_breakdown["UNMATCHED_EVENT"] = vm.events_unmatched
                                resource_metrics.valuebet_rejection_reasons_breakdown = val_rej_breakdown
                                diagnostics["valuebet_rejection_breakdown"] = val_rej_breakdown

                except Exception as exc:
                    err_msg = f"Unexpected exception in valuebet subsystem: {exc}"
                    logger.warning(err_msg, exc_info=True)
                    warnings.append(err_msg)
                    diagnostics["valuebet_error"] = str(exc)

            stage_timings.valuebet_seconds = time.perf_counter() - t_val_0

            # ──────────────────────────────────────────────────────────────────
            # 8. STAGE 6: DELIVERY RECONCILIATION & RETRY RECOVERY
            # ──────────────────────────────────────────────────────────────────
            if self.config.enable_reconciliation and self.reconciliation_service is not None:
                t_rec_0 = time.perf_counter()
                try:
                    with profiler.trace_phase("reconciliation"):
                        reconciliation_summary = self.reconciliation_service.reconcile(
                            current_time=now_dt,
                            limit=self.config.reconciliation_limit,
                        )
                except Exception as exc:
                    err_msg = f"Unexpected exception in delivery reconciliation service: {exc}"
                    logger.warning(err_msg, exc_info=True)
                    warnings.append(err_msg)

                stage_timings.reconciliation_seconds = time.perf_counter() - t_rec_0

        except Exception as fatal_exc:
            err_msg = f"Critical fatal exception during scan cycle execution: {fatal_exc}"
            logger.critical(err_msg, exc_info=True)
            errors.append(err_msg)
            cycle_status = CycleStatus.FAILED
            if self.config.fail_on_critical_error:
                raise CriticalPipelineFailure(err_msg) from fatal_exc

        finally:
            # Cleanly commit and release any repository sessions held during the cycle
            for repo in (self.opportunity_repository, self.delivery_repository):
                if repo is not None and hasattr(repo, "session") and repo.session is not None:
                    try:
                        repo.session.commit()
                    except Exception:
                        try:
                            repo.session.rollback()
                        except Exception:
                            pass

            cycle_duration = time.perf_counter() - cycle_t0
            stage_timings.total_duration_seconds = cycle_duration
            completed_at = datetime.now(timezone.utc).isoformat()

            # Record memory telemetry
            _, peak_mem = tracemalloc.get_traced_memory()
            resource_metrics.peak_memory_mb = peak_mem / (1024 * 1024)

            # Update aggregated telemetry
            if validation_result:
                resource_metrics.matched_events = len(validation_result.canonical_events)
            if detection_result:
                resource_metrics.selections_evaluated = sum(len(e.best_legs) for e in detection_result.evaluations)

            resource_metrics.market_coverage_breakdown = market_breakdown


            # Resource Budget Enforcement & Safety Tripwires
            if self.config.resource_budget:
                budget = self.config.resource_budget
                if budget.max_duration_seconds is not None and cycle_duration > budget.max_duration_seconds:
                    msg = f"Resource budget exceeded: scan duration {cycle_duration:.2f}s > {budget.max_duration_seconds:.2f}s limit"
                    warnings.append(msg)
                    diagnostics["budget_exceeded_duration"] = True

                if budget.max_http_requests is not None and resource_metrics.total_http_requests > budget.max_http_requests:
                    msg = f"Resource budget exceeded: HTTP requests {resource_metrics.total_http_requests} > {budget.max_http_requests} limit"
                    warnings.append(msg)
                    diagnostics["budget_exceeded_http_requests"] = True

                if budget.max_detail_requests is not None and resource_metrics.detail_http_requests > budget.max_detail_requests:
                    msg = f"Resource budget exceeded: detail requests {resource_metrics.detail_http_requests} > {budget.max_detail_requests} limit"
                    warnings.append(msg)
                    diagnostics["budget_exceeded_detail_requests"] = True

                if budget.max_events is not None and total_parsed > budget.max_events:
                    msg = f"Resource budget exceeded: parsed events {total_parsed} > {budget.max_events} limit"
                    warnings.append(msg)
                    diagnostics["budget_exceeded_events"] = True

                if budget.max_memory_mb is not None and resource_metrics.peak_memory_mb > budget.max_memory_mb:
                    msg = f"Resource budget exceeded: peak memory {resource_metrics.peak_memory_mb:.2f} MB > {budget.max_memory_mb:.2f} MB limit"
                    warnings.append(msg)
                    diagnostics["budget_exceeded_memory"] = True

        # ──────────────────────────────────────────────────────────────────────
        # 9. STRUCTURED SCAN CYCLE RESULT ASSEMBLY
        # ──────────────────────────────────────────────────────────────────────
        matched_events = len(validation_result.canonical_events) if validation_result else 0
        unmatched_events = len(validation_result.unmatched_events) if validation_result else 0

        # Calculate cross-bookmaker overlap if available
        overlap_rate = 0.0
        if validation_result and len(provider_graphs) >= 2:
            providers_list = list(provider_graphs.keys())
            src_p, tgt_p = providers_list[0], providers_list[1]
            src_sel = len(provider_graphs.get(src_p, []))
            tgt_sel = len(provider_graphs.get(tgt_p, []))
            min_sel = min(src_sel, tgt_sel)
            overlap_rate = (matched_events / min_sel) if min_sel > 0 else 0.0
            resource_metrics.cross_bookmaker_overlap_rate = overlap_rate

            src_res_item = provider_results.get(src_p)
            tgt_res_item = provider_results.get(tgt_p)
            diagnostics["event_universe"] = {
                f"{src_p}_discovered": len(src_res_item.discovered_objects) if src_res_item else 0,
                f"{tgt_p}_discovered": len(tgt_res_item.discovered_objects) if tgt_res_item else 0,
                f"{src_p}_selected": src_sel,
                f"{tgt_p}_selected": tgt_sel,
                "candidate_pairs": len(validation_result.event_candidates) if validation_result else 0,
            }

        # Collect Betclic Detail Diagnostics (profiled: full market scans + lookups
        # run on the scan critical path after reconciliation)
        profiler.start_phase("result_assembly")
        bc_inst = provider_instances.get("betclic")
        bc_raw_metrics = getattr(bc_inst, "acquisition_metrics", {}) if bc_inst else {}
        bc_metrics = bc_raw_metrics if isinstance(bc_raw_metrics, dict) else {}
        bc_res = provider_results.get("betclic")
        bc_parsed_list = bc_res.parsed_objects if bc_res else []
        bc_parsed_index = index_parsed_by_provider_id(bc_parsed_list, "provider_event_id")

        bc_matched_cnt = 0
        bc_detailed_matched_cnt = 0
        if validation_result and validation_result.canonical_events:
            for ce in validation_result.canonical_events:
                sources = getattr(ce, "sources", {})
                if "betclic" in sources:
                    bc_matched_cnt += 1
                    src = sources["betclic"]
                    bc_id = getattr(src, "provider_event_id", None) or getattr(src, "event_id", None)
                    bc_obj = bc_parsed_index.get(str(bc_id)) if bc_id is not None else None
                    if bc_obj and len(getattr(bc_obj, "markets", [])) > 1:
                        bc_detailed_matched_cnt += 1

        bc_req_succ = bc_metrics.get("detail_requests_successful", 0)
        bc_req_succ_val = bc_req_succ if isinstance(bc_req_succ, (int, float)) else 0
        bc_coverage = (bc_detailed_matched_cnt / bc_matched_cnt) if bc_matched_cnt > 0 else (1.0 if bc_req_succ_val > 0 else 0.0)

        diagnostics["betclic_telemetry"] = {
            "matched_events": bc_matched_cnt,
            "detailed_matched_events": bc_detailed_matched_cnt,
            "detail_coverage": bc_coverage,
            "detail_coverage_pct": round(bc_coverage * 100.0, 2),
            "detail_requests_attempted": bc_metrics.get("detail_requests_attempted", 0) if isinstance(bc_metrics.get("detail_requests_attempted", 0), (int, float)) else 0,
            "detail_requests_successful": bc_req_succ_val,
            "detail_requests_failed": bc_metrics.get("detail_requests_failed", 0) if isinstance(bc_metrics.get("detail_requests_failed", 0), (int, float)) else 0,
            "overview_payloads_used": bc_metrics.get("overview_payloads_used", 0) if isinstance(bc_metrics.get("overview_payloads_used", 0), (int, float)) else 0,
            "markets_acquired": bc_metrics.get("markets_acquired", 0) if isinstance(bc_metrics.get("markets_acquired", 0), (int, float)) else 0,
            "selections_acquired": bc_metrics.get("selections_acquired", 0) if isinstance(bc_metrics.get("selections_acquired", 0), (int, float)) else 0,
            "market_families": count_market_families(provider_graphs.get("betclic", [])),
        }

        # Collect Superbet Telemetry
        sb_inst = provider_instances.get("superbet")
        sb_raw_metrics = getattr(sb_inst, "acquisition_metrics", {}) if sb_inst else {}
        sb_metrics = sb_raw_metrics if isinstance(sb_raw_metrics, dict) else {}
        diagnostics["superbet_telemetry"] = {
            "events_discovered": sb_metrics.get("events_discovered", 0) if isinstance(sb_metrics.get("events_discovered", 0), (int, float)) else 0,
            "events_selected_for_detail": sb_metrics.get("events_selected_for_detail", 0) if isinstance(sb_metrics.get("events_selected_for_detail", 0), (int, float)) else 0,
            "detail_requests_attempted": sb_metrics.get("detail_requests_attempted", 0) if isinstance(sb_metrics.get("detail_requests_attempted", 0), (int, float)) else 0,
            "detail_requests_successful": sb_metrics.get("detail_requests_successful", 0) if isinstance(sb_metrics.get("detail_requests_successful", 0), (int, float)) else 0,
            "detail_requests_failed": sb_metrics.get("detail_requests_failed", 0) if isinstance(sb_metrics.get("detail_requests_failed", 0), (int, float)) else 0,
            "overview_payloads_used": sb_metrics.get("overview_payloads_used", 0) if isinstance(sb_metrics.get("overview_payloads_used", 0), (int, float)) else 0,
            "markets_acquired": sb_metrics.get("markets_acquired", 0) if isinstance(sb_metrics.get("markets_acquired", 0), (int, float)) else 0,
            "selections_acquired": sb_metrics.get("selections_acquired", 0) if isinstance(sb_metrics.get("selections_acquired", 0), (int, float)) else 0,
            "market_families": count_market_families(provider_graphs.get("superbet", [])),
        }

        # Collect Odds API Telemetry
        from providers.odds_api.quota_manager import get_quota_manager
        quota_mgr = get_quota_manager()
        quota_status = quota_mgr.get_status()

        oapi_inst = provider_instances.get("odds_api")
        oapi_res = provider_results.get("odds_api")
        oapi_norm = normalization_results.get("odds_api")
        if oapi_inst or oapi_res:
            oapi_raw_metrics = getattr(oapi_inst, "acquisition_metrics", {}) if oapi_inst else {}
            oapi_metrics = oapi_raw_metrics if isinstance(oapi_raw_metrics, dict) else {}
            parsed_list = oapi_res.parsed_objects if oapi_res else []
            b365_models = [ev for ev in parsed_list if getattr(ev, "bookmaker_name", "").lower() == "bet365"]
            unibet_models = [ev for ev in parsed_list if getattr(ev, "bookmaker_name", "").lower() == "unibet"]

            canon_contributed = 0
            if validation_result and validation_result.canonical_events:
                for ce in validation_result.canonical_events:
                    p_books = getattr(ce, "participating_bookmakers", []) or []
                    p_sources = list(getattr(ce, "sources", {}).keys())
                    if any(bm in ("bet365", "unibet", "odds_api") for bm in (p_books + p_sources)):
                        canon_contributed += 1

            norm_mkts_contributed = sum(len(g.markets) for g in oapi_norm.graphs) if oapi_norm else 0
            api_reqs = oapi_metrics.get("api_requests_made", 0)
            cache_hits = oapi_metrics.get("cache_hits", 0)
            cache_misses = oapi_metrics.get("cache_misses", max(0, api_reqs - cache_hits))

            oapi_status = oapi_res.status.name if (oapi_res and hasattr(oapi_res.status, "name")) else (
                oapi_res.status.value if (oapi_res and hasattr(oapi_res.status, "value")) else (
                    str(oapi_res.status) if oapi_res else "COMPLETED"
                )
            )

            # Truthful availability: True only if provider succeeded or has cached/parsed data
            is_avail = (
                oapi_res is not None
                and oapi_status not in ("FAILED", "UNAVAILABLE")
                and (len(parsed_list) > 0 or cache_hits > 0 or norm_mkts_contributed > 0 or len(getattr(oapi_res, "discovered_objects", [])) > 0)
            )

            diagnostics["odds_api_telemetry"] = {
                "status": oapi_status,
                "is_available": is_avail,
                "events_discovered": oapi_metrics.get("events_discovered", len(oapi_res.discovered_objects) if oapi_res else 0),
                "events_fetched": len(oapi_res.discovered_objects) if oapi_res else oapi_metrics.get("events_discovered", 0),
                "api_requests_made": api_reqs,
                "cache_hits": cache_hits,
                "cache_misses": cache_misses,
                "fetch_errors": oapi_metrics.get("fetch_errors", len(oapi_res.errors) if oapi_res else 0),
                "quota_blocked": oapi_metrics.get("quota_blocked", 0),
                "bookmaker_event_models": len(parsed_list),
                "bet365_count": len(b365_models),
                "unibet_count": len(unibet_models),
                "canonical_events_contributed": canon_contributed,
                "markets_contributed": norm_mkts_contributed,
                "markets_acquired": oapi_metrics.get("markets_acquired", 0),
                "selections_acquired": oapi_metrics.get("selections_acquired", 0),
                "quota": quota_status,
            }
        else:
            diagnostics["odds_api_telemetry"] = {
                "status": "UNAVAILABLE",
                "is_available": False,
                "events_discovered": 0,
                "events_fetched": 0,
                "api_requests_made": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "fetch_errors": 0,
                "quota_blocked": 0,
                "bookmaker_event_models": 0,
                "bet365_count": 0,
                "unibet_count": 0,
                "canonical_events_contributed": 0,
                "markets_contributed": 0,
                "markets_acquired": 0,
                "selections_acquired": 0,
                "quota": quota_status,
            }

        detected_opps = len(detection_result.opportunities) if detection_result else 0
        new_opps = lifecycle_summary.evaluation_batch.new_count if lifecycle_summary else 0
        updated_opps = lifecycle_summary.evaluation_batch.updated_count if lifecycle_summary else 0
        suppressed_opps = lifecycle_summary.evaluation_batch.suppressed_count if lifecycle_summary else 0
        expired_opps = len(lifecycle_summary.expired_records) if lifecycle_summary else 0

        dispatched_cnt = len(lifecycle_summary.evaluation_batch.to_dispatch) if lifecycle_summary else 0
        delivered_cnt = lifecycle_summary.delivered_count if lifecycle_summary else 0
        failed_del_cnt = lifecycle_summary.failed_count if lifecycle_summary else 0
        skipped_del_cnt = lifecycle_summary.skipped_count if lifecycle_summary else 0

        val_quota = getattr(self.reference_provider, "quota_metrics", None)

        profiler.finish_phase("result_assembly")
        scan_trace_report = profiler.finish_scan()
        diagnostics["scan_trace"] = scan_trace_report

        return ScanCycleResult(
            execution_id=execution_id,
            cycle_status=cycle_status,
            started_at=started_at,
            completed_at=completed_at,
            duration_seconds=cycle_duration,
                stage_timings=stage_timings,
                resource_metrics=resource_metrics,
                provider_results=provider_results,
                normalization_results=normalization_results,
                validation_result=validation_result,
                detection_result=detection_result,
                lifecycle_summary=lifecycle_summary,
                reconciliation_summary=reconciliation_summary,
                valuebet_result=valuebet_result,
                valuebet_lifecycle_batch=valuebet_lifecycle_batch,
                discovered_events_count=total_discovered,
                popular_events_discovered_count=resource_metrics.popular_events_discovered,
                popular_events_selected_count=resource_metrics.popular_events_selected,
                parsed_events_count=total_parsed,
                normalized_graphs_count=total_normalized,
                normalization_failed_count=total_norm_failed,
                markets_discovered_count=resource_metrics.markets_discovered,
                markets_normalized_count=resource_metrics.markets_normalized,
                markets_matched_count=resource_metrics.markets_matched,
                markets_evaluated_count=resource_metrics.markets_evaluated,
                matched_markets_total=resource_metrics.matched_markets_total,
                evaluation_candidates_total=resource_metrics.evaluation_candidates_total,
                evaluated_markets_total=resource_metrics.evaluated_markets_total,
                evaluation_excluded_total=resource_metrics.evaluation_excluded_total,
                evaluation_exclusion_breakdown=resource_metrics.evaluation_exclusion_breakdown,
                evaluation_coverage_rate=resource_metrics.evaluation_coverage_rate,
                surebet_candidates_count=resource_metrics.surebet_candidates,
                valid_surebets_count=resource_metrics.valid_surebets,
                rejected_markets_count=resource_metrics.rejected_markets_total,
                not_evaluated_markets_count=resource_metrics.not_evaluated_markets_total,
                rejection_reasons_breakdown=resource_metrics.rejection_reasons_breakdown,
                market_evaluation_records=resource_metrics.market_evaluation_records,
                matched_events_count=matched_events,
                unmatched_events_count=unmatched_events,
                cross_bookmaker_overlap_rate=overlap_rate,
                detail_candidates_available=resource_metrics.detail_candidates_available,
                detail_candidates_overlap=resource_metrics.detail_candidates_overlap,
                detail_events_selected=resource_metrics.detail_events_selected,
                detail_events_overlap_selected=resource_metrics.detail_events_overlap_selected,
                detail_overlap_selection_rate=resource_metrics.detail_overlap_selection_rate,
                multi_market_expected_events=resource_metrics.multi_market_expected_events,
                matched_events_eligible_for_detail=resource_metrics.matched_events_eligible_for_detail,
                matched_events_selected_for_detail=resource_metrics.matched_events_selected_for_detail,
                overview_only_matched_events=resource_metrics.overview_only_matched_events,
                full_detail_matched_events=resource_metrics.full_detail_matched_events,
                detail_budget_allocated=resource_metrics.detail_budget_allocated,
                scan_mode=resource_metrics.scan_mode,
                detail_acquisition_seconds=resource_metrics.detail_acquisition_seconds,
                markets_per_selected_event=resource_metrics.markets_per_selected_event,
                events_in_competition_scope=resource_metrics.events_in_competition_scope,
                raw_markets_received=resource_metrics.raw_markets_received,
                allowed_markets=resource_metrics.allowed_markets,
                discarded_markets=resource_metrics.discarded_markets,
                normalized_allowed_markets=resource_metrics.normalized_allowed_markets,
                discarded_market_families_top_20=resource_metrics.discarded_market_families_top_20,
                per_provider_counts=resource_metrics.per_provider_counts,
                detected_opportunities_count=detected_opps,
                new_opportunities_count=new_opps,
                updated_opportunities_count=updated_opps,
                suppressed_opportunities_count=suppressed_opps,
                expired_opportunities_count=expired_opps,
                dispatched_count=dispatched_cnt,
                delivered_count=delivered_cnt,
                failed_delivery_count=failed_del_cnt,
                skipped_delivery_count=skipped_del_cnt,
                valuebet_candidates_count=len(valuebet_result.candidates) if valuebet_result else 0,
                valuebets_qualified_count=len(valuebet_result.qualified_valuebets) if valuebet_result else 0,
                valuebets_new_count=valuebet_lifecycle_batch.new_count if valuebet_lifecycle_batch else 0,
                valuebets_updated_count=valuebet_lifecycle_batch.updated_count if valuebet_lifecycle_batch else 0,
                valuebets_suppressed_count=valuebet_lifecycle_batch.suppressed_count if valuebet_lifecycle_batch else 0,
                valuebets_expired_count=valuebet_lifecycle_batch.expired_count if valuebet_lifecycle_batch else 0,
                valuebets_dispatched_count=len(valuebet_lifecycle_batch.to_dispatch) if valuebet_lifecycle_batch else 0,
                valuebet_reference_requests=val_quota.total_requests if val_quota else 0,
                valuebet_reference_cache_hits=val_quota.cache_hits if val_quota else 0,
                valuebet_reference_cache_misses=val_quota.cache_misses if val_quota else 0,
                valuebet_rejection_reasons_breakdown=resource_metrics.valuebet_rejection_reasons_breakdown,
                market_coverage_breakdown=market_breakdown,
                nearest_opportunity=nearest_opp_dict,
                errors=errors,
                warnings=warnings,
                diagnostics=diagnostics,
            )


    def run_once(
        self,
        providers: Optional[Dict[str, BaseProvider]] = None,
        evaluation_time: Optional[datetime] = None,
    ) -> ScanCycleResult:
        """Alias for run_scan_cycle to execute one discrete scan cycle."""
        return self.run_scan_cycle(providers=providers, evaluation_time=evaluation_time)


# Convenience alias for ProductionScanOrchestrator
ScanOrchestrator = ProductionScanOrchestrator
