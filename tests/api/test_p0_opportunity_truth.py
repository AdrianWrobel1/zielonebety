"""Master P0 Opportunity Truth & Inspector Repair Test Suite.

Verifies:
1. Doncaster Rovers matched team market quote is classified as TEAM_PROP, BETTABLE, score=None.
2. TaxEngine strictly enforces Polish turnover tax rates (Betclic 0%, Superbet 12%).
3. TaxEngine strictly rejects same-outcome / non-partition quotes from surebet qualification.
4. TaxEngine handles empty/zero sums safely without claiming S < 1.0.
5. Opportunity Explorer strictly sorts by Net EV and handles None scores gracefully.
6. Detail endpoint serializes quote comparisons without fabricating surebet math.
"""

from decimal import Decimal
import pytest

from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
)
from core.tax_engine import TaxEngine, get_tax_engine
from api.services import PlatformAPIService


def test_matched_team_prop_dto_integrity():
    """Raw matched team market quotes must have score=None, status=AVAILABLE, type=QUOTE_COMPARISON."""
    event = {
        "id": "cev_041ef6c7eed6abdd",
        "home_team": "Doncaster Rovers",
        "away_team": "Plymouth Argyle",
        "competition": "FA Cup",
        "kickoff": "2026-09-05T19:45:00Z",
        "sport": "football",
    }
    market = {
        "market_type": "TOTALS",
        "line": 0.5,
        "scope": "TEAM",
        "period": "FULL_TIME",
    }
    selection = {
        "selection_type": "OVER",
        "line": 0.5,
        "participant": "Doncaster Rovers",
        "odds": {"betclic": 1.23, "superbet": 1.23},
        "best_odds": {"bookmaker": "betclic", "odds": 1.23},
    }

    dto = OpportunityExplorerAdapter.from_matched_team_market(event, market, selection)

    assert dto.score is None, f"Expected score None for unrated raw quote, got {dto.score}"
    assert dto.status == "AVAILABLE", f"Expected AVAILABLE status, got {dto.status}"
    assert dto.type == OpportunityType.QUOTE_COMPARISON.value
    assert dto.opportunity_type == OpportunityType.QUOTE_COMPARISON.value
    assert dto.gross_ev_pct is None
    assert dto.net_ev_pct is None
    assert dto.is_valuebet is False
    assert dto.reference_odds is None
    assert dto.execution_odds == 1.23
    assert dto.best_bookmaker == "betclic"
    assert "betclic" in dto.all_bookmakers and "superbet" in dto.all_bookmakers

    d_dict = dto.to_dict()
    assert d_dict["score"] is None
    assert d_dict["status"] == "AVAILABLE"
    assert d_dict["type"] == "QUOTE_COMPARISON"
    assert d_dict["opportunity_type"] == "QUOTE_COMPARISON"


def test_matched_team_prop_serialization_truth():
    """Detail serialization for matched team market must return clean quote comparison."""
    svc = PlatformAPIService()
    event = {
        "id": "cev_041ef6c7eed6abdd",
        "home_team": "Doncaster Rovers",
        "away_team": "Plymouth Argyle",
    }
    market = {"market_type": "TOTALS", "line": 0.5, "scope": "TEAM"}
    selection = {
        "selection_type": "OVER",
        "line": 0.5,
        "odds": {"betclic": 1.23, "superbet": 1.23},
    }
    dto = OpportunityExplorerAdapter.from_matched_team_market(event, market, selection)

    detail = svc._serialize_matched_team_market_detail(event, market, selection, dto)

    assert detail["opportunity_type"] == "QUOTE_COMPARISON"
    assert detail["type"] == "QUOTE_COMPARISON"
    assert detail["status"] == "AVAILABLE"
    assert detail["quality_score"] is None
    assert detail["value_percent"] is None

    math = detail["mathematical_explanation"]
    assert math["type"] == "QUOTE_COMPARISON"
    assert math["is_surebet"] is False
    assert math["is_valuebet"] is False
    assert math["implied_probability_sum"] is None
    assert "no model valuation" in math["explanation"]


def test_tax_engine_single_source_of_truth():
    """TaxEngine must strictly enforce Betclic 0% and Superbet 12%."""
    te = get_tax_engine()

    # Betclic: 0% turnover tax
    res_b = te.calculate_net_odds(raw_odds=Decimal("2.00"), bookmaker="betclic")
    assert res_b.is_tax_applied is False
    assert res_b.net_stake_multiplier == Decimal("1.0")
    assert res_b.effective_net_odds == Decimal("2.00")

    # Superbet: 12% turnover tax
    res_s = te.calculate_net_odds(raw_odds=Decimal("2.00"), bookmaker="superbet")
    assert res_s.is_tax_applied is True
    assert res_s.net_stake_multiplier == Decimal("0.88")
    assert res_s.effective_net_odds == Decimal("1.76")


def test_tax_engine_same_outcome_arbitrage_rejection():
    """Quotes on the same outcome (e.g. OVER and OVER) cannot qualify as arbitrage."""
    te = get_tax_engine()
    legs_same = [
        {"selection_type": "OVER", "provider": "betclic", "odds": 1.23},
        {"selection_type": "OVER", "provider": "superbet", "odds": 1.23},
    ]

    margin_res = te.calculate_net_surebet_margin(legs_same)
    assert margin_res["is_net_surebet"] is False
    assert margin_res["is_gross_surebet"] is False
    assert margin_res["rejection_reason"] == "SAME_OR_INSUFFICIENT_OUTCOMES"

    dist_res = te.calculate_stake_distribution(total_stake=1000, legs=legs_same)
    assert dist_res["is_surebet"] is False
    assert dist_res["rejection_reason"] == "SAME_OR_INSUFFICIENT_OUTCOMES"
    assert dist_res["guaranteed_payout"] == Decimal("0.00")
    assert dist_res["guaranteed_profit"] == Decimal("0.00")


def test_tax_engine_empty_sum_safety():
    """Empty legs or zero sum cannot be treated as S < 1.0 arbitrage."""
    te = get_tax_engine()

    margin_res = te.calculate_net_surebet_margin([])
    assert margin_res["is_net_surebet"] is False
    assert margin_res["is_gross_surebet"] is False
    assert margin_res["net_implied_probability_sum"] == Decimal("0")
    assert margin_res["rejection_reason"] == "EMPTY_LEGS"

    dist_res = te.calculate_stake_distribution(total_stake=1000, legs=[])
    assert dist_res["is_surebet"] is False
    assert dist_res["guaranteed_payout"] == Decimal("0.00")
    assert dist_res["guaranteed_profit"] == Decimal("0.00")


def test_tax_engine_genuine_arbitrage_acceptance():
    """Complementary outcomes forming a true partition with S < 1.0 qualify as surebet."""
    te = get_tax_engine()
    legs_arb = [
        {"selection_type": "OVER", "provider": "betclic", "odds": 2.10},
        {"selection_type": "UNDER", "provider": "betclic", "odds": 2.10},
    ]

    margin_res = te.calculate_net_surebet_margin(legs_arb)
    assert margin_res["is_net_surebet"] is True
    assert margin_res["is_gross_surebet"] is True
    assert margin_res["net_margin_percent"] > Decimal("0")

    dist_res = te.calculate_stake_distribution(total_stake=1000, legs=legs_arb)
    assert dist_res["is_surebet"] is True
    assert dist_res["guaranteed_profit"] > Decimal("0")
    assert dist_res["guaranteed_payout"] > Decimal("1000")


def test_explorer_net_ev_sorting_and_unscored_handling():
    """Sorting by 'net_ev' or 'ev' must rank by net_ev_pct and handle score=None cleanly."""
    svc = PlatformAPIService()

    # o1: Superbet taxed (+5% gross, -7.6% net)
    o1 = UnifiedOpportunityDTO(
        id="opp_superbet_taxed",
        type="VALUEBET",
        source="scanner",
        gross_ev_pct=5.0,
        net_ev_pct=-7.6,
        score=60.0,
        best_bookmaker="superbet",
    )
    # o2: Betclic tax-free (+2% gross, +2% net)
    o2 = UnifiedOpportunityDTO(
        id="opp_betclic_clean",
        type="VALUEBET",
        source="scanner",
        gross_ev_pct=2.0,
        net_ev_pct=2.0,
        score=75.0,
        best_bookmaker="betclic",
    )
    # o3: Raw team prop (no score, no EV)
    o3 = UnifiedOpportunityDTO(
        id="opp_raw_unscored",
        type="TEAM_PROP",
        source="scanner",
        score=None,
        best_bookmaker="superbet",
    )

    svc._unified_opportunities_cache = [o1, o2, o3]

    # 1. Sort by Net EV (DESC)
    res_net = svc.get_unified_explorer_opportunities(sort="net_ev", order="desc")
    items = res_net["items"]
    assert items[0]["id"] == "opp_betclic_clean", "Clean positive net EV (+2%) must rank #1"
    assert items[1]["id"] == "opp_superbet_taxed", "Negative net EV (-7.6%) must rank #2"
    assert items[2]["id"] == "opp_raw_unscored", "Unrated item with no EV must rank last"

    # 2. Sort by 'ev' (default EV sort should prioritize Net EV in Polish regulated context)
    res_ev = svc.get_unified_explorer_opportunities(sort="ev", order="desc")
    items_ev = res_ev["items"]
    assert items_ev[0]["id"] == "opp_betclic_clean"
    assert items_ev[1]["id"] == "opp_superbet_taxed"

    # 3. Sort by 'score' (DESC) - None score must not raise TypeError
    res_score = svc.get_unified_explorer_opportunities(sort="score", order="desc")
    items_score = res_score["items"]
    assert items_score[0]["id"] == "opp_betclic_clean"  # score 75.0
    assert items_score[1]["id"] == "opp_superbet_taxed"  # score 60.0
    assert items_score[2]["id"] == "opp_raw_unscored"  # score None
    assert items_score[2]["score"] is None
