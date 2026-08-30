"""
Stage 4.4: Cross-Bookmaker Event Scoring & Match Decision Engine

Implements the provider-independent event matching decision layer.
Evaluates multi-signal evidence across candidate event pairs:
- External Event IDs (fast-path & contradiction detection)
- Home & Away Team Token Similarity & Jaccard Overlap
- Orientation Evaluation (Normal vs Swapped)
- Timezone-safe Kickoff Proximity
- Competition Compatibility
- Sport Compatibility (Hard Veto)
- Identity Suffix Safety (Age Groups, Gender, Reserves Hard Vetoes)
- Configurable Thresholds & Heuristic Calibration Honesty
- Best-Match Ambiguity Resolution (one-to-one ambiguity margins)
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Tuple, Any, Set, Union
from datetime import datetime, timezone

from domain.models import Event, Competition
from normalization.identity import (
    TeamReference,
    CompetitionReference,
    compare_teams,
    parse_kickoff_to_utc,
    normalize_competition_name,
)
from normalization.candidate_generator import EventCandidate


# Safety Sets for Suffix Classification
AGE_GROUPS: Set[str] = {"u17", "u18", "u19", "u20", "u21", "u23"}
GENDER_SUFFIXES: Set[str] = {"women", "w", "fem", "feminino", "ladies", "k", "kobiet"}
RESERVE_SUFFIXES: Set[str] = {"ii", "b", "reserves", "reserve", "2"}


class MatchDecisionType(str, Enum):
    MATCHED = "MATCHED"
    AMBIGUOUS = "AMBIGUOUS"
    REJECTED = "REJECTED"


class OrientationType(str, Enum):
    NORMAL = "NORMAL"
    ORIENTATION_SWAP = "ORIENTATION_SWAP"
    UNKNOWN = "UNKNOWN"


class SuffixCompatibility(str, Enum):
    EXACT_COMPATIBLE = "EXACT_COMPATIBLE"
    MISMATCH = "MISMATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class MatcherConfig:
    """Configurable scoring weights, decision thresholds, and ambiguity margins.

    Note on Calibration:
    These are initial heuristic thresholds and weights designed for safety,
    pending calibration on a large verified ground-truth dataset.
    """
    matched_threshold: float = 0.80
    ambiguous_threshold: float = 0.55
    ambiguity_margin: float = 0.08
    allow_orientation_swap: bool = True
    orientation_swap_penalty: float = 0.10
    max_kickoff_diff_hours: float = 24.0
    weights: Dict[str, float] = field(default_factory=lambda: {
        "external_id": 0.25,
        "home_team": 0.25,
        "away_team": 0.25,
        "kickoff": 0.15,
        "competition": 0.05,
        "sport": 0.05,
    })


@dataclass(frozen=True)
class SignalScore:
    """Individual decomposed signal score and its weighted contribution."""
    name: str
    raw_score: float
    weight: float
    weighted_score: float
    details: Dict[str, Any] = field(default_factory=dict)


class MatchRejectionCode(str, Enum):
    KICKOFF_MISMATCH = "KICKOFF_MISMATCH"
    TEAM_IDENTITY_MISMATCH = "TEAM_IDENTITY_MISMATCH"
    COMPETITION_MISMATCH = "COMPETITION_MISMATCH"
    SPORT_MISMATCH = "SPORT_MISMATCH"
    SUFFIX_VETO = "SUFFIX_VETO"
    ORIENTATION_MISMATCH = "ORIENTATION_MISMATCH"
    LOW_MATCH_SCORE = "LOW_MATCH_SCORE"
    AMBIGUOUS_CANDIDATE = "AMBIGUOUS_CANDIDATE"


@dataclass
class MatchDecision:
    """Auditable multi-signal match decision for a candidate event pair."""
    source_event_id: str
    target_event_id: str
    decision: MatchDecisionType
    total_score: float
    orientation: OrientationType
    signals: Dict[str, SignalScore]
    veto_reasons: Tuple[str, ...] = field(default_factory=tuple)
    rejection_reason_code: Optional[str] = None
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MatchResult:
    """Aggregated result of cross-bookmaker candidate matching."""
    decisions: List[MatchDecision]
    matched_count: int
    ambiguous_count: int
    rejected_count: int
    total_scored: int
    rejection_reasons_breakdown: Dict[str, int] = field(default_factory=dict)


class EventMatcher:
    """Provider-independent multi-signal event matcher and decision engine."""

    def __init__(self, config: Optional[MatcherConfig] = None):
        self.config = config or MatcherConfig()

    def _extract_identity_suffixes(self, tokens: Tuple[str, ...]) -> Set[str]:
        """Identifies any age group, gender, or reserve suffix tokens."""
        all_suffixes = AGE_GROUPS | GENDER_SUFFIXES | RESERVE_SUFFIXES
        return {t for t in tokens if t in all_suffixes}

    def _check_suffix_compatibility(
        self,
        tokens_a: Tuple[str, ...],
        tokens_b: Tuple[str, ...]
    ) -> Tuple[SuffixCompatibility, Optional[str]]:
        """Checks whether team tokens have conflicting youth/women/reserve suffixes."""
        suf_a = self._extract_identity_suffixes(tokens_a)
        suf_b = self._extract_identity_suffixes(tokens_b)

        if suf_a == suf_b:
            return SuffixCompatibility.EXACT_COMPATIBLE, None

        # Check Age Group Mismatch
        age_a, age_b = suf_a & AGE_GROUPS, suf_b & AGE_GROUPS
        if age_a != age_b:
            return SuffixCompatibility.MISMATCH, f"age_group_mismatch: {age_a or 'senior'} vs {age_b or 'senior'}"

        # Check Gender Mismatch (both sides having any women designation is compatible)
        has_gen_a = bool(suf_a & GENDER_SUFFIXES)
        has_gen_b = bool(suf_b & GENDER_SUFFIXES)
        if has_gen_a != has_gen_b:
            gen_label_a = 'women' if has_gen_a else 'men'
            gen_label_b = 'women' if has_gen_b else 'men'
            return SuffixCompatibility.MISMATCH, f"gender_mismatch: {gen_label_a} vs {gen_label_b}"

        # Check Reserve Mismatch
        res_a, res_b = suf_a & RESERVE_SUFFIXES, suf_b & RESERVE_SUFFIXES
        if res_a != res_b:
            return SuffixCompatibility.MISMATCH, f"reserve_mismatch: {res_a or 'first_team'} vs {res_b or 'first_team'}"

        return SuffixCompatibility.EXACT_COMPATIBLE, None

    def _calculate_kickoff_score(
        self,
        dt_a: Optional[datetime],
        dt_b: Optional[datetime]
    ) -> Tuple[float, Optional[float], Dict[str, Any]]:
        """Calculates kickoff proximity score and difference in hours."""
        if dt_a is None or dt_b is None:
            return 0.5, None, {"status": "unknown_timestamp"}

        diff_seconds = abs((dt_a - dt_b).total_seconds())
        diff_hours = diff_seconds / 3600.0

        if diff_seconds <= 900:  # <= 15 min
            score = 1.0
        elif diff_seconds <= 3600:  # <= 1 hour
            score = 0.95
        elif diff_seconds <= 10800:  # <= 3 hours (midnight drift window)
            score = 0.85
        elif diff_hours <= self.config.max_kickoff_diff_hours:  # <= 24 hours
            score = max(0.0, 1.0 - (diff_hours / self.config.max_kickoff_diff_hours) * 0.6)
        else:
            score = 0.0

        return round(score, 4), round(diff_hours, 2), {"diff_hours": round(diff_hours, 2)}

    def score_candidate(
        self,
        candidate: EventCandidate,
        source_event: Event,
        target_event: Event,
        source_comp: Optional[Competition] = None,
        target_comp: Optional[Competition] = None,
    ) -> MatchDecision:
        """Evaluates all matching signals for a candidate pair and produces a MatchDecision."""
        veto_reasons: List[str] = []
        warnings: List[str] = []
        signals: Dict[str, SignalScore] = {}

        # 1. Sport Compatibility (Hard Veto if explicitly different)
        s_sport = (source_comp.sport if source_comp and source_comp.sport else "football").lower()
        t_sport = (target_comp.sport if target_comp and target_comp.sport else "football").lower()

        if s_sport and t_sport and s_sport != t_sport:
            veto_reasons.append(f"sport_mismatch: {s_sport} vs {t_sport}")
            sport_score = 0.0
        elif s_sport == t_sport:
            sport_score = 1.0
        else:
            sport_score = 0.5

        # 2. Team References & Suffix Safety Analysis
        ref_s_home = TeamReference.from_raw(source_event.home_participant)
        ref_s_away = TeamReference.from_raw(source_event.away_participant)
        ref_t_home = TeamReference.from_raw(target_event.home_participant)
        ref_t_away = TeamReference.from_raw(target_event.away_participant)

        # Check Normal Orientation Suffixes
        suf_compat_home, suf_err_home = self._check_suffix_compatibility(ref_s_home.tokens, ref_t_home.tokens)
        suf_compat_away, suf_err_away = self._check_suffix_compatibility(ref_s_away.tokens, ref_t_away.tokens)

        # 3. Orientation & Team Similarity
        comp_home_norm = compare_teams(ref_s_home, ref_t_home)
        comp_away_norm = compare_teams(ref_s_away, ref_t_away)
        score_home_norm = 1.0 if comp_home_norm.exact_name else comp_home_norm.token_overlap
        score_away_norm = 1.0 if comp_away_norm.exact_name else comp_away_norm.token_overlap
        norm_team_score = (score_home_norm + score_away_norm) / 2.0

        # Evaluate Swapped Orientation
        comp_home_swap = compare_teams(ref_s_home, ref_t_away)
        comp_away_swap = compare_teams(ref_s_away, ref_t_home)
        score_home_swap = 1.0 if comp_home_swap.exact_name else comp_home_swap.token_overlap
        score_away_swap = 1.0 if comp_away_swap.exact_name else comp_away_swap.token_overlap
        swap_team_score = ((score_home_swap + score_away_swap) / 2.0) * (1.0 - self.config.orientation_swap_penalty)

        suf_compat_swap_h, suf_err_swap_h = self._check_suffix_compatibility(ref_s_home.tokens, ref_t_away.tokens)
        suf_compat_swap_a, suf_err_swap_a = self._check_suffix_compatibility(ref_s_away.tokens, ref_t_home.tokens)

        # Select Orientation
        if (
            self.config.allow_orientation_swap
            and swap_team_score > (norm_team_score + 0.15)
            and suf_compat_swap_h == SuffixCompatibility.EXACT_COMPATIBLE
            and suf_compat_swap_a == SuffixCompatibility.EXACT_COMPATIBLE
        ):
            orientation = OrientationType.ORIENTATION_SWAP
            home_score = score_home_swap
            away_score = score_away_swap
            warnings.append("orientation_swap_detected")
            if suf_err_swap_h:
                veto_reasons.append(suf_err_swap_h)
            if suf_err_swap_a:
                veto_reasons.append(suf_err_swap_a)
        else:
            orientation = OrientationType.NORMAL
            home_score = score_home_norm
            away_score = score_away_norm
            if suf_compat_home == SuffixCompatibility.MISMATCH:
                veto_reasons.append(f"home_{suf_err_home}")
            if suf_compat_away == SuffixCompatibility.MISMATCH:
                veto_reasons.append(f"away_{suf_err_away}")

        # 4. External ID Signal
        common_ext = set(source_event.external_ids.keys()) & set(target_event.external_ids.keys())
        if common_ext:
            matches = [
                str(source_event.external_ids[k]).strip().lower() == str(target_event.external_ids[k]).strip().lower()
                for k in common_ext
            ]
            if all(matches):
                ext_score = 1.0
                ext_details = {"match": True, "keys": list(common_ext)}
            else:
                ext_score = 0.0
                ext_details = {"match": False, "keys": list(common_ext), "reason": "conflicting_external_ids"}
                warnings.append("conflicting_external_ids_detected")
        else:
            ext_score = 0.5  # Neutral when external IDs are missing on one or both
            ext_details = {"match": None, "reason": "no_common_external_ids"}

        # 5. Kickoff Time Signal
        dt_s = parse_kickoff_to_utc(source_event.scheduled_start, default_tz="UTC")
        dt_t = parse_kickoff_to_utc(target_event.scheduled_start, default_tz="UTC")
        kickoff_score, diff_hours, kickoff_details = self._calculate_kickoff_score(dt_s, dt_t)
        if diff_hours is not None and diff_hours > self.config.max_kickoff_diff_hours:
            veto_reasons.append(f"kickoff_mismatch: {diff_hours}h exceeds max {self.config.max_kickoff_diff_hours}h")

        # 6. Competition Similarity Signal
        if source_comp and target_comp and source_comp.name and target_comp.name:
            is_placeholder_s = bool(re.match(r"^(tournament\s+\d+|unknown\s+competition)$", source_comp.name.strip().lower()))
            is_placeholder_t = bool(re.match(r"^(tournament\s+\d+|unknown\s+competition)$", target_comp.name.strip().lower()))
            if is_placeholder_s or is_placeholder_t:
                comp_score = 0.5
            else:
                norm_c_s, tok_c_s = normalize_competition_name(source_comp.name)
                norm_c_t, tok_c_t = normalize_competition_name(target_comp.name)
                if norm_c_s == norm_c_t:
                    comp_score = 1.0
                else:
                    set_cs, set_ct = set(tok_c_s), set(tok_c_t)
                    union_c = set_cs | set_ct
                    comp_score = len(set_cs & set_ct) / len(union_c) if union_c else 0.5
        else:
            comp_score = 0.5

        # Assemble Signals
        weights = dict(self.config.weights)
        # If external IDs are not observed on both events, do not penalize
        if ext_details.get("match") is None:
            weights["external_id"] = 0.0

        total_w = sum(weights.values())

        raw_signals = {
            "external_id": (ext_score, weights.get("external_id", 0.25), ext_details),
            "home_team": (home_score, weights.get("home_team", 0.25), {"token_overlap": home_score}),
            "away_team": (away_score, weights.get("away_team", 0.25), {"token_overlap": away_score}),
            "kickoff": (kickoff_score, weights.get("kickoff", 0.15), kickoff_details),
            "competition": (comp_score, weights.get("competition", 0.05), {"score": comp_score}),
            "sport": (sport_score, weights.get("sport", 0.05), {"sport_s": s_sport, "sport_t": t_sport}),
        }

        weighted_total = 0.0
        for sig_name, (raw_val, weight, details) in raw_signals.items():
            norm_w = weight / total_w if total_w > 0 else 0.0
            weighted_val = raw_val * norm_w
            weighted_total += weighted_val
            signals[sig_name] = SignalScore(
                name=sig_name,
                raw_score=round(raw_val, 4),
                weight=round(norm_w, 4),
                weighted_score=round(weighted_val, 4),
                details=details,
            )

        final_score = round(weighted_total, 4)
        rejection_reason_code: Optional[str] = None

        # Determine Rejection / Decision Category
        if veto_reasons:
            final_score = 0.0
            decision = MatchDecisionType.REJECTED
            # Classify primary veto
            if any("kickoff_mismatch" in v for v in veto_reasons):
                rejection_reason_code = MatchRejectionCode.KICKOFF_MISMATCH.value
            elif any("sport_mismatch" in v for v in veto_reasons):
                rejection_reason_code = MatchRejectionCode.SPORT_MISMATCH.value
            elif any(any(s in v for s in ("age_group", "gender", "reserve", "suffix")) for v in veto_reasons):
                rejection_reason_code = MatchRejectionCode.SUFFIX_VETO.value
            else:
                rejection_reason_code = MatchRejectionCode.LOW_MATCH_SCORE.value
        elif home_score < 0.30 or away_score < 0.30:
            # If one participant is an outright identity mismatch (e.g. Manchester City vs Manchester United)
            decision = MatchDecisionType.REJECTED
            rejection_reason_code = MatchRejectionCode.TEAM_IDENTITY_MISMATCH.value
        elif final_score >= self.config.matched_threshold:
            decision = MatchDecisionType.MATCHED
        elif final_score >= self.config.ambiguous_threshold:
            decision = MatchDecisionType.AMBIGUOUS
            rejection_reason_code = MatchRejectionCode.AMBIGUOUS_CANDIDATE.value
        else:
            decision = MatchDecisionType.REJECTED
            if comp_score < 0.20:
                rejection_reason_code = MatchRejectionCode.COMPETITION_MISMATCH.value
            elif diff_hours is not None and diff_hours > 3.0:
                rejection_reason_code = MatchRejectionCode.KICKOFF_MISMATCH.value
            else:
                rejection_reason_code = MatchRejectionCode.LOW_MATCH_SCORE.value

        evidence = {
            "source_event_id": source_event.internal_id,
            "target_event_id": target_event.internal_id,
            "source_name": f"{source_event.home_participant} vs {source_event.away_participant}",
            "target_name": f"{target_event.home_participant} vs {target_event.away_participant}",
            "home_team": source_event.home_participant,
            "away_team": source_event.away_participant,
            "home_team_score": home_score,
            "away_team_score": away_score,
            "kickoff_score": kickoff_score,
            "kickoff_diff_hours": diff_hours,
            "competition_score": comp_score,
            "competition_name": source_comp.name if source_comp else (target_comp.name if target_comp else None),
            "start_time": source_event.scheduled_start or target_event.scheduled_start,
            "source_start": source_event.scheduled_start,
            "target_start": target_event.scheduled_start,
            "blocking_keys": candidate.blocking_keys,
            "is_veto": bool(veto_reasons),
            "dominant_rejection_factor": rejection_reason_code,
        }

        return MatchDecision(
            source_event_id=source_event.internal_id,
            target_event_id=target_event.internal_id,
            decision=decision,
            total_score=final_score,
            orientation=orientation,
            signals=signals,
            veto_reasons=tuple(veto_reasons),
            rejection_reason_code=rejection_reason_code,
            warnings=tuple(warnings),
            evidence=evidence,
        )

    def match_candidates(
        self,
        candidates: List[EventCandidate],
        source_events_map: Dict[str, Event],
        target_events_map: Dict[str, Event],
        comp_map: Optional[Dict[str, Competition]] = None,
    ) -> MatchResult:
        """Scores candidate pairs and applies global best-match ambiguity resolution."""
        decisions: List[MatchDecision] = []

        for cand in candidates:
            s_ev = source_events_map.get(cand.source_event_id)
            t_ev = target_events_map.get(cand.target_event_id)
            if not s_ev or not t_ev:
                continue

            s_comp = comp_map.get(s_ev.competition_id) if comp_map else None
            t_comp = comp_map.get(t_ev.competition_id) if comp_map else None

            dec = self.score_candidate(cand, s_ev, t_ev, s_comp, t_comp)
            decisions.append(dec)

        # Ambiguity Margin Resolution: Group by (source_event_id, target_provider)
        decisions_by_source_prov: Dict[Tuple[str, str], List[MatchDecision]] = {}
        for d in decisions:
            t_ev = target_events_map.get(d.target_event_id)
            t_prov = "default"
            if t_ev:
                if t_ev.provider_ids:
                    t_prov = list(t_ev.provider_ids.keys())[0]
                elif t_ev.metadata:
                    t_prov = list(t_ev.metadata.keys())[0]
            decisions_by_source_prov.setdefault((d.source_event_id, t_prov), []).append(d)

        for (s_id, t_prov), s_decisions in decisions_by_source_prov.items():
            if len(s_decisions) > 1:
                s_decisions.sort(key=lambda x: x.total_score, reverse=True)
                top = s_decisions[0]
                second = s_decisions[1]
                if (top.total_score - second.total_score) < self.config.ambiguity_margin and second.total_score >= self.config.ambiguous_threshold:
                    if top.decision == MatchDecisionType.MATCHED:
                        top.decision = MatchDecisionType.AMBIGUOUS
                        top.rejection_reason_code = MatchRejectionCode.AMBIGUOUS_CANDIDATE.value
                    if second.decision == MatchDecisionType.MATCHED:
                        second.decision = MatchDecisionType.AMBIGUOUS
                        second.rejection_reason_code = MatchRejectionCode.AMBIGUOUS_CANDIDATE.value
                    top.warnings = tuple(list(top.warnings) + [
                        f"competing_close_candidate: target {second.target_event_id} score {second.total_score}"
                    ])
                    second.warnings = tuple(list(second.warnings) + [
                        f"competing_close_candidate: target {top.target_event_id} score {top.total_score}"
                    ])

        # Ambiguity Margin Resolution: Symmetric check grouped by (target_event_id, source_provider)
        decisions_by_target_prov: Dict[Tuple[str, str], List[MatchDecision]] = {}
        for d in decisions:
            s_ev = source_events_map.get(d.source_event_id)
            s_prov = "default"
            if s_ev:
                if s_ev.provider_ids:
                    s_prov = list(s_ev.provider_ids.keys())[0]
                elif s_ev.metadata:
                    s_prov = list(s_ev.metadata.keys())[0]
            decisions_by_target_prov.setdefault((d.target_event_id, s_prov), []).append(d)

        for (t_id, s_prov), t_decisions in decisions_by_target_prov.items():
            if len(t_decisions) > 1:
                t_decisions.sort(key=lambda x: x.total_score, reverse=True)
                top = t_decisions[0]
                second = t_decisions[1]
                if (top.total_score - second.total_score) < self.config.ambiguity_margin and second.total_score >= self.config.ambiguous_threshold:
                    if top.decision == MatchDecisionType.MATCHED:
                        top.decision = MatchDecisionType.AMBIGUOUS
                        top.rejection_reason_code = MatchRejectionCode.AMBIGUOUS_CANDIDATE.value
                    if second.decision == MatchDecisionType.MATCHED:
                        second.decision = MatchDecisionType.AMBIGUOUS
                        second.rejection_reason_code = MatchRejectionCode.AMBIGUOUS_CANDIDATE.value
                    top.warnings = tuple(list(top.warnings) + [
                        f"competing_close_source: source {second.source_event_id} score {second.total_score}"
                    ])
                    second.warnings = tuple(list(second.warnings) + [
                        f"competing_close_source: source {top.source_event_id} score {top.total_score}"
                    ])

        # Deterministic sorting
        decisions.sort(key=lambda d: (d.source_event_id, d.target_event_id))

        matched = sum(1 for d in decisions if d.decision == MatchDecisionType.MATCHED)
        ambiguous = sum(1 for d in decisions if d.decision == MatchDecisionType.AMBIGUOUS)
        rejected = sum(1 for d in decisions if d.decision == MatchDecisionType.REJECTED)

        # Compute rejection reasons breakdown
        rejection_breakdown: Dict[str, int] = {}
        for d in decisions:
            if d.decision != MatchDecisionType.MATCHED and d.rejection_reason_code:
                rejection_breakdown[d.rejection_reason_code] = rejection_breakdown.get(d.rejection_reason_code, 0) + 1

        return MatchResult(
            decisions=decisions,
            matched_count=matched,
            ambiguous_count=ambiguous,
            rejected_count=rejected,
            total_scored=len(decisions),
            rejection_reasons_breakdown=rejection_breakdown,
        )
