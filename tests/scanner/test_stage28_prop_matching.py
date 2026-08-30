"""
Stage 28 — Player Props Execution Matching Regression Tests

Verifies:
1. End-to-end matching of StatsHub player props against Superbet and Betclic execution quotes.
2. Canonical Prop Key, fixture, player name, market type, and line matching (e.g. 0.5, 1.5, 2.5).
3. Superbet player market normalization: 2+ goals -> line 1.5, 3+ goals -> line 2.5.
4. Betclic 403 degradation isolation without failing Superbet or the props pipeline.
5. Correct execution statuses: BETTABLE, REFERENCE_ONLY, NO_EXECUTION_MARKET.
"""

import pytest
from decimal import Decimal
from typing import Dict, Any, List

from domain.models import Event, Market, Selection, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.superbet_normalizer import SuperbetNormalizer
from scanner.execution_providers import NormalizedExecutionQuote, ExecutionMarketEngine
from scanner.prop_execution_matcher import PropExecutionMatcher, PropOddsComparison
from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection


class TestStage28PropExecutionMatching:
    """Test suite covering Stage 28 acceptance criteria."""

    def test_a_superbet_matching_produces_bettable_with_real_odds(self):
        """Valid Superbet match produces BETTABLE status with Superbet bookmaker and valid Polish odds."""
        matcher = PropExecutionMatcher()

        # Simulated Superbet execution quotes
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Tjaronn Chery",
                fixture="NEC Nijmegen vs PEC Zwolle",
                stat_type="SHOTS",
                line=0.5,
                side="OVER",
                odds=1.45,
                active=True,
                event_id="sb_1001",
                market_name="Zawodnik - liczba strzałów",
                selection_name="Powyżej 0.5",
            ),
        ]

        ref_odds = [
            {"bookmaker": "Bet365", "decimal_odds": 1.30, "line": 0.5, "side": "OVER"}
        ]

        result = matcher.match_execution_odds(
            player_name="Tjaronn Chery",
            team="NEC Nijmegen",
            opponent="PEC Zwolle",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
            reference_odds=ref_odds,
            normalized_quotes=quotes,
        )

        assert result.execution_status == "BETTABLE"
        assert result.best_executable_bookmaker == "Superbet"
        assert result.best_executable_odds == 1.45
        assert result.reference_best_odds == 1.30
        assert result.reference_best_bookmaker == "Bet365"
        assert "Superbet" in result.execution_odds
        assert result.execution_odds["Superbet"].status == "AVAILABLE"
        assert result.execution_odds["Superbet"].decimal_odds == 1.45

    def test_b_player_name_and_fixture_variations(self):
        """Player name inverted comma-format, accents, and fixture naming match correctly."""
        matcher = PropExecutionMatcher()

        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Fernandes, Robson",
                fixture="Novorizontino SP vs Sport Recife",
                stat_type="GOALS",
                line=0.5,
                side="OVER",
                odds=2.20,
                active=True,
                event_id="sb_1002",
                market_name="Zawodnik - strzeli gola",
                selection_name="TAK",
            ),
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Brian Rodríguez",
                fixture="Club América vs Club Puebla",
                stat_type="SHOTS_ON_TARGET",
                line=0.5,
                side="OVER",
                odds=1.85,
                active=True,
                event_id="sb_1003",
                market_name="Zawodnik - liczba celnych strzałów",
                selection_name="Powyżej 0.5",
            ),
        ]

        # Inverted name match
        res1 = matcher.match_execution_odds(
            player_name="Robson Fernandes",
            team="Novorizontino SP",
            opponent="Sport Recife",
            stat_type="GOALS",
            line=0.5,
            side="OVER",
            normalized_quotes=quotes,
        )
        assert res1.execution_status == "BETTABLE"
        assert res1.best_executable_odds == 2.20
        assert res1.best_executable_bookmaker == "Superbet"

        # Accent stripping match
        res2 = matcher.match_execution_odds(
            player_name="Brian Rodriguez",
            team="Club America",
            opponent="Puebla",
            stat_type="SHOTS_ON_TARGET",
            line=0.5,
            side="OVER",
            normalized_quotes=quotes,
        )
        assert res2.execution_status == "BETTABLE"
        assert res2.best_executable_odds == 1.85

    def test_c_superbet_multigoal_market_normalization(self):
        """'Zawodnik - strzeli 2+ gole' maps to line 1.5, '3+ gole' maps to line 2.5."""
        normalizer = SuperbetNormalizer()

        sb_event = SuperbetEvent(
            event_id="sb_ev_1",
            name="Novorizontino SP vs Sport Recife",
            home_team="Novorizontino SP",
            away_team="Sport Recife",
            competition_name="Brasileiro Serie B",
            start_time="2026-08-26T20:00:00Z",
            markets=[
                SuperbetMarket(
                    market_id="m_2plus",
                    name="Zawodnik - strzeli 2+ gole",
                    market_type_id="233480",
                    specifiers={"player": "Robson Fernandes"},
                    selections=[
                        SuperbetSelection(
                            selection_id="s_2p",
                            name="TAK",
                            outcome_id=6376,
                            odds=Odds(selection_id="s_2p", bookmaker="Superbet", decimal_odds=Decimal("6.50")),
                        )
                    ],
                ),
                SuperbetMarket(
                    market_id="m_3plus",
                    name="Zawodnik - strzeli 3+ gole",
                    market_type_id="233481",
                    specifiers={"player": "Robson Fernandes"},
                    selections=[
                        SuperbetSelection(
                            selection_id="s_3p",
                            name="TAK",
                            outcome_id=6376,
                            odds=Odds(selection_id="s_3p", bookmaker="Superbet", decimal_odds=Decimal("21.00")),
                        )
                    ],
                ),
            ],
        )

        graph = normalizer.normalize_event(sb_event)
        assert len(graph.markets) == 2
        mkt_2p = [m for m in graph.markets if "2+" in m.metadata.get("raw_name", "")][0]
        mkt_3p = [m for m in graph.markets if "3+" in m.metadata.get("raw_name", "")][0]

        assert mkt_2p.market_type == "PLAYER_GOALS"
        assert mkt_2p.line == 1.5

        assert mkt_3p.market_type == "PLAYER_GOALS"
        assert mkt_3p.line == 2.5

    def test_d_betclic_403_isolation_preserves_superbet(self):
        """When Betclic degrades or fails, Superbet matching continues unimpeded."""
        matcher = PropExecutionMatcher()

        # Quotes with only Superbet present (Betclic returned 403)
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Oscar Aga",
                fixture="Moss FK vs Sogndal IL",
                stat_type="GOALS",
                line=0.5,
                side="OVER",
                odds=2.37,
                active=True,
                event_id="sb_1004",
                market_name="Zawodnik - strzeli gola",
                selection_name="TAK",
            ),
        ]

        ref_odds = [
            {"bookmaker": "Bet365", "decimal_odds": 2.10, "line": 0.5, "side": "OVER"}
        ]

        result = matcher.match_execution_odds(
            player_name="Oscar Aga",
            team="Moss FK",
            opponent="Sogndal IL",
            stat_type="GOALS",
            line=0.5,
            side="OVER",
            reference_odds=ref_odds,
            normalized_quotes=quotes,
        )

        assert result.execution_status == "BETTABLE"
        assert result.best_executable_bookmaker == "Superbet"
        assert result.best_executable_odds == 2.37
        assert result.execution_odds["Superbet"].status == "AVAILABLE"
        assert result.execution_odds["Betclic"].status == "UNAVAILABLE"

    def test_e_unmatched_props_remain_reference_only_not_bettable(self):
        """Props without valid executable bookmaker odds must remain REFERENCE_ONLY (or NO_EXECUTION_MARKET)."""
        matcher = PropExecutionMatcher()

        # No matching execution quotes available
        quotes = [
            NormalizedExecutionQuote(
                bookmaker="Superbet",
                player="Other Player",
                fixture="Different Team vs Another Team",
                stat_type="SHOTS",
                line=0.5,
                side="OVER",
                odds=1.50,
                active=True,
                event_id="sb_9999",
            )
        ]

        # Case 1: Has reference odds -> REFERENCE_ONLY
        ref_odds = [
            {"bookmaker": "Bet365", "decimal_odds": 1.36, "line": 0.5, "side": "OVER"}
        ]
        res1 = matcher.match_execution_odds(
            player_name="Juan Valencia",
            team="Club Necaxa",
            opponent="Cruz Azul",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
            reference_odds=ref_odds,
            normalized_quotes=quotes,
        )
        assert res1.execution_status == "REFERENCE_ONLY"
        assert res1.best_executable_odds is None
        assert res1.reference_best_odds == 1.36

        # Case 2: No reference odds -> NO_EXECUTION_MARKET
        res2 = matcher.match_execution_odds(
            player_name="Juan Valencia",
            team="Club Necaxa",
            opponent="Cruz Azul",
            stat_type="SHOTS",
            line=0.5,
            side="OVER",
            reference_odds=[],
            normalized_quotes=quotes,
        )
        assert res2.execution_status == "NO_EXECUTION_MARKET"
        assert res2.best_executable_odds is None
