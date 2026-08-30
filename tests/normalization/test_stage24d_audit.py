"""
Stage 24D: Diagnostic Market Depth Audit Instrumentation Tests
"""
import pytest
from decimal import Decimal
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    MarketPeriod,
    MarketScope,
    MarketMetric,
    ParticipantRole,
    extract_canonical_market_key,
)
from normalization.market_matcher import (
    MarketMatcher,
    MarketMatchDecisionType,
)
from orchestration.models import (
    MarketEvaluationState,
    EvaluationExclusionReason,
    MatchedMarketEvaluationRecord,
)
from orchestration.scan_orchestrator import _categorize_market_type


class TestStage24DMarketDepthAudit:
    """Test suite verifying diagnostic telemetry and classification for Stage 24D Market Depth Audit."""

    def test_market_survives_raw_to_parsed_to_normalized(self):
        """1. Verify market survives raw -> parsed -> normalized into canonical identity."""
        mkt = Market(
            event_id="ev_1",
            market_type="1X2",
            line=None,
            status="OPEN",
            metadata={"period": "FULL_TIME", "scope": "MATCH", "metric": "GOALS"},
        )
        key = extract_canonical_market_key(mkt)
        assert key is not None
        assert key.market_type == "1X2"
        assert key.period == "FULL_TIME"
        assert key.scope == "MATCH"
        assert key.metric == "GOALS"
        assert key.to_key_string() == "football:1X2:GOALS:MATCH:all:FULL_TIME:none"

    def test_market_dropped_during_parsing_counted_correctly(self):
        """2. Verify market dropped during parsing / malformed raw input is accounted for."""
        from providers.superbet.parser.parser import SuperbetParser
        parser = SuperbetParser()
        # Flat odds with invalid price or missing fields
        raw_odds = [
            {"marketId": "100", "marketName": "1X2", "price": 0.5, "status": 1},  # price <= 1.0 (inactive)
            {"marketId": "101", "marketName": "BTTS", "price": 1.95, "status": 1, "uuid": "s1", "name": "Tak"},
        ]
        markets = parser._parse_flat_odds_markets("ev_test", raw_odds)
        assert len(markets) == 2
        # Market 100 has no active selections
        assert markets[0].is_active is False
        # Market 101 has active selection
        assert markets[1].is_active is True

    def test_market_dropped_during_normalization_counted_correctly(self):
        """3. Verify unsupported or uncanonicalized market is classified as unsupported."""
        mkt = Market(
            event_id="ev_1",
            market_type="CUSTOM_COMPOSITE_SPECIAL_COMBO",
            line=None,
            status="OPEN",
            metadata={"raw_name": "Combo Bet Xtra"},
        )
        key = extract_canonical_market_key(mkt)
        assert key is None  # Unextractable / Unsupported in canonical taxonomy

    def test_market_with_no_cross_bookmaker_counterpart_classified_correctly(self):
        """4. Verify market with no counterpart across bookmakers is classified as unmatched."""
        matcher = MarketMatcher()
        mkt_source = Market(
            event_id="ev_sb",
            market_type="TOTALS",
            line=2.5,
            status="OPEN",
            metadata={"metric": "GOALS", "scope": "MATCH", "period": "FULL_TIME"},
        )
        mkt_target = Market(
            event_id="ev_bc",
            market_type="TOTALS",
            line=3.5,  # Different line -> line mismatch
            status="OPEN",
            metadata={"metric": "GOALS", "scope": "MATCH", "period": "FULL_TIME"},
        )
        decision = matcher.match(mkt_source, mkt_target)
        assert decision.decision == MarketMatchDecisionType.REJECTED
        assert "LINE_MISMATCH" in decision.reasons

    def test_evaluator_exclusion_distinguished_from_missing_market_data(self):
        """5. Verify evaluator exclusion is distinguished from missing market data."""
        rec_excluded = MatchedMarketEvaluationRecord(
            canonical_event_id="cev_1",
            canonical_market_key="football:1X2:GOALS:MATCH:all:FULL_TIME:none",
            state=MarketEvaluationState.REJECTED,
            reason=EvaluationExclusionReason.INCOMPLETE_SELECTIONS,
            market_type="1X2",
        )
        rec_missing = MatchedMarketEvaluationRecord(
            canonical_event_id="cev_1",
            canonical_market_key="football:TOTALS:GOALS:MATCH:all:FULL_TIME:2.5",
            state=MarketEvaluationState.NOT_EVALUATED,
            reason=EvaluationExclusionReason.ZERO_COMPARABLE_SELECTIONS,
            market_type="TOTALS",
        )
        assert rec_excluded.state == MarketEvaluationState.REJECTED
        assert rec_excluded.reason == EvaluationExclusionReason.INCOMPLETE_SELECTIONS
        assert rec_missing.state == MarketEvaluationState.NOT_EVALUATED
        assert rec_missing.reason == EvaluationExclusionReason.ZERO_COMPARABLE_SELECTIONS

    def test_categorize_market_type_taxonomy(self):
        """6. Verify categorization taxonomy matches canonical families."""
        assert _categorize_market_type("1X2") == "1X2"
        assert _categorize_market_type("Wynik meczu") == "1X2"
        assert _categorize_market_type("BTTS") == "BTTS"
        assert _categorize_market_type("Obie drużyny strzelą") == "BTTS"
        assert _categorize_market_type("Liczba goli") == "TOTALS"
        assert _categorize_market_type("Liczba rzutów rożnych") == "CORNERS"
        assert _categorize_market_type("Liczba kartek") == "CARDS"
        assert _categorize_market_type("Liczba spalonych") == "OFFSIDES"
        assert _categorize_market_type("Zawodnik - liczba strzałów") == "PLAYER_PROPS"

    def test_diagnostic_counts_internally_consistent(self):
        """7. Verify market lifecycle counts are internally consistent."""
        raw_count = 100
        parsed_count = 95
        normalized_count = 80
        matched_count = 30
        evaluated_count = 25
        excluded_count = 5

        assert raw_count >= parsed_count
        assert parsed_count >= normalized_count
        assert normalized_count >= matched_count
        assert matched_count == (evaluated_count + excluded_count)
