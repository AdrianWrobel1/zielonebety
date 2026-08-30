import pytest
from datetime import datetime, timezone, timedelta
from typing import List, Set, Tuple
from unittest.mock import MagicMock

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from orchestration.event_selection import DefaultEventSelectionPolicy, DetailPrioritizationResult
from orchestration.models import ScanConfig
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.superbet.models import SuperbetDiscoveredItem
from providers.betclic.models import BetclicDiscoveredItem
from api.routes import APIRouter
from api.models import APIResponse


class TestStage25BRegressionAndScanUI:
    """Test suite verifying Stage 25B fixes for paired selection and scan modes."""

    def test_paired_selection_guarantees_100_percent_mutual_overlap(self):
        """Verify that when matching pairs exist, Superbet and Betclic select identical matched pairs."""
        policy = DefaultEventSelectionPolicy()
        now = datetime.now(timezone.utc)

        pairs = [
            ("sb_1", "bc_1", "Premier League", "Anglia 1", 0, 1.0, "Arsenal vs Chelsea"),
            ("sb_2", "bc_2", "La Liga", "Hiszpania 1", 0, 2.0, "Real Madrid vs Barcelona"),
            ("sb_3", "bc_3", "Serie A", "Wlochy 1", 0, 3.0, "Inter vs Milan"),
            ("sb_4", "bc_4", "Bundesliga", "Niemcy 1", 0, 4.0, "Bayern vs Dortmund"),
            ("sb_5", "bc_5", "Ligue 1", "Francja 1", 0, 5.0, "PSG vs Marseille"),
        ]

        sb_items = [
            SuperbetDiscoveredItem(event_id=p[0], match_name=p[6], competition_name=p[2], start_time=(now + timedelta(hours=p[5])).isoformat())
            for p in pairs
        ]
        bc_items = [
            BetclicDiscoveredItem(provider_event_id=p[1], name=p[6], competition_name=p[3], url="http://x", start_time=(now + timedelta(hours=p[5])).isoformat())
            for p in pairs
        ]

        ordered_sb = [p[0] for p in pairs]
        ordered_bc = [p[1] for p in pairs]

        budget = 3
        prio_sb = policy.prioritize_detail_events(
            discovered_items=sb_items,
            overlap_event_ids={p[0] for p in pairs},
            max_detail_requests=budget,
            forced_ranked_ids=ordered_sb,
        )
        prio_bc = policy.prioritize_detail_events(
            discovered_items=bc_items,
            overlap_event_ids={p[1] for p in pairs},
            max_detail_requests=budget,
            forced_ranked_ids=ordered_bc,
        )

        assert prio_sb.selected_event_ids == ["sb_1", "sb_2", "sb_3"]
        assert prio_bc.selected_event_ids == ["bc_1", "bc_2", "bc_3"]
        assert len(prio_sb.selected_event_ids) == budget
        assert len(prio_bc.selected_event_ids) == budget

    def test_api_handle_post_run_scan_accepts_scan_mode(self):
        """Verify that APIRouter passes scan_mode to the scanner service."""
        mock_service = MagicMock()
        mock_service.run_scan.return_value = {
            "execution_id": "test_exec_01",
            "cycle_status": "SUCCESS",
        }
        router = APIRouter(service=mock_service)

        # 1. Normal mode
        res_normal = router.handle_post_run_scan(payload={"scan_mode": "NORMAL"})
        assert res_normal.status_code == 200
        called_config = mock_service.run_scan.call_args[1]["config"]
        assert called_config.scan_mode == "NORMAL"
        assert called_config.effective_max_detail_requests == 40

        # 2. Deep mode
        res_deep = router.handle_post_run_scan(payload={"scan_mode": "DEEP"})
        assert res_deep.status_code == 200
        called_config_deep = mock_service.run_scan.call_args[1]["config"]
        assert called_config_deep.scan_mode == "DEEP"
        assert called_config_deep.effective_max_detail_requests == 100
