"""
Stage 24 Test Suite: Stake Calculator, Selections & Cross-Bookmaker Odds Comparison, and Market Coverage Diagnostics
"""

from decimal import Decimal
import pytest

from core.tax_engine import TaxEngine, BookmakerTaxConfig, get_tax_engine
from api.services import (
    PlatformAPIService,
    serialize_opportunity_detail,
    _format_selection_outcome,
    _format_market_human_readable,
)
from normalization.market_identity import CanonicalMarketKey, CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionKey, CanonicalSelectionType
from normalization.surebet import SurebetOpportunity, SurebetLeg, SurebetStatus
from domain.models import MatchEvidence, Odds


def test_format_market_human_readable_team_offsides():
    """Verify human readable market formatting for team offsides."""
    mkt = {
        "type": "TOTALS",
        "metric": "OFFSIDES",
        "scope": "TEAM",
        "participant_role": "away",
        "period": "FULL_TIME",
        "line": 1.5,
        "key_string": "football:TOTALS:OFFSIDES:TEAM:away:FULL_TIME:1.5",
    }
    event = {
        "home_team": "Maccabi Tel Aviv",
        "away_team": "Hapoel Beer Sheva",
    }
    formatted = _format_market_human_readable(mkt, event)
    assert formatted["market_name"] == "Total Offsides — Hapoel Beer Sheva"
    assert formatted["line_display"] == "1.5"
    assert formatted["period_display"] == "Full Time"
    assert formatted["scope_display"] == "Team (Hapoel Beer Sheva)"


def test_format_selection_outcome():
    """Verify selection outcome formatting for home/away/draw/over/under."""
    event = {"home_team": "Arsenal", "away_team": "Chelsea"}
    assert _format_selection_outcome("HOME", event) == "Home (Arsenal)"
    assert _format_selection_outcome("AWAY", event) == "Away (Chelsea)"
    assert _format_selection_outcome("DRAW", event) == "Draw (X)"
    assert _format_selection_outcome("OVER", event) == "Over"
    assert _format_selection_outcome("UNDER", event) == "Under"
    assert _format_selection_outcome("1X", event) == "1X (Arsenal or Draw)"


def test_surebet_stake_calculator_2way_exact_reconciliation():
    """Test 2-way surebet stake calculation, effective odds, and cent reconciliation summing to total stake."""
    engine = TaxEngine()
    # Leg 1: Superbet (12% tax -> factor 0.88), raw odds 2.40 -> eff 2.112
    # Leg 2: Betclic (0% tax -> factor 1.00), raw odds 2.10 -> eff 2.10
    # Net S = 1/2.112 + 1/2.10 = 0.47348 + 0.47619 = 0.94967 < 1.0 (Surebet!)
    legs = [
        {"selection_type": "OVER", "provider": "superbet", "odds": 2.40, "line": 2.5},
        {"selection_type": "UNDER", "provider": "betclic", "odds": 2.10, "line": 2.5},
    ]

    for test_stake in (50, 100, 200, 500, 1000, 1234.56):
        res = engine.calculate_stake_distribution(total_stake=test_stake, legs=legs)
        assert res["is_surebet"] is True
        assert res["net_implied_probability_sum"] < Decimal("1.0")
        assert res["roi_percentage"] > Decimal("0")
        assert res["guaranteed_profit"] > Decimal("0")

        # Exact cent reconciliation check: sum of allocated stakes must equal total stake exactly
        allocated_sum = sum(l["allocated_stake"] for l in res["legs"])
        assert allocated_sum == Decimal(str(test_stake)).quantize(Decimal("0.01"))

        # Effective odds check
        l1 = res["legs"][0]
        l2 = res["legs"][1]
        assert l1["effective_odds"] == Decimal("2.40") * Decimal("0.88")
        assert l2["effective_odds"] == Decimal("2.10") * Decimal("1.00")


def test_surebet_stake_calculator_3way():
    """Test 3-way surebet stake calculation (1X2 match result)."""
    engine = TaxEngine()
    # 3-way 1X2:
    # 1: Betclic @ 3.40 (net 3.40)
    # X: Betclic @ 3.40 (net 3.40)
    # 2: Betclic @ 3.40 (net 3.40)
    # Net S = 3 * (1/3.40) = 0.8823 < 1.0 (Surebet)
    legs = [
        {"selection_type": "HOME", "provider": "betclic", "odds": 3.40},
        {"selection_type": "DRAW", "provider": "betclic", "odds": 3.40},
        {"selection_type": "AWAY", "provider": "betclic", "odds": 3.40},
    ]

    res = engine.calculate_stake_distribution(total_stake=1000, legs=legs)
    assert res["is_surebet"] is True
    assert len(res["legs"]) == 3
    assert sum(l["allocated_stake"] for l in res["legs"]) == Decimal("1000.00")
    assert res["guaranteed_profit"] > Decimal("100.00")


def test_non_surebet_safety():
    """Verify that when S >= 1.0, is_surebet is False and no fake positive profit is calculated."""
    engine = TaxEngine()
    # Superbet (12% tax -> net 0.88) raw 1.90 -> eff 1.672
    # Betclic (0% tax) raw 1.90 -> eff 1.90
    # Net S = 1/1.672 + 1/1.90 = 0.598 + 0.526 = 1.124 > 1.0 (Not a surebet!)
    legs = [
        {"selection_type": "OVER", "provider": "superbet", "odds": 1.90},
        {"selection_type": "UNDER", "provider": "betclic", "odds": 1.90},
    ]
    res = engine.calculate_stake_distribution(total_stake=1000, legs=legs)
    assert res["is_surebet"] is False
    assert res["guaranteed_profit"] == Decimal("0")
    assert res["guaranteed_payout"] == Decimal("0")


def test_preset_table_generation():
    """Verify preset table generates rows for 50, 100, 200, 500, 1000 PLN."""
    engine = TaxEngine()
    legs = [
        {"selection_type": "HOME", "provider": "betclic", "odds": 2.10},
        {"selection_type": "AWAY", "provider": "betclic", "odds": 2.10},
    ]
    presets = engine.generate_preset_table(legs=legs, presets=(50, 100, 200, 500, 1000))
    assert len(presets) == 5
    expected_totals = [Decimal("50"), Decimal("100"), Decimal("200"), Decimal("500"), Decimal("1000")]
    for row, expected_tot in zip(presets, expected_totals):
        assert row["total_stake"] == expected_tot
        assert row["is_surebet"] is True
        assert sum(l["allocated_stake"] for l in row["legs"]) == expected_tot
        assert row["guaranteed_profit"] > Decimal("0")


def test_user_settings_betclic_configurable_tax():
    """Verify Betclic tax can be configured in settings."""
    service = PlatformAPIService()
    # Update Betclic tax to 12%
    service.update_user_settings({
        "bookmaker_tax_rates": {
            "superbet": 0.12,
            "betclic": 0.12,
        }
    })
    settings = service.get_user_settings()
    assert settings["bookmaker_tax_rates"]["betclic"] == 0.12

    # Reset back to default 0%
    service.update_user_settings({
        "bookmaker_tax_rates": {
            "superbet": 0.12,
            "betclic": 0.0,
        }
    })
    settings_restored = service.get_user_settings()
    assert settings_restored["bookmaker_tax_rates"]["betclic"] == 0.0


def test_serialize_opportunity_detail_enrichment():
    """Verify serialize_opportunity_detail embeds all rich Selections and Stake Calculator fields."""
    mkt_key = CanonicalMarketKey(
        sport="football",
        market_type=CanonicalMarketType.TOTALS,
        metric="OFFSIDES",
        scope="TEAM",
        participant_role="away",
        period="FULL_TIME",
        line=Decimal("1.5"),
    )
    sel_key_o = CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.OVER, selection_line=Decimal("1.5"))
    sel_key_u = CanonicalSelectionKey(market_key=mkt_key, selection_type=CanonicalSelectionType.UNDER, selection_line=Decimal("1.5"))

    ev = MatchEvidence(
        source_provider="superbet",
        target_provider="betclic",
        source_event_id="sb_123",
        target_event_id="bc_456",
        decision="MATCH",
        total_score=0.95,
        orientation="DIRECT",
        home_team="Fenerbahce",
        away_team="Lyon",
        competition_name="UEFA Europa League",
    )

    leg_o = SurebetLeg(
        canonical_selection_key=sel_key_o,
        selection_type="OVER",
        provider="superbet",
        odds=Decimal("2.40"),
        source_selection_id="sb_sel_123",
    )
    leg_u = SurebetLeg(
        canonical_selection_key=sel_key_u,
        selection_type="UNDER",
        provider="betclic",
        odds=Decimal("2.10"),
        source_selection_id="bc_sel_456",
    )

    opp = SurebetOpportunity(
        opportunity_id="opp_stage24_test",
        canonical_event_id="evt_test_1",
        canonical_market_key=mkt_key,
        legs=(leg_o, leg_u),
        implied_probability_sum=Decimal("0.8928"),
        arbitrage_margin=Decimal("0.1200"),
        status=SurebetStatus.SUREBET,
        is_mixed_bookmakers=True,
        bookmakers=("superbet", "betclic"),
        event_evidence=ev,
    )

    detail = serialize_opportunity_detail(opp)

    assert detail["opportunity_id"] == "opp_stage24_test"
    assert "stake_calculator" in detail
    assert detail["stake_calculator"]["is_surebet"] is True
    assert len(detail["stake_calculator"]["preset_table"]) == 5

    # Check Selections enrichment
    assert len(detail["selections"]) == 2
    l1 = detail["selections"][0]
    assert l1["selection_outcome"] == "Over"
    assert l1["market_name"] == "Total Offsides — Lyon"
    assert l1["line_display"] == "1.5"
    assert l1["period_display"] == "Full Time"
    assert l1["scope_display"] == "Team (Lyon)"
    assert l1["tax_factor"] == 0.88
    assert l1["raw_odds"] == 2.40
    assert l1["effective_net_odds"] == pytest.approx(2.112, rel=1e-3)
    assert l1["source_selection_id"] == "sb_sel_123"

    l2 = detail["selections"][1]
    assert l2["selection_outcome"] == "Under"
    assert l2["market_name"] == "Total Offsides — Lyon"
    assert l2["tax_factor"] == 1.00
    assert l2["raw_odds"] == 2.10
    assert l2["effective_net_odds"] == 2.10
    assert l2["source_selection_id"] == "bc_sel_456"
