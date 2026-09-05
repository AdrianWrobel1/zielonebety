"""P1-NEW-002 regression: canonical event ID stability (scheme v2).

- sub-minute / small provider-time jitter within one 15-min bucket: same ID;
- genuinely different bucket/hour/day/sport/teams: different IDs;
- dateless branch disambiguated by competition;
- orientation preserved (no silent home/away merge at hash level).
"""
from domain.models import generate_deterministic_canonical_event_id as cev


def test_kickoff_jitter_within_bucket_keeps_identity():
    base = cev("football", "arsenal", "chelsea", "2026-08-25T18:00:00Z")
    assert cev("football", "arsenal", "chelsea", "2026-08-25T18:00:45Z") == base
    assert cev("football", "arsenal", "chelsea", "2026-08-25T18:05:00Z") == base
    assert cev("football", "arsenal", "chelsea", "2026-08-25T18:14:59+00:00") == base
    # offset-equivalent instant maps to the same bucket
    assert cev("football", "arsenal", "chelsea", "2026-08-25T20:05:00+02:00") == base


def test_genuinely_different_fixtures_remain_distinct():
    base = cev("football", "arsenal", "chelsea", "2026-08-25T18:00:00Z")
    assert cev("football", "arsenal", "chelsea", "2026-08-25T18:15:00Z") != base
    assert cev("football", "arsenal", "chelsea", "2026-08-25T20:00:00Z") != base
    assert cev("football", "arsenal", "chelsea", "2026-08-26T18:00:00Z") != base
    assert cev("basketball", "arsenal", "chelsea", "2026-08-25T18:00:00Z") != base
    assert cev("football", "atletico madrid", "chelsea", "2026-08-25T18:00:00Z") != base


def test_missing_kickoff_disambiguated_by_competition():
    dated = cev("football", "arsenal", "chelsea", "2026-08-25T18:00:00Z")
    dateless_plain = cev("football", "arsenal", "chelsea", None)
    dateless_pl = cev("football", "arsenal", "chelsea", None, competition_norm="Premier League")
    dateless_cup = cev("football", "arsenal", "chelsea", None, competition_norm="FA Cup")
    assert dateless_plain != dated
    assert dateless_pl != dated
    assert dateless_pl != dateless_cup
    # garbage kickoff never silently becomes a dated identity
    assert cev("football", "arsenal", "chelsea", "TBD") != dated


def test_orientation_preserved_at_hash_level():
    fwd = cev("football", "arsenal", "chelsea", "2026-08-25T18:00:00Z")
    rev = cev("football", "chelsea", "arsenal", "2026-08-25T18:00:00Z")
    assert fwd != rev
