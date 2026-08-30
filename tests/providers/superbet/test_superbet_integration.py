"""
End-to-End Integration Tests for SuperbetProvider (Task 041 / Stage 2.1)
"""

from providers.base.execution_engine import ExecutionEngine
from providers.base.models import QualityStatus
from providers.base.provider_factory import ProviderFactory
from providers.base.provider_state import ProviderState
from providers.superbet.provider import SuperbetProvider


def test_superbet_provider_end_to_end_mock_execution():
    # Instantiate SuperbetProvider via factory
    provider: SuperbetProvider = ProviderFactory.create_provider("superbet")

    # Inject mock discovery payload
    raw_discovery = [
        {
            "competitionId": "comp_901",
            "competitionName": "Ekstraklasa",
            "events": [
                {
                    "eventId": "ev_801",
                    "matchName": "Legia Warszawa vs Lech Poznan",
                    "matchDate": "2026-08-20T18:00:00Z",
                },
                {
                    "eventId": "ev_802",
                    "matchName": "Rakow Czestochowa vs Pogon Szczecin",
                    "matchDate": "2026-08-20T20:00:00Z",
                },
            ],
        }
    ]
    provider.set_mock_discovery_payload(raw_discovery)

    # Inject mock fetch function
    def mock_fetch_fn(item):
        return {
            "eventId": item.event_id,
            "matchName": item.match_name,
            "competitionName": item.competition_name,
            "matchDate": item.start_time,
            "markets": [
                {
                    "marketId": f"{item.event_id}_m1",
                    "name": "Match Winner",
                    "selections": [
                        {"selectionId": f"{item.event_id}_s1", "name": "Home", "odds": 2.15},
                        {"selectionId": f"{item.event_id}_s2", "name": "Draw", "odds": 3.40},
                        {"selectionId": f"{item.event_id}_s3", "name": "Away", "odds": 3.50},
                    ],
                }
            ],
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


def test_superbet_provider_error_handling_in_engine():
    provider: SuperbetProvider = ProviderFactory.create_provider("superbet")

    # Inject invalid discovery payload type to trigger SuperbetDiscoveryError
    provider.set_mock_discovery_payload(99999)  # Invalid int

    engine = ExecutionEngine()
    result = engine.execute(provider)

    assert result.status == ProviderState.FAILED
    assert len(result.errors) > 0
