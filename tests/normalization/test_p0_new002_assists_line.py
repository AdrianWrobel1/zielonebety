"""P0-NEW-002 regression: PLAYER_ASSISTS line is part of canonical identity.

Same player + same stat + same direction + different line => different market.
Coherent with PLAYER_GOALS/CARDS (explicit line preserved, YES/NO lineless allowed).
"""
from domain.models import Market
from normalization.market_identity import extract_canonical_market_key
from normalization.market_matcher import MarketMatcher


def _assists_market(line):
    kwargs = {"event_id": "e1", "market_type": "PLAYER_ASSISTS",
              "metadata": {"scope": "PLAYER", "player_name": "Kevin De Bruyne",
                           "sport": "football"}}
    if line is not None:
        kwargs["line"] = line
    return Market(**kwargs)


def test_assists_different_lines_yield_different_keys():
    k1 = extract_canonical_market_key(_assists_market(0.5))
    k2 = extract_canonical_market_key(_assists_market(1.5))
    assert k1 is not None and k2 is not None
    assert k1 != k2
    assert k1.to_key_string() != k2.to_key_string()


def test_assists_cross_line_cannot_match():
    matcher = MarketMatcher()
    batch = matcher.match_markets([_assists_market(0.5)], [_assists_market(1.5)])
    assert batch.matched_pairs == []


def test_assists_same_line_still_matches():
    matcher = MarketMatcher()
    k1 = extract_canonical_market_key(_assists_market(0.5))
    k3 = extract_canonical_market_key(_assists_market(0.5))
    assert k1 == k3
    batch = matcher.match_markets([_assists_market(0.5)], [_assists_market(0.5)])
    assert len(batch.matched_pairs) == 1


def test_assists_lineless_yes_no_preserved_and_goals_cards_unchanged():
    kn = extract_canonical_market_key(_assists_market(None))
    assert kn is not None and kn.line is None
    g1 = Market(event_id="e1", market_type="PLAYER_GOALS", line=0.5,
                metadata={"scope": "PLAYER", "player_name": "Erling Haaland", "sport": "football"})
    g2 = Market(event_id="e1", market_type="PLAYER_GOALS", line=1.5,
                metadata={"scope": "PLAYER", "player_name": "Erling Haaland", "sport": "football"})
    c1 = Market(event_id="e1", market_type="PLAYER_CARDS", line=0.5,
                metadata={"scope": "PLAYER", "player_name": "Rodri", "sport": "football"})
    c2 = Market(event_id="e1", market_type="PLAYER_CARDS", line=1.5,
                metadata={"scope": "PLAYER", "player_name": "Rodri", "sport": "football"})
    gk1, gk2 = extract_canonical_market_key(g1), extract_canonical_market_key(g2)
    ck1, ck2 = extract_canonical_market_key(c1), extract_canonical_market_key(c2)
    assert gk1 != gk2 and ck1 != ck2
