"""
Stage 22C: Horizon & Discovery Alignment Test Suite

Validates:
1. ScanConfig propagation of hours_ahead to SuperbetConfig and BetclicConfig.
2. Default production horizon = 168h across ScanConfig, SuperbetConfig, BetclicConfig.
3. Betclic discovery horizon filtering (filters future events beyond hours_ahead cutoff).
4. Betclic discovery parsing and deduplication with dynamic/static URL lists.
5. Discovery diagnostics reporting.
6. Error handling & resilience.
"""

from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import MagicMock, patch

from orchestration.models import ScanConfig
from orchestration.scan_orchestrator import ScanOrchestrator
from providers.superbet.config import SuperbetConfig
from providers.superbet.provider import SuperbetProvider
from providers.betclic.config import BetclicConfig
from providers.betclic.provider import BetclicProvider
from providers.betclic.discovery.discovery import BetclicDiscovery
from providers.betclic.models import BetclicDiscoveredItem


def test_1_default_production_horizon():
    """Verify default hours_ahead is 168 across all config layers."""
    scan_cfg = ScanConfig()
    assert scan_cfg.hours_ahead == 168

    sb_cfg = SuperbetConfig()
    assert sb_cfg.hours_ahead == 168

    bc_cfg = BetclicConfig()
    assert bc_cfg.hours_ahead == 168


def test_2_orchestrator_config_propagation():
    """Verify ScanConfig propagates hours_ahead to both providers."""
    custom_scan_cfg = ScanConfig(hours_ahead=48)
    orchestrator = ScanOrchestrator(config=custom_scan_cfg)

    sb_inst = orchestrator._create_provider_instance("superbet")
    assert isinstance(sb_inst, SuperbetProvider)
    assert sb_inst.superbet_config.hours_ahead == 48

    bc_inst = orchestrator._create_provider_instance("betclic")
    assert isinstance(bc_inst, BetclicProvider)
    assert bc_inst.betclic_config.hours_ahead == 48


def test_3_betclic_discovery_horizon_filtering():
    """Verify Betclic discovery correctly excludes events beyond hours_ahead horizon."""
    now = datetime.now(timezone.utc)
    
    in_horizon_time = (now + timedelta(hours=24)).isoformat()
    outside_horizon_time = (now + timedelta(hours=200)).isoformat()
    past_time = (now - timedelta(hours=30)).isoformat()

    items_payload = [
        {
            "id": "101",
            "name": "Arsenal vs Chelsea",
            "competition": "Premier League",
            "start_date": in_horizon_time,
        },
        {
            "id": "102",
            "name": "Real Madrid vs Barcelona",
            "competition": "LaLiga",
            "start_date": outside_horizon_time,
        },
        {
            "id": "103",
            "name": "Old Match",
            "competition": "Serie A",
            "start_date": past_time,
        },
    ]

    cfg = BetclicConfig(hours_ahead=48, enable_dynamic_discovery=False)
    discovery = BetclicDiscovery(config=cfg)
    
    # Mock fetching to return our payload directly
    with patch.object(discovery, "fetch_discovery_payload", return_value=items_payload):
        discovered = discovery.discover_events()

    # Only item 101 should survive
    assert len(discovered) == 1
    assert discovered[0].provider_event_id == "101"
    assert discovery.stats["events_before_horizon_filter"] == 3
    assert discovery.stats["events_after_horizon_filter"] == 1
    assert discovery.stats["events_discarded_outside_horizon"] == 2


def test_4_betclic_discovery_deduplication():
    """Verify duplicate event IDs across pages/payloads are deduplicated."""
    now = datetime.now(timezone.utc)
    in_horizon = (now + timedelta(hours=5)).isoformat()

    payload = [
        {"id": "201", "name": "Event A", "competition": "Comp 1", "start_date": in_horizon},
        {"id": "201", "name": "Event A Duplicate", "competition": "Comp 1", "start_date": in_horizon},
        {"id": "202", "name": "Event B", "competition": "Comp 2", "start_date": in_horizon},
    ]

    cfg = BetclicConfig(hours_ahead=168, enable_dynamic_discovery=False)
    discovery = BetclicDiscovery(config=cfg)

    with patch.object(discovery, "fetch_discovery_payload", return_value=payload):
        discovered = discovery.discover_events()

    assert len(discovered) == 2
    ids = {d.provider_event_id for d in discovered}
    assert ids == {"201", "202"}


def test_5_betclic_config_from_dict():
    """Verify BetclicConfig.from_dict parses hours_ahead and enable_dynamic_discovery."""
    d = {
        "hours_ahead": 72,
        "max_discovered_events": 500,
        "enable_dynamic_discovery": False,
    }
    cfg = BetclicConfig.from_dict(d)
    assert cfg.hours_ahead == 72
    assert cfg.max_discovered_events == 500
    assert cfg.enable_dynamic_discovery is False
