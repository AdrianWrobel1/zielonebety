"""P1-NEW-004 regression: explorer must not promote on gross EV when net is negative."""
from core.opportunity_explorer import OpportunityExplorerAdapter as A


def _prop(**over):
    base = {"prop_id": "x", "player_name": "P", "team": "A", "opponent": "B",
            "fixture": "A vs B", "stat_type": "SHOTS", "line": 1.5, "side": "OVER",
            "execution_status": "BETTABLE", "execution_ev_pct": 5.0,
            "best_execution_bookmaker": "Superbet", "best_execution_odds": 2.1,
            "score": 80.0}
    base.update(over)
    return base


def test_player_prop_negative_net_stays_bettable():
    d = A.from_player_prop(_prop(net_ev_pct=-7.6))
    assert d.is_valuebet is False
    assert d.status == "BETTABLE"
    assert d.net_ev_pct == -7.6 and d.gross_ev_pct == 5.0


def test_player_prop_positive_net_promotes():
    d = A.from_player_prop(_prop(net_ev_pct=4.2))
    assert d.is_valuebet is True and d.status == "VALUEBET"


def test_player_prop_unknown_net_legacy_behavior_preserved():
    d = A.from_player_prop(_prop())
    assert d.is_valuebet is True and d.net_ev_pct is None


def test_team_prop_negative_net_stays_bettable():
    t = {"prop_id": "t", "team": "A", "opponent": "B", "fixture": "A vs B",
         "stat_type": "CORNERS", "line": 5.5, "side": "OVER",
         "execution_status": "BETTABLE", "execution_ev_pct": 3.0,
         "net_ev_pct": -1.0, "score": 10.0}
    d = A.from_team_prop(t)
    assert d.is_valuebet is False and d.status == "BETTABLE"


def test_valuebet_adapter_follows_engine_qualification():
    v = {"candidate_id": "v1", "event_name": "A vs B", "bookmaker": "superbet",
         "bookmaker_odds": 2.1, "value_percent": 5.0, "net_value_percent": -7.6,
         "is_qualified": False, "selection_type": "HOME", "market_type": "1X2"}
    d = A.from_valuebet(v)
    assert d.is_valuebet is False and d.status == "REFERENCE_ONLY"
    v2 = dict(v, is_qualified=True, net_value_percent=6.2)
    d2 = A.from_valuebet(v2)
    assert d2.is_valuebet is True and d2.status == "VALUEBET"
