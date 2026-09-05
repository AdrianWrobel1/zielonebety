"""P1-NEW-003 regression: conservative player abbreviation compatibility.

- 'R. Lewandowski' <-> 'Robert Lewandowski' match (either direction);
- exact matches unchanged;
- different full first names ('Joao Silva' vs 'Jose Silva') stay distinct;
- single-token and short-surname forms never force-match;
- ambiguous multi-candidate abbreviations stay unmatched.
"""
from domain.models import Market
from normalization.market_matcher import MarketMatcher, _is_abbreviated_player_compatible


def _player_market(player_name, line=2.5):
    return Market(event_id="e1", market_type="PLAYER_SHOTS", line=line,
                  metadata={"scope": "PLAYER", "player_name": player_name, "sport": "football"})


def test_abbreviated_full_name_match_both_directions():
    m = MarketMatcher()
    assert len(m.match_markets([_player_market("R. Lewandowski")],
                               [_player_market("Robert Lewandowski")]).matched_pairs) == 1
    assert len(m.match_markets([_player_market("Robert Lewandowski")],
                               [_player_market("R Lewandowski")]).matched_pairs) == 1


def test_exact_matches_unchanged():
    m = MarketMatcher()
    assert len(m.match_markets([_player_market("Robert Lewandowski")],
                               [_player_market("Robert Lewandowski")]).matched_pairs) == 1


def test_different_full_names_remain_distinct():
    assert _is_abbreviated_player_compatible("joao silva", "jose silva") is False
    m = MarketMatcher()
    assert len(m.match_markets([_player_market("Joao Silva")],
                               [_player_market("Jose Silva")]).matched_pairs) == 0


def test_weak_forms_never_force_match():
    assert _is_abbreviated_player_compatible("murilo", "murilo costa") is False
    assert _is_abbreviated_player_compatible("r li", "rui li") is False
    assert _is_abbreviated_player_compatible("robert lewandowski", "robert lewandowski") is False
    m = MarketMatcher()
    assert len(m.match_markets([_player_market("Murilo")],
                               [_player_market("Murilo Costa")]).matched_pairs) == 0


def test_ambiguous_multi_candidate_abbreviation_stays_unmatched():
    m = MarketMatcher()
    # One source abbreviation, two compatible full-form targets -> no force-match.
    res = m.match_markets(
        [_player_market("R. Lewandowski")],
        [_player_market("Robert Lewandowski"), _player_market("Robin Lewandowski")],
    )
    # 'robin lewandowski' is NOT compatible ('robert' vs initial handled per-target:
    # 'r'+surname matches both) -> ambiguous -> unmatched, never force-matched.
    assert len(res.matched_pairs) == 0
