"""
Stage 4.3: Cross-Bookmaker Event Candidate Generation & Blocking Engine

Implements deterministic, provider-independent candidate generation to reduce
O(N x M) comparison space to small, auditable candidate pairs for Stage 4.4 scoring.

Core Invariants:
- High Recall over Precision (False Negative in Blocking > False Positive in Blocking).
- No Final Decisions (does NOT score or decide MATCHED/REJECTED).
- Multi-key Inverted Index (External IDs, Sport, Date, Home/Away tokens, Midnight tolerance, Orientation swaps).
- Output is deterministic and fully auditable.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Set, Tuple, Any, Union

from domain.models import Event, Competition
from normalization.base_normalizer import NormalizedGraph
from normalization.identity import (
    normalize_team_name,
    parse_kickoff_to_utc,
    WEAK_TOKENS,
)
from normalization.aliases import resolve_canonical_team_name


@dataclass(frozen=True)
class EventCandidate:
    """Immutable candidate pair produced by the blocking engine."""
    source_event_id: str
    target_event_id: str
    source_provider: str
    target_provider: str
    blocking_keys: Tuple[str, ...]
    evidence: Dict[str, Any]


@dataclass(frozen=True)
class CandidateGenerationResult:
    """Structured result of the candidate generation process."""
    candidates: List[EventCandidate]
    total_source_events: int
    total_target_events: int
    total_naive_pairs: int
    total_candidates: int
    reduction_percentage: float
    block_stats: Dict[str, int]
    warnings: List[str]


class EventCandidateGenerator:
    """Deterministic, provider-independent cross-bookmaker event candidate generator."""

    def __init__(self, midnight_tolerance_hours: int = 2, max_block_warning_threshold: int = 50):
        self.midnight_tolerance_hours = midnight_tolerance_hours
        self.max_block_warning_threshold = max_block_warning_threshold

    def _extract_event_and_comp(
        self,
        item: Union[Event, NormalizedGraph],
        comp_map: Optional[Dict[str, Competition]] = None
    ) -> Tuple[Event, Optional[Competition]]:
        """Extracts Event and optional Competition from either NormalizedGraph or Event."""
        if isinstance(item, NormalizedGraph):
            return item.event, item.competition
        elif isinstance(item, Event):
            comp = comp_map.get(item.competition_id) if comp_map else None
            return item, comp
        else:
            raise TypeError(f"Unsupported item type for candidate generation: {type(item)}")

    def _get_provider_name(self, event: Event) -> str:
        """Determines authoritative provider/bookmaker name from metadata or provider_ids."""
        if event.metadata:
            for k, v in event.metadata.items():
                if isinstance(v, dict) and "bookmaker" in v and v["bookmaker"]:
                    return str(v["bookmaker"]).lower()
        if event.provider_ids:
            return next(iter(event.provider_ids.keys())).lower()
        if event.metadata:
            return next(iter(event.metadata.keys())).lower()
        return "unknown"

    def _extract_tokens(self, participant: str) -> List[str]:
        """Extracts normalized and canonical tokens for a participant, filtering weak tokens if stronger tokens exist."""
        norm_name, tokens = normalize_team_name(participant)
        _, canon_tokens = resolve_canonical_team_name(norm_name, tokens)
        all_tokens = sorted(list(set(tokens) | set(canon_tokens)))
        filtered = [t for t in all_tokens if t not in WEAK_TOKENS]
        return filtered if filtered else all_tokens

    def generate_candidates(
        self,
        source_items: List[Union[Event, NormalizedGraph]],
        target_items: List[Union[Event, NormalizedGraph]],
        competition_map: Optional[Dict[str, Competition]] = None,
    ) -> CandidateGenerationResult:
        """Generates candidate pairs from source and target event collections.

        Args:
            source_items: List of source Events or NormalizedGraphs (e.g. Superbet).
            target_items: List of target Events or NormalizedGraphs (e.g. Betclic).
            competition_map: Optional mapping of competition_id -> Competition.

        Returns:
            CandidateGenerationResult with deduplicated, sorted EventCandidate pairs.
        """
        if not source_items or not target_items:
            total_source = len(source_items)
            total_target = len(target_items)
            return CandidateGenerationResult(
                candidates=[],
                total_source_events=total_source,
                total_target_events=total_target,
                total_naive_pairs=total_source * total_target,
                total_candidates=0,
                reduction_percentage=100.0 if (total_source * total_target) > 0 else 0.0,
                block_stats={},
                warnings=[],
            )

        # 1. Build Inverted Index over Target Items
        # index: key -> list of (target_event, target_comp, target_provider)
        inverted_index: Dict[str, List[Tuple[Event, Optional[Competition], str]]] = defaultdict(list)
        warnings: List[str] = []

        for target_item in target_items:
            t_event, t_comp = self._extract_event_and_comp(target_item, competition_map)
            t_provider = self._get_provider_name(t_event)
            sport = (t_comp.sport if t_comp and t_comp.sport else "football").lower()
            kickoff_dt = parse_kickoff_to_utc(t_event.scheduled_start, default_tz="UTC")

            home_tokens = self._extract_tokens(t_event.home_participant)
            away_tokens = self._extract_tokens(t_event.away_participant)

            entry = (t_event, t_comp, t_provider)

            # 1a. External ID Keys
            for ext_key, ext_val in t_event.external_ids.items():
                if ext_val:
                    k = f"EXT:{ext_key.lower()}:{str(ext_val).strip().lower()}"
                    inverted_index[k].append(entry)

            # 1b. Date + Sport + Team Tokens
            if kickoff_dt:
                date_str = kickoff_dt.strftime("%Y-%m-%d")
                for ht in home_tokens:
                    inverted_index[f"HOME:{sport}:{date_str}:{ht}"].append(entry)
                    # For orientation swap detection
                    inverted_index[f"SWAP_AWAY:{sport}:{date_str}:{ht}"].append(entry)

                for at in away_tokens:
                    inverted_index[f"AWAY:{sport}:{date_str}:{at}"].append(entry)
                    # For orientation swap detection
                    inverted_index[f"SWAP_HOME:{sport}:{date_str}:{at}"].append(entry)

                # Midnight boundary tolerance (hour >= 22 -> next day; hour <= 2 -> prev day)
                if kickoff_dt.hour >= (24 - self.midnight_tolerance_hours):
                    next_date = (kickoff_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in home_tokens:
                        inverted_index[f"HOME:{sport}:{next_date}:{ht}"].append(entry)
                        inverted_index[f"SWAP_AWAY:{sport}:{next_date}:{ht}"].append(entry)
                    for at in away_tokens:
                        inverted_index[f"AWAY:{sport}:{next_date}:{at}"].append(entry)
                        inverted_index[f"SWAP_HOME:{sport}:{next_date}:{at}"].append(entry)
                elif kickoff_dt.hour <= self.midnight_tolerance_hours:
                    prev_date = (kickoff_dt - timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in home_tokens:
                        inverted_index[f"HOME:{sport}:{prev_date}:{ht}"].append(entry)
                        inverted_index[f"SWAP_AWAY:{sport}:{prev_date}:{ht}"].append(entry)
                    for at in away_tokens:
                        inverted_index[f"AWAY:{sport}:{prev_date}:{ht}"].append(entry)
                        inverted_index[f"SWAP_HOME:{sport}:{prev_date}:{ht}"].append(entry)
            else:
                # Fallback: No-date token index
                for ht in home_tokens:
                    inverted_index[f"HOME_NODATE:{sport}:{ht}"].append(entry)
                    inverted_index[f"SWAP_AWAY_NODATE:{sport}:{ht}"].append(entry)
                for at in away_tokens:
                    inverted_index[f"AWAY_NODATE:{sport}:{at}"].append(entry)
                    inverted_index[f"SWAP_HOME_NODATE:{sport}:{at}"].append(entry)

        # Track block sizes for audit
        block_stats: Dict[str, int] = {}
        for b_key, b_entries in inverted_index.items():
            b_len = len(b_entries)
            block_stats[b_key] = b_len
            if b_len > self.max_block_warning_threshold:
                warnings.append(f"Oversized block detected for key '{b_key}': {b_len} target events")

        # 2. Query Index for Each Source Item & Deduplicate Candidates
        # candidate_map: (source_id, target_id) -> (source_event, target_event, source_prov, target_prov, set_of_keys, evidence_dict)
        candidate_map: Dict[Tuple[str, str], Tuple[Event, Event, str, str, Set[str], Dict[str, Any]]] = {}

        for source_item in source_items:
            s_event, s_comp = self._extract_event_and_comp(source_item, competition_map)
            s_provider = self._get_provider_name(s_event)
            s_sport = (s_comp.sport if s_comp and s_comp.sport else "football").lower()
            s_kickoff = parse_kickoff_to_utc(s_event.scheduled_start, default_tz="UTC")

            s_home_tokens = self._extract_tokens(s_event.home_participant)
            s_away_tokens = self._extract_tokens(s_event.away_participant)

            # Generate query keys for source
            source_query_keys: List[Tuple[str, str]] = []  # (lookup_key, reason_label)

            # External IDs
            for ext_k, ext_v in s_event.external_ids.items():
                if ext_v:
                    source_query_keys.append((f"EXT:{ext_k.lower()}:{str(ext_v).strip().lower()}", f"EXTERNAL_ID:{ext_k}"))

            if s_kickoff:
                s_date_str = s_kickoff.strftime("%Y-%m-%d")
                for ht in s_home_tokens:
                    source_query_keys.append((f"HOME:{s_sport}:{s_date_str}:{ht}", f"HOME_TOKEN:{ht}"))
                    source_query_keys.append((f"SWAP_HOME:{s_sport}:{s_date_str}:{ht}", f"ORIENTATION_SWAP:home_{ht}"))
                for at in s_away_tokens:
                    source_query_keys.append((f"AWAY:{s_sport}:{s_date_str}:{at}", f"AWAY_TOKEN:{at}"))
                    source_query_keys.append((f"SWAP_AWAY:{s_sport}:{s_date_str}:{at}", f"ORIENTATION_SWAP:away_{at}"))

                # Midnight tolerance query
                if s_kickoff.hour >= (24 - self.midnight_tolerance_hours):
                    next_date = (s_kickoff + timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in s_home_tokens:
                        source_query_keys.append((f"HOME:{s_sport}:{next_date}:{ht}", f"HOME_TOKEN_ADJACENT:{ht}"))
                    for at in s_away_tokens:
                        source_query_keys.append((f"AWAY:{s_sport}:{next_date}:{at}", f"AWAY_TOKEN_ADJACENT:{at}"))
                elif s_kickoff.hour <= self.midnight_tolerance_hours:
                    prev_date = (s_kickoff - timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in s_home_tokens:
                        source_query_keys.append((f"HOME:{s_sport}:{prev_date}:{ht}", f"HOME_TOKEN_ADJACENT:{ht}"))
                    for at in s_away_tokens:
                        source_query_keys.append((f"AWAY:{s_sport}:{prev_date}:{ht}", f"AWAY_TOKEN_ADJACENT:{at}"))
            else:
                for ht in s_home_tokens:
                    source_query_keys.append((f"HOME_NODATE:{s_sport}:{ht}", f"HOME_TOKEN_NODATE:{ht}"))
                for at in s_away_tokens:
                    source_query_keys.append((f"AWAY_NODATE:{s_sport}:{at}", f"AWAY_TOKEN_NODATE:{at}"))

            # Execute queries against inverted index
            for lookup_key, reason_label in source_query_keys:
                matches = inverted_index.get(lookup_key, [])
                for t_event, t_comp, t_prov in matches:
                    pair_key = (s_event.internal_id, t_event.internal_id)
                    if pair_key not in candidate_map:
                        evidence = {
                            "source_name": f"{s_event.home_participant} vs {s_event.away_participant}",
                            "target_name": f"{t_event.home_participant} vs {t_event.away_participant}",
                            "source_start": s_event.scheduled_start,
                            "target_start": t_event.scheduled_start,
                            "source_sport": s_sport,
                            "target_sport": (t_comp.sport if t_comp else "football").lower(),
                        }
                        candidate_map[pair_key] = (s_event, t_event, s_provider, t_prov, set(), evidence)

                    candidate_map[pair_key][4].add(reason_label)

        # 3. Assemble and Sort Candidates Deterministically
        candidates: List[EventCandidate] = []
        for (s_id, t_id), (s_ev, t_ev, s_prov, t_prov, keys_set, ev_dict) in candidate_map.items():
            candidates.append(
                EventCandidate(
                    source_event_id=s_id,
                    target_event_id=t_id,
                    source_provider=s_prov,
                    target_provider=t_prov,
                    blocking_keys=tuple(sorted(keys_set)),
                    evidence=ev_dict,
                )
            )

        # Deterministic sorting by source ID, then target ID
        candidates.sort(key=lambda c: (c.source_event_id, c.target_event_id))

        total_source = len(source_items)
        total_target = len(target_items)
        naive_pairs = total_source * total_target
        total_cand = len(candidates)
        reduction = ((naive_pairs - total_cand) / naive_pairs * 100.0) if naive_pairs > 0 else 0.0

        return CandidateGenerationResult(
            candidates=candidates,
            total_source_events=total_source,
            total_target_events=total_target,
            total_naive_pairs=naive_pairs,
            total_candidates=total_cand,
            reduction_percentage=round(reduction, 2),
            block_stats=block_stats,
            warnings=warnings,
        )

    def generate_candidates_n_way(
        self,
        items: List[Union[Event, NormalizedGraph]],
        competition_map: Optional[Dict[str, Competition]] = None,
    ) -> CandidateGenerationResult:
        """Generates cross-provider candidate pairs from an all-provider event collection (N-way matching).

        Rules:
        1. Candidates are ONLY formed between DIFFERENT providers (source_provider != target_provider).
        2. No self-matches (event_a.internal_id != event_b.internal_id).
        3. Every unordered provider pair {A, B} is generated exactly once (no A->B and B->A duplicates).
        4. High recall inverted-index blocking across external IDs, sport, date, team tokens, midnight drift, and orientation swaps.
        5. Output is deterministically sorted.

        Args:
            items: List of all Events or NormalizedGraphs across all available providers.
            competition_map: Optional mapping of competition_id -> Competition.

        Returns:
            CandidateGenerationResult containing deduplicated EventCandidate pairs across all provider combinations.
        """
        if not items or len(items) < 2:
            total_events = len(items) if items else 0
            return CandidateGenerationResult(
                candidates=[],
                total_source_events=total_events,
                total_target_events=0,
                total_naive_pairs=0,
                total_candidates=0,
                reduction_percentage=0.0,
                block_stats={},
                warnings=[],
            )

        comp_map = dict(competition_map or {})
        warnings: List[str] = []

        # 1. Unpack all items and group by provider
        unpacked: List[Tuple[Event, Optional[Competition], str, str, Optional[datetime], List[str], List[str]]] = []
        provider_counts: Dict[str, int] = defaultdict(int)

        # Deduplicate incoming items by internal_id in case duplicates were passed
        seen_internal_ids: Set[str] = set()
        for item in items:
            ev, comp = self._extract_event_and_comp(item, comp_map)
            if ev.internal_id in seen_internal_ids:
                continue
            seen_internal_ids.add(ev.internal_id)

            prov = self._get_provider_name(ev)
            provider_counts[prov] += 1

            sport = (comp.sport if comp and comp.sport else "football").lower()
            kickoff_dt = parse_kickoff_to_utc(ev.scheduled_start, default_tz="UTC")
            home_tokens = self._extract_tokens(ev.home_participant)
            away_tokens = self._extract_tokens(ev.away_participant)

            unpacked.append((ev, comp, prov, sport, kickoff_dt, home_tokens, away_tokens))

        # 2. Build Global Inverted Index over all items
        inverted_index: Dict[str, List[Tuple[Event, Optional[Competition], str]]] = defaultdict(list)

        for ev, comp, prov, sport, kickoff_dt, home_tokens, away_tokens in unpacked:
            entry = (ev, comp, prov)

            # 2a. External ID Keys
            for ext_key, ext_val in ev.external_ids.items():
                if ext_val:
                    k = f"EXT:{ext_key.lower()}:{str(ext_val).strip().lower()}"
                    inverted_index[k].append(entry)

            # 2b. Date + Sport + Team Tokens
            if kickoff_dt:
                date_str = kickoff_dt.strftime("%Y-%m-%d")
                for ht in home_tokens:
                    inverted_index[f"HOME:{sport}:{date_str}:{ht}"].append(entry)
                    inverted_index[f"SWAP_AWAY:{sport}:{date_str}:{ht}"].append(entry)

                for at in away_tokens:
                    inverted_index[f"AWAY:{sport}:{date_str}:{at}"].append(entry)
                    inverted_index[f"SWAP_HOME:{sport}:{date_str}:{at}"].append(entry)

                # Midnight boundary tolerance
                if kickoff_dt.hour >= (24 - self.midnight_tolerance_hours):
                    next_date = (kickoff_dt + timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in home_tokens:
                        inverted_index[f"HOME:{sport}:{next_date}:{ht}"].append(entry)
                        inverted_index[f"SWAP_AWAY:{sport}:{next_date}:{ht}"].append(entry)
                    for at in away_tokens:
                        inverted_index[f"AWAY:{sport}:{next_date}:{at}"].append(entry)
                        inverted_index[f"SWAP_HOME:{sport}:{next_date}:{at}"].append(entry)
                elif kickoff_dt.hour <= self.midnight_tolerance_hours:
                    prev_date = (kickoff_dt - timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in home_tokens:
                        inverted_index[f"HOME:{sport}:{prev_date}:{ht}"].append(entry)
                        inverted_index[f"SWAP_AWAY:{sport}:{prev_date}:{ht}"].append(entry)
                    for at in away_tokens:
                        inverted_index[f"AWAY:{sport}:{prev_date}:{ht}"].append(entry)
                        inverted_index[f"SWAP_HOME:{sport}:{prev_date}:{ht}"].append(entry)
            else:
                for ht in home_tokens:
                    inverted_index[f"HOME_NODATE:{sport}:{ht}"].append(entry)
                    inverted_index[f"SWAP_AWAY_NODATE:{sport}:{ht}"].append(entry)
                for at in away_tokens:
                    inverted_index[f"AWAY_NODATE:{sport}:{at}"].append(entry)
                    inverted_index[f"SWAP_HOME_NODATE:{sport}:{at}"].append(entry)

        # Track block sizes
        block_stats: Dict[str, int] = {}
        for b_key, b_entries in inverted_index.items():
            b_len = len(b_entries)
            block_stats[b_key] = b_len
            if b_len > self.max_block_warning_threshold:
                warnings.append(f"Oversized block detected for key '{b_key}': {b_len} events")

        # 3. Query Index and Form Unique Unordered Cross-Provider Candidate Pairs
        candidate_map: Dict[Tuple[str, str], Tuple[Event, Event, str, str, Set[str], Dict[str, Any]]] = {}

        for s_event, s_comp, s_prov, s_sport, s_kickoff, s_home_tokens, s_away_tokens in unpacked:
            query_keys: List[Tuple[str, str]] = []

            # External IDs
            for ext_k, ext_v in s_event.external_ids.items():
                if ext_v:
                    query_keys.append((f"EXT:{ext_k.lower()}:{str(ext_v).strip().lower()}", f"EXTERNAL_ID:{ext_k}"))

            if s_kickoff:
                s_date_str = s_kickoff.strftime("%Y-%m-%d")
                for ht in s_home_tokens:
                    query_keys.append((f"HOME:{s_sport}:{s_date_str}:{ht}", f"HOME_TOKEN:{ht}"))
                    query_keys.append((f"SWAP_HOME:{s_sport}:{s_date_str}:{ht}", f"ORIENTATION_SWAP:home_{ht}"))
                for at in s_away_tokens:
                    query_keys.append((f"AWAY:{s_sport}:{s_date_str}:{at}", f"AWAY_TOKEN:{at}"))
                    query_keys.append((f"SWAP_AWAY:{s_sport}:{s_date_str}:{at}", f"ORIENTATION_SWAP:away_{at}"))

                # Midnight tolerance query
                if s_kickoff.hour >= (24 - self.midnight_tolerance_hours):
                    next_date = (s_kickoff + timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in s_home_tokens:
                        query_keys.append((f"HOME:{s_sport}:{next_date}:{ht}", f"HOME_TOKEN_ADJACENT:{ht}"))
                    for at in s_away_tokens:
                        query_keys.append((f"AWAY:{s_sport}:{next_date}:{at}", f"AWAY_TOKEN_ADJACENT:{at}"))
                elif s_kickoff.hour <= self.midnight_tolerance_hours:
                    prev_date = (s_kickoff - timedelta(days=1)).strftime("%Y-%m-%d")
                    for ht in s_home_tokens:
                        query_keys.append((f"HOME:{s_sport}:{prev_date}:{ht}", f"HOME_TOKEN_ADJACENT:{ht}"))
                    for at in s_away_tokens:
                        query_keys.append((f"AWAY:{s_sport}:{prev_date}:{ht}", f"AWAY_TOKEN_ADJACENT:{at}"))
            else:
                for ht in s_home_tokens:
                    query_keys.append((f"HOME_NODATE:{s_sport}:{ht}", f"HOME_TOKEN_NODATE:{ht}"))
                for at in s_away_tokens:
                    query_keys.append((f"AWAY_NODATE:{s_sport}:{at}", f"AWAY_TOKEN_NODATE:{at}"))

            # Execute queries against inverted index
            for lookup_key, reason_label in query_keys:
                matches = inverted_index.get(lookup_key, [])
                for t_event, t_comp, t_prov in matches:
                    # Invariant 1: ONLY compare DIFFERENT providers
                    if s_prov == t_prov:
                        continue
                    # Invariant 2: Never self-match
                    if s_event.internal_id == t_event.internal_id:
                        continue

                    # Invariant 3: Canonicalize pair order by provider/internal_id for exact unordered deduplication
                    if (s_prov, s_event.internal_id) < (t_prov, t_event.internal_id):
                        cand_src_ev, cand_tgt_ev = s_event, t_event
                        cand_src_prov, cand_tgt_prov = s_prov, t_prov
                        cand_src_comp, cand_tgt_comp = s_comp, t_comp
                    else:
                        cand_src_ev, cand_tgt_ev = t_event, s_event
                        cand_src_prov, cand_tgt_prov = t_prov, s_prov
                        cand_src_comp, cand_tgt_comp = t_comp, s_comp

                    pair_key = (cand_src_ev.internal_id, cand_tgt_ev.internal_id)
                    if pair_key not in candidate_map:
                        evidence = {
                            "source_name": f"{cand_src_ev.home_participant} vs {cand_src_ev.away_participant}",
                            "target_name": f"{cand_tgt_ev.home_participant} vs {cand_tgt_ev.away_participant}",
                            "source_start": cand_src_ev.scheduled_start,
                            "target_start": cand_tgt_ev.scheduled_start,
                            "source_sport": (cand_src_comp.sport if cand_src_comp else "football").lower(),
                            "target_sport": (cand_tgt_comp.sport if cand_tgt_comp else "football").lower(),
                        }
                        candidate_map[pair_key] = (cand_src_ev, cand_tgt_ev, cand_src_prov, cand_tgt_prov, set(), evidence)

                    candidate_map[pair_key][4].add(reason_label)

        # 4. Assemble and Sort Candidates Deterministically
        candidates: List[EventCandidate] = []
        for (s_id, t_id), (s_ev, t_ev, s_prov, t_prov, keys_set, ev_dict) in candidate_map.items():
            candidates.append(
                EventCandidate(
                    source_event_id=s_id,
                    target_event_id=t_id,
                    source_provider=s_prov,
                    target_provider=t_prov,
                    blocking_keys=tuple(sorted(keys_set)),
                    evidence=ev_dict,
                )
            )

        candidates.sort(key=lambda c: (c.source_event_id, c.target_event_id))

        # Naive cross-provider pairs calculation
        prov_list = list(provider_counts.keys())
        naive_pairs = 0
        for i in range(len(prov_list)):
            for j in range(i + 1, len(prov_list)):
                naive_pairs += provider_counts[prov_list[i]] * provider_counts[prov_list[j]]

        total_cand = len(candidates)
        reduction = ((naive_pairs - total_cand) / naive_pairs * 100.0) if naive_pairs > 0 else 0.0

        return CandidateGenerationResult(
            candidates=candidates,
            total_source_events=len(unpacked),
            total_target_events=len(unpacked),
            total_naive_pairs=naive_pairs,
            total_candidates=total_cand,
            reduction_percentage=round(reduction, 2),
            block_stats=block_stats,
            warnings=warnings,
        )

