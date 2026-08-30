"""
End-to-End Mock Integration Tests for BetclicProvider (Task 031)
"""

import pytest
from providers.base.execution_engine import ExecutionEngine
from providers.base.models import QualityStatus
from providers.base.provider_factory import ProviderFactory
from providers.base.provider_state import ProviderState
from providers.betclic.provider import BetclicProvider


def test_betclic_provider_end_to_end_mock_execution():
    # Instantiate BetclicProvider via factory
    provider: BetclicProvider = ProviderFactory.create_provider("betclic")

    # Inject mock discovery payload
    raw_discovery = [
        {
            "name": "Ekstraklasa",
            "events": [
                {"id": "ev_501", "name": "Legia Warsaw vs Lech Poznan", "start_date": "2026-08-20T18:00:00Z"},
                {"id": "ev_502", "name": "Rakow vs Pogon Szczecin", "start_date": "2026-08-20T20:00:00Z"},
            ]
        }
    ]
    provider.set_mock_discovery_payload(raw_discovery)

    # Inject mock fetch function
    def mock_fetch_fn(item):
        return {
            "id": item.provider_event_id,
            "name": item.name,
            "competition": item.competition_name,
            "start_date": item.start_time,
            "home_team": item.name.split(" vs ")[0],
            "away_team": item.name.split(" vs ")[1],
            "markets": [
                {
                    "id": f"{item.provider_event_id}_m1",
                    "name": "Match Result",
                    "code": "1X2",
                    "is_open": True,
                    "selections": [
                        {"id": f"{item.provider_event_id}_s1", "name": "Home", "code": "1", "odds": 2.10},
                        {"id": f"{item.provider_event_id}_s2", "name": "Draw", "code": "X", "odds": 3.40},
                        {"id": f"{item.provider_event_id}_s3", "name": "Away", "code": "2", "odds": 3.60},
                    ]
                }
            ]
        }
    provider.set_mock_fetch_provider(mock_fetch_fn)

    # Execute end-to-end via ExecutionEngine
    engine = ExecutionEngine()
    result = engine.execute(provider)

    # Assertions
    assert result.status == ProviderState.COMPLETED
    assert result.execution_id is not None
    assert len(result.discovered_objects) == 2
    assert len(result.parsed_objects) == 2
    assert result.validation_report.is_valid is True
    assert result.quality_report.status == QualityStatus.EXCELLENT
    assert result.quality_report.coverage_pct == 100.0
    assert len(result.errors) == 0
