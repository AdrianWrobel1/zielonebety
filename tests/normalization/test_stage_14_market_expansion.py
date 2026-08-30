"""
Stage 14: Market Coverage Expansion Verification Tests (Superbet & Betclic)

Tests coverage and semantic invariants for:
- CARDS & TEAM_CARDS
- CORNERS & TEAM_CORNERS
- OFFSIDES & TEAM_OFFSIDES
"""

from decimal import Decimal
import unittest

from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketMetric,
    MarketPeriod,
    MarketScope,
    ParticipantRole,
    extract_canonical_market_key,
)
from domain.models import Market, Selection, Event, Competition, Odds
from normalization.base_normalizer import NormalizedGraph
from normalization.superbet_normalizer import SuperbetNormalizer
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.market_matcher import MarketMatcher
from normalization.selection_matcher import SelectionMatcher
from normalization.surebet import SurebetDetectorEngine, SurebetStatus
from providers.superbet.models import (
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)


class TestStage14MarketCoverageExpansion(unittest.TestCase):
    """Verifies complete end-to-end normalization, matching, and semantic correctness for Stage 14."""

    def setUp(self):
        self.sb_norm = SuperbetNormalizer()
        self.bc_norm = BetclicNormalizer()
        self.market_matcher = MarketMatcher()
        self.selection_matcher = SelectionMatcher()
        self.detector = SurebetDetectorEngine()

    def test_superbet_match_and_team_corners_normalization(self):
        """Verify Superbet match corners and team corners normalization."""
        ev = SuperbetEvent(
            event_id="sb_100",
            name="Real Madrid vs Barcelona",
            competition_name="La Liga",
            start_time="2026-08-25T20:00:00Z",
            home_team="Real Madrid",
            away_team="Barcelona",
            markets=[
                # Match Corners Over/Under 9.5
                SuperbetMarket(
                    market_id="m1",
                    name="Liczba rzutów rożnych",
                    market_type_id="704",
                    specifiers={"total": "9.5"},
                    is_active=True,
                    selections=[
                        SuperbetSelection(selection_id="s1", name="Poniżej 9.5", odds=SuperbetOdds(decimal_odds=1.85)),
                        SuperbetSelection(selection_id="s2", name="Powyżej 9.5", odds=SuperbetOdds(decimal_odds=1.95)),
                    ]
                ),
                # Home Team Corners Over/Under 5.5
                SuperbetMarket(
                    market_id="m2",
                    name="Real Madrid - liczba rzutów rożnych",
                    market_type_id="713",
                    specifiers={"total": "5.5"},
                    is_active=True,
                    selections=[
                        SuperbetSelection(selection_id="s3", name="Poniżej 5.5", odds=SuperbetOdds(decimal_odds=1.75)),
                        SuperbetSelection(selection_id="s4", name="Powyżej 5.5", odds=SuperbetOdds(decimal_odds=2.05)),
                    ]
                ),
            ]
        )
        graph = self.sb_norm.normalize_event(ev)
        self.assertEqual(len(graph.markets), 2)

        k1 = extract_canonical_market_key(graph.markets[0])
        self.assertEqual(k1.market_type, CanonicalMarketType.TOTALS.value)
        self.assertEqual(k1.metric, MarketMetric.CORNERS.value)
        self.assertEqual(k1.scope, MarketScope.MATCH.value)
        self.assertEqual(k1.line, Decimal("9.5"))
        self.assertEqual(k1.period, MarketPeriod.FULL_TIME.value)

        k2 = extract_canonical_market_key(graph.markets[1])
        self.assertEqual(k2.market_type, CanonicalMarketType.TOTALS.value)
        self.assertEqual(k2.metric, MarketMetric.CORNERS.value)
        self.assertEqual(k2.scope, MarketScope.TEAM.value)
        self.assertEqual(k2.participant_role, ParticipantRole.HOME.value)
        self.assertEqual(k2.line, Decimal("5.5"))

    def test_betclic_match_and_team_cards_normalization(self):
        """Verify Betclic match cards and team cards normalization."""
        ev = BetclicEvent(
            provider_event_id="bc_200",
            name="Arsenal - Chelsea",
            competition_name="Premier League",
            home_team="Arsenal",
            away_team="Chelsea",
            start_time="2026-08-25T18:00:00Z",
            markets=[
                # Match Cards Over/Under 4.5
                BetclicMarket(
                    provider_market_id="m_bc_cards",
                    name="Liczba żółtych kartek w meczu",
                    market_type_code="LICZBA ŻÓŁTYCH KARTEK",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s1",
                            name="Powyżej 4.5",
                            type_code="OVER",
                            handicap=4.5,
                            odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.10)
                        ),
                        BetclicSelection(
                            provider_selection_id="s2",
                            name="Poniżej 4.5",
                            type_code="UNDER",
                            handicap=4.5,
                            odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.70)
                        ),
                    ]
                ),
                # Away Team Cards Over/Under 2.5
                BetclicMarket(
                    provider_market_id="m_bc_away_cards",
                    name="Liczba kartek - Chelsea",
                    market_type_code="LICZBA KARTEK - CHELSEA",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="s3",
                            name="Powyżej 2.5",
                            type_code="OVER",
                            handicap=2.5,
                            odds=BetclicOdds(provider_odds_id="o3", decimal_odds=1.90)
                        ),
                        BetclicSelection(
                            provider_selection_id="s4",
                            name="Poniżej 2.5",
                            type_code="UNDER",
                            handicap=2.5,
                            odds=BetclicOdds(provider_odds_id="o4", decimal_odds=1.85)
                        ),
                    ]
                )
            ]
        )
        graph = self.bc_norm.normalize_event(ev)
        self.assertEqual(len(graph.markets), 2)

        k1 = extract_canonical_market_key(graph.markets[0])
        self.assertEqual(k1.market_type, CanonicalMarketType.TOTALS.value)
        self.assertEqual(k1.metric, MarketMetric.CARDS.value)
        self.assertEqual(k1.scope, MarketScope.MATCH.value)
        self.assertEqual(k1.line, Decimal("4.5"))

        k2 = extract_canonical_market_key(graph.markets[1])
        self.assertEqual(k2.market_type, CanonicalMarketType.TOTALS.value)
        self.assertEqual(k2.metric, MarketMetric.CARDS.value)
        self.assertEqual(k2.scope, MarketScope.TEAM.value)
        self.assertEqual(k2.participant_role, ParticipantRole.AWAY.value)
        self.assertEqual(k2.line, Decimal("2.5"))

    def test_offsides_normalization_and_cross_bookmaker_matching(self):
        """Verify offsides normalization and successful cross-bookmaker matching."""
        sb_ev = SuperbetEvent(
            event_id="sb_off",
            name="Liverpool vs Everton",
            competition_name="Premier League",
            start_time="2026-08-25T21:00:00Z",
            home_team="Liverpool",
            away_team="Everton",
            markets=[
                SuperbetMarket(
                    market_id="m_sb_off",
                    name="Liczba spalonych",
                    specifiers={"total": "3.5"},
                    is_active=True,
                    selections=[
                        SuperbetSelection(selection_id="sb_o", name="Powyżej 3.5", odds=SuperbetOdds(decimal_odds=1.90)),
                        SuperbetSelection(selection_id="sb_u", name="Poniżej 3.5", odds=SuperbetOdds(decimal_odds=1.90)),
                    ]
                )
            ]
        )
        bc_ev = BetclicEvent(
            provider_event_id="bc_off",
            name="Liverpool - Everton",
            competition_name="Premier League",
            start_time="2026-08-25T21:00:00Z",
            home_team="Liverpool",
            away_team="Everton",
            markets=[
                BetclicMarket(
                    provider_market_id="m_bc_off",
                    name="Liczba spalonych w meczu",
                    market_type_code="LICZBA SPALONYCH",
                    is_open=True,
                    selections=[
                        BetclicSelection(
                            provider_selection_id="bc_o",
                            name="Powyżej 3.5",
                            type_code="OVER",
                            handicap=3.5,
                            odds=BetclicOdds(provider_odds_id="o1", decimal_odds=2.15)
                        ),
                        BetclicSelection(
                            provider_selection_id="bc_u",
                            name="Poniżej 3.5",
                            type_code="UNDER",
                            handicap=3.5,
                            odds=BetclicOdds(provider_odds_id="o2", decimal_odds=1.70)
                        ),
                    ]
                )
            ]
        )
        sb_graph = self.sb_norm.normalize_event(sb_ev)
        bc_graph = self.bc_norm.normalize_event(bc_ev)

        sb_mkt = sb_graph.markets[0]
        bc_mkt = bc_graph.markets[0]

        sb_key = extract_canonical_market_key(sb_mkt)
        bc_key = extract_canonical_market_key(bc_mkt)

        # 1. Canonical keys match perfectly
        self.assertEqual(sb_key, bc_key)
        self.assertEqual(sb_key.to_key_string(), "football:TOTALS:OFFSIDES:MATCH:all:FULL_TIME:3.5")

        # 2. Market Matcher indexes both under the exact same canonical key
        market_index, unsupported = self.market_matcher.index_markets([sb_mkt, bc_mkt])
        self.assertEqual(len(unsupported), 0)
        self.assertIn(sb_key, market_index)
        self.assertEqual(len(market_index[sb_key]), 2)

    def test_strict_isolation_between_metrics_and_scopes(self):
        """Verify strict isolation: Corners vs Cards vs Goals vs Offsides, and Match vs Team."""
        keys = [
            extract_canonical_market_key(Market(event_id="e", market_type="TOTALS", line=3.5, metadata={"metric": "GOALS", "scope": "MATCH", "period": "FULL_TIME"})),
            extract_canonical_market_key(Market(event_id="e", market_type="TOTALS", line=3.5, metadata={"metric": "CORNERS", "scope": "MATCH", "period": "FULL_TIME"})),
            extract_canonical_market_key(Market(event_id="e", market_type="TOTALS", line=3.5, metadata={"metric": "CARDS", "scope": "MATCH", "period": "FULL_TIME"})),
            extract_canonical_market_key(Market(event_id="e", market_type="TOTALS", line=3.5, metadata={"metric": "OFFSIDES", "scope": "MATCH", "period": "FULL_TIME"})),
            extract_canonical_market_key(Market(event_id="e", market_type="TOTALS", line=3.5, metadata={"metric": "CARDS", "scope": "TEAM", "participant_role": "HOME", "period": "FULL_TIME"})),
            extract_canonical_market_key(Market(event_id="e", market_type="TOTALS", line=3.5, metadata={"metric": "CARDS", "scope": "TEAM", "participant_role": "AWAY", "period": "FULL_TIME"})),
        ]
        key_strings = [k.to_key_string() for k in keys]
        # All 6 must be uniquely distinct
        self.assertEqual(len(set(key_strings)), 6)


if __name__ == "__main__":
    unittest.main()
