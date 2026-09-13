"""
Tests for Props Valuation Integrity & Anti-Fabrication Invariants.

Verifies strict mathematical & architectural invariants:
1. Hit rate only (e.g., 80% or 90%) NEVER produces a ValueBet.
2. Hit rate is a historical statistic, NEVER a probability model (model_probability is None).
3. Fair odds are NEVER fabricated without a valid probability model (fair_odds is None).
4. No ValueBet classification or status without valid probability provenance.
5. Canonical reference-based ValueBet evaluation (via PropsValueEvaluator) continues to work.
6. Sample statistics (hit_rate_pct, sample_size, stat_average) remain 100% accessible.
7. Downstream OpportunityExplorerAdapter strictly blocks ungrounded ValueBet promotion.
8. API serialization and prop detail preserve these integrity guarantees.
"""

import pytest
from scanner.prop_opportunity_engine import PropOpportunityEngine
from scanner.team_prop_opportunity_engine import TeamPropOpportunityEngine
from scanner.props_value_evaluator import PropsValueEvaluator
from core.opportunity_explorer import OpportunityExplorerAdapter, OpportunityType


class TestPropsValuationIntegrity:

    def test_a_hit_rate_only_no_valuebet(self):
        """
        Scenario A: Hit rate 80%, execution odds 1.80, no reference odds.
        In the legacy system, this erroneously generated a ValueBet (EV = 0.80 * 1.80 - 1 = +44%).
        In the integrity contract, hit_rate CANNOT qualify a ValueBet.
        """
        engine = PropOpportunityEngine()
        res = engine.evaluate(
            hit_rate_pct=80.0,
            sample_size=10,
            stat_average=2.5,
            line=1.5,
            side="OVER",
            best_odds=None,
            best_bookmaker=None,
            best_execution_odds=1.80,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            stat_type="SHOTS",
        )

        assert res.is_valuebet is False
        assert res.model_probability is None
        assert res.fair_odds is None
        assert res.value_edge_pp is None
        assert res.status == "BETTABLE"
        assert res.actionability == "BETTABLE"
        assert "VALUEBET DETECTED" not in " ".join(res.reasons)
        assert "VALUEBET_POSITIVE_EV" not in res.data_quality_flags

    def test_b_90_percent_hit_rate_no_model_probability(self):
        """
        Scenario B: 90% hit rate with large sample size (20 games).
        Even with exceptional historical sample statistics, model_probability MUST remain None.
        """
        engine = PropOpportunityEngine()
        res = engine.evaluate(
            hit_rate_pct=90.0,
            sample_size=20,
            stat_average=3.1,
            line=1.5,
            side="OVER",
            best_odds=1.60,
            best_bookmaker="Bet365",
            best_execution_odds=1.75,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            stat_type="SHOTS",
        )

        assert res.historical_probability == 0.90
        assert res.model_probability is None
        assert res.fair_odds is None
        assert res.is_valuebet is False
        assert res.status == "BETTABLE"
        assert res.actionability == "BETTABLE"

    def test_c_no_fair_odds_without_valid_probability(self):
        """
        Scenario C: Verifies that fair_odds is strictly None across both player
        and team prop engines when only historical hit rates are present.
        """
        player_engine = PropOpportunityEngine()
        p_res = player_engine.evaluate(
            hit_rate_pct=75.0,
            sample_size=12,
            stat_average=1.8,
            line=0.5,
            side="OVER",
            best_execution_odds=2.00,
            best_execution_bookmaker="Betclic",
            execution_status="BETTABLE",
            stat_type="GOALS",
        )
        assert p_res.fair_odds is None
        assert p_res.model_probability is None

        team_engine = TeamPropOpportunityEngine()
        t_res = team_engine.evaluate(
            hit_rate_pct=85.0,
            sample_size=15,
            stat_average=6.5,
            line=4.5,
            side="OVER",
            best_execution_odds=1.55,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            hit_rate_count=13,
            stat_type="CORNERS",
            participant_role="HOME",
        )
        assert t_res.fair_odds is None
        assert t_res.model_probability is None

    def test_d_no_ev_classification_without_provenance(self):
        """
        Scenario D: Verifies that to_dict() serialization never outputs
        status="VALUEBET" or synthetic model_probability_pct.
        """
        engine = PropOpportunityEngine()
        eval_res = engine.evaluate(
            hit_rate_pct=85.0,
            sample_size=10,
            stat_average=2.8,
            line=1.5,
            side="OVER",
            best_execution_odds=1.90,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            stat_type="SHOTS",
        )
        d = eval_res.to_dict()

        assert d["is_valuebet"] is False
        assert d["status"] == "BETTABLE"
        assert d["actionability"] == "BETTABLE"
        assert d["fair_odds"] is None
        assert d["model_probability"] is None
        assert d["model_probability_pct"] is None
        assert d["value_edge_pp"] is None

    def test_e_reference_path_canonical_valuebet_works(self):
        """
        Scenario E: Verifies that canonical PropsValueEvaluator (reference odds-based)
        still properly evaluates and identifies genuine ValueBets with de-margined fair odds.
        """
        evaluator = PropsValueEvaluator()
        ref_odds = [
            {"bookmaker": "Bet365", "line": 1.5, "side": "OVER", "odds": 1.9231},  # Implied = 0.52, Fair = 0.50
        ]
        exec_odds = {
            "Superbet": {
                "status": "AVAILABLE",
                "decimal_odds": 2.50,
                "selection_id": "sb-sel-1",
                "market_id": "sb-mkt-1",
            }
        }
        val_res = evaluator.evaluate_matched_prop(
            canonical_key="player_prop:bukayo_saka:shots:over:1.5",
            stat_type="SHOTS",
            line=1.5,
            side="OVER",
            reference_odds_list=ref_odds,
            execution_odds=exec_odds,
        )

        assert val_res.reference_calculation is not None
        assert val_res.reference_calculation.fair_odds is not None
        assert val_res.is_valuebet is True
        assert val_res.best_net_ev_pct is not None and val_res.best_net_ev_pct > 0.0

    def test_f_statistics_remain_accessible(self):
        """
        Scenario F: Confirms that preserving integrity does not discard historical sample statistics.
        hit_rate_pct, sample_size, stat_average, and raw_edge must remain fully accessible.
        """
        engine = PropOpportunityEngine()
        eval_res = engine.evaluate(
            hit_rate_pct=70.0,
            sample_size=10,
            stat_average=2.2,
            line=1.5,
            side="OVER",
            best_odds=1.80,
            best_bookmaker="Pinnacle",
            best_execution_odds=1.95,
            best_execution_bookmaker="Superbet",
            execution_status="BETTABLE",
            stat_type="SHOTS",
        )

        assert eval_res.historical_probability == 0.70
        assert eval_res.raw_edge is not None
        assert eval_res.raw_edge_pct is not None
        assert eval_res.execution_edge is not None
        assert eval_res.execution_edge_pct is not None
        assert eval_res.execution_ev is not None
        assert eval_res.execution_ev_pct is not None

        d = eval_res.to_dict()
        assert d["historical_probability"] == 0.70
        assert d["raw_edge_pct"] is not None
        assert d["execution_edge_pct"] is not None
        assert d["execution_ev_pct"] is not None

    def test_g_downstream_explorer_adapter_blocks_ungrounded_valuebet(self):
        """
        Scenario G: OpportunityExplorerAdapter MUST NOT promote a prop to VALUEBET
        when is_valuebet is False, even if gross_ev_pct or execution_ev_pct is high.
        """
        prop_with_hit_rate_ev = {
            "prop_id": "test_prop_1",
            "player_name": "Erling Haaland",
            "team": "Man City",
            "opponent": "Arsenal",
            "stat_type": "SHOTS",
            "line": 2.5,
            "side": "OVER",
            "execution_status": "BETTABLE",
            "actionability": "BETTABLE",
            "execution_ev_pct": 35.0,  # High statistical edge from 80% hit rate
            "best_execution_odds": 1.70,
            "best_execution_bookmaker": "Superbet",
            "is_valuebet": False,       # Explicitly not a canonical ValueBet
            "fair_odds": None,
            "model_probability": None,
            "model_probability_pct": None,
            "score": 78.0,
        }

        dto = OpportunityExplorerAdapter.from_player_prop(prop_with_hit_rate_ev)

        assert dto.is_valuebet is False
        assert dto.status != "VALUEBET"
        assert dto.status == "BETTABLE"
        assert dto.fair_odds is None
        assert dto.model_probability_pct is None

        # Same contract for Team Prop
        team_prop_with_hit_rate_ev = {
            "prop_id": "test_team_prop_1",
            "team": "Arsenal",
            "opponent": "Chelsea",
            "stat_type": "CORNERS",
            "line": 4.5,
            "side": "OVER",
            "execution_status": "BETTABLE",
            "actionability": "BETTABLE",
            "execution_ev_pct": 25.0,
            "best_execution_odds": 1.60,
            "best_execution_bookmaker": "Betclic",
            "is_valuebet": False,
            "fair_odds": None,
            "model_probability": None,
            "score": 82.0,
        }

        dto_team = OpportunityExplorerAdapter.from_team_prop(team_prop_with_hit_rate_ev)

        assert dto_team.is_valuebet is False
        assert dto_team.status != "VALUEBET"
        assert dto_team.status == "BETTABLE"
        assert dto_team.fair_odds is None

    def test_h_props_scan_serialization_contract(self):
        """
        Scenario H: Verifies the serialization contract expected by the props scan pipeline:
        - When canonical reference odds are missing: is_valuebet is False, fair_odds is None,
          model_probability is None, status is BETTABLE/REFERENCE_ONLY (never VALUEBET).
        """
        from api.services import PlatformAPIService

        service = PlatformAPIService()
        # Test mock prop serialization helper directly
        raw_prop = {
            "prop_id": "p_mock_1",
            "player_name": "Test Player",
            "team": "Team A",
            "opponent": "Team B",
            "stat_type": "SHOTS",
            "line": 1.5,
            "side": "OVER",
            "best_execution_odds": 1.85,
            "best_execution_bookmaker": "Superbet",
            "execution_status": "BETTABLE",
            "actionability": "BETTABLE",
            "is_valuebet": False,
            "fair_odds": None,
            "model_probability": None,
            "hit_rate_pct": 80.0,
            "sample_size": 10,
            "stat_average": 2.1,
            "score": 75.0,
        }

        serialized = service._serialize_player_prop_opportunity_detail(raw_prop, "p_mock_1")

        assert serialized["type"] == "PLAYER_PROP"
        assert serialized["status"] != "VALUEBET"
        assert serialized["status"] == "BETTABLE"
        assert serialized["mathematical_explanation"]["is_valuebet"] is False
        assert serialized["mathematical_explanation"]["fair_odds"] is None
        assert serialized["mathematical_explanation"]["fair_probability"] is None
        assert serialized["fair_odds"] is None
        assert serialized["fair_probability"] is None
        assert serialized["value_percent"] is None
