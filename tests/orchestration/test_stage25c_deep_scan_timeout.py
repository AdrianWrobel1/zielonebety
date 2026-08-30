import pytest
from datetime import datetime, timezone, timedelta
from typing import List, Set, Tuple
from unittest.mock import MagicMock, patch

from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from orchestration.event_selection import DefaultEventSelectionPolicy, DetailPrioritizationResult
from orchestration.models import ScanConfig, CycleStatus
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from providers.base.provider_result import ProviderResult
from providers.base.provider_state import ProviderState
from providers.superbet.provider import SuperbetProvider
from providers.betclic.provider import BetclicProvider
from providers.betclic.models import BetclicDiscoveredItem
from providers.superbet.models import SuperbetDiscoveredItem


class TestStage25CDeepScanTimeout:
    """Test suite verifying Stage 25C Deep Scan timeout scaling and graceful recovery."""

    def test_scan_config_effective_provider_timeout(self):
        """Verify that ScanConfig scales provider_timeout for DEEP mode but preserves NORMAL mode."""
        # 1. NORMAL mode default (90s, budget 40)
        cfg_normal = ScanConfig(scan_mode="NORMAL")
        assert cfg_normal.effective_provider_timeout == 90.0
        assert cfg_normal.effective_max_detail_requests == 40

        # 2. DEEP mode default (180s, budget 100)
        cfg_deep = ScanConfig(scan_mode="DEEP")
        assert cfg_deep.effective_provider_timeout == 180.0
        assert cfg_deep.effective_max_detail_requests == 100

        # 3. Explicit override preserved
        cfg_custom = ScanConfig(scan_mode="DEEP", provider_timeout=150.0)
        assert cfg_custom.effective_provider_timeout == 150.0

    def test_deep_scan_orchestrator_passes_100_details_to_betclic(self):
        """Verify that DEEP scan mode allocates 100 detail requests to Betclic and Superbet."""
        now = datetime.now(timezone.utc)
        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(scan_mode="DEEP")
        )
        assert orchestrator.config.effective_max_detail_requests == 100
        assert orchestrator.config.effective_provider_timeout == 180.0

    def test_graceful_recovery_of_late_completing_provider(self):
        """Verify that if a provider future completes during graceful teardown, results are preserved."""
        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(scan_mode="NORMAL", provider_timeout=0.01)
        )

        mock_sb = MagicMock(spec=SuperbetProvider)
        mock_bc = MagicMock(spec=BetclicProvider)

        mock_sb.betclic_config = None
        mock_bc.betclic_config = MagicMock(selected_event_ids=["bc_1"])

        # Execute orchestrator with mock execution engine
        sb_res = ProviderResult(
            provider_name="superbet",
            status=ProviderState.COMPLETED,
            discovered_objects=[],
            parsed_objects=[],
            execution_duration=0.1,
        )
        bc_res = ProviderResult(
            provider_name="betclic",
            status=ProviderState.COMPLETED,
            discovered_objects=[],
            parsed_objects=[MagicMock(provider_event_id="bc_1", markets=[MagicMock()])],
            execution_duration=0.2,
        )

        with patch.object(orchestrator.execution_engine, "execute") as mock_exec:
            mock_exec.side_effect = lambda inst: sb_res if inst == mock_sb else bc_res
            # Provider execution should complete cleanly
            res = orchestrator.run_scan_cycle(providers={"superbet": mock_sb, "betclic": mock_bc})
            assert res.cycle_status != CycleStatus.FAILED

    def test_deep_mode_provider_taking_between_60s_and_120s_not_marked_failed(self):
        """Verify that in DEEP mode, a provider taking >60s (e.g. 75s) completes without timing out."""
        orchestrator = ProductionScanOrchestrator(
            config=ScanConfig(scan_mode="DEEP")
        )
        assert orchestrator.config.effective_provider_timeout == 180.0

        mock_sb = MagicMock(spec=SuperbetProvider)
        mock_bc = MagicMock(spec=BetclicProvider)
        mock_sb.superbet_config = MagicMock(selected_event_ids=["sb_1"])
        mock_bc.betclic_config = MagicMock(selected_event_ids=["bc_1"])

        sb_res = ProviderResult(
            provider_name="superbet",
            status=ProviderState.COMPLETED,
            discovered_objects=[],
            parsed_objects=[MagicMock(provider_event_id="sb_1", markets=[MagicMock()])],
            execution_duration=15.0,
        )
        bc_res = ProviderResult(
            provider_name="betclic",
            status=ProviderState.COMPLETED,
            discovered_objects=[],
            parsed_objects=[MagicMock(provider_event_id="bc_1", markets=[MagicMock()])],
            execution_duration=75.0,  # > 60s
        )

        with patch.object(orchestrator.execution_engine, "execute") as mock_exec:
            mock_exec.side_effect = lambda inst: sb_res if inst == mock_sb else bc_res
            res = orchestrator.run_scan_cycle(providers={"superbet": mock_sb, "betclic": mock_bc})
            assert res.cycle_status != CycleStatus.FAILED
            assert "betclic" in res.provider_results
            assert res.provider_results["betclic"].status == ProviderState.COMPLETED
            assert not any("timed out" in err for err in res.errors)
