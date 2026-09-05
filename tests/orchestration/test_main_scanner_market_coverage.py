"""
Unit tests for Main Scanner / Production Scanner Market Coverage.

Verifies:
1. BetclicParser team extraction fallback from event name when contestants list is empty.
2. EventSelectionPolicy competition resolution for generic Superbet discovery items.
3. BetclicFetcher category resolution preserving full gRPC categories for Tier 1 competitions.
4. API serialization mapping raw and normalized market counts reliably by provider event ID.
"""

from datetime import datetime, timezone
import pytest

from providers.betclic.parser.parser import BetclicParser
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.config import BetclicConfig
from providers.superbet.models import SuperbetDiscoveredItem
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig
from domain.models import Event, Competition, Market, Selection, Odds, CanonicalEvent, CanonicalCompetition, EventSource, MatchEvidence
from normalization.market_identity import CanonicalMarketKey
from normalization.base_normalizer import NormalizedGraph
from normalization.validation_pipeline import (
    CanonicalEventValidationRecord,
    CrossBookmakerValidationResult,
    MatchedMarketLineage,
    PipelineMetrics,
)
from api.services import _serialize_events_from_scan_result
from orchestration.scan_orchestrator import ScanCycleResult, ProviderState
from providers.betclic.models import BetclicEvent, BetclicMarket, BetclicSelection
from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection


def test_betclic_parser_fallback_team_split_from_name():
    """BetclicParser must extract home_team and away_team from name when contestants list is missing or empty."""
    parser = BetclicParser()

    # Payload simulating gRPC subcategory response where contestants array is empty
    payload = {
        "id": "1209271800836096",
        "name": "West Ham - Wolverhampton",
        "competition": {"id": "123", "name": "Anglia Championship"},
        "contestants": [],  # Empty contestants!
        "subCategories": [
            {
                "id": "sc1",
                "name": "Wynik meczu",
                "markets": [
                    {
                        "id": "m1",
                        "name": "Wynik meczu",
                        "mainSelections": [
                            {"id": "s1", "name": "West Ham", "odds": 2.10, "status": 1},
                            {"id": "s2", "name": "Remis", "odds": 3.40, "status": 1},
                            {"id": "s3", "name": "Wolverhampton", "odds": 3.60, "status": 1},
                        ],
                    }
                ],
            }
        ],
    }

    parsed = parser.parse_payloads([payload])
    assert len(parsed) == 1
    ev = parsed[0]
    assert ev.home_team == "West Ham"
    assert ev.away_team == "Wolverhampton"
    assert ev.competition_name == "Anglia Championship"
    assert len(ev.markets) >= 1


def test_event_selection_resolves_generic_superbet_competition():
    """DefaultEventSelectionPolicy must resolve competition from match_name when competition_name is generic."""
    policy = DefaultEventSelectionPolicy()

    # Superbet Fastly overview item with generic competition_name
    item = SuperbetDiscoveredItem(
        event_id="13777812",
        match_name="Birmingham City·Southampton",
        competition_id="27",
        competition_name="Superbet Football",  # Generic!
        start_time="2026-09-05T14:00:00Z",
        url="https://production-superbet-offer-pl.freetls.fastly.net/v3/pl-PL/events/13777812",
    )

    resolved_comp = policy._extract_item_competition(item)
    # Must resolve to Premier League or Championship or senior English football (Tier 0 or 1), NOT "Superbet Football"
    assert resolved_comp != "Superbet Football"
    tier = policy.calculate_competition_tier(resolved_comp)
    assert tier in (0, 1)


def test_betclic_fetcher_category_resolution_for_championship():
    """BetclicFetcher._resolve_categories_for_item must return full gRPC categories for Championship."""
    config = BetclicConfig()
    fetcher = BetclicFetcher(config=config)

    item = BetclicDiscoveredItem(
        provider_event_id="1209271800836096",
        name="Portsmouth - Derby",
        competition_name="Anglia Championship",
        start_time="2026-09-05T14:00:00Z",
        url="https://www.betclic.pl/pilka-nozna-sfootball/championship-c2",
    )

    cats = fetcher._resolve_categories_for_item(item)
    # Championship is Tier 1 -> must have full categories including player props and stats
    assert "ca_ftb_prp" in cats
    assert "ca_ftb_gsc" in cats
    assert len(cats) >= 5


def test_api_serialization_maps_markets_by_provider_id():
    """_serialize_events_from_scan_result must map raw and normalized market counts using provider_event_id."""
    home = "Birmingham City"
    away = "Southampton"
    comp = CanonicalCompetition(name="Championship", sport="football", country="England")

    sources = {
        "superbet": EventSource(
            provider="superbet",
            provider_event_id="13777812",
            internal_event_id="sb_int_1",
            home_participant=home,
            away_participant=away,
            competition_name="Championship",
        ),
        "betclic": EventSource(
            provider="betclic",
            provider_event_id="1209280877309952",
            internal_event_id="bc_int_1",
            home_participant="Birmingham",  # Alias!
            away_participant=away,
            competition_name="Championship",
        ),
    }

    ce_birmingham = CanonicalEvent(
        canonical_event_id="cev_bir_sou_001",
        sport="football",
        home_team=home,
        away_team=away,
        scheduled_start="2026-09-05T14:00:00Z",
        competition=comp,
        sources=sources,
    )

    from normalization.market_matcher import MarketMatchDecision, MarketMatchDecisionType, MarketMatchBatchResult
    from normalization.selection_matcher import SelectionMatchBatchResult

    # Simulate matched market lineage
    m_key = CanonicalMarketKey(market_type="1X2", period="REGULAR_TIME", scope="FULL_EVENT")
    src_mkt = Market(event_id="sb_int_1", market_type="1X2", internal_id="mkt_sb_1")
    tgt_mkt = Market(event_id="bc_int_1", market_type="1X2", internal_id="mkt_bc_1")
    m_lineage = MatchedMarketLineage(
        canonical_event_id=ce_birmingham.canonical_event_id,
        canonical_market_key=m_key,
        source_market_id="mkt_sb_1",
        target_market_id="mkt_bc_1",
        source_market=src_mkt,
        target_market=tgt_mkt,
        market_decision=MarketMatchDecision(
            decision=MarketMatchDecisionType.MATCHED,
            source_market_id="mkt_sb_1",
            target_market_id="mkt_bc_1",
        ),
        selection_batch_result=SelectionMatchBatchResult(),
        comparable_selections=[],
    )
    val_record = CanonicalEventValidationRecord(
        canonical_event=ce_birmingham,
        market_batch_result=MarketMatchBatchResult(),
        matched_markets=[m_lineage],
    )

    # Create Superbet NormalizedGraph with 50 markets
    sb_event = Event(
        competition_id="comp_1",
        home_participant=home,
        away_participant=away,
        provider_ids={"superbet": "13777812"},
    )
    sb_mkts = [
        Market(
            event_id=sb_event.internal_id,
            market_type=f"MKT_{i}",
        )
        for i in range(50)
    ]
    sb_graph = NormalizedGraph(event=sb_event, competition=Competition(name="Championship", sport="Football"), markets=sb_mkts)

    # Create Betclic NormalizedGraph with 40 markets (with slightly different team name alias "Birmingham")
    bc_event = Event(
        competition_id="comp_1",
        home_participant="Birmingham",  # Alias!
        away_participant=away,
        provider_ids={"betclic": "1209280877309952"},
    )
    bc_mkts = [
        Market(
            event_id=bc_event.internal_id,
            market_type=f"MKT_{i}",
        )
        for i in range(40)
    ]
    bc_graph = NormalizedGraph(event=bc_event, competition=Competition(name="Championship", sport="Football"), markets=bc_mkts)

    # Create ScanCycleResult
    val_res = CrossBookmakerValidationResult(
        canonical_events=[ce_birmingham],
        event_validation_records=[val_record],
        metrics=PipelineMetrics(),
    )

    norm_res_sb = type("NormResult", (), {"graphs": [sb_graph]})()
    norm_res_bc = type("NormResult", (), {"graphs": [bc_graph]})()

    from orchestration.models import CycleStatus

    scan_res = ScanCycleResult(
        execution_id="scan_test_1",
        cycle_status=CycleStatus.SUCCESS,
        duration_seconds=1.0,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        scan_mode="NORMAL",
        validation_result=val_res,
        normalization_results={
            "superbet": norm_res_sb,
            "betclic": norm_res_bc,
        },
    )

    events_list, detail_map = _serialize_events_from_scan_result(scan_res)
    assert len(events_list) == 1
    ev_summary = events_list[0]
    ev_detail = detail_map.get(ev_summary["canonical_event_id"])
    assert ev_detail is not None

    prov_list = ev_detail.get("providers", [])
    assert len(prov_list) == 2

    sb_prov = next((p for p in prov_list if p["provider"] == "superbet"), None)
    bc_prov = next((p for p in prov_list if p["provider"] == "betclic"), None)

    assert sb_prov is not None
    assert sb_prov["normalized_market_count"] == 50
    assert bc_prov is not None
    # Must match by provider_event_id "1209280877309952" even though home_participant is "Birmingham"
    assert bc_prov["normalized_market_count"] == 40


def test_detail_planning_allocates_championship_pairs():
    """CoordinatedDetailSelectionPlanner must rank Championship matched pairs as Tier 1 and select them for detail."""
    from orchestration.detail_planning import CoordinatedDetailSelectionPlanner
    from normalization.engine import NormalizationEngine
    from normalization.validation_pipeline import CrossBookmakerValidationPipeline
    from providers.superbet.parser.parser import SuperbetParser
    from providers.betclic.parser.parser import BetclicParser

    planner = CoordinatedDetailSelectionPlanner(
        normalization_engine=NormalizationEngine(),
        validation_pipeline=CrossBookmakerValidationPipeline(),
    )

    sb_disc = [
        SuperbetDiscoveredItem(
            event_id="sb_champ_1",
            match_name="Portsmouth·Derby",
            competition_id="215",
            competition_name="Superbet Football",
            start_time="2026-09-02T14:00:00Z",
            metadata={"raw": {"id": "sb_champ_1", "matchName": "Portsmouth·Derby", "markets": []}},
        ),
        SuperbetDiscoveredItem(
            event_id="sb_minor_1",
            match_name="Minor Team A·Minor Team B",
            competition_id="9999",
            competition_name="Superbet Football",
            start_time="2026-09-02T14:00:00Z",
            metadata={"raw": {"id": "sb_minor_1", "matchName": "Minor Team A·Minor Team B", "markets": []}},
        ),
    ]

    bc_disc = [
        BetclicDiscoveredItem(
            provider_event_id="bc_champ_1",
            name="Portsmouth - Derby",
            competition_name="Anglia Championship",
            start_time="2026-09-02T14:00:00Z",
            url="https://www.betclic.pl/match/bc_champ_1",
            metadata={"raw": {"id": "bc_champ_1", "name": "Portsmouth - Derby", "competition": "Anglia Championship", "markets": []}},
        ),
        BetclicDiscoveredItem(
            provider_event_id="bc_minor_1",
            name="Minor Team A - Minor Team B",
            competition_name="Unknown Minor League",
            start_time="2026-09-02T14:00:00Z",
            url="https://www.betclic.pl/match/bc_minor_1",
            metadata={"raw": {"id": "bc_minor_1", "name": "Minor Team A - Minor Team B", "competition": "Unknown Minor League", "markets": []}},
        ),
    ]

    config = ScanConfig(
        providers=("superbet", "betclic"),
        hours_ahead=72,
        max_detail_requests=1,  # Budget of only 1! Championship should beat Minor
        scan_mode="NORMAL",
    )

    plan = planner.create_plan(
        sb_discovered=sb_disc,
        bc_discovered=bc_disc,
        sb_parser=SuperbetParser(),
        bc_parser=BetclicParser(),
        config=config,
    )

    assert "sb_champ_1" in plan.selected_event_ids_superbet
    assert "bc_champ_1" in plan.selected_event_ids_betclic
    assert "sb_minor_1" not in plan.selected_event_ids_superbet
    assert "bc_minor_1" not in plan.selected_event_ids_betclic
