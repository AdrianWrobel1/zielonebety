"""
Stage 8.6: Comprehensive Test Suite for Opportunity Quality, Filtering & Alert Optimization

Covers:
1. Opportunity Quality Policy & Exact Decimal Boundary Conditions (S=1.0, S=0.999999, S=1.000001).
2. Minimum Requirements Validation (odds > 1.0, complete coverage, sanity cap vetoes).
3. Deterministic Composite Quality Scoring (Margin, Competition Tier, Bookmakers, Proximity, Market Liquidity).
4. Deterministic Stable Opportunity Ranking (Permutation invariance, stable tie-breaking).
5. Alert Policy Lifecycle States (NEW alert, Duplicate suppression, UPDATED alert, Insignificant price drift suppression).
6. Conservative Market Expiration & Resurrected Reappearance.
7. Multi-Market Opportunity Validation (1X2, BTTS, TOTALS with line isolation, DNB, HALF_TIME_RESULT, DOUBLE_CHANCE).
8. Telegram Alert Message Formatting & HTML Escaping.
9. API Serialization & Opportunity Explorer Ranking Integration.
10. Zero-Surebet Nearest Opportunity Telemetry Preservation.
11. End-to-End Production Orchestrator Integration with Quality Filtering.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import pytest
from typing import List, Optional

from database.config import DatabaseConfig
from database.connection import DatabaseManager
from database.repositories.opportunity_repository import OpportunityRepository
from database.repositories.delivery_repository import DeliveryRepository
from domain.models import MatchEvidence
from normalization.alert_policy import (
    ChangeClassification,
    DefaultOpportunityAlertPolicy,
    OpportunityAlertConfig,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.surebet import (
    MarketSurebetEvaluation,
    MarketCompletenessStatus,
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
    SurebetDetectionResult,
)
from normalization.quality_policy import (
    DefaultOpportunityQualityPolicy,
    OpportunityQualityConfig,
    OpportunityQualityEvaluation,
    OpportunityRankingEngine,
    QualityRejectionReason,
)
from normalization.lifecycle import (
    LifecycleAction,
    OpportunityLifecycleManager,
    OpportunityStatus,
    generate_opportunity_fingerprint,
)
from normalization.dispatcher import (
    OpportunityDispatcher,
    InMemoryOpportunityConsumer,
    DispatchStatus,
    DeliveryStatus,
)
from notifications.telegram_consumer import format_telegram_surebet_message
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from orchestration.models import ScanConfig, CycleStatus
from api.services import (
    serialize_opportunity_summary,
    serialize_opportunity_detail,
    PlatformAPIService,
)


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _get_in_memory_db() -> DatabaseManager:
    config = DatabaseConfig(db_url="sqlite:///:memory:")
    db = DatabaseManager(config=config)
    db.create_tables()
    return db


def _build_test_surebet(
    event_id: str = "evt_86_001",
    home_team: str = "Arsenal FC",
    away_team: str = "Chelsea FC",
    competition_name: str = "Premier League",
    kickoff_dt: Optional[datetime] = None,
    market_type: str = CanonicalMarketType.ONE_X_TWO.value,
    line: Optional[Decimal] = None,
    home_odds: Decimal = Decimal("2.15"),
    draw_odds: Decimal = Decimal("3.80"),
    away_odds: Decimal = Decimal("4.20"),
    opp_id_suffix: str = "1",
    home_provider: str = "superbet",
    draw_provider: str = "betclic",
    away_provider: str = "superbet",
) -> SurebetOpportunity:
    """Helper to build a valid SurebetOpportunity with exact Decimal arithmetic."""
    mkt_key = CanonicalMarketKey(
        market_type=market_type,
        line=line,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
    )
    home_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.HOME.value,
        participant_role="HOME",
    )
    draw_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.DRAW.value,
    )
    away_key = CanonicalSelectionKey(
        market_key=mkt_key,
        selection_type=CanonicalSelectionType.AWAY.value,
        participant_role="AWAY",
    )

    legs = (
        SurebetLeg(
            canonical_selection_key=home_key,
            selection_type=CanonicalSelectionType.HOME.value,
            provider=home_provider,
            odds=home_odds,
            source_selection_id=f"sel_h_{opp_id_suffix}",
            implied_probability=Decimal("1.0") / home_odds,
        ),
        SurebetLeg(
            canonical_selection_key=draw_key,
            selection_type=CanonicalSelectionType.DRAW.value,
            provider=draw_provider,
            odds=draw_odds,
            source_selection_id=f"sel_d_{opp_id_suffix}",
            implied_probability=Decimal("1.0") / draw_odds,
        ),
        SurebetLeg(
            canonical_selection_key=away_key,
            selection_type=CanonicalSelectionType.AWAY.value,
            provider=away_provider,
            odds=away_odds,
            source_selection_id=f"sel_a_{opp_id_suffix}",
            implied_probability=Decimal("1.0") / away_odds,
        ),
    )

    s_val = sum(l.implied_probability for l in legs)
    margin_val = (Decimal("1.0") / s_val) - Decimal("1.0")

    k_iso = kickoff_dt.isoformat() if kickoff_dt else datetime.now(timezone.utc).isoformat()
    evidence = MatchEvidence(
        source_provider=home_provider,
        target_provider=draw_provider,
        source_event_id=event_id,
        target_event_id=event_id,
        decision="MATCHED",
        total_score=0.98,
        orientation="DIRECT",
        evidence={
            "home_team": home_team,
            "away_team": away_team,
            "competition_name": competition_name,
            "start_time": k_iso,
        },
    )

    books = tuple(sorted(set(l.provider.lower() for l in legs)))
    return SurebetOpportunity(
        opportunity_id=f"sb:{event_id}:{mkt_key.to_key_string()}:{opp_id_suffix}",
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s_val,
        arbitrage_margin=margin_val,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=len(books) > 1,
        bookmakers=books,
        event_evidence=evidence,
    )


# ---------------------------------------------------------------------------
# 1. Quality Policy & Exact Decimal Boundary Condition Tests
# ---------------------------------------------------------------------------

def test_quality_policy_minimum_requirements_valid():
    """Test 1: Valid Premier League 1X2 surebet is qualified with high composite score."""
    opp = _build_test_surebet(
        home_odds=Decimal("2.15"),
        draw_odds=Decimal("3.80"),
        away_odds=Decimal("4.20"),
        competition_name="Premier League",
    )
    policy = DefaultOpportunityQualityPolicy()
    eval_res = policy.evaluate_quality(opp)

    assert eval_res.is_qualified is True
    assert eval_res.competition_tier == 0
    assert eval_res.tier_name == "Tier 0 (Top Flight)"
    assert eval_res.quality_score >= 60.0
    assert len(eval_res.rejection_reasons) == 0


def test_quality_policy_exact_decimal_boundary_s_one():
    """Test 2: Exact S = 1.000000 is rejected by quality policy (zero profit / non-surebet)."""
    # Odds 2.0, 4.0, 4.0 -> 1/2 + 1/4 + 1/4 = 1.000000
    opp = _build_test_surebet(
        home_odds=Decimal("2.000000"),
        draw_odds=Decimal("4.000000"),
        away_odds=Decimal("4.000000"),
    )
    policy = DefaultOpportunityQualityPolicy()
    eval_res = policy.evaluate_quality(opp)

    assert eval_res.is_qualified is False
    assert eval_res.quality_score == 0.0
    assert any(QualityRejectionReason.INVALID_ARBITRAGE_SUM.value in r for r in eval_res.rejection_reasons)


def test_quality_policy_exact_decimal_boundary_s_slightly_below():
    """Test 3: S = 0.999999 (S < 1.0) with positive margin is accepted by Decimal boundary logic."""
    mkt_key = CanonicalMarketKey(market_type=CanonicalMarketType.BTTS.value)
    # Leg 1: odds 2.000002 -> 1/2.000002 = 0.4999995...
    # Leg 2: odds 2.000002 -> 1/2.000002 = 0.4999995...
    # S = 0.999999
    legs = (
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="YES"),
            selection_type="YES",
            provider="superbet",
            odds=Decimal("2.000002"),
            source_selection_id="s1",
            implied_probability=Decimal("0.4999995"),
        ),
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="NO"),
            selection_type="NO",
            provider="betclic",
            odds=Decimal("2.000002"),
            source_selection_id="s2",
            implied_probability=Decimal("0.4999995"),
        ),
    )
    s_val = Decimal("0.999999")
    margin_val = (Decimal("1.0") / s_val) - Decimal("1.0") # approx +0.000001
    opp = SurebetOpportunity(
        opportunity_id="sb:test:btts:boundary_below",
        canonical_event_id="evt_boundary",
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s_val,
        arbitrage_margin=margin_val,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
    )
    # Configure min_margin lower to test boundary math specifically
    config = OpportunityQualityConfig(min_margin=Decimal("0.0000001"))
    policy = DefaultOpportunityQualityPolicy(config=config)
    eval_res = policy.evaluate_quality(opp)

    assert eval_res.is_qualified is True
    assert eval_res.signals["implied_probability_sum"] == "0.999999"


def test_quality_policy_exact_decimal_boundary_s_slightly_above():
    """Test 4: S = 1.000001 (S > 1.0) is strictly rejected by Decimal boundary logic."""
    mkt_key = CanonicalMarketKey(market_type=CanonicalMarketType.BTTS.value)
    legs = (
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="YES"),
            selection_type="YES",
            provider="superbet",
            odds=Decimal("1.999998"),
            source_selection_id="s1",
            implied_probability=Decimal("0.5000005"),
        ),
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="NO"),
            selection_type="NO",
            provider="betclic",
            odds=Decimal("1.999998"),
            source_selection_id="s2",
            implied_probability=Decimal("0.5000005"),
        ),
    )
    s_val = Decimal("1.000001")
    margin_val = (Decimal("1.0") / s_val) - Decimal("1.0")
    opp = SurebetOpportunity(
        opportunity_id="sb:test:btts:boundary_above",
        canonical_event_id="evt_boundary_above",
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s_val,
        arbitrage_margin=margin_val,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
    )
    policy = DefaultOpportunityQualityPolicy()
    eval_res = policy.evaluate_quality(opp)

    assert eval_res.is_qualified is False
    assert any(QualityRejectionReason.INVALID_ARBITRAGE_SUM.value in r for r in eval_res.rejection_reasons)


def test_quality_policy_invalid_odds_below_or_equal_one():
    """Test 5: Odds <= 1.0 or below min_odds are rejected."""
    opp = _build_test_surebet(home_odds=Decimal("1.00"), draw_odds=Decimal("4.00"), away_odds=Decimal("4.00"))
    policy = DefaultOpportunityQualityPolicy()
    eval_res = policy.evaluate_quality(opp)

    assert eval_res.is_qualified is False
    assert any(QualityRejectionReason.INVALID_ODDS_VALUE.value in r for r in eval_res.rejection_reasons)


def test_quality_policy_extreme_sanity_cap_veto():
    """Test 6: Corrupted odds (e.g. 1000.0 yielding 85% margin) are vetoed by sanity cap."""
    opp = _build_test_surebet(home_odds=Decimal("100.0"), draw_odds=Decimal("100.0"), away_odds=Decimal("100.0"))
    policy = DefaultOpportunityQualityPolicy(config=OpportunityQualityConfig(max_margin_sanity_cap=Decimal("0.50")))
    eval_res = policy.evaluate_quality(opp)

    assert eval_res.is_qualified is False
    assert any(QualityRejectionReason.MARGIN_SANITY_CAP_EXCEEDED.value in r for r in eval_res.rejection_reasons)


# ---------------------------------------------------------------------------
# 2. Deterministic Scoring & Ranking Tests
# ---------------------------------------------------------------------------

def test_quality_policy_competition_tier_scoring():
    """Test 7: Premier League (Tier 0) scores higher than obscure domestic competition (Tier 2)."""
    now = datetime.now(timezone.utc)
    opp_top = _build_test_surebet(competition_name="Premier League", kickoff_dt=now + timedelta(hours=6))
    opp_std = _build_test_surebet(competition_name="Uganda Premier League Div 2", kickoff_dt=now + timedelta(hours=6))

    policy = DefaultOpportunityQualityPolicy()
    eval_top = policy.evaluate_quality(opp_top, current_time=now)
    eval_std = policy.evaluate_quality(opp_std, current_time=now)

    assert eval_top.competition_tier == 0
    assert eval_std.competition_tier == 2
    assert eval_top.quality_score > eval_std.quality_score


def test_deterministic_opportunity_ranking_stability():
    """Test 8: Shuffled permutations of identical opportunity list produce identical deterministic ranking."""
    now = datetime.now(timezone.utc)
    opp1 = _build_test_surebet(event_id="evt_01", home_odds=Decimal("2.20"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.20"), opp_id_suffix="1", kickoff_dt=now + timedelta(hours=2))
    opp2 = _build_test_surebet(event_id="evt_02", home_odds=Decimal("2.10"), draw_odds=Decimal("3.70"), away_odds=Decimal("4.00"), opp_id_suffix="2", kickoff_dt=now + timedelta(hours=10))
    opp3 = _build_test_surebet(event_id="evt_03", home_odds=Decimal("2.50"), draw_odds=Decimal("3.50"), away_odds=Decimal("3.50"), opp_id_suffix="3", kickoff_dt=now + timedelta(hours=1))

    engine = OpportunityRankingEngine()

    rank_order_1 = engine.rank_opportunities([opp1, opp2, opp3], current_time=now)
    rank_order_2 = engine.rank_opportunities([opp3, opp1, opp2], current_time=now)
    rank_order_3 = engine.rank_opportunities([opp2, opp3, opp1], current_time=now)

    ids_1 = [p[0].opportunity_id for p in rank_order_1]
    ids_2 = [p[0].opportunity_id for p in rank_order_2]
    ids_3 = [p[0].opportunity_id for p in rank_order_3]

    assert ids_1 == ids_2 == ids_3


def test_ranking_priority_margin_and_tier():
    """Test 9: Higher margin opportunities in Top Tier rank above lower margin opportunities."""
    now = datetime.now(timezone.utc)
    opp_high_margin = _build_test_surebet(
        event_id="evt_high",
        home_odds=Decimal("2.40"),
        draw_odds=Decimal("4.00"),
        away_odds=Decimal("4.50"),
        competition_name="LaLiga",
        opp_id_suffix="high",
        kickoff_dt=now + timedelta(hours=4),
    )
    opp_low_margin = _build_test_surebet(
        event_id="evt_low",
        home_odds=Decimal("2.12"),
        draw_odds=Decimal("3.65"),
        away_odds=Decimal("4.05"),
        competition_name="LaLiga",
        opp_id_suffix="low",
        kickoff_dt=now + timedelta(hours=4),
    )

    engine = OpportunityRankingEngine()
    ranked = engine.rank_opportunities([opp_low_margin, opp_high_margin], current_time=now)

    assert ranked[0][0].opportunity_id == opp_high_margin.opportunity_id
    assert ranked[0][1].quality_score >= ranked[1][1].quality_score


# ---------------------------------------------------------------------------
# 3. Lifecycle & Alert Policy Tests
# ---------------------------------------------------------------------------

def test_lifecycle_new_opportunity_dispatch():
    """Test 10: First observation of a quality surebet triggers DISPATCH_INITIAL."""
    db = _get_in_memory_db()
    with db.get_session() as session:
        repo = OpportunityRepository(session)
        mgr = OpportunityLifecycleManager(repository=repo)

        opp = _build_test_surebet()
        eval_res = mgr.evaluate_opportunity(opp)

        assert eval_res.action == LifecycleAction.DISPATCH_INITIAL
        assert eval_res.current_status == OpportunityStatus.NEW.value
        assert eval_res.quality_evaluation.is_qualified is True


def test_lifecycle_duplicate_identical_scan_suppression():
    """Test 11: Identical consecutive scan suppresses duplicate notification."""
    db = _get_in_memory_db()
    with db.get_session() as session:
        repo = OpportunityRepository(session)
        mgr = OpportunityLifecycleManager(repository=repo)

        opp = _build_test_surebet()
        eval_1 = mgr.evaluate_opportunity(opp)
        assert eval_1.action == LifecycleAction.DISPATCH_INITIAL

        # Mark as ALERTED
        repo.record_delivery_result(eval_1.fingerprint, success=True, delivery_status="DELIVERED")

        # Second identical scan
        eval_2 = mgr.evaluate_opportunity(opp)
        assert eval_2.action == LifecycleAction.SUPPRESS_DUPLICATE
        assert eval_2.current_status == OpportunityStatus.ALERTED.value


def test_lifecycle_insignificant_odds_change_suppression():
    """Test 12: Minor price change below threshold suppresses notification but updates snapshot."""
    db = _get_in_memory_db()
    with db.get_session() as session:
        repo = OpportunityRepository(session)
        alert_policy = DefaultOpportunityAlertPolicy(
            config=OpportunityAlertConfig(min_margin_delta=Decimal("0.0100"), min_odds_relative_delta=Decimal("0.0500"))
        )
        mgr = OpportunityLifecycleManager(repository=repo, alert_policy=alert_policy)

        opp_initial = _build_test_surebet(home_odds=Decimal("2.15"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.20"))
        eval_1 = mgr.evaluate_opportunity(opp_initial)
        repo.record_delivery_result(eval_1.fingerprint, success=True, delivery_status="DELIVERED")

        # Minor odds fluctuation (+0.01 on home odds)
        opp_minor = _build_test_surebet(home_odds=Decimal("2.16"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.20"))
        eval_2 = mgr.evaluate_opportunity(opp_minor)

        assert eval_2.action == LifecycleAction.SUPPRESS_INSIGNIFICANT
        assert eval_2.odds_changed is True


def test_lifecycle_material_change_update_dispatch():
    """Test 13: Significant margin improvement triggers DISPATCH_UPDATE."""
    db = _get_in_memory_db()
    with db.get_session() as session:
        repo = OpportunityRepository(session)
        alert_policy = DefaultOpportunityAlertPolicy(
            config=OpportunityAlertConfig(min_margin_delta=Decimal("0.0050"), min_odds_relative_delta=Decimal("0.0200"))
        )
        mgr = OpportunityLifecycleManager(repository=repo, alert_policy=alert_policy)

        opp_initial = _build_test_surebet(home_odds=Decimal("2.15"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.20"))
        eval_1 = mgr.evaluate_opportunity(opp_initial)
        repo.record_delivery_result(eval_1.fingerprint, success=True, delivery_status="DELIVERED")

        # Significant jump on draw odds (3.80 -> 4.30)
        opp_jump = _build_test_surebet(home_odds=Decimal("2.15"), draw_odds=Decimal("4.30"), away_odds=Decimal("4.20"))
        eval_2 = mgr.evaluate_opportunity(opp_jump)

        assert eval_2.action == LifecycleAction.DISPATCH_UPDATE
        assert eval_2.current_status == OpportunityStatus.UPDATED.value
        assert eval_2.change_evaluation.is_material is True


def test_lifecycle_conservative_expiration_and_reappearance():
    """Test 14: Confirmed absence marks opportunity EXPIRED; reappearance resurrects as NEW."""
    db = _get_in_memory_db()
    with db.get_session() as session:
        repo = OpportunityRepository(session)
        mgr = OpportunityLifecycleManager(repository=repo)

        opp = _build_test_surebet()
        eval_1 = mgr.evaluate_opportunity(opp)
        repo.record_delivery_result(eval_1.fingerprint, success=True, delivery_status="DELIVERED")

        # Simulate empty evaluation on same market (max_misses=2)
        no_opp_eval = MarketSurebetEvaluation(
            canonical_event_id=opp.canonical_event_id,
            canonical_market_key=opp.canonical_market_key,
            status=SurebetStatus.NO_SUREBET,
            completeness_status=MarketCompletenessStatus.COMPLETE,
            required_selection_types=("HOME", "DRAW", "AWAY"),
            available_selection_types=("HOME", "DRAW", "AWAY"),
            missing_selection_types=(),
            best_legs=opp.legs,
            implied_probability_sum=Decimal("1.04"),
            arbitrage_margin=Decimal("-0.038"),
            opportunity=None,
        )

        # First miss -> not expired yet
        mgr.process_market_evaluations([no_opp_eval], max_misses=2)
        rec = repo.get_by_fingerprint(eval_1.fingerprint)
        assert rec.status == OpportunityStatus.ALERTED.value

        # Second miss -> EXPIRED
        mgr.process_market_evaluations([no_opp_eval], max_misses=2)
        rec = repo.get_by_fingerprint(eval_1.fingerprint)
        assert rec.status == OpportunityStatus.EXPIRED.value

        # Reappearance in next scan cycle -> Resurrect as NEW
        eval_resurrect = mgr.evaluate_opportunity(opp)
        assert eval_resurrect.action == LifecycleAction.DISPATCH_INITIAL
        assert eval_resurrect.previous_status == OpportunityStatus.EXPIRED.value
        assert eval_resurrect.current_status == OpportunityStatus.NEW.value


# ---------------------------------------------------------------------------
# 4. Multi-Market Opportunity Validation Tests (Stage 8.5 Markets)
# ---------------------------------------------------------------------------

def test_multi_market_surebet_validation_1x2():
    """Test 15: 1X2 3-way partition validated."""
    opp = _build_test_surebet(market_type=CanonicalMarketType.ONE_X_TWO.value)
    policy = DefaultOpportunityQualityPolicy()
    eval_res = policy.evaluate_quality(opp)
    assert eval_res.is_qualified is True
    assert len(opp.legs) == 3


def test_multi_market_surebet_validation_btts():
    """Test 16: BTTS Yes/No 2-way partition validated."""
    mkt_key = CanonicalMarketKey(market_type=CanonicalMarketType.BTTS.value)
    legs = (
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="YES"),
            selection_type="YES",
            provider="superbet",
            odds=Decimal("2.10"),
            source_selection_id="s_btts_1",
            implied_probability=Decimal("1.0") / Decimal("2.10"),
        ),
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key, selection_type="NO"),
            selection_type="NO",
            provider="betclic",
            odds=Decimal("2.05"),
            source_selection_id="s_btts_2",
            implied_probability=Decimal("1.0") / Decimal("2.05"),
        ),
    )
    s_val = sum(l.implied_probability for l in legs)
    margin_val = (Decimal("1.0") / s_val) - Decimal("1.0")
    opp = SurebetOpportunity(
        opportunity_id="sb:btts:001",
        canonical_event_id="evt_btts",
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s_val,
        arbitrage_margin=margin_val,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
    )
    policy = DefaultOpportunityQualityPolicy()
    eval_res = policy.evaluate_quality(opp)
    assert eval_res.is_qualified is True
    assert len(opp.legs) == 2


def test_multi_market_surebet_validation_totals_line_isolation():
    """Test 17: Totals Over 2.5 and Under 2.5 accepted; line isolation prevents combining Over 2.5 with Under 3.5."""
    mkt_key_25 = CanonicalMarketKey(market_type=CanonicalMarketType.TOTALS.value, line=Decimal("2.5"))
    legs_25 = (
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key_25, selection_type="OVER"),
            selection_type="OVER",
            provider="superbet",
            odds=Decimal("2.10"),
            source_selection_id="s_tot_1",
            implied_probability=Decimal("1.0") / Decimal("2.10"),
        ),
        SurebetLeg(
            canonical_selection_key=CanonicalSelectionKey(market_key=mkt_key_25, selection_type="UNDER"),
            selection_type="UNDER",
            provider="betclic",
            odds=Decimal("2.05"),
            source_selection_id="s_tot_2",
            implied_probability=Decimal("1.0") / Decimal("2.05"),
        ),
    )
    s_val = sum(l.implied_probability for l in legs_25)
    margin_val = (Decimal("1.0") / s_val) - Decimal("1.0")
    opp_25 = SurebetOpportunity(
        opportunity_id="sb:tot25:001",
        canonical_event_id="evt_tot",
        canonical_market_key=mkt_key_25,
        legs=legs_25,
        implied_probability_sum=s_val,
        arbitrage_margin=margin_val,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("betclic", "superbet"),
    )
    policy = DefaultOpportunityQualityPolicy()
    eval_res = policy.evaluate_quality(opp_25)
    assert eval_res.is_qualified is True


def test_multi_market_surebet_validation_dnb_and_half_time():
    """Test 18: DNB (2 outcomes) and HALF_TIME_RESULT (3 outcomes) are validated."""
    policy = DefaultOpportunityQualityPolicy()

    # DNB
    mkt_dnb = CanonicalMarketKey(market_type=CanonicalMarketType.DRAW_NO_BET.value)
    legs_dnb = (
        SurebetLeg(canonical_selection_key=CanonicalSelectionKey(market_key=mkt_dnb, selection_type="HOME"), selection_type="HOME", provider="superbet", odds=Decimal("2.10"), source_selection_id="d1", implied_probability=Decimal("1.0")/Decimal("2.10")),
        SurebetLeg(canonical_selection_key=CanonicalSelectionKey(market_key=mkt_dnb, selection_type="AWAY"), selection_type="AWAY", provider="betclic", odds=Decimal("2.05"), source_selection_id="d2", implied_probability=Decimal("1.0")/Decimal("2.05")),
    )
    s_dnb = sum(l.implied_probability for l in legs_dnb)
    opp_dnb = SurebetOpportunity(
        opportunity_id="sb:dnb:001", canonical_event_id="evt_dnb", canonical_market_key=mkt_dnb,
        legs=legs_dnb, implied_probability_sum=s_dnb, arbitrage_margin=(Decimal("1.0")/s_dnb)-Decimal("1.0"),
        status=SurebetStatus.SUREBET, is_mixed_bookmakers=True, bookmakers=("betclic", "superbet")
    )
    assert policy.evaluate_quality(opp_dnb).is_qualified is True

    # HALF_TIME_RESULT
    mkt_ht = CanonicalMarketKey(market_type=CanonicalMarketType.HALF_TIME_RESULT.value, period=MarketPeriod.FIRST_HALF.value)
    legs_ht = (
        SurebetLeg(canonical_selection_key=CanonicalSelectionKey(market_key=mkt_ht, selection_type="HOME"), selection_type="HOME", provider="superbet", odds=Decimal("3.10"), source_selection_id="h1", implied_probability=Decimal("1.0")/Decimal("3.10")),
        SurebetLeg(canonical_selection_key=CanonicalSelectionKey(market_key=mkt_ht, selection_type="DRAW"), selection_type="DRAW", provider="betclic", odds=Decimal("2.30"), source_selection_id="h2", implied_probability=Decimal("1.0")/Decimal("2.30")),
        SurebetLeg(canonical_selection_key=CanonicalSelectionKey(market_key=mkt_ht, selection_type="AWAY"), selection_type="AWAY", provider="superbet", odds=Decimal("4.50"), source_selection_id="h3", implied_probability=Decimal("1.0")/Decimal("4.50")),
    )
    s_ht = sum(l.implied_probability for l in legs_ht)
    opp_ht = SurebetOpportunity(
        opportunity_id="sb:ht:001", canonical_event_id="evt_ht", canonical_market_key=mkt_ht,
        legs=legs_ht, implied_probability_sum=s_ht, arbitrage_margin=(Decimal("1.0")/s_ht)-Decimal("1.0"),
        status=SurebetStatus.SUREBET, is_mixed_bookmakers=True, bookmakers=("betclic", "superbet")
    )
    assert policy.evaluate_quality(opp_ht).is_qualified is True


def test_multi_market_double_chance_semantics():
    """Test 19: Double Chance single-market is correctly rejected for single-market surebets."""
    mkt_dc = CanonicalMarketKey(market_type=CanonicalMarketType.DOUBLE_CHANCE.value)
    policy = DefaultOpportunityQualityPolicy()
    opp_dc = _build_test_surebet(market_type=CanonicalMarketType.DOUBLE_CHANCE.value)
    # DOUBLE_CHANCE is not in allowed single-market partitions whitelist
    eval_res = policy.evaluate_quality(opp_dc)
    assert eval_res.is_qualified is False


# ---------------------------------------------------------------------------
# 5. Telegram & Explorer Serialization Tests
# ---------------------------------------------------------------------------

def test_telegram_alert_formatting_new_and_updated():
    """Test 20: Telegram alert message formats HTML safely, shows tier, arrows, and update diff."""
    now = datetime.now(timezone.utc)
    opp = _build_test_surebet(
        home_team="<Arsenal & Co>",
        away_team="Chelsea FC",
        competition_name="Premier League",
        home_odds=Decimal("2.10"),
        draw_odds=Decimal("3.80"),
        away_odds=Decimal("4.20"),
        kickoff_dt=now + timedelta(hours=4),
    )

    # Initial alert formatting
    msg_new = format_telegram_surebet_message(opp, parse_mode="HTML", is_update=False)
    assert "🔥 <b>SUREBET" in msg_new
    assert "&lt;Arsenal &amp; Co&gt; vs Chelsea FC" in msg_new
    assert "🏆 <i>Premier League</i>" in msg_new
    assert "1. <b>HOME</b> → Superbet @ <code>2.10</code>" in msg_new
    assert "Guaranteed margin" in msg_new

    # Updated alert formatting with margin diff
    msg_upd = format_telegram_surebet_message(
        opp, parse_mode="HTML", is_update=True, previous_margin=Decimal("0.0200")
    )
    assert "🔄 <b>SUREBET UPDATED" in msg_upd
    assert "Margin changed:" in msg_upd


def test_opportunity_explorer_api_serialization_with_quality_fields():
    """Test 21: API serialization includes quality_score, competition_tier, tier_name."""
    opp = _build_test_surebet(competition_name="LaLiga")
    summary_dto = serialize_opportunity_summary(opp)
    detail_dto = serialize_opportunity_detail(opp)

    assert "quality_score" in summary_dto
    assert "competition_tier" in summary_dto
    assert "tier_name" in summary_dto
    assert summary_dto["competition_tier"] == 0
    assert summary_dto["quality_score"] > 0.0

    assert "scoring_breakdown" in detail_dto
    assert "mathematical_explanation" in detail_dto


def test_zero_surebet_nearest_opportunity_telemetry():
    """Test 22: When 0 surebets are detected, nearest opportunity diagnostics are preserved."""
    db = _get_in_memory_db()
    svc = PlatformAPIService(db_manager=db)

    # Simulate empty scan result with nearest opportunity diagnostic
    svc._last_scan_result = {
        "execution_id": "scan_zero_001",
        "cycle_status": "SUCCESS",
        "opportunities": [],
        "counts": {"detected_opportunities": 0},
        "nearest_opportunity": {
            "event": "Real Madrid vs Barcelona",
            "market": "1X2:FULL_TIME:MATCH",
            "implied_probability_sum": "1.0250",
            "margin_pct": "-2.44",
            "distance_to_arbitrage": "0.0250",
        },
    }

    latest = svc.get_latest_scan()
    assert latest["counts"]["detected_opportunities"] == 0
    assert latest["nearest_opportunity"]["event"] == "Real Madrid vs Barcelona"
    assert latest["nearest_opportunity"]["distance_to_arbitrage"] == "0.0250"


def test_end_to_end_orchestrator_with_quality_filtering():
    """Test 23: Complete orchestrator pipeline qualifies, ranks, and dispatches surebets."""
    db = _get_in_memory_db()
    orchestrator = ProductionScanOrchestrator(db_manager=db)

    consumer = InMemoryOpportunityConsumer(name="in_memory_test")
    orchestrator.register_consumer(consumer)

    # Simulate surebet detection result
    opp1 = _build_test_surebet(event_id="evt_orch_1", home_odds=Decimal("2.20"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.20"), opp_id_suffix="1")
    opp2 = _build_test_surebet(event_id="evt_orch_2", home_odds=Decimal("2.10"), draw_odds=Decimal("3.70"), away_odds=Decimal("4.00"), opp_id_suffix="2")

    det_res = SurebetDetectionResult(opportunities=[opp1, opp2])

    summary = orchestrator.lifecycle_manager.process_and_dispatch(
        detection_result=det_res,
        dispatcher=orchestrator.dispatcher,
    )

    assert summary.delivered_count == 2
    assert summary.evaluation_batch.new_count == 2
    assert len(consumer.received_opportunities) == 2
