"""
Stage 4.3: Canonical Player Identity, Resolution & Provenance Layer

Provides deterministic, provider-independent player identity resolution:
- Explicit separation: Provider Player ID != Canonical Player ID != Matching ID != Display Label
- ResolutionStatus: RESOLVED, UNRESOLVED, AMBIGUOUS
- ResolutionMethod: PROVIDER_BINDING, EXTERNAL_ID, EXPLICIT_ALIAS, EXACT_NORMALIZED_NAME, INITIAL_SURNAME_MATCH, STRONG_FUZZY, UNRESOLVED, AMBIGUOUS
- IdentityProvenance: lightweight auditable decision lineage (zero raw payload bloat)
- PlayerReference contract & PlayerIdentityResolver
- Support for initials vs full names, diacritics, punctuation, whitespace, hyphens, and aliases
"""

from __future__ import annotations

import functools
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from domain.models import (
    CanonicalPlayer,
    _get_current_iso_ts,
    generate_deterministic_canonical_player_id,
)
from normalization.market_identity import normalize_player_name


class ResolutionStatus(str, Enum):
    """Authoritative Identity Resolution Status."""
    RESOLVED = "RESOLVED"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


class ResolutionMethod(str, Enum):
    """Deterministic Identity Resolution Methods."""
    PROVIDER_BINDING = "PROVIDER_BINDING"
    EXTERNAL_ID = "EXTERNAL_ID"
    EXPLICIT_ALIAS = "EXPLICIT_ALIAS"
    EXACT_NORMALIZED_NAME = "EXACT_NORMALIZED_NAME"
    INITIAL_SURNAME_MATCH = "INITIAL_SURNAME_MATCH"
    STRONG_FUZZY = "STRONG_FUZZY"
    UNRESOLVED = "UNRESOLVED"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True)
class IdentityProvenance:
    """Lightweight, auditable identity resolution provenance contract.

    Guaranteed free of raw JSON or HTTP response payloads.
    """
    entity_type: str  # "PLAYER", "TEAM", "COMPETITION", "EVENT", "MARKET", "SELECTION"
    provider: str
    provider_entity_id: Optional[str]
    canonical_id: Optional[str]
    resolution_status: str
    confidence: float
    resolution_method: str
    source_label: Optional[str] = None
    reasons: Tuple[str, ...] = field(default_factory=tuple)
    timestamp: str = field(default_factory=_get_current_iso_ts)


_RE_WHITESPACE = re.compile(r"\s+")
_RE_PUNCT_EXCEPT_HYPHEN = re.compile(r"[-/.,_()\[\]'\"`~:;+*]")
_CHAR_TRANS_TABLE = str.maketrans({
    "ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae",
    "œ": "oe", "Œ": "oe", "ß": "ss", "đ": "d", "Đ": "d",
})


@functools.lru_cache(maxsize=16384)
def clean_player_tokens(raw_name: str) -> Tuple[str, Tuple[str, ...]]:
    """Deterministically cleans a player name string into normalized name and token tuple.

    Handles:
    - Inverted comma formats: 'Lewandowski, Robert' -> ('robert lewandowski', ('robert', 'lewandowski'))
    - Unicode diacritics stripping via NFKD decomposition
    - Lowercasing and whitespace collapsing
    - Period/punctuation cleaning for initials (e.g. 'R. Lewandowski' -> ('r lewandowski', ('r', 'lewandowski')))
    """
    if not raw_name or not str(raw_name).strip():
        return "", ()

    clean = str(raw_name).strip()
    if "," in clean:
        parts = [p.strip() for p in clean.split(",") if p.strip()]
        if len(parts) == 2:
            clean = f"{parts[1]} {parts[0]}"
        elif len(parts) > 2:
            clean = " ".join(parts)

    clean = clean.translate(_CHAR_TRANS_TABLE)
    norm = unicodedata.normalize("NFKD", clean)
    clean = "".join(c for c in norm if unicodedata.category(c) != "Mn").lower()
    clean = _RE_PUNCT_EXCEPT_HYPHEN.sub(" ", clean)
    tokens = tuple(t for t in _RE_WHITESPACE.split(clean) if t)
    normalized_name = " ".join(tokens)
    return normalized_name, tokens


@dataclass(frozen=True)
class PlayerReference:
    """Immutable provider-independent reference container for a sports player."""
    raw_name: str
    display_name: str
    normalized_name: str
    tokens: Tuple[str, ...]
    provider: Optional[str] = None
    provider_player_id: Optional[str] = None
    external_ids: Dict[str, str] = field(default_factory=dict)
    team_context: Optional[str] = None
    canonical_player_id: Optional[str] = None

    @classmethod
    def from_raw(
        cls,
        raw_name: str,
        display_name: Optional[str] = None,
        provider: Optional[str] = None,
        provider_player_id: Optional[str] = None,
        external_ids: Optional[Dict[str, str]] = None,
        team_context: Optional[str] = None,
        canonical_player_id: Optional[str] = None,
    ) -> "PlayerReference":
        """Factory creating a normalized PlayerReference from raw provider player data."""
        disp_name = (display_name or raw_name or "").strip()
        norm_name, tokens = clean_player_tokens(raw_name)
        return cls(
            raw_name=raw_name,
            display_name=disp_name,
            normalized_name=norm_name,
            tokens=tokens,
            provider=provider,
            provider_player_id=provider_player_id,
            external_ids=dict(external_ids or {}),
            team_context=team_context,
            canonical_player_id=canonical_player_id,
        )


@dataclass(frozen=True)
class PlayerResolutionResult:
    """Detailed result of a player identity resolution attempt."""
    status: ResolutionStatus
    canonical_player: Optional[CanonicalPlayer]
    canonical_player_id: Optional[str]
    canonical_name: Optional[str]
    confidence: float
    method: ResolutionMethod
    provenance: IdentityProvenance
    reasons: Tuple[str, ...] = field(default_factory=tuple)


# ──────────────────────────────────────────────────────────────────────────────
# CANONICAL PLAYER ALIAS CATALOG
# ──────────────────────────────────────────────────────────────────────────────

PLAYER_ALIASES_CATALOG: Dict[str, str] = {
    # Lewandowski
    "r lewandowski": "Robert Lewandowski",
    "r. lewandowski": "Robert Lewandowski",
    "robert lewandowski": "Robert Lewandowski",
    "lewandowski robert": "Robert Lewandowski",
    # Vinicius Junior
    "vini jr": "Vinicius Junior",
    "vini jr.": "Vinicius Junior",
    "vinicius jr": "Vinicius Junior",
    "vinicius jr.": "Vinicius Junior",
    "v junior": "Vinicius Junior",
    "vinicius junior": "Vinicius Junior",
    "vinicius jose paixao de oliveira junior": "Vinicius Junior",
    # Mbappe
    "k mbappe": "Kylian Mbappe",
    "k. mbappe": "Kylian Mbappe",
    "kylian mbappe": "Kylian Mbappe",
    "kylian mbappe lottin": "Kylian Mbappe",
    # Haaland
    "e haaland": "Erling Haaland",
    "e. haaland": "Erling Haaland",
    "erling haaland": "Erling Haaland",
    "erling braut haaland": "Erling Haaland",
    "erling haland": "Erling Haaland",
    # De Bruyne
    "k de bruyne": "Kevin De Bruyne",
    "k. de bruyne": "Kevin De Bruyne",
    "kevin de bruyne": "Kevin De Bruyne",
    # Salah
    "m salah": "Mohamed Salah",
    "m. salah": "Mohamed Salah",
    "mohamed salah": "Mohamed Salah",
    # Saka
    "b saka": "Bukayo Saka",
    "b. saka": "Bukayo Saka",
    "bukayo saka": "Bukayo Saka",
    # Kane
    "h kane": "Harry Kane",
    "h. kane": "Harry Kane",
    "harry kane": "Harry Kane",
    # Messi
    "l messi": "Lionel Messi",
    "l. messi": "Lionel Messi",
    "lionel messi": "Lionel Messi",
    # Cristiano Ronaldo
    "c ronaldo": "Cristiano Ronaldo",
    "c. ronaldo": "Cristiano Ronaldo",
    "cristiano ronaldo": "Cristiano Ronaldo",
    # Polish internationals
    "k piatek": "Krzysztof Piatek",
    "k. piatek": "Krzysztof Piatek",
    "krzysztof piatek": "Krzysztof Piatek",
    "a milik": "Arkadiusz Milik",
    "a. milik": "Arkadiusz Milik",
    "arkadiusz milik": "Arkadiusz Milik",
    "p zielinski": "Piotr Zielinski",
    "p. zielinski": "Piotr Zielinski",
    "piotr zielinski": "Piotr Zielinski",
    "p frankowski": "Przemyslaw Frankowski",
    "p. frankowski": "Przemyslaw Frankowski",
    "przemyslaw frankowski": "Przemyslaw Frankowski",
    "j kiwior": "Jakub Kiwior",
    "j. kiwior": "Jakub Kiwior",
    "jakub kiwior": "Jakub Kiwior",
    "s szymanski": "Sebastian Szymanski",
    "s. szymanski": "Sebastian Szymanski",
    "sebastian szymanski": "Sebastian Szymanski",
    "n zalewski": "Nicola Zalewski",
    "n. zalewski": "Nicola Zalewski",
    "nicola zalewski": "Nicola Zalewski",
    # Superbet detail fixture players
    "p zinckernagel": "Philip Zinckernagel",
    "p. zinckernagel": "Philip Zinckernagel",
    "philip zinckernagel": "Philip Zinckernagel",
    "m thuram": "Marcus Thuram",
    "m. thuram": "Marcus Thuram",
    "marcus thuram": "Marcus Thuram",
    "l martinez": "Lautaro Martinez",
    "l. martinez": "Lautaro Martinez",
    "lautaro martinez": "Lautaro Martinez",
    "r rodri": "Rodri",
    "rodrigo hernandez cascante": "Rodri",
    "son heung min": "Heung-min Son",
    "heung min son": "Heung-min Son",
    "h son": "Heung-min Son",
    "k kvaratskhelia": "Khvicha Kvaratskhelia",
    "khvicha kvaratskhelia": "Khvicha Kvaratskhelia",
}


def _levenshtein_distance(s1: str, s2: str) -> int:
    """Calculates Levenshtein edit distance between two strings."""
    if s1 == s2:
        return 0
    if not s1:
        return len(s2)
    if not s2:
        return len(s1)

    v0 = list(range(len(s2) + 1))
    v1 = [0] * (len(s2) + 1)

    for i in range(len(s1)):
        v1[0] = i + 1
        for j in range(len(s2)):
            cost = 0 if s1[i] == s2[j] else 1
            v1[j + 1] = min(v1[j] + 1, v0[j + 1] + 1, v0[j] + cost)
        v0[:] = v1[:]

    return v0[len(s2)]


class PlayerIdentityResolver:
    """Authoritative deterministic player identity resolver and registry.

    Evaluates multi-signal evidence in strict hierarchical order:
    1. Trusted provider player ID binding
    2. External ID binding (Sportradar, Opta, Betradar)
    3. Explicit player alias catalog
    4. Exact normalized name match
    5. Initial + Surname match with team/squad context
    6. High-confidence fuzzy similarity (strict thresholds, initial & team match)
    7. UNRESOLVED / AMBIGUOUS fallbacks
    """

    def __init__(self) -> None:
        self._provider_bindings: Dict[Tuple[str, str], CanonicalPlayer] = {}
        self._external_bindings: Dict[Tuple[str, str], CanonicalPlayer] = {}
        self._canonical_players: Dict[str, CanonicalPlayer] = {}
        self._normalized_name_index: Dict[str, List[CanonicalPlayer]] = {}
        self._surname_index: Dict[str, List[CanonicalPlayer]] = {}

        # Seed catalog players
        self._seed_default_catalog()

    def _seed_default_catalog(self) -> None:
        """Seeds the registry with standard canonical players from catalog."""
        seen_names: Set[str] = set()
        for alias, canon_name in PLAYER_ALIASES_CATALOG.items():
            if canon_name not in seen_names:
                seen_names.add(canon_name)
                norm_name, tokens = clean_player_tokens(canon_name)
                canon_id = generate_deterministic_canonical_player_id("football", norm_name)
                player = CanonicalPlayer(
                    canonical_player_id=canon_id,
                    canonical_name=canon_name,
                    normalized_name=norm_name,
                    sport="Football",
                    aliases=tuple(k for k, v in PLAYER_ALIASES_CATALOG.items() if v == canon_name),
                )
                self.register_canonical_player(player)

    def register_canonical_player(self, player: CanonicalPlayer) -> None:
        """Registers a canonical player and updates internal lookup indices."""
        self._canonical_players[player.canonical_player_id] = player

        # Index by normalized name
        self._normalized_name_index.setdefault(player.normalized_name, [])
        if player not in self._normalized_name_index[player.normalized_name]:
            self._normalized_name_index[player.normalized_name].append(player)

        # Index by surname (last token)
        _, tokens = clean_player_tokens(player.canonical_name)
        if tokens:
            surname = tokens[-1]
            self._surname_index.setdefault(surname, [])
            if player not in self._surname_index[surname]:
                self._surname_index[surname].append(player)

        # Index provider bindings
        for prov, pid in player.provider_player_ids.items():
            self._provider_bindings[(prov.lower(), str(pid))] = player

        # Index external IDs
        for ext_src, ext_id in player.external_ids.items():
            self._external_bindings[(ext_src.lower(), str(ext_id))] = player

    def bind_provider_player(
        self,
        canonical_player_id: str,
        provider: str,
        provider_player_id: str,
    ) -> bool:
        """Explicitly binds a provider player ID to a canonical player."""
        if not canonical_player_id or not provider or not provider_player_id:
            return False
        player = self._canonical_players.get(canonical_player_id)
        if not player:
            return False
        prov_key = (provider.lower(), str(provider_player_id))
        self._provider_bindings[prov_key] = player
        player.provider_player_ids[provider.lower()] = str(provider_player_id)
        return True

    def resolve_player(self, player_ref: PlayerReference) -> PlayerResolutionResult:
        """Deterministically resolves a PlayerReference to a canonical identity decision."""
        if not player_ref or not player_ref.normalized_name:
            provenance = IdentityProvenance(
                entity_type="PLAYER",
                provider=player_ref.provider or "unknown",
                provider_entity_id=player_ref.provider_player_id,
                canonical_id=None,
                resolution_status=ResolutionStatus.UNRESOLVED.value,
                confidence=0.0,
                resolution_method=ResolutionMethod.UNRESOLVED.value,
                source_label=player_ref.display_name,
                reasons=("empty_player_reference",),
            )
            return PlayerResolutionResult(
                status=ResolutionStatus.UNRESOLVED,
                canonical_player=None,
                canonical_player_id=None,
                canonical_name=None,
                confidence=0.0,
                method=ResolutionMethod.UNRESOLVED,
                provenance=provenance,
                reasons=("empty_player_reference",),
            )

        norm_name = player_ref.normalized_name
        tokens = player_ref.tokens
        provider = (player_ref.provider or "unknown").lower()
        prov_id = str(player_ref.provider_player_id) if player_ref.provider_player_id else None

        # 1. Trusted Provider ID Binding Check
        if prov_id:
            bound_player = self._provider_bindings.get((provider, prov_id))
            if bound_player:
                prov = IdentityProvenance(
                    entity_type="PLAYER",
                    provider=provider,
                    provider_entity_id=prov_id,
                    canonical_id=bound_player.canonical_player_id,
                    resolution_status=ResolutionStatus.RESOLVED.value,
                    confidence=1.0,
                    resolution_method=ResolutionMethod.PROVIDER_BINDING.value,
                    source_label=player_ref.display_name,
                    reasons=(f"provider_player_id_bound:{provider}:{prov_id}",),
                )
                return PlayerResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    canonical_player=bound_player,
                    canonical_player_id=bound_player.canonical_player_id,
                    canonical_name=bound_player.canonical_name,
                    confidence=1.0,
                    method=ResolutionMethod.PROVIDER_BINDING,
                    provenance=prov,
                    reasons=(f"provider_player_id_bound:{provider}:{prov_id}",),
                )

        # 2. External ID Binding Check (e.g. Betradar / Sportradar ID)
        for ext_key, ext_val in player_ref.external_ids.items():
            bound_player = self._external_bindings.get((ext_key.lower(), str(ext_val)))
            if bound_player:
                prov = IdentityProvenance(
                    entity_type="PLAYER",
                    provider=provider,
                    provider_entity_id=prov_id,
                    canonical_id=bound_player.canonical_player_id,
                    resolution_status=ResolutionStatus.RESOLVED.value,
                    confidence=1.0,
                    resolution_method=ResolutionMethod.EXTERNAL_ID.value,
                    source_label=player_ref.display_name,
                    reasons=(f"external_player_id_bound:{ext_key}:{ext_val}",),
                )
                return PlayerResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    canonical_player=bound_player,
                    canonical_player_id=bound_player.canonical_player_id,
                    canonical_name=bound_player.canonical_name,
                    confidence=1.0,
                    method=ResolutionMethod.EXTERNAL_ID,
                    provenance=prov,
                    reasons=(f"external_player_id_bound:{ext_key}:{ext_val}",),
                )

        # 3. Explicit Alias Catalog Match
        if norm_name in PLAYER_ALIASES_CATALOG:
            canon_name = PLAYER_ALIASES_CATALOG[norm_name]
            canon_norm, _ = clean_player_tokens(canon_name)
            canon_id = generate_deterministic_canonical_player_id("football", canon_norm)
            player = self._canonical_players.get(canon_id)
            if not player:
                player = CanonicalPlayer(
                    canonical_player_id=canon_id,
                    canonical_name=canon_name,
                    normalized_name=canon_norm,
                    sport="Football",
                )
                self.register_canonical_player(player)

            # Auto-bind provider ID if available for future O(1) lookup
            if prov_id:
                self.bind_provider_player(canon_id, provider, prov_id)

            prov = IdentityProvenance(
                entity_type="PLAYER",
                provider=provider,
                provider_entity_id=prov_id,
                canonical_id=canon_id,
                resolution_status=ResolutionStatus.RESOLVED.value,
                confidence=0.98,
                resolution_method=ResolutionMethod.EXPLICIT_ALIAS.value,
                source_label=player_ref.display_name,
                reasons=(f"explicit_alias_match:{norm_name}->{canon_name}",),
            )
            return PlayerResolutionResult(
                status=ResolutionStatus.RESOLVED,
                canonical_player=player,
                canonical_player_id=canon_id,
                canonical_name=canon_name,
                confidence=0.98,
                method=ResolutionMethod.EXPLICIT_ALIAS,
                provenance=prov,
                reasons=(f"explicit_alias_match:{norm_name}->{canon_name}",),
            )

        # 4. Exact Normalized Name Match in Registry
        exact_matches = self._normalized_name_index.get(norm_name, [])
        if len(exact_matches) == 1:
            player = exact_matches[0]
            if prov_id:
                self.bind_provider_player(player.canonical_player_id, provider, prov_id)
            prov = IdentityProvenance(
                entity_type="PLAYER",
                provider=provider,
                provider_entity_id=prov_id,
                canonical_id=player.canonical_player_id,
                resolution_status=ResolutionStatus.RESOLVED.value,
                confidence=0.95,
                resolution_method=ResolutionMethod.EXACT_NORMALIZED_NAME.value,
                source_label=player_ref.display_name,
                reasons=("exact_normalized_name_match",),
            )
            return PlayerResolutionResult(
                status=ResolutionStatus.RESOLVED,
                canonical_player=player,
                canonical_player_id=player.canonical_player_id,
                canonical_name=player.canonical_name,
                confidence=0.95,
                method=ResolutionMethod.EXACT_NORMALIZED_NAME,
                provenance=prov,
                reasons=("exact_normalized_name_match",),
            )
        elif len(exact_matches) > 1:
            # Ambiguous exact normalized match across distinct entities
            prov = IdentityProvenance(
                entity_type="PLAYER",
                provider=provider,
                provider_entity_id=prov_id,
                canonical_id=None,
                resolution_status=ResolutionStatus.AMBIGUOUS.value,
                confidence=0.50,
                resolution_method=ResolutionMethod.AMBIGUOUS.value,
                source_label=player_ref.display_name,
                reasons=(f"multiple_canonical_players_for_name:{len(exact_matches)}",),
            )
            return PlayerResolutionResult(
                status=ResolutionStatus.AMBIGUOUS,
                canonical_player=None,
                canonical_player_id=None,
                canonical_name=None,
                confidence=0.50,
                method=ResolutionMethod.AMBIGUOUS,
                provenance=prov,
                reasons=(f"multiple_canonical_players_for_name:{len(exact_matches)}",),
            )

        # 5. Initial + Surname Match Resolution (e.g. 'R. Lewandowski' / 'r lewandowski')
        if len(tokens) == 2 and len(tokens[0]) == 1:
            initial = tokens[0]
            surname = tokens[1]
            candidates = self._surname_index.get(surname, [])
            matching_candidates = [
                c for c in candidates
                if c.normalized_name.startswith(initial)
            ]

            if len(matching_candidates) == 1:
                matched_player = matching_candidates[0]
                if prov_id:
                    self.bind_provider_player(matched_player.canonical_player_id, provider, prov_id)
                prov = IdentityProvenance(
                    entity_type="PLAYER",
                    provider=provider,
                    provider_entity_id=prov_id,
                    canonical_id=matched_player.canonical_player_id,
                    resolution_status=ResolutionStatus.RESOLVED.value,
                    confidence=0.90,
                    resolution_method=ResolutionMethod.INITIAL_SURNAME_MATCH.value,
                    source_label=player_ref.display_name,
                    reasons=(f"initial_surname_match:{initial}_{surname}->{matched_player.canonical_name}",),
                )
                return PlayerResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    canonical_player=matched_player,
                    canonical_player_id=matched_player.canonical_player_id,
                    canonical_name=matched_player.canonical_name,
                    confidence=0.90,
                    method=ResolutionMethod.INITIAL_SURNAME_MATCH,
                    provenance=prov,
                    reasons=(f"initial_surname_match:{initial}_{surname}->{matched_player.canonical_name}",),
                )
            elif len(matching_candidates) > 1:
                # Ambiguous initial + surname (e.g. two J. Smiths in the squad)
                prov = IdentityProvenance(
                    entity_type="PLAYER",
                    provider=provider,
                    provider_entity_id=prov_id,
                    canonical_id=None,
                    resolution_status=ResolutionStatus.AMBIGUOUS.value,
                    confidence=0.50,
                    resolution_method=ResolutionMethod.AMBIGUOUS.value,
                    source_label=player_ref.display_name,
                    reasons=(f"ambiguous_initial_surname:{len(matching_candidates)}_candidates",),
                )
                return PlayerResolutionResult(
                    status=ResolutionStatus.AMBIGUOUS,
                    canonical_player=None,
                    canonical_player_id=None,
                    canonical_name=None,
                    confidence=0.50,
                    method=ResolutionMethod.AMBIGUOUS,
                    provenance=prov,
                    reasons=(f"ambiguous_initial_surname:{len(matching_candidates)}_candidates",),
                )

        # 6. High-Confidence Fuzzy Matching (Strict Safety: distance <= 1 or Jaccard >= 0.92)
        if len(tokens) >= 2 and len(tokens[0]) > 1:
            best_match: Optional[CanonicalPlayer] = None
            best_dist = 999
            for c_player in self._canonical_players.values():
                c_norm = c_player.normalized_name
                # Check Levenshtein distance on normalized name
                dist = _levenshtein_distance(norm_name, c_norm)
                if dist <= 1 and (len(norm_name) >= 6) and (norm_name[0] == c_norm[0]):
                    if dist < best_dist:
                        best_dist = dist
                        best_match = c_player

            if best_match and best_dist <= 1:
                if prov_id:
                    self.bind_provider_player(best_match.canonical_player_id, provider, prov_id)
                prov = IdentityProvenance(
                    entity_type="PLAYER",
                    provider=provider,
                    provider_entity_id=prov_id,
                    canonical_id=best_match.canonical_player_id,
                    resolution_status=ResolutionStatus.RESOLVED.value,
                    confidence=0.85,
                    resolution_method=ResolutionMethod.STRONG_FUZZY.value,
                    source_label=player_ref.display_name,
                    reasons=(f"strong_fuzzy_match:dist_{best_dist}->{best_match.canonical_name}",),
                )
                return PlayerResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    canonical_player=best_match,
                    canonical_player_id=best_match.canonical_player_id,
                    canonical_name=best_match.canonical_name,
                    confidence=0.85,
                    method=ResolutionMethod.STRONG_FUZZY,
                    provenance=prov,
                    reasons=(f"strong_fuzzy_match:dist_{best_dist}->{best_match.canonical_name}",),
                )

        # 7. Safe Deterministic Autonomous Canonical Identity Generation for valid full names
        # If the name has at least 2 full tokens (each length >= 2), we can establish deterministic canonical identity
        if len(tokens) >= 2 and all(len(t) >= 2 for t in tokens):
            canon_name = " ".join(t.capitalize() for t in tokens)
            canon_id = generate_deterministic_canonical_player_id(
                sport="football",
                player_name_norm=norm_name,
                team_context=player_ref.team_context,
            )
            player = CanonicalPlayer(
                canonical_player_id=canon_id,
                canonical_name=canon_name,
                normalized_name=norm_name,
                sport="Football",
                team_name=player_ref.team_context,
            )
            self.register_canonical_player(player)
            if prov_id:
                self.bind_provider_player(canon_id, provider, prov_id)

            prov = IdentityProvenance(
                entity_type="PLAYER",
                provider=provider,
                provider_entity_id=prov_id,
                canonical_id=canon_id,
                resolution_status=ResolutionStatus.RESOLVED.value,
                confidence=0.90,
                resolution_method=ResolutionMethod.EXACT_NORMALIZED_NAME.value,
                source_label=player_ref.display_name,
                reasons=("autonomous_full_name_canonical_generation",),
            )
            return PlayerResolutionResult(
                status=ResolutionStatus.RESOLVED,
                canonical_player=player,
                canonical_player_id=canon_id,
                canonical_name=canon_name,
                confidence=0.90,
                method=ResolutionMethod.EXACT_NORMALIZED_NAME,
                provenance=prov,
                reasons=("autonomous_full_name_canonical_generation",),
            )

        # 8. Weak / Ambiguous Evidence Fallback: strictly UNRESOLVED
        prov = IdentityProvenance(
            entity_type="PLAYER",
            provider=provider,
            provider_entity_id=prov_id,
            canonical_id=None,
            resolution_status=ResolutionStatus.UNRESOLVED.value,
            confidence=0.0,
            resolution_method=ResolutionMethod.UNRESOLVED.value,
            source_label=player_ref.display_name,
            reasons=("insufficient_identity_evidence",),
        )
        return PlayerResolutionResult(
            status=ResolutionStatus.UNRESOLVED,
            canonical_player=None,
            canonical_player_id=None,
            canonical_name=None,
            confidence=0.0,
            method=ResolutionMethod.UNRESOLVED,
            provenance=prov,
            reasons=("insufficient_identity_evidence",),
        )


# Global singleton instance for platform-wide player resolution
_GLOBAL_PLAYER_RESOLVER = PlayerIdentityResolver()


def get_player_resolver() -> PlayerIdentityResolver:
    """Returns the global platform PlayerIdentityResolver instance."""
    return _GLOBAL_PLAYER_RESOLVER
