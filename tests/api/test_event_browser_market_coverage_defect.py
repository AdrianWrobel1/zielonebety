"""
Dedicated regression test for Event Browser and Main Scanner Market Coverage Defect.

Reproduces and verifies:
1. CoordinatedDetailSelectionPlanner must prioritize kickoff proximity (ko_ts) over raw confidence tie-breaking, so upcoming Tier 1 matches (e.g. today/this week) are selected for detail ahead of distant fixtures.
2. PlatformAPIService.get_event_detail must provide full market coverage in Event Browser even if an event was initially recorded with overview-only 1X2 in a batch scan.
"""

import pytest
from datetime import datetime, timezone, timedelta
from orchestration.detail_planning import CoordinatedDetailSelectionPlanner
from orchestration.event_selection import DefaultEventSelectionPolicy
from orchestration.models import ScanConfig
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem
from providers.superbet.parser.parser import SuperbetParser
from providers.betclic.parser.parser import BetclicParser
from normalization.engine import NormalizationEngine
from normalization.validation_pipeline import CrossBookmakerValidationPipeline
from api.services import PlatformAPIService


def test_coordinated_detail_planning_prioritizes_kickoff_proximity_over_competition_name_exactness():
    """Upcoming matches (e.g. starting in 2h) must be ranked before distant matches (starting in 5 days) within the same tier."""
    planner = CoordinatedDetailSelectionPlanner(
        event_selection_policy=DefaultEventSelectionPolicy(),
        normalization_engine=NormalizationEngine(),
        validation_pipeline=CrossBookmakerValidationPipeline(),
    )

    now = datetime.now(timezone.utc)
    t_soon = (now + timedelta(hours=2)).isoformat()
    t_distant = (now + timedelta(days=5)).isoformat()

    # Event A: Today (2h ahead), Superbet comp generic "Superbet Football" (confidence 0.93-0.97)
    sb_disc_soon = SuperbetDiscoveredItem(
        event_id="sb_soon",
        match_name="Sheffield United·Bolton Wanderers",
        competition_id="27",
        competition_name="Superbet Football",
        start_time=t_soon,
        metadata={"raw": {"id": "sb_soon", "matchName": "Sheffield United·Bolton Wanderers", "matchDate": t_soon, "markets": []}},
    )
    bc_disc_soon = BetclicDiscoveredItem(
        provider_event_id="bc_soon",
        name="Sheffield United - Bolton",
        competition_name="Anglia Championship",
        start_time=t_soon,
        url="https://www.betclic.pl/championship",
        metadata={"raw": {"id": "bc_soon", "name": "Sheffield United - Bolton", "competition": "Anglia Championship", "start_date": t_soon, "markets": []}},
    )

    # Event B: In 5 days, exact competition name (confidence 1.00)
    sb_disc_distant = SuperbetDiscoveredItem(
        event_id="sb_distant",
        match_name="Toulouse·Lille",
        competition_id="4",
        competition_name="Ligue 1",
        start_time=t_distant,
        metadata={"raw": {"id": "sb_distant", "matchName": "Toulouse·Lille", "competition": "Ligue 1", "matchDate": t_distant, "markets": []}},
    )
    bc_disc_distant = BetclicDiscoveredItem(
        provider_event_id="bc_distant",
        name="Toulouse - Lille",
        competition_name="Ligue 1",
        start_time=t_distant,
        url="https://www.betclic.pl/ligue-1",
        metadata={"raw": {"id": "bc_distant", "name": "Toulouse - Lille", "competition": "Ligue 1", "start_date": t_distant, "markets": []}},
    )

    config = ScanConfig(
        providers=("superbet", "betclic"),
        hours_ahead=168,
        max_detail_requests=1,  # Budget only allows 1 event!
        scan_mode="NORMAL",
    )

    plan = planner.create_plan(
        sb_discovered=[sb_disc_distant, sb_disc_soon],
        bc_discovered=[bc_disc_distant, bc_disc_soon],
        sb_parser=SuperbetParser(),
        bc_parser=BetclicParser(),
        config=config,
    )

    # The upcoming match (sb_soon / bc_soon) MUST be selected over the distant match (sb_distant / bc_distant)
    assert "sb_soon" in plan.selected_event_ids_superbet, f"Expected sb_soon in selected, got {plan.selected_event_ids_superbet}"
    assert "bc_soon" in plan.selected_event_ids_betclic, f"Expected bc_soon in selected, got {plan.selected_event_ids_betclic}"


def test_get_event_detail_on_demand_resolves_full_markets_when_overview_only():
    """Event Browser requesting an event that only has overview data (1X2) must fetch on-demand details."""
    service = PlatformAPIService()

    # Create a cached scan result where Millwall vs Bolton was stored with 1 market
    ce_id = "cev_04b4e0fbcd9d1781"
    ev_detail = {
        "id": ce_id,
        "event_id": ce_id,
        "canonical_event_id": ce_id,
        "home_team": "Millwall",
        "away_team": "Bolton",
        "competition": "Championship",
        "matching_status": "MATCHED",
        "providers": [
            {
                "provider": "superbet",
                "status": "Available",
                "market_count": 1,
                "raw_market_count": 1,
                "normalized_market_count": 1,
                "matched_market_count": 1,
                "provider_event_id": "13777981",
            },
            {
                "provider": "betclic",
                "status": "Available",
                "market_count": 1,
                "raw_market_count": 1,
                "normalized_market_count": 1,
                "matched_market_count": 1,
                "provider_event_id": "1210985496977408",
            }
        ],
        "markets": [
            {
                "canonical_market_key": "football:1X2:MATCH_RESULT:MATCH:all:FULL_TIME:none",
                "market_type": "1X2",
                "period": "FULL_TIME",
                "scope": "MATCH",
                "line": None,
                "status": "MATCHED",
                "selections": [
                    {"selection_type": "HOME", "odds": {"superbet": 2.10, "betclic": 2.05}},
                    {"selection_type": "DRAW", "odds": {"superbet": 3.25, "betclic": 3.30}},
                    {"selection_type": "AWAY", "odds": {"superbet": 3.50, "betclic": 3.60}},
                ]
            }
        ]
    }

    service._events_cache[ce_id] = ev_detail
    service._last_scan_result = {"_events_detail_map": {ce_id: ev_detail}, "events": [ev_detail]}

    # Call get_event_detail
    detail = service.get_event_detail(ce_id)
    assert detail is not None
    # Must have more than 1 market (e.g. Over/Under, BTTS, Handicap, Player props, etc.)
    assert len(detail.get("markets", [])) > 1, f"Expected > 1 market in Event Browser for {ce_id}, got {len(detail.get('markets', []))}"
