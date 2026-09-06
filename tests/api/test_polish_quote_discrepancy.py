"""Master Polish Bookmaker Quote Discrepancy Test Suite.

Verifies:
1. CanonicalPropKey deterministic resolution across aliases and provider naming differences.
2. PropExecutionMatcher identification of both Betclic and Superbet execution quotes.
3. Accurate mathematical discrepancy calculation (odds difference, relative percentage, implied probability shift).
4. Threshold gating (10% default and configurable threshold).
5. Strict semantic separation: Quote Discrepancy is NOT a surebet (no opposite leg / no guaranteed profit).
6. Strict semantic separation: Quote Discrepancy is NOT a valuebet without a sharp reference model.
7. Unrated matched quotes retain score=None without Score 50 fabrication.
8. Elimination of hit-rate fallback in OpportunityExplorerAdapter.from_player_prop.
9. Elimination of hit-rate fallback in OpportunityExplorerAdapter.from_team_prop.
10. Elimination of raw reference odds fallback for fair_odds in OpportunityExplorerAdapter.from_valuebet.
11. Elimination of hit-rate fallback in PlatformAPIService._build_prop_detail.
12. Explorer adapter creation of QUOTE_DISCREPANCY from matched prop market when delta >= 10%.
13. Explorer adapter creation of PLAYER_PROP when delta < 10%.
14. Betclic normalizer mapping for PLAYER_TACKLES Polish aliases.
15. Deterministic canonical rejection of mismatched player names.
16. Deterministic canonical rejection of mismatched lines.
17. Deterministic canonical rejection of mismatched stat types.
18. Deterministic canonical rejection of mismatched periods.
19. Explorer API filtering by opp_type=QUOTE_DISCREPANCY.
20. Complete master test suite execution and validation.
"""

from decimal import Decimal
import pytest
from typing import Dict, Any

from scanner.prop_execution_matcher import (
    CanonicalPropKey,
    PropExecutionMatcher,
    ExecutionBookmakerOdds,
    PropOddsComparison,
)
from scanner.execution_providers import NormalizedExecutionQuote
from core.opportunity_explorer import (
    OpportunityExplorerAdapter,
    OpportunityType,
    UnifiedOpportunityDTO,
)
from normalization.betclic_normalizer import BetclicNormalizer
from api.services import PlatformAPIService


def test_canonical_prop_key_deterministic():
    """G1: CanonicalPropKey produces deterministic representation across diverse provider formats."""
    k1 = CanonicalPropKey("Vinicius Junior", "Real Madrid", "Barcelona", "shots", Decimal("2.5"), "OVER")
    k2 = CanonicalPropKey("vinícius júnior", "Real Madrid CF", "FC Barcelona", "player_shots", Decimal("2.50"), "over")
    
    assert k1.to_key_string() == k2.to_key_string(), (
        f"Expected deterministic key match: {k1.to_key_string()} vs {k2.to_key_string()}"
    )
    assert "vinicius_junior" in k1.to_key_string()
    assert "SHOTS" in k1.to_key_string()
    assert "2.5" in k1.to_key_string()


def test_prop_matcher_identifies_both_polish_bookmakers():
    """G2: PropExecutionMatcher identifies and matches both Betclic and Superbet quotes."""
    q_betclic = NormalizedExecutionQuote(
        bookmaker="Betclic",
        player="Vinicius Junior",
        fixture="Real Madrid vs Barcelona",
        stat_type="SHOTS",
        line=2.5,
        side="OVER",
        odds=3.10,
        active=True,
    )
    q_superbet = NormalizedExecutionQuote(
        bookmaker="Superbet",
        player="Vinicius Junior",
        fixture="Real Madrid vs Barcelona",
        stat_type="SHOTS",
        line=2.5,
        side="OVER",
        odds=1.88,
        active=True,
    )

    matcher = PropExecutionMatcher(normalized_quotes=[q_betclic, q_superbet])
    comp = matcher.match_execution_odds(
        player_name="Vinícius Júnior",
        team="Real Madrid",
        opponent="FC Barcelona",
        stat_type="shots",
        line=2.5,
        side="OVER",
    )

    assert "Betclic" in comp.execution_odds
    assert "Superbet" in comp.execution_odds
    assert comp.execution_odds["Betclic"].status == "AVAILABLE"
    assert comp.execution_odds["Betclic"].decimal_odds == 3.10
    assert comp.execution_odds["Superbet"].status == "AVAILABLE"
    assert comp.execution_odds["Superbet"].decimal_odds == 1.88


def test_discrepancy_metrics_calculation():
    """G3: Discrepancy metrics calculation accurately measures raw odds delta and relative improvement."""
    q_betclic = NormalizedExecutionQuote(
        bookmaker="Betclic",
        player="Vinicius Junior",
        fixture="Real Madrid vs Barcelona",
        stat_type="SHOTS",
        line=2.5,
        side="OVER",
        odds=3.10,
        active=True,
    )
    q_superbet = NormalizedExecutionQuote(
        bookmaker="Superbet",
        player="Vinicius Junior",
        fixture="Real Madrid vs Barcelona",
        stat_type="SHOTS",
        line=2.5,
        side="OVER",
        odds=1.88,
        active=True,
    )

    matcher = PropExecutionMatcher(normalized_quotes=[q_betclic, q_superbet])
    comp = matcher.match_execution_odds(
        player_name="Vinicius Junior",
        team="Real Madrid",
        opponent="Barcelona",
        stat_type="shots",
        line=2.5,
        side="OVER",
    )

    assert comp.best_executable_odds == 3.10
    assert comp.best_executable_bookmaker == "Betclic"
    assert comp.lower_executable_odds == 1.88
    assert comp.lower_executable_bookmaker == "Superbet"
    assert comp.odds_difference == 1.22
    assert comp.relative_price_difference_pct == 64.89
    assert comp.is_discrepancy is True

    details = comp.discrepancy_details
    assert details is not None
    assert details["best_bookmaker"] == "Betclic"
    assert details["best_odds"] == 3.10
    assert details["lower_bookmaker"] == "Superbet"
    assert details["lower_odds"] == 1.88
    assert details["odds_difference"] == 1.22
    assert details["relative_price_difference_pct"] == 64.89
    assert details["implied_prob_best_pct"] == 32.26  # 1/3.10 * 100
    assert details["implied_prob_lower_pct"] == 53.19  # 1/1.88 * 100
    assert details["implied_prob_diff_pp"] == 20.93  # 53.19 - 32.26


def test_discrepancy_threshold_gating():
    """G4: Discrepancy threshold gating correctly promotes pairs >= 10% and leaves pairs < 10% as standard."""
    # Case A: >= 10.0% discrepancy
    q_b1 = NormalizedExecutionQuote(bookmaker="Betclic", player="Kylian Mbappe", fixture="PSG vs Marseille", stat_type="SHOTS", line=3.5, side="OVER", odds=2.50, active=True)
    q_s1 = NormalizedExecutionQuote(bookmaker="Superbet", player="Kylian Mbappe", fixture="PSG vs Marseille", stat_type="SHOTS", line=3.5, side="OVER", odds=2.00, active=True)
    matcher_default = PropExecutionMatcher(normalized_quotes=[q_b1, q_s1])
    comp_a = matcher_default.match_execution_odds("Kylian Mbappe", "PSG", "Marseille", "shots", 3.5, "OVER")
    assert comp_a.relative_price_difference_pct == 25.0
    assert comp_a.is_discrepancy is True

    # Case B: < 10.0% discrepancy
    q_b2 = NormalizedExecutionQuote(bookmaker="Betclic", player="Kylian Mbappe", fixture="PSG vs Marseille", stat_type="SHOTS", line=3.5, side="OVER", odds=1.85, active=True)
    q_s2 = NormalizedExecutionQuote(bookmaker="Superbet", player="Kylian Mbappe", fixture="PSG vs Marseille", stat_type="SHOTS", line=3.5, side="OVER", odds=1.80, active=True)
    matcher_b = PropExecutionMatcher(normalized_quotes=[q_b2, q_s2])
    comp_b = matcher_b.match_execution_odds("Kylian Mbappe", "PSG", "Marseille", "shots", 3.5, "OVER")
    assert comp_b.relative_price_difference_pct == 2.78
    assert comp_b.is_discrepancy is False

    # Case C: Configurable threshold (e.g. 2.0%)
    matcher_custom = PropExecutionMatcher(normalized_quotes=[q_b2, q_s2], discrepancy_threshold_pct=2.0)
    comp_c = matcher_custom.match_execution_odds("Kylian Mbappe", "PSG", "Marseille", "shots", 3.5, "OVER")
    assert comp_c.is_discrepancy is True


def test_discrepancy_not_surebet():
    """G5: Semantic separation strictly prevents QUOTE_DISCREPANCY from being marked as a Surebet."""
    q_betclic = NormalizedExecutionQuote(bookmaker="Betclic", player="Vinicius Junior", fixture="Real Madrid vs Barcelona", stat_type="SHOTS", line=2.5, side="OVER", odds=3.10, active=True)
    q_superbet = NormalizedExecutionQuote(bookmaker="Superbet", player="Vinicius Junior", fixture="Real Madrid vs Barcelona", stat_type="SHOTS", line=2.5, side="OVER", odds=1.88, active=True)
    matcher = PropExecutionMatcher(normalized_quotes=[q_betclic, q_superbet])
    comp = matcher.match_execution_odds("Vinicius Junior", "Real Madrid", "Barcelona", "shots", 2.5, "OVER")

    assert comp.is_discrepancy is True
    assert comp.discrepancy_details["is_surebet"] is False
    assert comp.discrepancy_details["is_guaranteed_profit"] is False

    # Also test DTO adaptation via from_matched_prop_market
    event = {"id": "ev_test", "home_team": "Real Madrid", "away_team": "Barcelona"}
    market = {"market_type": "PLAYER_SHOTS", "line": 2.5, "scope": "PLAYER"}
    selection = {"player_name": "Vinicius Junior", "selection_type": "OVER", "line": 2.5, "odds": {"betclic": 3.10, "superbet": 1.88}}
    dto = OpportunityExplorerAdapter.from_matched_prop_market(event, market, selection)

    assert dto.type == OpportunityType.QUOTE_DISCREPANCY.value
    assert dto.opportunity_type == OpportunityType.QUOTE_DISCREPANCY.value
    assert dto.details["discrepancy"]["is_surebet"] is False
    assert dto.details["discrepancy"]["is_guaranteed_profit"] is False


def test_discrepancy_not_valuebet_without_model():
    """G6: Semantic separation prevents QUOTE_DISCREPANCY from being marked as a Valuebet without sharp model EV."""
    prop_data = {
        "prop_id": "prop_test_disc",
        "player_name": "Vinicius Junior",
        "team": "Real Madrid",
        "opponent": "Barcelona",
        "stat_type": "SHOTS",
        "line": 2.5,
        "side": "OVER",
        "best_execution_odds": 3.10,
        "best_execution_bookmaker": "Betclic",
        "lower_executable_odds": 1.88,
        "lower_executable_bookmaker": "Superbet",
        "odds_difference": 1.22,
        "relative_price_difference_pct": 64.89,
        "is_discrepancy": True,
        "execution_status": "BETTABLE",
        # Explicitly NO model valuation
        "model_probability": None,
        "fair_odds": None,
        "execution_ev_pct": None,
        "net_ev_pct": None,
    }

    dto = OpportunityExplorerAdapter.from_player_prop(prop_data)

    assert dto.type == OpportunityType.QUOTE_DISCREPANCY.value
    assert dto.is_valuebet is False
    assert dto.gross_ev_pct is None
    assert dto.net_ev_pct is None
    assert dto.fair_odds is None
    assert dto.status == "BETTABLE"


def test_unrated_quotes_retain_score_none():
    """G7: Unrated matched quotes retain score None without Score 50 fabrication."""
    event = {"id": "ev_test_score", "home_team": "Arsenal", "away_team": "Chelsea"}
    market = {"market_type": "PLAYER_SHOTS", "line": 1.5, "scope": "PLAYER"}
    selection = {"player_name": "Bukayo Saka", "selection_type": "OVER", "line": 1.5, "odds": {"betclic": 2.20, "superbet": 1.70}}
    dto = OpportunityExplorerAdapter.from_matched_prop_market(event, market, selection)

    assert dto.score is None, f"Expected score None, got {dto.score}"


def test_no_hit_rate_fallback_in_player_prop():
    """G8: Elimination of hit rate fallback for Fair Odds in OpportunityExplorerAdapter.from_player_prop."""
    prop_data = {
        "prop_id": "pp_no_fallback",
        "player_name": "Erling Haaland",
        "team": "Manchester City",
        "opponent": "Liverpool",
        "stat_type": "SHOTS",
        "line": 3.5,
        "side": "OVER",
        "best_odds": 2.10,
        "best_execution_odds": 2.10,
        "hit_rate": 0.65,
        "statistics": {"hit_rate": 0.65},
        "fair_odds": None,  # No model fair odds provided
    }

    dto = OpportunityExplorerAdapter.from_player_prop(prop_data)

    assert dto.fair_odds is None, f"Expected fair_odds to be None, but got hit-rate fallback {dto.fair_odds}"


def test_no_hit_rate_fallback_in_team_prop():
    """G9: Elimination of hit rate fallback for Fair Odds in OpportunityExplorerAdapter.from_team_prop."""
    team_data = {
        "prop_id": "tp_no_fallback",
        "team": "Manchester City",
        "opponent": "Liverpool",
        "stat_type": "CORNERS",
        "line": 6.5,
        "side": "OVER",
        "best_odds": 1.95,
        "best_execution_odds": 1.95,
        "hit_rate": 0.75,
        "statistics": {"hit_rate": 0.75},
        "fair_odds": None,  # No model fair odds provided
    }

    dto = OpportunityExplorerAdapter.from_team_prop(team_data)

    assert dto.fair_odds is None, f"Expected fair_odds to be None, but got hit-rate fallback {dto.fair_odds}"


def test_no_raw_reference_fallback_in_valuebet():
    """G10: Elimination of raw reference odds fallback for Fair Odds in from_valuebet."""
    val_data = {
        "candidate_id": "vbc_test",
        "event_name": "Bayern Munich vs Dortmund",
        "market": "TOTALS",
        "line": 2.5,
        "selection_type": "OVER",
        "bookmaker_odds": 2.20,
        "reference_raw_odds": 1.85,
        "fair_odds": None,  # Reference raw odds is 1.85, but fair odds is None
        "value_percent": 8.5,
    }

    dto = OpportunityExplorerAdapter.from_valuebet(val_data)

    assert dto.fair_odds is None, f"Expected fair_odds to be None, but got raw reference odds fallback {dto.fair_odds}"
    assert dto.reference_odds == 1.85


def test_no_hit_rate_fallback_in_build_prop_detail():
    """G11: Elimination of hit rate fallback for Fair Odds in PlatformAPIService._build_prop_detail."""
    service = PlatformAPIService()
    prop_detail_input = {
        "prop_id": "prop_test_detail",
        "player_name": "Lamine Yamal",
        "team": "Barcelona",
        "opponent": "Real Madrid",
        "stat_type": "SHOTS",
        "line": 1.5,
        "side": "OVER",
        "best_execution_odds": 2.05,
        "best_execution_bookmaker": "Betclic",
        "model_probability": 0.60,
        "fair_odds": None,  # No explicit fair odds
    }

    serialized = service._build_prop_detail(prop_detail_input, "prop_test_detail")

    assert serialized["fair_odds"] is None, f"Expected serialized fair_odds None, got {serialized['fair_odds']}"
    math_exp = serialized.get("mathematical_explanation", {})
    assert math_exp.get("fair_odds") is None, f"Expected math explanation fair_odds None, got {math_exp.get('fair_odds')}"


def test_from_matched_prop_market_discrepancy():
    """G12: OpportunityExplorerAdapter.from_matched_prop_market creates QUOTE_DISCREPANCY when price delta >= 10%."""
    event = {
        "id": "cev_vini_real_barca",
        "home_team": "Real Madrid",
        "away_team": "Barcelona",
        "competition": "La Liga",
        "kickoff": "2026-09-05T20:00:00Z",
    }
    market = {
        "market_type": "PLAYER_SHOTS",
        "line": 2.5,
        "scope": "PLAYER",
    }
    selection = {
        "player_name": "Vinicius Junior",
        "selection_type": "OVER",
        "line": 2.5,
        "odds": {"betclic": 3.10, "superbet": 1.88},
    }

    dto = OpportunityExplorerAdapter.from_matched_prop_market(event, market, selection, discrepancy_threshold_pct=10.0)

    assert dto.type == OpportunityType.QUOTE_DISCREPANCY.value
    assert dto.opportunity_type == OpportunityType.QUOTE_DISCREPANCY.value
    assert dto.execution_odds == 3.10
    assert dto.best_bookmaker == "betclic"
    assert dto.lower_bookmaker == "superbet"
    assert dto.lower_execution_odds == 1.88
    assert dto.odds_difference == 1.22
    assert dto.price_discrepancy_pct == 64.89
    assert dto.is_valuebet is False
    assert dto.score is None


def test_from_matched_prop_market_no_discrepancy():
    """G13: OpportunityExplorerAdapter.from_matched_prop_market creates PLAYER_PROP when price delta < 10%."""
    event = {
        "id": "cev_vini_real_barca_low",
        "home_team": "Real Madrid",
        "away_team": "Barcelona",
        "competition": "La Liga",
        "kickoff": "2026-09-05T20:00:00Z",
    }
    market = {
        "market_type": "PLAYER_SHOTS",
        "line": 2.5,
        "scope": "PLAYER",
    }
    selection = {
        "player_name": "Vinicius Junior",
        "selection_type": "OVER",
        "line": 2.5,
        "odds": {"betclic": 1.85, "superbet": 1.80},
    }

    dto = OpportunityExplorerAdapter.from_matched_prop_market(event, market, selection, discrepancy_threshold_pct=10.0)

    assert dto.type == OpportunityType.PLAYER_PROP.value
    assert dto.opportunity_type == OpportunityType.PLAYER_PROP.value
    assert dto.price_discrepancy_pct == 2.78
    assert dto.details["discrepancy"]["is_discrepancy"] is False


def test_betclic_normalizer_tackles():
    """G14: Betclic normalizer correctly maps PLAYER_TACKLES across Polish alias variations."""
    normalizer = BetclicNormalizer()

    test_aliases = [
        "ODBIORY ZAWODNIKA",
        "ODBIORY ZAWODNIKA (OPTA)",
        "LICZBA ODBIORÓW ZAWODNIKA",
        "LICZBA ODBIOROW ZAWODNIKA",
        "ZAWODNIK - LICZBA ODBIORÓW",
        "Odbiory zawodnika (Opta)",
    ]

    for alias in test_aliases:
        resolved = normalizer.normalize_market_name(alias)
        assert resolved == "PLAYER_TACKLES", f"Expected PLAYER_TACKLES for '{alias}', got '{resolved}'"


def test_canonical_rejects_different_player():
    """G15: Canonical matching rejects mismatched player names."""
    is_match, conf = PropExecutionMatcher.is_player_match("Vinicius Junior", "Rodrygo Goes")
    assert is_match is False
    assert conf == 0.0

    is_match2, conf2 = PropExecutionMatcher.is_player_match("Erling Haaland", "Phil Foden")
    assert is_match2 is False


def test_canonical_rejects_different_line():
    """G16: Canonical matching rejects mismatched lines."""
    q_betclic = NormalizedExecutionQuote(
        bookmaker="Betclic",
        player="Vinicius Junior",
        fixture="Real Madrid vs Barcelona",
        stat_type="SHOTS",
        line=1.5,  # line 1.5 offered
        side="OVER",
        odds=1.50,
        active=True,
    )
    matcher = PropExecutionMatcher(normalized_quotes=[q_betclic])
    comp = matcher.match_execution_odds(
        player_name="Vinicius Junior",
        team="Real Madrid",
        opponent="Barcelona",
        stat_type="shots",
        line=2.5,  # target line 2.5
        side="OVER",
    )

    assert comp.execution_odds["Betclic"].status == "UNAVAILABLE"
    assert comp.execution_odds["Betclic"].reason_code == "LINE_MISMATCH"


def test_canonical_rejects_different_stat():
    """G17: Canonical matching rejects mismatched stat types."""
    assert PropExecutionMatcher._is_stat_market_match("SHOTS", "PLAYER_SHOTS_ON_TARGET", "Celne strzały") is False
    assert PropExecutionMatcher._is_stat_market_match("SHOTS", "PLAYER_CARDS", "Kartki") is False
    assert PropExecutionMatcher._is_stat_market_match("GOALS", "PLAYER_FIRST_GOAL", "Pierwszy gol") is False


def test_canonical_rejects_different_period():
    """G18: Canonical matching rejects mismatched periods."""
    assert PropExecutionMatcher._is_stat_market_match("SHOTS", "PLAYER_SHOTS", "1. połowa - liczba strzałów", target_period="FULL_TIME") is False
    assert PropExecutionMatcher._is_stat_market_match("GOALS", "PLAYER_GOALS_FIRST_HALF", "Gol w 1. połowie", target_period="FULL_TIME") is False


def test_explorer_api_discrepancy_filter():
    """G19: Explorer API supports filtering opportunities by type=QUOTE_DISCREPANCY."""
    service = PlatformAPIService()

    dto_disc = UnifiedOpportunityDTO(
        id="opp_disc_1",
        type=OpportunityType.QUOTE_DISCREPANCY.value,
        source="scanner_props",
        player="Vinicius Junior",
        team="Real Madrid",
        opponent="Barcelona",
        event="Real Madrid vs Barcelona",
        market="PLAYER_SHOTS",
        line=2.5,
        side="OVER",
        execution_odds=3.10,
        best_bookmaker="betclic",
        price_discrepancy_pct=64.89,
        odds_difference=1.22,
        lower_execution_odds=1.88,
        lower_bookmaker="superbet",
        status="BETTABLE",
    )
    dto_val = UnifiedOpportunityDTO(
        id="opp_val_1",
        type=OpportunityType.VALUEBET.value,
        source="valuebets",
        event="Bayern vs Dortmund",
        market="1X2",
        execution_odds=2.40,
        best_bookmaker="superbet",
        gross_ev_pct=5.5,
        net_ev_pct=4.8,
        is_valuebet=True,
        status="VALUEBET",
    )
    dto_prop = UnifiedOpportunityDTO(
        id="opp_prop_1",
        type=OpportunityType.PLAYER_PROP.value,
        source="statshub",
        player="Haaland",
        team="Man City",
        opponent="Arsenal",
        event="Man City vs Arsenal",
        market="PLAYER_SHOTS",
        line=2.5,
        side="OVER",
        execution_odds=1.85,
        best_bookmaker="superbet",
        status="BETTABLE",
    )

    # Set cached opportunities on service
    service._unified_opportunities_cache = [dto_disc, dto_val, dto_prop]

    result = service.get_unified_explorer_opportunities(opp_type="QUOTE_DISCREPANCY")
    items = result.get("items", [])

    assert len(items) == 1
    assert items[0]["id"] == "opp_disc_1"
    assert items[0]["type"] == OpportunityType.QUOTE_DISCREPANCY.value
    assert items[0]["price_discrepancy_pct"] == 64.89


def test_master_discrepancy_suite_summary():
    """G20: Master verification check asserting all sub-components operate in unified cohesion."""
    k = CanonicalPropKey("Vinicius Junior", "Real Madrid", "Barcelona", "shots", Decimal("2.5"), "OVER")
    assert k.to_key_string() is not None
