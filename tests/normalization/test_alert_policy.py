"""
Stage 6.4: Comprehensive Unit, Property, and Adversarial Test Suite
for Opportunity Alert Policy & Change Significance.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import pytest

from normalization.alert_policy import (
    ChangeClassification,
    DefaultOpportunityAlertPolicy,
    OpportunityAlertConfig,
    OpportunityChangeEvaluation,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
)
from normalization.surebet import (
    SurebetLeg,
    SurebetOpportunity,
    SurebetStatus,
)


# ---------------------------------------------------------------------------
# Helpers & Fixtures
# ---------------------------------------------------------------------------

def make_1x2_opp(
    home_odds: Decimal = Decimal("2.10"),
    draw_odds: Decimal = Decimal("3.60"),
    away_odds: Decimal = Decimal("4.20"),
    home_provider: str = "superbet",
    draw_provider: str = "superbet",
    away_provider: str = "betclic",
    event_id: str = "evt_001",
    opp_id: str = "opp_001",
) -> SurebetOpportunity:
    """Constructs a test 1X2 SurebetOpportunity."""
    mkt_key = CanonicalMarketKey(
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
    )
    leg_h = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(
            market_key=mkt_key,
            selection_type=CanonicalSelectionType.HOME.value,
            participant_role="HOME",
        ),
        selection_type="HOME",
        provider=home_provider,
        odds=home_odds,
        source_selection_id=f"{home_provider}_h",
        implied_probability=Decimal("1.0") / home_odds,
    )
    leg_d = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(
            market_key=mkt_key,
            selection_type=CanonicalSelectionType.DRAW.value,
        ),
        selection_type="DRAW",
        provider=draw_provider,
        odds=draw_odds,
        source_selection_id=f"{draw_provider}_d",
        implied_probability=Decimal("1.0") / draw_odds,
    )
    leg_a = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(
            market_key=mkt_key,
            selection_type=CanonicalSelectionType.AWAY.value,
            participant_role="AWAY",
        ),
        selection_type="AWAY",
        provider=away_provider,
        odds=away_odds,
        source_selection_id=f"{away_provider}_a",
        implied_probability=Decimal("1.0") / away_odds,
    )
    legs = (leg_h, leg_d, leg_a)
    s = sum(l.implied_probability for l in legs)
    margin = (Decimal("1.0") / s) - Decimal("1.0")
    books = tuple(sorted(set([home_provider, draw_provider, away_provider])))

    return SurebetOpportunity(
        opportunity_id=opp_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s,
        arbitrage_margin=margin,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=len(books) > 1,
        bookmakers=books,
    )


def make_totals_opp(
    line: Decimal = Decimal("2.5"),
    over_odds: Decimal = Decimal("2.05"),
    under_odds: Decimal = Decimal("2.10"),
    over_provider: str = "superbet",
    under_provider: str = "betclic",
    event_id: str = "evt_tot_001",
    opp_id: str = "opp_tot_001",
) -> SurebetOpportunity:
    """Constructs a test Totals (Over/Under) SurebetOpportunity."""
    mkt_key = CanonicalMarketKey(
        market_type=CanonicalMarketType.TOTALS.value,
        line=line,
        period=MarketPeriod.FULL_TIME.value,
        scope=MarketScope.MATCH.value,
    )
    leg_o = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(
            market_key=mkt_key,
            selection_type=CanonicalSelectionType.OVER.value,
        ),
        selection_type="OVER",
        provider=over_provider,
        odds=over_odds,
        source_selection_id=f"{over_provider}_over",
        implied_probability=Decimal("1.0") / over_odds,
    )
    leg_u = SurebetLeg(
        canonical_selection_key=CanonicalSelectionKey(
            market_key=mkt_key,
            selection_type=CanonicalSelectionType.UNDER.value,
        ),
        selection_type="UNDER",
        provider=under_provider,
        odds=under_odds,
        source_selection_id=f"{under_provider}_under",
        implied_probability=Decimal("1.0") / under_odds,
    )
    legs = (leg_o, leg_u)
    s = sum(l.implied_probability for l in legs)
    margin = (Decimal("1.0") / s) - Decimal("1.0")
    books = tuple(sorted(set([over_provider, under_provider])))

    return SurebetOpportunity(
        opportunity_id=opp_id,
        canonical_event_id=event_id,
        canonical_market_key=mkt_key,
        legs=legs,
        implied_probability_sum=s,
        arbitrage_margin=margin,
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=len(books) > 1,
        bookmakers=books,
    )


# ---------------------------------------------------------------------------
# Section 1: Classification Test Matrix
# ---------------------------------------------------------------------------

class TestAlertPolicyClassificationMatrix:
    """Comprehensive test matrix verifying all change dimensions."""

    def test_identical_snapshot_returns_no_change(self):
        """Identical previous and current opportunities return NO_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp = make_1x2_opp()
        eval_res = policy.classify_change(previous=opp, current=opp)

        assert eval_res.classification == ChangeClassification.NO_CHANGE
        assert eval_res.is_material is False
        assert eval_res.margin_delta == Decimal("0.0")
        assert eval_res.max_odds_relative_delta == Decimal("0.0")
        assert eval_res.structural_change is False

    def test_decimal_precision_formatting_equivalence(self):
        """Equivalent numeric values formatted differently ('2.100' vs '2.1') evaluate to NO_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp1 = make_1x2_opp(home_odds=Decimal("2.1000"))
        opp2 = make_1x2_opp(home_odds=Decimal("2.1"))

        eval_res = policy.classify_change(previous=opp1, current=opp2)
        assert eval_res.classification == ChangeClassification.NO_CHANGE
        assert eval_res.is_material is False

    def test_tiny_odds_movement_classified_insignificant(self):
        """Tiny odds movement (e.g. 2.10 -> 2.11, ~0.48% rel delta < 2.0%) is INSIGNIFICANT_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp1 = make_1x2_opp(home_odds=Decimal("2.10"))
        opp2 = make_1x2_opp(home_odds=Decimal("2.11"))

        eval_res = policy.classify_change(previous=opp1, current=opp2)
        assert eval_res.classification == ChangeClassification.INSIGNIFICANT_CHANGE
        assert eval_res.is_material is False
        assert len(eval_res.reasons) > 0
        assert "below material significance" in eval_res.reasons[-1]

    def test_small_margin_movement_classified_insignificant(self):
        """Small margin movement below threshold (0.50 pp) is INSIGNIFICANT_CHANGE."""
        policy = DefaultOpportunityAlertPolicy(
            OpportunityAlertConfig(
                min_margin_delta=Decimal("0.0050"),  # 0.50 percentage points
                min_odds_relative_delta=Decimal("0.0500"),  # 5.0% relative odds
            )
        )
        opp1 = make_1x2_opp(home_odds=Decimal("2.10"), draw_odds=Decimal("3.60"), away_odds=Decimal("4.20"))
        # Very slight shift in draw odds
        opp2 = make_1x2_opp(home_odds=Decimal("2.10"), draw_odds=Decimal("3.64"), away_odds=Decimal("4.20"))

        eval_res = policy.classify_change(previous=opp1, current=opp2)
        assert eval_res.classification == ChangeClassification.INSIGNIFICANT_CHANGE
        assert eval_res.is_material is False

    def test_material_margin_improvement(self):
        """Large margin improvement (+2.01% -> +4.50%) is MATERIAL_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp1 = make_1x2_opp(home_odds=Decimal("2.10"), draw_odds=Decimal("3.60"), away_odds=Decimal("4.20"))
        opp2 = make_1x2_opp(home_odds=Decimal("2.35"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.50"))

        eval_res = policy.classify_change(previous=opp1, current=opp2)
        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.is_material is True
        assert eval_res.margin_delta > Decimal("0.0")
        assert any("improved" in r for r in eval_res.reasons)

    def test_material_margin_deterioration(self):
        """Large margin deterioration (+4.50% -> +1.50%) is MATERIAL_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp_high = make_1x2_opp(home_odds=Decimal("2.35"), draw_odds=Decimal("3.80"), away_odds=Decimal("4.50"))
        opp_low = make_1x2_opp(home_odds=Decimal("2.10"), draw_odds=Decimal("3.60"), away_odds=Decimal("4.20"))

        eval_res = policy.classify_change(previous=opp_high, current=opp_low)
        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.is_material is True
        assert eval_res.margin_delta < Decimal("0.0")
        assert any("deteriorated" in r for r in eval_res.reasons)

    def test_material_individual_odds_shift(self):
        """Individual leg odds relative change >= 2% triggers MATERIAL_CHANGE even if margin delta is small."""
        policy = DefaultOpportunityAlertPolicy(
            OpportunityAlertConfig(
                min_margin_delta=Decimal("0.5000"),  # high margin threshold
                min_odds_relative_delta=Decimal("0.0200"),  # 2% odds threshold
            )
        )
        opp1 = make_1x2_opp(home_odds=Decimal("2.10"))
        # 2.10 -> 2.20 is +4.76% rel change
        opp2 = make_1x2_opp(home_odds=Decimal("2.20"))

        eval_res = policy.classify_change(previous=opp1, current=opp2)
        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.is_material is True
        assert any("HOME" in r and "relative delta" in r for r in eval_res.reasons)

    def test_structural_change_provider_switch(self):
        """Provider changing on a leg is always a structural MATERIAL_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp_sb = make_1x2_opp(away_provider="betclic")
        opp_sts = make_1x2_opp(away_provider="sts")

        eval_res = policy.classify_change(previous=opp_sb, current=opp_sts)
        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.is_material is True
        assert eval_res.structural_change is True
        assert any("Bookmaker changed" in r for r in eval_res.reasons)

    def test_structural_change_market_line_change(self):
        """Market line change (Totals 2.5 -> Totals 3.5) is a structural MATERIAL_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp_2_5 = make_totals_opp(line=Decimal("2.5"))
        opp_3_5 = make_totals_opp(line=Decimal("3.5"))

        eval_res = policy.classify_change(previous=opp_2_5, current=opp_3_5)
        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.is_material is True
        assert eval_res.structural_change is True
        assert any("Market line changed" in r for r in eval_res.reasons)

    def test_structural_change_leg_added_or_removed(self):
        """Adding or removing a leg is a structural MATERIAL_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp_1x2 = make_1x2_opp()  # 3 legs
        opp_tot = make_totals_opp()  # 2 legs

        eval_res = policy.classify_change(previous=opp_1x2, current=opp_tot)
        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.structural_change is True
        assert any("Leg count changed" in r for r in eval_res.reasons)


# ---------------------------------------------------------------------------
# Section 2: Boundary Precision & Decimal Arithmetic
# ---------------------------------------------------------------------------

class TestAlertPolicyBoundaryPrecision:
    """Strict Decimal boundary testing."""

    def test_exact_margin_delta_threshold_boundary(self):
        """Exact threshold boundary behavior: strictly < is insignificant, >= is material."""
        threshold = Decimal("0.0050")  # 0.50 pp
        policy = DefaultOpportunityAlertPolicy(
            OpportunityAlertConfig(
                min_margin_delta=threshold,
                min_odds_relative_delta=Decimal("1.0000"),  # disable odds trigger
            )
        )

        base_opp = make_1x2_opp()
        base_margin = base_opp.arbitrage_margin

        # 1. Delta strictly below threshold (threshold - 1e-7)
        dict_below = _parse_snapshot_to_dict(base_opp)
        dict_below["arbitrage_margin"] = str(base_margin - (threshold - Decimal("0.0000001")))
        eval_below = policy.classify_change(previous=dict_below, current=base_opp)
        assert eval_below.classification == ChangeClassification.INSIGNIFICANT_CHANGE

        # 2. Delta strictly equal to threshold
        dict_exact = _parse_snapshot_to_dict(base_opp)
        dict_exact["arbitrage_margin"] = str(base_margin - threshold)
        eval_exact = policy.classify_change(previous=dict_exact, current=base_opp)
        assert eval_exact.classification == ChangeClassification.MATERIAL_CHANGE

        # 3. Delta strictly above threshold (threshold + 1e-7)
        dict_above = _parse_snapshot_to_dict(base_opp)
        dict_above["arbitrage_margin"] = str(base_margin - (threshold + Decimal("0.0000001")))
        eval_above = policy.classify_change(previous=dict_above, current=base_opp)
        assert eval_above.classification == ChangeClassification.MATERIAL_CHANGE

    def test_absolute_odds_delta_threshold(self):
        """Testing optional min_odds_absolute_delta configuration."""
        policy = DefaultOpportunityAlertPolicy(
            OpportunityAlertConfig(
                min_margin_delta=Decimal("1.0000"),  # disable margin
                min_odds_relative_delta=Decimal("1.0000"),  # disable relative odds
                min_odds_absolute_delta=Decimal("0.1000"),  # exact 0.10 absolute delta
            )
        )
        opp1 = make_1x2_opp(home_odds=Decimal("2.1000"))
        opp2_below = make_1x2_opp(home_odds=Decimal("2.1999"))
        opp2_exact = make_1x2_opp(home_odds=Decimal("2.2000"))

        assert policy.classify_change(opp1, opp2_below).classification == ChangeClassification.INSIGNIFICANT_CHANGE
        assert policy.classify_change(opp1, opp2_exact).classification == ChangeClassification.MATERIAL_CHANGE


# ---------------------------------------------------------------------------
# Section 3: Cooldown Window & Bypass Mechanics
# ---------------------------------------------------------------------------

class TestAlertPolicyCooldown:
    """Verifies configurable alert cooldown behavior."""

    def test_price_noise_suppressed_during_cooldown(self):
        """Material price change is suppressed if evaluated within cooldown window."""
        policy = DefaultOpportunityAlertPolicy(
            OpportunityAlertConfig(
                cooldown_seconds=60.0,
                bypass_cooldown_on_structural=True,
            )
        )
        opp1 = make_1x2_opp(home_odds=Decimal("2.10"))
        opp2 = make_1x2_opp(home_odds=Decimal("2.30"))  # Material price change

        last_alert = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)
        curr_time_inside = last_alert + timedelta(seconds=30)  # 30s < 60s cooldown

        eval_res = policy.classify_change(
            previous=opp1,
            current=opp2,
            last_alerted_at=last_alert,
            current_time=curr_time_inside,
        )

        assert eval_res.classification == ChangeClassification.INSIGNIFICANT_CHANGE
        assert eval_res.is_cooldown_suppressed is True
        assert any("active alert cooldown" in r for r in eval_res.reasons)

    def test_price_change_alerted_after_cooldown_expires(self):
        """Material price change is alerted after cooldown window has elapsed."""
        policy = DefaultOpportunityAlertPolicy(
            OpportunityAlertConfig(
                cooldown_seconds=60.0,
                bypass_cooldown_on_structural=True,
            )
        )
        opp1 = make_1x2_opp(home_odds=Decimal("2.10"))
        opp2 = make_1x2_opp(home_odds=Decimal("2.30"))  # Material price change

        last_alert = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)
        curr_time_after = last_alert + timedelta(seconds=65)  # 65s > 60s cooldown

        eval_res = policy.classify_change(
            previous=opp1,
            current=opp2,
            last_alerted_at=last_alert,
            current_time=curr_time_after,
        )

        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.is_cooldown_suppressed is False

    def test_structural_change_bypasses_cooldown(self):
        """Structural changes (e.g. bookmaker change) bypass cooldown when configured."""
        policy = DefaultOpportunityAlertPolicy(
            OpportunityAlertConfig(
                cooldown_seconds=60.0,
                bypass_cooldown_on_structural=True,
            )
        )
        opp_sb = make_1x2_opp(away_provider="betclic")
        opp_sts = make_1x2_opp(away_provider="sts")

        last_alert = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)
        curr_time_inside = last_alert + timedelta(seconds=10)

        eval_res = policy.classify_change(
            previous=opp_sb,
            current=opp_sts,
            last_alerted_at=last_alert,
            current_time=curr_time_inside,
        )

        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.structural_change is True
        assert eval_res.is_cooldown_suppressed is False
        assert any("Structural change bypasses alert cooldown" in r for r in eval_res.reasons)


# ---------------------------------------------------------------------------
# Section 4: Adversarial & Safe Fallback Handling
# ---------------------------------------------------------------------------

class TestAlertPolicyAdversarialCases:
    """Adversarial testing on corrupt, malformed, or boundary inputs."""

    def test_malformed_json_snapshot_fails_safely_as_material(self):
        """Corrupt or non-JSON snapshot string fails safely to MATERIAL_CHANGE."""
        policy = DefaultOpportunityAlertPolicy()
        opp = make_1x2_opp()

        eval_res = policy.classify_change(previous="not valid json {{{", current=opp)
        assert eval_res.classification == ChangeClassification.MATERIAL_CHANGE
        assert eval_res.is_material is True
        assert "unparseable_snapshot" in eval_res.details.get("error", "")

    def test_non_opportunity_current_raises_type_error(self):
        """Passing non-SurebetOpportunity for current raises TypeError."""
        policy = DefaultOpportunityAlertPolicy()
        opp = make_1x2_opp()

        with pytest.raises(TypeError):
            policy.classify_change(previous=opp, current={"not": "an_opp"})  # type: ignore

    def test_invalid_config_values_raise_value_error(self):
        """Negative or non-Decimal thresholds raise ValueError."""
        with pytest.raises(ValueError):
            OpportunityAlertConfig(min_margin_delta=Decimal("-0.01"))

        with pytest.raises(ValueError):
            OpportunityAlertConfig(min_odds_relative_delta=Decimal("-0.05"))

        with pytest.raises(ValueError):
            OpportunityAlertConfig(cooldown_seconds=-10.0)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse_snapshot_to_dict(opp: SurebetOpportunity) -> dict:
    from normalization.alert_policy import _parse_snapshot_dict
    d = _parse_snapshot_dict(opp)
    assert d is not None
    return dict(d)
