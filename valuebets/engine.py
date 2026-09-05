"""
Production Valuebet Detection Engine

Consumes canonical bookmaker markets and external reference odds,
normalizes benchmark fair probabilities via margin removal,
and calculates mathematical expected value:

    value = (bookmaker_odds * reference_fair_probability) - 1
    value_percent = value * 100
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP, getcontext
import logging
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple
import uuid

from domain.models import (
    Event,
    Market,
    Odds,
    Selection,
    generate_deterministic_canonical_event_id,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.identity import (
    TeamReference,
    compare_teams,
    normalize_team_name,
    parse_kickoff_to_utc,
)
from normalization.market_identity import (
    CanonicalMarketKey,
    CanonicalMarketType,
    extract_canonical_market_key,
    normalize_line,
)
from normalization.selection_identity import (
    CanonicalSelectionKey,
    CanonicalSelectionType,
    extract_canonical_selection_key,
)
from reference_odds.fair_calculator import FairProbabilityCalculator
from reference_odds.models import (
    FairProbabilityResult,
    ReferenceEvent,
    ReferenceMarket,
    ReferenceSelection,
    ReferenceSource,
)
from reference_odds.provider import ReferenceOddsProvider
from valuebets.config import ValuebetConfig
from valuebets.models import (
    ValueBetCandidate,
    ValueBetDetectionMetrics,
    ValueBetDetectionResult,
)

logger = logging.getLogger("zielonebety.valuebets")

# Set exact Decimal precision
getcontext().prec = 28
DECIMAL_ONE = Decimal("1")
DECIMAL_ZERO = Decimal("0")
DECIMAL_HUNDRED = Decimal("100")


AGE_GROUPS = frozenset({"u17", "u18", "u19", "u20", "u21", "u23"})
GENDER_SUFFIXES = frozenset({"women", "w", "fem", "feminino", "ladies"})
RESERVE_SUFFIXES = frozenset({"ii", "b", "reserves", "reserve", "2"})

# Executable bookmakers only (mirrors normalization.surebet.ALLOWED_EXECUTION_BOOKMAKERS).
# Reference bookmakers (e.g. bet365/unibet via odds_api) may price fair probability
# but must never become the executable ValueBetCandidate.bookmaker.
EXECUTABLE_BOOKMAKERS = frozenset({"superbet", "betclic"})


def _check_suffix_compatibility(tokens_a: Tuple[str, ...], tokens_b: Tuple[str, ...]) -> bool:
    suf_a = set(tokens_a) & (AGE_GROUPS | GENDER_SUFFIXES | RESERVE_SUFFIXES)
    suf_b = set(tokens_b) & (AGE_GROUPS | GENDER_SUFFIXES | RESERVE_SUFFIXES)
    if (suf_a & AGE_GROUPS) != (suf_b & AGE_GROUPS):
        return False
    if (suf_a & GENDER_SUFFIXES) != (suf_b & GENDER_SUFFIXES):
        return False
    if (suf_a & RESERVE_SUFFIXES) != (suf_b & RESERVE_SUFFIXES):
        return False
    return True


class ValuebetEngine:
    """Core analytical engine for valuebet candidate detection."""

    def __init__(
        self,
        config: Optional[ValuebetConfig] = None,
        fair_calculator: Optional[FairProbabilityCalculator] = None,
    ) -> None:
        self.config = config or ValuebetConfig()
        self.fair_calculator = fair_calculator or FairProbabilityCalculator(
            max_freshness_seconds=self.config.max_freshness_seconds
        )

    def detect_valuebets(
        self,
        bookmaker_graphs: Sequence[NormalizedGraph],
        reference_events: Sequence[ReferenceEvent],
    ) -> ValueBetDetectionResult:
        """Evaluates bookmaker graphs against reference events to identify valuebets."""
        start_time = time.perf_counter()
        metrics = ValueBetDetectionMetrics()
        candidates: List[ValueBetCandidate] = []
        qualified_valuebets: List[ValueBetCandidate] = []
        diagnostics: List[Dict[str, Any]] = []

        metrics.reference_events_ingested = len(reference_events)
        total_ref_mkts = sum(len(re.markets) for re in reference_events)
        metrics.reference_markets_ingested = total_ref_mkts

        # Index reference events by matched bookmaker graph
        for graph in bookmaker_graphs:
            bm_event = graph.event
            matched_ref_event = self._match_reference_event(bm_event, reference_events)

            if not matched_ref_event:
                metrics.events_unmatched += 1
                continue

            metrics.events_matched += 1

            # Prepare bookmaker lookups
            market_map = {m.internal_id: m for m in graph.markets}
            selections_by_market: Dict[str, List[Selection]] = {}
            for s in graph.selections:
                selections_by_market.setdefault(s.market_id, []).append(s)

            odds_by_selection: Dict[str, List[Odds]] = {}
            for o in graph.odds_list:
                odds_by_selection.setdefault(o.selection_id, []).append(o)

            # Evaluate each bookmaker market
            for mkt_id, mkt in market_map.items():
                mkt_key = extract_canonical_market_key(mkt)
                if not mkt_key:
                    # P1-004: market has no canonical identity (unknown taxonomy
                    # or missing mandatory line) -> cannot be matched. Count only.
                    metrics.markets_rejected_market_key += 1
                    continue

                # Find matching reference market
                ref_mkt = self._find_matching_reference_market(mkt_key, matched_ref_event)
                if not ref_mkt:
                    # P1-004: canonical market but no same-type+line benchmark.
                    # Count only.
                    metrics.markets_rejected_reference_missing += 1
                    continue

                metrics.markets_matched += 1

                # Calculate fair probabilities on reference market
                fair_res = self.fair_calculator.calculate_fair_probabilities(ref_mkt)
                if not fair_res.is_valid:
                    if fair_res.diagnostic == "INCOMPLETE_REFERENCE_MARKET":
                        metrics.markets_rejected_incomplete += 1
                    elif fair_res.diagnostic == "STALE_REFERENCE_DATA":
                        metrics.markets_rejected_stale += 1
                    elif fair_res.diagnostic == "INVALID_ODDS":
                        metrics.markets_rejected_invalid_odds += 1
                    else:
                        metrics.markets_rejected_unsupported += 1

                    diagnostics.append({
                        "event": f"{bm_event.home_participant} vs {bm_event.away_participant}",
                        "market_key": mkt_key.to_key_string(),
                        "diagnostic": fair_res.diagnostic,
                        "details": fair_res.details,
                    })
                    continue

                # Evaluate bookmaker selections against fair benchmark
                mkt_selections = selections_by_market.get(mkt_id, [])
                for sel in mkt_selections:
                    sel_key = extract_canonical_selection_key(sel, mkt_key, bm_event)
                    if not sel_key:
                        # P1-004: selection cannot be canonicalized under its
                        # parent market -> cannot be benchmarked. Count only.
                        metrics.selections_rejected_key_missing += 1
                        continue

                    sel_type = sel_key.selection_type
                    fair_prob = fair_res.fair_probabilities.get(sel_type)
                    fair_odds = fair_res.fair_odds.get(sel_type)

                    if fair_prob is None or fair_odds is None:
                        # P1-004: canonical selection but no fair benchmark for
                        # this outcome. Count only.
                        metrics.selections_rejected_reference_missing += 1
                        continue

                    sel_odds_list = odds_by_selection.get(sel.internal_id, [])
                    for o in sel_odds_list:
                        # P0-002: reference-only bookmakers must never become executable candidates.
                        # Fair probability/EV math above is untouched; only bookmaker identity is gated.
                        if (o.bookmaker or "").strip().lower() not in EXECUTABLE_BOOKMAKERS:
                            continue
                        metrics.selections_evaluated += 1

                        try:
                            bm_odds_dec = Decimal(str(o.decimal_odds))
                        except Exception:
                            # P1-004: executable quote with non-numeric price.
                            # Count only.
                            metrics.selections_rejected_invalid_odds += 1
                            continue

                        if bm_odds_dec <= DECIMAL_ONE:
                            # P1-004: executable quote with mathematically
                            # impossible price (<= 1). Count only.
                            metrics.selections_rejected_invalid_odds += 1
                            continue

                        # Authoritative Value Formula:
                        # value = (bm_odds * fair_prob) - 1
                        # value_percent = value * 100
                        value_edge = (bm_odds_dec * fair_prob) - DECIMAL_ONE
                        value_percent = value_edge * DECIMAL_HUNDRED

                        # Centralized Tax Engine Net Evaluation
                        from core.tax_engine import get_tax_engine
                        tax_calc = get_tax_engine().calculate_ev(
                            raw_odds=bm_odds_dec,
                            fair_probability=fair_prob,
                            bookmaker=o.bookmaker,
                        )

                        is_positive_val = value_edge > DECIMAL_ZERO
                        # P0-NEW-001: qualification is gated on tax-adjusted NET EV,
                        # not gross EV. Gross remains a diagnostic field on the
                        # candidate; tax is computed once via TaxEngine above.
                        is_qualified = tax_calc["net_ev_percent"] >= self.config.min_value_percent

                        if is_positive_val or self.config.enable_negative_value_candidates:
                            metrics.candidates_found += 1
                            if is_qualified:
                                metrics.qualified_valuebets += 1

                            bm_implied_prob = DECIMAL_ONE / bm_odds_dec

                            ref_sel = ref_mkt.selections.get(sel_type)
                            ref_raw_odds = ref_sel.odds if ref_sel else fair_odds

                            candidate = ValueBetCandidate(
                                candidate_id=f"vbc_{uuid.uuid4().hex[:12]}",
                                canonical_event_id=bm_event.internal_id,
                                event_name=f"{bm_event.home_participant} vs {bm_event.away_participant}",
                                sport=bm_event.metadata.get("sport", "football") if bm_event.metadata else "football",
                                competition_name=graph.competition.name if graph.competition else None,
                                kickoff=bm_event.scheduled_start,
                                market_key=mkt_key,
                                market_type=mkt_key.market_type,
                                line=mkt_key.line,
                                selection_key=sel_key,
                                selection_type=sel_type,
                                bookmaker=o.bookmaker,
                                bookmaker_odds=bm_odds_dec,
                                bookmaker_implied_prob=bm_implied_prob,
                                reference_source=matched_ref_event.source,
                                reference_bookmaker=ref_mkt.bookmaker_name,
                                reference_raw_odds=ref_raw_odds,
                                reference_overround=fair_res.raw_overround or DECIMAL_ONE,
                                reference_fair_probability=fair_prob,
                                reference_fair_odds=fair_odds,
                                value_edge=value_edge,
                                value_percent=value_percent,
                                effective_net_odds=tax_calc["effective_net_odds"],
                                net_value_edge=tax_calc["net_ev_edge"],
                                net_value_percent=tax_calc["net_ev_percent"],
                                is_tax_applied=tax_calc["is_tax_applied"],
                                is_qualified=is_qualified,
                                reference_timestamp=ref_mkt.timestamp,
                                calculation_metadata={
                                    "provider_selection_id": sel.internal_id,
                                    "provider_market_id": mkt.internal_id,
                                },
                            )
                            candidates.append(candidate)
                            if is_qualified:
                                qualified_valuebets.append(candidate)

        duration = time.perf_counter() - start_time
        return ValueBetDetectionResult(
            candidates=candidates,
            qualified_valuebets=qualified_valuebets,
            metrics=metrics,
            diagnostics=diagnostics,
            duration_seconds=round(duration, 4),
        )

    def _match_reference_event(
        self,
        bm_event: Event,
        reference_events: Sequence[ReferenceEvent],
    ) -> Optional[ReferenceEvent]:
        """Matches a bookmaker Event to a ReferenceEvent with strict adversarial checks."""
        best_ref: Optional[ReferenceEvent] = None
        best_score = 0.0

        ref_bm_h = TeamReference.from_raw(bm_event.home_participant)
        ref_bm_a = TeamReference.from_raw(bm_event.away_participant)

        for ref in reference_events:
            ref_ev_h = TeamReference.from_raw(ref.home_team)
            ref_ev_a = TeamReference.from_raw(ref.away_team)

            # Suffix compatibility checks
            if not _check_suffix_compatibility(ref_bm_h.tokens, ref_ev_h.tokens):
                continue
            if not _check_suffix_compatibility(ref_bm_a.tokens, ref_ev_a.tokens):
                continue

            # 1. Direct orientation
            home_cmp = compare_teams(ref_bm_h, ref_ev_h)
            away_cmp = compare_teams(ref_bm_a, ref_ev_a)
            score_h = 1.0 if home_cmp.exact_name else home_cmp.token_overlap
            score_a = 1.0 if away_cmp.exact_name else away_cmp.token_overlap
            direct_score = (score_h + score_a) / 2.0

            # 2. Inverted orientation (Adversarial check: Home mapped to Away)
            inv_home_cmp = compare_teams(ref_bm_h, ref_ev_a)
            inv_away_cmp = compare_teams(ref_bm_a, ref_ev_h)
            score_inv_h = 1.0 if inv_home_cmp.exact_name else inv_home_cmp.token_overlap
            score_inv_a = 1.0 if inv_away_cmp.exact_name else inv_away_cmp.token_overlap
            inv_score = (score_inv_h + score_inv_a) / 2.0

            if inv_score > direct_score and inv_score >= self.config.min_event_match_score:
                # Inverted match: reject to prevent cross-selection false positive value calculation
                continue

            if direct_score >= self.config.min_event_match_score and direct_score > best_score:
                best_score = direct_score
                best_ref = ref

        return best_ref

    def _find_matching_reference_market(
        self,
        bm_market_key: CanonicalMarketKey,
        ref_event: ReferenceEvent,
    ) -> Optional[ReferenceMarket]:
        """Matches a bookmaker CanonicalMarketKey to a ReferenceMarket."""
        for ref_mkt in ref_event.markets:
            if ref_mkt.market_type != bm_market_key.market_type:
                continue

            # Line integrity check: Decimal equality
            if bm_market_key.line is not None or ref_mkt.line is not None:
                norm_bm_line = normalize_line(bm_market_key.line)
                norm_ref_line = normalize_line(ref_mkt.line)
                if norm_bm_line != norm_ref_line:
                    continue

            return ref_mkt

        return None
