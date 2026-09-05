"""P1-NEW-001 regression: prop fixture matching must use kickoff.

- same teams + same kickoff -> match;
- same teams + clearly different kickoff (>24h shared window) -> no match;
- missing/unparseable kickoff -> names-only (no recall loss);
- player matcher accepts swapped orientation (documented policy);
- team matcher rejects swapped orientation (documented policy) and stays
  backward compatible without kickoffs.
"""
from scanner.prop_execution_matcher import PropExecutionMatcher as P
from scanner.team_prop_execution_matcher import TeamPropExecutionMatcher as T

K1 = "2026-09-01T18:00:00Z"
K2 = "2026-09-05T18:00:00Z"
K_WITHIN = "2026-09-02T14:00:00Z"  # 20h later, inside the 24h window


def test_player_same_teams_same_kickoff_match():
    assert P.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea", K1, K1)[0] is True


def test_player_same_teams_different_fixture_no_match():
    assert P.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea", K1, K2) == (False, 0.0)


def test_player_kickoff_within_window_matches():
    assert P.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea", K1, K_WITHIN)[0] is True


def test_player_missing_or_unparseable_kickoff_falls_back_to_names():
    assert P.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea", None, K2)[0] is True
    assert P.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea", "TBD", K2)[0] is True


def test_player_swapped_orientation_accepted_with_kickoff():
    # Explicit policy: providers may list the same fixture reversed; the
    # player side is resolved independently by is_player_match.
    assert P.is_fixture_match("Arsenal", "Chelsea", "Chelsea", "Arsenal", K1, K1)[0] is True


def test_team_same_teams_different_fixture_no_match():
    assert T.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea", K1, K2) == (False, 0.0)
    assert T.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea", K1, K1)[0] is True


def test_team_inversion_rejected_and_backward_compatible():
    # Explicit policy: team props are role-sensitive, inversion rejected.
    assert T.is_fixture_match("Arsenal", "Chelsea", "Chelsea", "Arsenal", K1, K1) == (False, 0.0)
    assert T.is_fixture_match("Arsenal", "Chelsea", "Arsenal", "Chelsea")[0] is True
