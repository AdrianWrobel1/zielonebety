"""
Targeted contract tests for Player Shots Settlement Runner (Stage A.10.3).

Contract requirements:
1. TEST 1 — FUTURE KICKOFF:
   Pending snapshot with kickoff in the future is skipped; remains PENDING, no settlement.
2. TEST 2 — ELIGIBLE SNAPSHOT WITH ACTUAL DATA:
   Pending snapshot with kickoff in the past, deterministic StatsHub data provides actual_shots;
   runner coordinates settlement through existing PlayerShotsSettler, outcome is persisted in DB.
"""

from datetime import datetime, timezone, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import BaseORM, PlayerPropSnapshotORM
from database.repositories.player_prop_snapshot_repository import PlayerPropSnapshotRepository
from providers.statshub.models import (
    StatsHubFixture,
    StatsHubHistoricalMatch,
    StatsHubPlayerStat,
    StatsHubPropResult,
)
from scanner.player_shots_settler import PlayerShotsSettler
from scanner.player_shots_settlement_runner import PlayerShotsSettlementRunner


@pytest.fixture
def db_session():
    """In-memory SQLite database session for targeted runner tests."""
    engine = create_engine("sqlite:///:memory:")
    BaseORM.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture
def repository(db_session):
    return PlayerPropSnapshotRepository(db_session)


@pytest.fixture
def settler(repository):
    return PlayerShotsSettler(repository=repository)


def test_runner_skips_future_kickoff_leaving_snapshot_pending(repository, settler):
    """TEST 1 — FUTURE KICKOFF: Pending snapshot with kickoff in future must NOT be settled."""
    now = datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc)
    future_kickoff = now + timedelta(hours=2)  # 2026-09-02 20:00 UTC

    snapshot = PlayerPropSnapshotORM(
        id="snap_future_001",
        canonical_prop_id="cpp_future_001",
        canonical_event_id="cev_burnley_boro",
        canonical_player_id="cplr_banel",
        player_name="Jaydon Banel",
        home_team="Burnley",
        away_team="Middlesbrough",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=future_kickoff,
        observed_at=now - timedelta(hours=1),
        created_at=now - timedelta(hours=1),
        outcome_status="PENDING",
        actual_shots=None,
        actual_outcome=None,
        is_locked=True,
    )
    repository.save_snapshot(snapshot)

    runner = PlayerShotsSettlementRunner(repository=repository, settler=settler)
    summary = runner.run(now=now)

    assert summary.eligible_count == 0
    assert summary.skipped_future_count == 1
    assert summary.settled_count == 0

    # Verify DB record is untouched and still PENDING
    rec = repository.get_by_canonical_prop_id("cpp_future_001")
    assert rec is not None
    assert rec.outcome_status == "PENDING"
    assert rec.actual_shots is None
    assert rec.actual_outcome is None


def test_runner_settles_eligible_snapshot_with_actual_statshub_data(repository, settler):
    """TEST 2 — ELIGIBLE SNAPSHOT WITH ACTUAL DATA: Past kickoff + real StatsHub shots -> SETTLED_WIN."""
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    past_kickoff = now - timedelta(hours=3)  # 2026-09-02 19:00 UTC

    snapshot = PlayerPropSnapshotORM(
        id="snap_eligible_001",
        canonical_prop_id="cpp_eligible_001",
        canonical_event_id="cev_preston_bristol",
        canonical_player_id="cplr_tolaj",
        player_name="Lorent Tolaj",
        home_team="Preston North End",
        away_team="Bristol City",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=past_kickoff,
        observed_at=past_kickoff - timedelta(hours=2),
        created_at=past_kickoff - timedelta(hours=2),
        outcome_status="PENDING",
        actual_shots=None,
        actual_outcome=None,
        is_locked=True,
    )
    repository.save_snapshot(snapshot)

    # Deterministic StatsHub boundary providing actual_shots = 4
    fix = StatsHubFixture(fixture_id="16391195", home_team="Preston North End", away_team="Bristol City")
    hm = StatsHubHistoricalMatch(
        opponent="Preston North End",
        date="2026-09-02",
        timestamp=int(past_kickoff.timestamp()),
        minutes_played=84,
        stat_value=4,
        event_id=16391195,
    )
    ps = StatsHubPlayerStat(
        player_name="Lorent Tolaj",
        team="Bristol City",
        opponent="Preston North End",
        fixture=fix,
        stat_type="shots",
        historical_matches=[hm],
    )
    mock_prop_results = [StatsHubPropResult(player_stat=ps)]

    runner = PlayerShotsSettlementRunner(repository=repository, settler=settler)
    summary = runner.run(now=now, prop_results=mock_prop_results)

    assert summary.eligible_count == 1
    assert summary.settled_count == 1
    assert len(summary.settlement_results) == 1

    settle_res = summary.settlement_results[0]
    assert settle_res.is_settled is True
    assert settle_res.outcome_status == "SETTLED_WIN"
    assert settle_res.actual_shots == 4
    assert settle_res.actual_outcome == 1

    # Verify DB persistence via existing repository
    db_rec = repository.get_by_canonical_prop_id("cpp_eligible_001")
    assert db_rec.outcome_status == "SETTLED_WIN"
    assert db_rec.actual_shots == 4
    assert db_rec.actual_outcome == 1
    assert db_rec.settled_at is not None


def test_runner_acquires_fixture_props_for_eligible_snapshots(repository, settler):
    """Regression test: when prop_results is None, runner extracts fixture_id and acquires StatsHub trends for eligible fixtures."""
    import json
    from unittest.mock import MagicMock
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    past_kickoff = now - timedelta(hours=3)

    execution_odds = {
        "Superbet": {
            "provenance": {
                "statshub_fixture_id": "16391195",
            }
        }
    }

    snapshot = PlayerPropSnapshotORM(
        id="snap_auto_acq_001",
        canonical_prop_id="cpp_auto_acq_001",
        canonical_event_id="cev_preston_bristol",
        canonical_player_id="cplr_tolaj",
        player_name="Lorent Tolaj",
        home_team="Preston North End",
        away_team="Bristol City",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=past_kickoff,
        observed_at=past_kickoff - timedelta(hours=2),
        created_at=past_kickoff - timedelta(hours=2),
        execution_odds_json=json.dumps(execution_odds),
        outcome_status="PENDING",
    )
    repository.save_snapshot(snapshot)

    # Mock provider that expects fixture_id in config
    fix = StatsHubFixture(fixture_id="16391195", home_team="Preston North End", away_team="Bristol City")
    hm = StatsHubHistoricalMatch(
        opponent="Preston North End",
        date="2026-09-02",
        timestamp=int(past_kickoff.timestamp()),
        minutes_played=84,
        stat_value=2,
        event_id=16391195,
    )
    ps = StatsHubPlayerStat(
        player_name="Lorent Tolaj",
        team="Bristol City",
        opponent="Preston North End",
        fixture=fix,
        stat_type="shots",
        historical_matches=[hm],
    )
    expected_prop = StatsHubPropResult(player_stat=ps)

    mock_provider = MagicMock()
    mock_res = MagicMock()
    mock_res.parsed_objects = [expected_prop]
    mock_provider.run.return_value = mock_res

    runner = PlayerShotsSettlementRunner(repository=repository, settler=settler, statshub_provider=mock_provider)
    summary = runner.run(now=now)

    assert summary.eligible_count == 1
    assert summary.settled_count == 1
    assert summary.settlement_results[0].outcome_status == "SETTLED_WIN"
    assert summary.settlement_results[0].actual_shots == 2


def test_runner_queries_trends_per_fixture_when_provider_is_none(repository, settler, monkeypatch):
    """Regression test: default runner without injected provider fetches player trends using fixture IDs from eligible snapshots."""
    import json
    from unittest.mock import MagicMock
    now = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)
    past_kickoff = now - timedelta(hours=3)

    execution_odds = {
        "Superbet": {
            "provenance": {
                "statshub_fixture_id": "16391195",
            }
        }
    }

    snapshot = PlayerPropSnapshotORM(
        id="snap_fixture_fetch_001",
        canonical_prop_id="cpp_fixture_fetch_001",
        canonical_event_id="cev_preston_bristol",
        canonical_player_id="cplr_tolaj",
        player_name="Lorent Tolaj",
        home_team="Preston North End",
        away_team="Bristol City",
        competition="Championship",
        stat_type="SHOTS",
        line=0.5,
        direction="OVER",
        kickoff_at=past_kickoff,
        observed_at=past_kickoff - timedelta(hours=2),
        created_at=past_kickoff - timedelta(hours=2),
        execution_odds_json=json.dumps(execution_odds),
        outcome_status="PENDING",
    )
    repository.save_snapshot(snapshot)

    # Track how StatsHub is called
    called_games = []
    from providers.statshub.client import StatsHubClient

    def mock_fetch_player_trends(self, games=None, **kwargs):
        called_games.append(games)
        return {
            "data": [{
                "id": 1,
                "playerId": 100,
                "playerName": "Lorent Tolaj",
                "statType": "shots",
                "line": 0.5,
                "oddsType": "over",
                "eventId": 16391195,
                "teamName": "Bristol City",
                "opponentTeamName": "Preston North End",
                "recentGames": [{
                    "eventId": 16391195,
                    "statValue": 3,
                    "minutesPlayed": 90,
                    "opponentName": "Preston North End",
                    "eventTimestamp": int(past_kickoff.timestamp()),
                }]
            }],
            "pagination": {"hasNextPage": False, "totalPages": 1}
        }

    monkeypatch.setattr(StatsHubClient, "fetch_player_trends", mock_fetch_player_trends)

    runner = PlayerShotsSettlementRunner(repository=repository, settler=settler)
    summary = runner.run(now=now)

    # Runner must have called fetch_player_trends with games="16391195"
    assert "16391195" in called_games
    assert summary.settled_count == 1
    assert summary.settlement_results[0].actual_shots == 3
    assert summary.settlement_results[0].outcome_status == "SETTLED_WIN"


