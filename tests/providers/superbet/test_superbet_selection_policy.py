"""
Unit Tests for Event Selection Policy in Superbet Provider (Stage 2.2)
"""

from unittest.mock import MagicMock
from providers.superbet.config import SuperbetConfig, EventSelectionMode
from providers.superbet.models import SuperbetDiscoveredItem
from providers.superbet.fetch.fetcher import SuperbetFetcher
from providers.base.response_interceptor import InterceptedResponse


def test_selection_policy_overview_only():
    mock_session = MagicMock()
    config = SuperbetConfig(selection_mode=EventSelectionMode.OVERVIEW_ONLY.value)
    fetcher = SuperbetFetcher(config=config, session_manager=mock_session)

    items = [
        SuperbetDiscoveredItem(event_id="e1", match_name="Match 1", metadata={"raw": {"id": "e1", "markets": []}}),
        SuperbetDiscoveredItem(event_id="e2", match_name="Match 2", metadata={"raw": {"id": "e2", "markets": []}}),
    ]

    responses = fetcher.fetch_event_data(items)
    assert len(responses) == 2
    assert fetcher.stats["overview_payloads_used"] == 2
    assert fetcher.stats["events_selected_for_detail"] == 0
    assert not mock_session.get.called


def test_selection_policy_selected():
    mock_session = MagicMock()
    mock_session.get.return_value = InterceptedResponse(
        url="https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL/events/e1",
        status_code=200,
        body=b'{"data": [{"eventId": "e1", "matchName": "Match 1", "odds": []}]}',
    )

    config = SuperbetConfig(
        selection_mode=EventSelectionMode.SELECTED.value,
        selected_event_ids=["e1"],
    )
    fetcher = SuperbetFetcher(config=config, session_manager=mock_session)

    items = [
        SuperbetDiscoveredItem(event_id="e1", match_name="Match 1", metadata={"raw": {"id": "e1", "markets": []}}),
        SuperbetDiscoveredItem(event_id="e2", match_name="Match 2", metadata={"raw": {"id": "e2", "markets": []}}),
    ]

    responses = fetcher.fetch_event_data(items)
    assert len(responses) == 2
    assert fetcher.stats["events_selected_for_detail"] == 1
    assert fetcher.stats["overview_payloads_used"] == 1
    assert mock_session.get.call_count == 1


def test_selection_policy_all():
    mock_session = MagicMock()
    mock_session.get.return_value = InterceptedResponse(
        url="https://production-superbet-offer-pl.freetls.fastly.net/v2/pl-PL/events/e1",
        status_code=200,
        body=b'{"data": [{"eventId": "ex", "matchName": "Match X", "odds": []}]}',
    )

    config = SuperbetConfig(selection_mode=EventSelectionMode.ALL.value)
    fetcher = SuperbetFetcher(config=config, session_manager=mock_session)

    items = [
        SuperbetDiscoveredItem(event_id="e1", match_name="Match 1", metadata={"raw": {"id": "e1"}}),
        SuperbetDiscoveredItem(event_id="e2", match_name="Match 2", metadata={"raw": {"id": "e2"}}),
    ]

    responses = fetcher.fetch_event_data(items)
    assert len(responses) == 2
    assert fetcher.stats["events_selected_for_detail"] == 2
    assert mock_session.get.call_count == 2


def test_superbet_normal_scan_1639_events_only_15_details():
    """
    Regression test for Normal Scan (15 details):
    - Discovery returns 1639 events
    - Exactly 15 events are selected for Detail API
    - Exactly 15 Detail HTTP requests are made
    - Remaining 1624 events use lightweight overview without network calls or heavy parsing
    - Parsed models reflect 15 detailed events + 1624 overview events
    """
    total_events = 1639
    detail_budget = 15

    # Build 1639 discovered items
    items = []
    for i in range(total_events):
        eid = f"sb_ev_{i:04d}"
        raw_overview = {
            "event_id": eid,
            "fixture": {
                "event_name": f"Home Team {i}·Away Team {i}",
                "utc_date": "2026-08-30T18:00:00Z",
                "tournament_id": 100 + (i % 10),
            },
            "tournamentName": f"Competition {i % 10}",
            "markets": [
                {
                    "id": 547,
                    "name": "Mecz",
                    "odds": [
                        {"uuid": f"{eid}_1", "price": 2.10, "status": 1, "metadata": {"name": "1"}},
                        {"uuid": f"{eid}_x", "price": 3.20, "status": 1, "metadata": {"name": "X"}},
                        {"uuid": f"{eid}_2", "price": 3.40, "status": 1, "metadata": {"name": "2"}},
                    ],
                }
            ],
        }
        items.append(
            SuperbetDiscoveredItem(
                event_id=eid,
                match_name=f"Home Team {i}·Away Team {i}",
                competition_name=f"Competition {i % 10}",
                start_time="2026-08-30T18:00:00Z",
                metadata={"raw": raw_overview},
            )
        )

    selected_ids = [items[i].event_id for i in range(detail_budget)]

    def mock_get(url, **kwargs):
        # Extract event ID from /events/{event_id}
        parts = url.rstrip("/").split("/")
        ev_id = parts[-1]
        body_json = (
            '{"error": false, "data": [{"eventId": "%s", "matchName": "Match %s", "odds": ['
            '{"marketId": 547, "marketName": "Mecz", "selectionName": "1", "price": 2.10, "status": "active"},'
            '{"marketId": 547, "marketName": "Mecz", "selectionName": "X", "price": 3.20, "status": "active"},'
            '{"marketId": 547, "marketName": "Mecz", "selectionName": "2", "price": 3.40, "status": "active"},'
            '{"marketId": 200734, "marketName": "Liczba goli", "selectionName": "Powyżej", "price": 1.85, "specifiers": {"total": "2.5"}, "status": "active"}'
            ']}]}' % (ev_id, ev_id)
        )
        return InterceptedResponse(
            url=url,
            status_code=200,
            body=body_json.encode("utf-8"),
        )

    mock_session = MagicMock()
    mock_session.get.side_effect = mock_get

    config = SuperbetConfig(
        selection_mode=EventSelectionMode.SELECTED.value,
        selected_event_ids=selected_ids,
        max_detail_requests=detail_budget,
    )
    fetcher = SuperbetFetcher(config=config, session_manager=mock_session)

    # Execute fetch
    raw_responses = fetcher.fetch_event_data(items)

    # 1. Assertions on acquisition telemetry
    assert len(raw_responses) == total_events
    assert fetcher.stats["events_considered"] == total_events
    assert fetcher.stats["events_selected_for_detail"] == detail_budget
    assert fetcher.stats["detail_requests_attempted"] == detail_budget
    assert fetcher.stats["detail_requests_successful"] == detail_budget
    assert fetcher.stats["overview_payloads_used"] == total_events - detail_budget
    # Crucial: Exactly 15 HTTP requests were executed (NOT 1639)
    assert mock_session.get.call_count == detail_budget

    # 2. Assertions on parsing
    from providers.superbet.parser.parser import SuperbetParser
    parser = SuperbetParser()
    parsed_events = parser.parse_payloads(raw_responses)

    assert len(parsed_events) == total_events

    detail_events = [ev for ev in parsed_events if ev.event_id in selected_ids]
    overview_events = [ev for ev in parsed_events if ev.event_id not in selected_ids]

    assert len(detail_events) == detail_budget
    assert len(overview_events) == total_events - detail_budget

    # Selected detail events parsed full Tier 2 markets (Mecz + Liczba goli = 2 distinct markets)
    for ev in detail_events:
        assert len(ev.markets) == 2
        mkt_names = [m.name for m in ev.markets]
        assert "Mecz" in mkt_names
        assert "Liczba goli" in mkt_names

    # Overview events have exactly 1 lightweight market ("Mecz")
    for ev in overview_events:
        assert len(ev.markets) == 1
        assert ev.markets[0].name == "Mecz"


def test_superbet_pipeline_normal_scan_matching_with_1639_events():
    """
    End-to-end integration test verifying that with 1639 Superbet discovered events
    and 30 Betclic discovered events in Normal Scan (15 details):
    - 15 detail requests are executed for Superbet and Betclic
    - 1624 Superbet overview items are preserved for discovery/matching without heavy parsing
    - Pipeline completes and event matching successfully links matched pairs
    """
    from domain.models import Competition, Event
    from normalization.engine import NormalizationEngine
    from orchestration.models import ScanConfig, CycleStatus
    from orchestration.scan_orchestrator import ProductionScanOrchestrator
    from providers.superbet.provider import SuperbetProvider
    from providers.betclic.provider import BetclicProvider
    from providers.betclic.models import BetclicDiscoveredItem

    num_sb_total = 1639
    num_bc_total = 30
    num_overlap = 20

    sb_items = []
    sb_detail_map = {}
    for i in range(num_sb_total):
        eid = f"sb_fix_{i:04d}"
        mname = f"Club H{i} vs Club A{i}"
        comp = f"Premier League" if i < 30 else f"Lower League {i % 50}"
        sb_raw = {
            "id": eid,
            "eventId": eid,
            "name": mname,
            "matchName": mname,
            "competitionName": comp,
            "matchDate": "2026-08-30T19:00:00Z",
            "markets": [
                {
                    "marketId": f"{eid}_1x2",
                    "name": "Mecz",
                    "odds": [
                        {"id": f"{eid}_1", "name": "1", "price": 2.20},
                        {"id": f"{eid}_x", "name": "X", "price": 3.30},
                        {"id": f"{eid}_2", "name": "2", "price": 3.10},
                    ],
                }
            ],
        }
        sb_items.append(
            SuperbetDiscoveredItem(
                event_id=eid,
                match_name=mname,
                competition_name=comp,
                start_time="2026-08-30T19:00:00Z",
                metadata={"raw": sb_raw},
            )
        )
        sb_detail_map[eid] = {
            "error": False,
            "data": [
                {
                    "eventId": eid,
                    "matchName": mname,
                    "competitionName": comp,
                    "matchDate": "2026-08-30T19:00:00Z",
                    "odds": [
                        {"marketId": 547, "marketName": "Mecz", "selectionName": "1", "price": 2.20, "status": "active"},
                        {"marketId": 547, "marketName": "Mecz", "selectionName": "X", "price": 3.30, "status": "active"},
                        {"marketId": 547, "marketName": "Mecz", "selectionName": "2", "price": 3.10, "status": "active"},
                        {"marketId": 200734, "marketName": "Liczba goli", "selectionName": "Powyżej", "price": 1.90, "specifiers": {"total": "2.5"}, "status": "active"},
                    ],
                }
            ],
        }

    bc_items = []
    bc_detail_map = {}
    for i in range(num_bc_total):
        eid = f"bc_fix_{i:04d}"
        mname = f"Club H{i} vs Club A{i}" if i < num_overlap else f"BC Only {i} vs Other {i}"
        comp = f"Premier League" if i < 30 else f"Betclic Comp {i}"
        bc_raw = {
            "id": eid,
            "name": mname,
            "competition": comp,
            "start_date": "2026-08-30T19:00:00Z",
            "markets": [
                {
                    "id": f"{eid}_1x2",
                    "name": "Wynik meczu",
                    "mainSelections": [
                        {"id": f"{eid}_1", "name": "1", "odds": 2.15, "status": 1},
                        {"id": f"{eid}_x", "name": "X", "odds": 3.35, "status": 1},
                        {"id": f"{eid}_2", "name": "2", "odds": 3.15, "status": 1},
                    ],
                }
            ],
        }
        bc_items.append(
            BetclicDiscoveredItem(
                provider_event_id=eid,
                name=mname,
                competition_name=comp,
                url=f"https://www.betclic.pl/events/{eid}",
                start_time="2026-08-30T19:00:00Z",
                metadata={"raw": bc_raw},
            )
        )
        bc_detail_map[eid] = {
            "id": eid,
            "name": mname,
            "competition": {"name": comp},
            "start_date": "2026-08-30T19:00:00Z",
            "subCategories": [
                {
                    "name": "Główne",
                    "markets": [
                        {
                            "id": f"{eid}_1x2",
                            "name": "Wynik meczu",
                            "mainSelections": [
                                {"id": f"{eid}_1", "name": "1", "odds": 2.15, "status": 1},
                                {"id": f"{eid}_x", "name": "X", "odds": 3.35, "status": 1},
                                {"id": f"{eid}_2", "name": "2", "odds": 3.15, "status": 1},
                            ],
                        },
                        {
                            "id": f"{eid}_tot25",
                            "name": "Gole Powyżej/Poniżej",
                            "code": "TOTALS",
                            "selectionMatrix": [
                                {
                                    "selections": [
                                        {"selectionOneof": {"selection": {"id": f"{eid}_t25_o", "name": "Powyżej 2,5", "odds": 1.85, "status": 1}}},
                                    ]
                                }
                            ],
                        },
                    ],
                }
            ],
        }

    sb_prov = SuperbetProvider()
    sb_prov.set_mock_discovery_payload([it.metadata["raw"] for it in sb_items])

    bc_prov = BetclicProvider()
    bc_prov.set_mock_discovery_payload([it.metadata["raw"] for it in bc_items])

    def mock_sb_fetch(item):
        if sb_prov.fetcher._is_event_selected_for_detail(item) or (item.event_id in sb_prov.superbet_config.selected_event_ids):
            sb_prov.fetcher.stats["detail_requests_attempted"] += 1
            sb_prov.fetcher.stats["detail_requests_successful"] += 1
            sb_prov.fetcher.stats["events_selected_for_detail"] += 1
            return sb_detail_map.get(item.event_id, item.metadata["raw"])
        else:
            sb_prov.fetcher.stats["overview_payloads_used"] += 1
            return item.metadata["raw"]

    def mock_bc_fetch(item):
        if bc_prov.fetcher._is_event_selected_for_detail(item) or (item.provider_event_id in bc_prov.betclic_config.selected_event_ids):
            bc_prov.fetcher.stats["detail_requests_attempted"] += 1
            bc_prov.fetcher.stats["detail_requests_successful"] += 1
            bc_prov.fetcher.stats["events_selected_for_detail"] += 1
            return bc_detail_map.get(item.provider_event_id, item.metadata["raw"])
        else:
            bc_prov.fetcher.stats["overview_payloads_used"] += 1
            return item.metadata["raw"]

    sb_prov.set_mock_fetch_provider(mock_sb_fetch)
    bc_prov.set_mock_fetch_provider(mock_bc_fetch)

    config = ScanConfig(
        providers=("superbet", "betclic"),
        scan_mode="NORMAL",
        max_detail_requests=15,
        enable_reconciliation=False,
    )
    orchestrator = ProductionScanOrchestrator(config=config)

    result = orchestrator.run_scan_cycle(
        providers={"superbet": sb_prov, "betclic": bc_prov}
    )

    assert result.cycle_status == CycleStatus.SUCCESS
    assert sb_prov.acquisition_metrics["detail_requests_attempted"] == 15
    assert sb_prov.acquisition_metrics["detail_requests_successful"] == 15
    assert sb_prov.acquisition_metrics["overview_payloads_used"] == num_sb_total - 15
    assert result.matched_events_count == num_overlap

