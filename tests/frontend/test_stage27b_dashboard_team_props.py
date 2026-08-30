import json
import pytest
from decimal import Decimal
from datetime import datetime, timezone

from orchestration.models import ScanCycleResult, CycleStatus, StageTiming, ResourceMetrics
from api.services import _serialize_scan_cycle_result

def test_team_props_market_breakdown_serialization_contract():
    market_breakdown = {
        '1X2': {'discovered': 100, 'normalized': 90, 'matched': 10, 'evaluated': 10},
        'TEAM_SHOTS': {'discovered': 73, 'normalized': 253, 'matched': 3, 'evaluated': 3},
        'TEAM_SHOTS_ON_TARGET': {'discovered': 70, 'normalized': 320, 'matched': 10, 'evaluated': 10},
        'TEAM_CORNERS': {'discovered': 335, 'normalized': 993, 'matched': 79, 'evaluated': 77},
        'TEAM_FOULS': {'discovered': 10, 'normalized': 40, 'matched': 3, 'evaluated': 3},
        'TEAM_CARDS': {'discovered': 65, 'normalized': 225, 'matched': 21, 'evaluated': 21},
        'TEAM_OFFSIDES': {'discovered': 10, 'normalized': 40, 'matched': 9, 'evaluated': 9},
        'TEAM_GOALS': {'discovered': 0, 'normalized': 2082, 'matched': 0, 'evaluated': 0},
    }
    res = ScanCycleResult(
        execution_id='scan_test_27b',
        cycle_status=CycleStatus.SUCCESS,
        started_at=datetime.now(timezone.utc).isoformat(),
        completed_at=datetime.now(timezone.utc).isoformat(),
        duration_seconds=3.5,
        stage_timings=StageTiming(),
        resource_metrics=ResourceMetrics(
            events_discovered=100,
            events_selected=50,
            events_parsed=50,
            normalized_graphs=50,
            matched_events=15,
            markets_evaluated=133,
            market_coverage_breakdown=market_breakdown,
        ),
    )
    serialized = _serialize_scan_cycle_result(res)
    bd = serialized['resource_metrics']['market_coverage_breakdown']
    assert bd['TEAM_SHOTS']['matched'] == 3
    assert bd['TEAM_SHOTS_ON_TARGET']['matched'] == 10
    assert bd['TEAM_CORNERS']['matched'] == 79
    assert bd['TEAM_FOULS']['matched'] == 3
    assert bd['TEAM_CARDS']['matched'] == 21
    assert bd['TEAM_OFFSIDES']['matched'] == 9
    assert bd['TEAM_GOALS']['normalized'] == 2082


def test_frontend_app_js_contains_team_props_section():
    with open('web/app.js', 'r', encoding='utf-8') as f:
        content = f.read()
    assert 'Team Props Coverage' in content
    assert 'TEAM_SHOTS' in content
    assert 'TEAM_SHOTS_ON_TARGET' in content
    assert 'TEAM_CORNERS' in content
    assert 'TEAM_FOULS' in content
    assert 'TEAM_CARDS' in content
    assert 'TEAM_OFFSIDES' in content
    assert 'TEAM_GOALS' in content
    assert 'teamPropsCoverageTableHtml' in content
