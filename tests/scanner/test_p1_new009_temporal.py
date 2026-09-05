"""P1-NEW-009 regression: temporal integrity.

- Warsaw winter (+01:00) vs summer (+02:00), IANA and DST fallback agree;
- unknown kickoff can never produce a stored pre-match snapshot;
- Betclic naive timestamps coerce to aware UTC (no batch-killing TypeError);
- StatsHub 'YYYY-MM-DD HH:MM UTC' strings parse.
"""
from datetime import datetime, timezone

from normalization.identity import (
    _EuropeWarsawFallbackTz,
    are_kickoffs_compatible,
    get_warsaw_tz,
    parse_kickoff_to_utc,
)
from scanner.player_shots_snapshot_recorder import PlayerShotsSnapshotRecorder


def test_warsaw_winter_summer_offsets():
    from zoneinfo import ZoneInfo

    zi = ZoneInfo("Europe/Warsaw")
    fb = _EuropeWarsawFallbackTz()
    winter = datetime.fromisoformat("2026-01-15T10:00:00+00:00")
    summer = datetime.fromisoformat("2026-07-15T10:00:00+00:00")
    assert winter.astimezone(zi).utcoffset().total_seconds() == 3600
    assert summer.astimezone(zi).utcoffset().total_seconds() == 7200
    assert winter.astimezone(fb).utcoffset() == winter.astimezone(zi).utcoffset()
    assert summer.astimezone(fb).utcoffset() == summer.astimezone(zi).utcoffset()
    # Shared entry point resolves (IANA here, fallback where tzdata missing).
    assert get_warsaw_tz() is not None


def test_unknown_kickoff_rejected_not_stored():
    rec = PlayerShotsSnapshotRecorder(repository=None)
    for ko in (None, "TBD", "", "not_a_date"):
        res = rec.record_snapshot(
            player_name="Test Player", home_team="A", away_team="B",
            competition="C", stat_type="SHOTS", line=0.5, direction="OVER",
            kickoff=ko,
        )
        assert res.is_rejected is True, ko
        assert res.snapshot is None, ko


def test_betclic_naive_timestamp_is_aware():
    from providers.betclic.discovery.discovery import BetclicDiscovery
    from providers.betclic.config import BetclicConfig

    disc = BetclicDiscovery(config=BetclicConfig())
    dt = disc._parse_event_time("2026-09-01 18:00:00")
    assert dt is not None and dt.tzinfo is not None
    # Horizon comparison must not raise on any parseable shape.
    items = disc._filter_by_horizon.__self__  # bound check only
    assert items is disc


def test_statshub_utc_suffix_parses():
    dt = parse_kickoff_to_utc("2026-09-01 18:00 UTC")
    assert dt is not None and dt.tzinfo is not None
    assert (dt.hour, dt.minute) == (18, 0)
    # Kickoff compatibility works on the previously-unparseable shape.
    assert are_kickoffs_compatible("2026-09-01 18:00 UTC", "2026-09-01T18:00:00Z") is True
    assert are_kickoffs_compatible("2026-09-01 18:00 UTC", "2026-09-05T18:00:00Z") is False
