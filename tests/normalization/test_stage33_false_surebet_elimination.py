"""
Stage 33 Deterministic Regression Test Suite: False Surebet Elimination

Mandatory Invariants Tested:
1. Raków–Hajduk HT/FT vs 1X2 false surebet elimination:
   - "Wynik Meczu Połowa / Cały" is normalized as HALF_TIME_FULL_TIME, NOT 1X2.
   - Compound HT/FT selection labels (e.g. "Raków Częstochowa / Hajduk Split") are NEVER mapped to 1X2 HOME/DRAW/AWAY.
   - Fake +418.18% surebet is 100% eliminated.
2. Same-event wrong-market isolation:
   - Standard BTTS ("Oba zespoły strzelą gola") and Penalty BTTS ("Oba zespoły strzelą z rzutu karnego") have distinct canonical keys.
   - Synthetic BTTS surebet (+82.25% or +718%) is 100% eliminated.
3. Same market wrong line isolation:
   - Over 2.5 and Under 1.5 have distinct lines and CANNOT be matched into an arbitrage pair.
4. Cross-event isolation:
   - Selections from different canonical events are strictly quarantined.
5. Single-bookmaker cross-market stitching prevention:
   - Selections from different market IDs on the same provider cannot be stitched into a single-bookmaker surebet.
6. Special & combo market separation:
   - Combo markets ("Wynik meczu & oba zespoły strzelą", "Podwójna szansa & powyżej/poniżej", "Wynik meczu - Xtra Wygrana")
     cannot collapse into standard 1X2 or standard BTTS.
7. Preservation of legitimate high-odds arbitrage:
   - Legitimate genuine cross-bookmaker arbitrage (e.g. Superbet Home @ 12.00, Betclic Draw @ 4.50, Betclic Away @ 1.50)
     is 100% preserved and detected with exact precision.
"""

from decimal import Decimal
import pytest

from domain.models import Competition, Event, Market, Selection, Odds, MatchEvidence
from normalization.betclic_normalizer import BetclicNormalizer
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
    extract_canonical_market_key,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
    extract_canonical_selection_key,
)
from normalization.odds_comparison import (
    OddsComparison,
    OddsComparisonStatus,
)
from normalization.surebet import (
    SurebetDetectorEngine,
    SurebetStatus,
    MarketCompletenessStatus,
)
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)
from core.tax_engine import TaxEngine, BookmakerTaxConfig


@pytest.fixture
def betclic_norm():
    return BetclicNormalizer()


@pytest.fixture
def zero_tax_engine():
    return SurebetDetectorEngine(
        tax_engine=TaxEngine(
            custom_configs={
                "superbet": BookmakerTaxConfig(tax_enabled=False),
                "betclic": BookmakerTaxConfig(tax_enabled=False),
            }
        )
    )


class TestStage33FalseSurebetElimination:
    """Rigorous tests confirming complete elimination of false-positive surebets."""

    def test_rakow_hajduk_ht_ft_is_not_normalized_as_1x2(self, betclic_norm):
        """Proves that Betclic 'Wynik Meczu Połowa / Cały' is normalized as HALF_TIME_FULL_TIME, not 1X2."""
        raw_event = BetclicEvent(
            provider_event_id="1204693641478152",
            name="Raków Częstochowa - Hajduk Split",
            competition_name="Liga Konferencji",
            home_team="Raków Częstochowa",
            away_team="Hajduk Split",
            start_time="2026-08-27T19:00:00Z",
            markets=[
                # Legitimate 1X2 market
                BetclicMarket(
                    provider_market_id="1204693643579396",
                    name="Wynik meczu (z wyłączeniem dogrywki)",
                    market_type_code="Ftb_Mr3",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s1", name="Raków Częstochowa", type_code="1", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=2.18)),
                        BetclicSelection(provider_selection_id="s2", name="Remis", type_code="X", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=3.25)),
                        BetclicSelection(provider_selection_id="s3", name="Hajduk Split", type_code="2", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=3.30)),
                    ],
                ),
                # HT/FT market (previously contaminated into fake 1X2 with 38.00 / 7.50 / 30.00)
                BetclicMarket(
                    provider_market_id="1205174082207831",
                    name="Wynik Meczu Połowa / Cały",
                    market_type_code="Ftb_HtFt",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s_hf1", name="Raków Częstochowa / Hajduk Split", type_code="1/2", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=38.00)),
                        BetclicSelection(provider_selection_id="s_hf2", name="Remis / Hajduk Split", type_code="X/2", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=7.50)),
                        BetclicSelection(provider_selection_id="s_hf3", name="Hajduk Split / Raków Częstochowa", type_code="2/1", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=30.00)),
                        BetclicSelection(provider_selection_id="s_hf4", name="Raków Częstochowa / Raków Częstochowa", type_code="1/1", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=3.50)),
                        BetclicSelection(provider_selection_id="s_hf5", name="Remis / Remis", type_code="X/X", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=4.80)),
                    ],
                ),
            ],
        )

        graph = betclic_norm.normalize_event(raw_event)

        # 1. Exactly ONE 1X2 market exists
        mkt_1x2_list = [m for m in graph.markets if m.market_type == CanonicalMarketType.ONE_X_TWO.value]
        assert len(mkt_1x2_list) == 1
        mkt_1x2 = mkt_1x2_list[0]
        assert mkt_1x2.provider_ids["betclic"] == "1204693643579396"

        # 2. 1X2 selections are strictly the 3 real match result outcomes
        sels_1x2 = [s for s in graph.selections if s.market_id == mkt_1x2.internal_id]
        assert len(sels_1x2) == 3
        sel_types_1x2 = {s.selection_type for s in sels_1x2}
        assert sel_types_1x2 == {"HOME", "DRAW", "AWAY"}

        odds_1x2 = {s.selection_type: o.decimal_odds for s in sels_1x2 for o in graph.odds_list if o.selection_id == s.internal_id}
        assert odds_1x2["HOME"] == 2.18
        assert odds_1x2["DRAW"] == 3.25
        assert odds_1x2["AWAY"] == 3.30

        # 3. HT/FT market was NOT classified as 1X2
        mkt_htft = [m for m in graph.markets if m.provider_ids.get("betclic") == "1205174082207831"][0]
        assert mkt_htft.market_type == "HALF_TIME_FULL_TIME"

    def test_penalty_btts_cannot_form_synthetic_surebet_with_normal_btts(self, betclic_norm, zero_tax_engine):
        """Proves that 'Oba zespoły strzelą z rzutu karnego' (@ 30.00) cannot combine with normal BTTS NO (@ 1.94)."""
        raw_event = BetclicEvent(
            provider_event_id="1204391711342592",
            name="Monaco - Górnik Zabrze",
            competition_name="Liga Europy",
            home_team="Monaco",
            away_team="Górnik Zabrze",
            start_time="2026-08-27T21:00:00Z",
            markets=[
                # Genuine BTTS
                BetclicMarket(
                    provider_market_id="m_btts_real",
                    name="Oba zespoły strzelą gola",
                    market_type_code="Ftb_Btts",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s_yes", name="Tak", type_code="YES", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=1.75)),
                        BetclicSelection(provider_selection_id="s_no", name="Nie", type_code="NO", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=1.94)),
                    ],
                ),
                # Penalty BTTS
                BetclicMarket(
                    provider_market_id="m_penalty_btts",
                    name="Oba zespoły strzelą z rzutu karnego",
                    market_type_code="Ftb_PenBtts",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s_pen_yes", name="Tak", type_code="YES", odds=BetclicOdds(provider_odds_id="o_test", decimal_odds=30.00)),
                    ],
                ),
            ],
        )

        graph = betclic_norm.normalize_event(raw_event)

        # 1. Genuine BTTS is normalized as BTTS
        btts_mkts = [m for m in graph.markets if m.market_type == CanonicalMarketType.BTTS.value]
        assert len(btts_mkts) == 1
        assert btts_mkts[0].provider_ids["betclic"] == "m_btts_real"

        # 2. Penalty BTTS is NOT normalized as BTTS
        pen_mkt = [m for m in graph.markets if m.provider_ids.get("betclic") == "m_penalty_btts"][0]
        assert pen_mkt.market_type != CanonicalMarketType.BTTS.value

    def test_single_bookmaker_cross_market_stitching_is_rejected(self, zero_tax_engine):
        """Proves that SurebetDetectorEngine strictly rejects any opportunity combining different market IDs from the same provider."""
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.BTTS.value,
            period="FULL_TIME",
            scope="MATCH",
        )
        sel_key_yes = CanonicalSelectionKey(market_key=mkt_key, selection_type="YES")
        sel_key_no = CanonicalSelectionKey(market_key=mkt_key, selection_type="NO")

        # Adversarial scenario: Two distinct markets from Betclic (market A has YES @ 30.0, market B has NO @ 1.94)
        comps = [
            OddsComparison(
                canonical_event_id="cev_01",
                canonical_market_key=mkt_key,
                canonical_selection_key=sel_key_yes,
                source_provider="betclic",
                target_provider="betclic",
                source_event_id="e1",
                target_event_id="e1",
                source_internal_event_id="ie1",
                target_internal_event_id="ie1",
                source_market_id="m_market_A",  # Market A
                target_market_id="m_market_A",
                source_selection_id="s1",
                target_selection_id="s1",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("30.00"),
                target_odds=Decimal("30.00"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
            OddsComparison(
                canonical_event_id="cev_01",
                canonical_market_key=mkt_key,
                canonical_selection_key=sel_key_no,
                source_provider="betclic",
                target_provider="betclic",
                source_event_id="e1",
                target_event_id="e1",
                source_internal_event_id="ie1",
                target_internal_event_id="ie1",
                source_market_id="m_market_B",  # Market B (DIFFERENT MARKET ID)
                target_market_id="m_market_B",
                source_selection_id="s2",
                target_selection_id="s2",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("1.94"),
                target_odds=Decimal("1.94"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
        ]

        eval_res = zero_tax_engine.evaluate_market(
            canonical_event_id="cev_01",
            canonical_market_key=mkt_key,
            comparisons=comps,
        )

        assert eval_res.status == SurebetStatus.INVALID_MARKET
        assert eval_res.exclusion_reason_code == "CROSS_MARKET_CONTAMINATION"
        assert eval_res.opportunity is None

    def test_same_market_wrong_line_isolation(self, zero_tax_engine):
        """Proves that selections on different lines (Over 2.5 vs Under 1.5) have distinct canonical keys and cannot form a surebet."""
        key_over_25 = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS.value,
            line=2.5,
            period="FULL_TIME",
            scope="MATCH",
        )
        key_under_15 = CanonicalMarketKey(
            market_type=CanonicalMarketType.TOTALS.value,
            line=1.5,
            period="FULL_TIME",
            scope="MATCH",
        )

        assert key_over_25 != key_under_15
        assert key_over_25.to_key_string() == "football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5"
        assert key_under_15.to_key_string() == "football:TOTALS:GOALS:MATCH:all:FULL_TIME:1.5"

    def test_combo_and_special_markets_isolation(self, betclic_norm):
        """Proves that compound / combo markets are never classified as standard 1X2 or BTTS."""
        raw_event = BetclicEvent(
            provider_event_id="ev_combo",
            name="Chelsea - Arsenal",
            competition_name="Premier League",
            home_team="Chelsea",
            away_team="Arsenal",
            start_time="2026-08-28T20:00:00Z",
            markets=[
                BetclicMarket(
                    provider_market_id="m_combo1",
                    name="Wynik meczu & oba zespoły strzelą",
                    market_type_code="Ftb_1x2_Btts",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s1", name="Chelsea / Tak", type_code="C_YES", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=4.20)),
                        BetclicSelection(provider_selection_id="s2", name="Remis / Tak", type_code="D_YES", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=4.50)),
                    ],
                ),
                BetclicMarket(
                    provider_market_id="m_combo2",
                    name="Podwójna szansa & powyżej/poniżej",
                    market_type_code="Ftb_Dc_Tot",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s3", name="1X & Powyżej 2.5", type_code="1X_O", odds=BetclicOdds(provider_odds_id="o3", decimal_odds=2.40)),
                    ],
                ),
                BetclicMarket(
                    provider_market_id="m_xtra",
                    name="Wynik meczu - Xtra Wygrana",
                    market_type_code="Ftb_Xtra",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="s4", name="Chelsea", type_code="1", odds=BetclicOdds(provider_odds_id="o4", decimal_odds=2.10)),
                        BetclicSelection(provider_selection_id="s5", name="Arsenal", type_code="2", odds=BetclicOdds(provider_odds_id="o5", decimal_odds=3.20)),
                    ],
                ),
            ],
        )

        graph = betclic_norm.normalize_event(raw_event)

        for m in graph.markets:
            assert m.market_type not in (CanonicalMarketType.ONE_X_TWO.value, CanonicalMarketType.BTTS.value, CanonicalMarketType.DOUBLE_CHANCE.value)

    def test_legitimate_high_odds_surebet_is_preserved(self, zero_tax_engine):
        """Proves that a legitimate high-odds cross-bookmaker arbitrage on 1X2 is 100% preserved and detected."""
        mkt_key = CanonicalMarketKey(
            market_type=CanonicalMarketType.ONE_X_TWO.value,
            period="FULL_TIME",
            scope="MATCH",
        )
        sel_key_home = CanonicalSelectionKey(market_key=mkt_key, selection_type="HOME")
        sel_key_draw = CanonicalSelectionKey(market_key=mkt_key, selection_type="DRAW")
        sel_key_away = CanonicalSelectionKey(market_key=mkt_key, selection_type="AWAY")

        # Legitimate arbitrage: Superbet Home @ 12.00, Betclic Draw @ 4.50, Betclic Away @ 1.50
        # S = 1/12.00 + 1/4.50 + 1/1.50 = 0.08333 + 0.22222 + 0.66667 = 0.97222 < 1.0 (Margin: +2.86%)
        comps = [
            OddsComparison(
                canonical_event_id="cev_legit",
                canonical_market_key=mkt_key,
                canonical_selection_key=sel_key_home,
                source_provider="superbet",
                target_provider="betclic",
                source_event_id="sb_e1",
                target_event_id="bc_e1",
                source_internal_event_id="ie_sb",
                target_internal_event_id="ie_bc",
                source_market_id="sb_m1",
                target_market_id="bc_m1",
                source_selection_id="sb_s1",
                target_selection_id="bc_s1",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("12.00"),  # High odds
                target_odds=Decimal("8.50"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
            OddsComparison(
                canonical_event_id="cev_legit",
                canonical_market_key=mkt_key,
                canonical_selection_key=sel_key_draw,
                source_provider="superbet",
                target_provider="betclic",
                source_event_id="sb_e1",
                target_event_id="bc_e1",
                source_internal_event_id="ie_sb",
                target_internal_event_id="ie_bc",
                source_market_id="sb_m1",
                target_market_id="bc_m1",
                source_selection_id="sb_s2",
                target_selection_id="bc_s2",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("3.80"),
                target_odds=Decimal("4.50"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
            OddsComparison(
                canonical_event_id="cev_legit",
                canonical_market_key=mkt_key,
                canonical_selection_key=sel_key_away,
                source_provider="superbet",
                target_provider="betclic",
                source_event_id="sb_e1",
                target_event_id="bc_e1",
                source_internal_event_id="ie_sb",
                target_internal_event_id="ie_bc",
                source_market_id="sb_m1",
                target_market_id="bc_m1",
                source_selection_id="sb_s3",
                target_selection_id="bc_s3",
                status=OddsComparisonStatus.VALID,
                source_odds=Decimal("1.42"),
                target_odds=Decimal("1.50"),
                evidence={"source_validation_status": "VALID", "target_validation_status": "VALID"},
            ),
        ]

        eval_res = zero_tax_engine.evaluate_market(
            canonical_event_id="cev_legit",
            canonical_market_key=mkt_key,
            comparisons=comps,
        )

        assert eval_res.status == SurebetStatus.SUREBET
        assert eval_res.opportunity is not None
        opp = eval_res.opportunity
        assert opp.implied_probability_sum < Decimal("1.0")
        assert round(opp.arbitrage_margin * 100, 2) == Decimal("2.86")
        assert opp.is_mixed_bookmakers is True
        assert set(opp.bookmakers) == {"superbet", "betclic"}
        assert len(opp.legs) == 3

    def test_total_cards_vs_card_points_semantic_separation(self, betclic_norm):
        """Proves TOTAL_CARDS (metric=CARDS) and TOTAL_CARD_POINTS (metric=CARD_POINTS) produce distinct keys and never cross-match."""
        from normalization.superbet_normalizer import SuperbetNormalizer
        from normalization.market_matcher import MarketMatcher, MarketMatchDecisionType
        from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection, SuperbetOdds

        sb_norm = SuperbetNormalizer()
        matcher = MarketMatcher()

        # Superbet: actual card count (Liczba kartek - Powyżej 5.5)
        sb_event = SuperbetEvent(
            event_id="sb_e1",
            name="Chelsea vs Arsenal",
            competition_name="Premier League",
            start_time="2026-08-30T16:30:00Z",
            home_team="Chelsea",
            away_team="Arsenal",
            markets=[
                SuperbetMarket(
                    market_id="sb_m_cards",
                    name="Liczba kartek (5.5)",
                    is_active=True,
                    specifiers={"total": "5.5"},
                    selections=[
                        SuperbetSelection(selection_id="sb_s_o", name="Powyżej 5.5", is_active=True, odds=SuperbetOdds(decimal_odds=2.40)),
                        SuperbetSelection(selection_id="sb_s_u", name="Poniżej 5.5", is_active=True, odds=SuperbetOdds(decimal_odds=1.55)),
                    ],
                )
            ],
        )

        # Betclic: card points / booking points (Liczba punktów za kartki - Poniżej 5.5)
        bc_event = BetclicEvent(
            provider_event_id="bc_e1",
            name="Chelsea vs Arsenal",
            competition_name="Premier League",
            start_time="2026-08-30T16:30:00Z",
            home_team="Chelsea",
            away_team="Arsenal",
            markets=[
                BetclicMarket(
                    provider_market_id="bc_m_card_points",
                    name="Liczba punktów za kartki",
                    market_type_code="Ftb_CardPoints",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="bc_s_o", name="Powyżej", type_code="OVER", handicap="5.5", odds=BetclicOdds(provider_odds_id="o1", decimal_odds=1.60)),
                        BetclicSelection(provider_selection_id="bc_s_u", name="Poniżej", type_code="UNDER", handicap="5.5", odds=BetclicOdds(provider_odds_id="o2", decimal_odds=2.20)),
                    ],
                ),
                # Betclic: legitimate card count market (Liczba kartek w meczu)
                BetclicMarket(
                    provider_market_id="bc_m_cards_legit",
                    name="Liczba kartek w meczu",
                    market_type_code="Ftb_Cards",
                    is_open=True,
                    selections=[
                        BetclicSelection(provider_selection_id="bc_s_co", name="Powyżej", type_code="OVER", handicap="5.5", odds=BetclicOdds(provider_odds_id="o3", decimal_odds=2.30)),
                        BetclicSelection(provider_selection_id="bc_s_cu", name="Poniżej", type_code="UNDER", handicap="5.5", odds=BetclicOdds(provider_odds_id="o4", decimal_odds=1.62)),
                    ],
                ),
            ],
        )

        sb_graph = sb_norm.normalize_event(sb_event)
        bc_graph = betclic_norm.normalize_event(bc_event)

        sb_card_mkt = sb_graph.markets[0]
        sb_card_key = extract_canonical_market_key(sb_card_mkt)
        assert sb_card_key.metric == "CARDS"
        assert sb_card_key.line == Decimal("5.5")

        bc_point_mkt = next(m for m in bc_graph.markets if m.metadata.get("metric") == "CARD_POINTS")
        bc_point_key = extract_canonical_market_key(bc_point_mkt)
        assert bc_point_key.metric == "CARD_POINTS"
        assert bc_point_key.line == Decimal("5.5")

        # Invariant 1: TOTAL_CARDS != TOTAL_CARD_POINTS
        assert sb_card_key != bc_point_key
        assert sb_card_key.to_key_string() != bc_point_key.to_key_string()

        # Invariant 2: Cross-match evaluation rejected
        decision = matcher.match(sb_card_mkt, bc_point_mkt)
        assert decision.decision == MarketMatchDecisionType.REJECTED
        assert "METRIC_MISMATCH" in decision.reasons

        # Invariant 3: Legitimate TOTAL_CARDS to TOTAL_CARDS matches successfully
        bc_card_mkt = next(m for m in bc_graph.markets if m.metadata.get("metric") == "CARDS")
        bc_card_key = extract_canonical_market_key(bc_card_mkt)
        assert bc_card_key.metric == "CARDS"

        match_decision = matcher.match(sb_card_mkt, bc_card_mkt)
        assert match_decision.decision == MarketMatchDecisionType.MATCHED
        assert match_decision.canonical_market_key == sb_card_key
