"""
Stage 4.2: Team, Competition, and Kickoff Identity Normalization & Reference Design

Provides deterministic, provider-independent preprocessing primitives:
- TeamReference & CompetitionReference contracts
- Deterministic text normalization (Unicode NFKD, casing, punctuation, tokenization)
- Identity-significant suffix preservation (U17-U23, II, B, Reserves, Women, Fem)
- Timezone-safe UTC kickoff parsing
- Team comparison metrics (exact match, token Jaccard similarity, ID match)
- AliasResolver extension point
"""

import functools
import re
import unicodedata
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple, Set, Any


# Character translation map for non-standard Latin characters before NFKD
CHAR_TRANSLATIONS = {
    "ł": "l",
    "Ł": "l",
    "ø": "o",
    "Ø": "o",
    "æ": "ae",
    "Æ": "ae",
    "œ": "oe",
    "Œ": "oe",
    "ß": "ss",
    "đ": "d",
    "Đ": "d",
}
_CHAR_TRANS_TABLE = str.maketrans(CHAR_TRANSLATIONS)

from normalization.aliases import (
    TRANSLITERATION_MAP,
    resolve_canonical_team_name,
    is_year_or_number_noise,
    DISTINGUISHING_MODIFIERS,
)

# Standard European city and transliteration aliases for cross-bookmaker team normalization
CITY_TRANSLATIONS: Dict[str, str] = dict(TRANSLITERATION_MAP)

# Common noise, sport club prefixes, and descriptor tokens
WEAK_TOKENS: Set[str] = {
    "fc", "cf", "sc", "ac", "fk", "sk", "ks", "gks", "if", "club", "clube", "clubul",
    "de", "la", "el", "the", "and", "vs", "of", "del", "le", "da", "do", "du", "d", "a",
    "city", "united", "town", "st", "saint", "afc", "sporting", "ca", "atletico", "athletic", "atletica",
    "msk", "tj", "ofk", "gnk", "hnk", "rnk", "nk", "mks", "zks", "lks", "ts",
    "ss", "as", "us", "cd", "ud", "sd", "rc", "rcd", "cs", "bsc", "vfb", "vfl",
    "tsv", "fsv", "spvgg", "sg", "sv", "rb", "bk", "ik", "ff", "aif", "dif",
    "hif", "gif", "uif", "olympique", "ec", "ad", "sad", "csd", "deportivo",
}

_RE_DELIM = re.compile(r"[-/.,_()\[\]'\"`~:;+*]")
_RE_NON_ALPHANUM = re.compile(r"[^a-z0-9\s]")


@functools.lru_cache(maxsize=16384)
def _strip_diacritics(text: str) -> str:
    """Removes diacritics and converts non-ASCII Latin characters deterministically."""
    text = text.translate(_CHAR_TRANS_TABLE)
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


@functools.lru_cache(maxsize=16384)
def normalize_team_name(raw_name: str) -> Tuple[str, Tuple[str, ...]]:
    """Deterministically normalizes a team name into a clean string and token tuple.

    Rules:
    1. Unicode NFKD diacritic removal (e.g. 'América' -> 'america', 'Atlético' -> 'atletico').
    2. Lowercase conversion.
    3. Punctuation to space conversion (e.g. 'Paris Saint-Germain' -> 'paris saint germain',
       'St.Louis' -> 'st louis', 'América Mineiro (MG)' -> 'america mineiro mg').
    4. Non-alphanumeric character removal (preserving ASCII letters, digits, and spaces).
    5. Consecutive whitespace collapsing.
    6. Strict preservation of identity suffixes (e.g. 'u19', 'ii', 'b', 'reserves', 'women').
    7. Multi-lingual city/transliteration synonym mapping (e.g. 'zagrzeb' -> 'zagreb', 'ateny' -> 'athens').

    Returns:
        (normalized_name, tokens_tuple)
    """
    if not raw_name or not raw_name.strip():
        return "", ()

    text = _strip_diacritics(raw_name)
    text = text.lower()

    # Convert common team delimiters and punctuation to spaces
    text = _RE_DELIM.sub(" ", text)

    # Keep only ASCII alphanumeric characters and whitespace
    text = _RE_NON_ALPHANUM.sub(" ", text)

    # Collapse multiple whitespace characters and map synonyms
    raw_tokens = text.split()
    translated_tokens = tuple(t_mapped for t in raw_tokens for t_mapped in [CITY_TRANSLATIONS.get(t, t)] if t_mapped)
    normalized_name = " ".join(translated_tokens)

    return normalized_name, translated_tokens


@functools.lru_cache(maxsize=4096)
def normalize_competition_name(raw_name: str) -> Tuple[str, Tuple[str, ...]]:
    """Deterministically normalizes a competition name into a clean string and token tuple.

    Rules:
    1. Unicode NFKD diacritic removal.
    2. Lowercase conversion.
    3. Punctuation to space conversion.
    4. Non-alphanumeric character removal.
    5. Whitespace collapsing and tokenization.

    Returns:
        (normalized_name, tokens_tuple)
    """
    if not raw_name or not raw_name.strip():
        return "", ()

    text = _strip_diacritics(raw_name)
    text = text.lower()

    # Convert delimiters and punctuation to spaces
    text = _RE_DELIM.sub(" ", text)

    # Keep only ASCII alphanumeric characters and whitespace
    text = _RE_NON_ALPHANUM.sub(" ", text)

    tokens = tuple(text.split())
    normalized_name = " ".join(tokens)

    return normalized_name, tokens


def parse_kickoff_to_utc(
    timestamp_str: Optional[str],
    default_tz: Optional[str] = None
) -> Optional[datetime]:
    """Parses a raw provider timestamp string into a timezone-aware UTC datetime.

    Supported formats:
    - ISO-8601 with UTC 'Z' (e.g. '2026-08-25T20:00:00Z')
    - ISO-8601 with offset (e.g. '2026-08-25T22:00:00+02:00' -> converted to UTC)
    - ISO-8601 without 'T' (e.g. '2026-08-16 21:30:00') when default_tz is 'UTC'

    Safety Invariant:
    - If a naive timestamp is provided without an explicit default_tz ('UTC'),
      it returns None to prevent guessing local machine timezones.

    Returns:
        timezone-aware datetime in UTC, or None if invalid/unparseable.
    """
    if not timestamp_str or not timestamp_str.strip():
        return None

    clean_str = timestamp_str.strip()

    # 1. Handle ISO-8601 ending with 'Z'
    if clean_str.endswith("Z"):
        try:
            # Replace Z with +00:00 for fromisoformat compatibility across Python versions
            iso_str = clean_str[:-1] + "+00:00"
            dt = datetime.fromisoformat(iso_str)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            pass

    # 2. Try standard fromisoformat (handles offsets like +02:00, -05:00)
    try:
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc)
        elif default_tz == "UTC":
            return dt.replace(tzinfo=timezone.utc)
        else:
            # Naive timestamp with unknown timezone - reject to prevent local guessing
            return None
    except (ValueError, TypeError):
        pass

    # 3. Handle 'YYYY-MM-DD HH:MM:SS' format
    try:
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M:%S")
        if default_tz == "UTC":
            return dt.replace(tzinfo=timezone.utc)
        return None
    except (ValueError, TypeError):
        pass

    return None


@dataclass(frozen=True)
class TeamReference:
    """Immutable provider-independent reference container for a sporting team."""
    raw_name: str
    normalized_name: str
    tokens: Tuple[str, ...]
    provider: Optional[str] = None
    provider_team_id: Optional[str] = None
    external_ids: Dict[str, str] = field(default_factory=dict)
    canonical_team_id: Optional[str] = None  # Unresolved at Stage 4.2

    @classmethod
    def from_raw(
        cls,
        raw_name: str,
        provider: Optional[str] = None,
        provider_team_id: Optional[str] = None,
        external_ids: Optional[Dict[str, str]] = None,
        canonical_team_id: Optional[str] = None,
    ) -> "TeamReference":
        """Factory creating a normalized TeamReference from a raw provider team name."""
        norm_name, tokens = normalize_team_name(raw_name)
        return cls(
            raw_name=raw_name,
            normalized_name=norm_name,
            tokens=tokens,
            provider=provider,
            provider_team_id=provider_team_id,
            external_ids=dict(external_ids or {}),
            canonical_team_id=canonical_team_id,
        )


@dataclass(frozen=True)
class CompetitionReference:
    """Immutable provider-independent reference container for a sports competition/league."""
    raw_name: str
    normalized_name: str
    tokens: Tuple[str, ...]
    sport: str = "Football"
    country: Optional[str] = None
    category_id: Optional[str] = None
    tournament_id: Optional[str] = None
    provider: Optional[str] = None
    provider_competition_id: Optional[str] = None
    external_ids: Dict[str, str] = field(default_factory=dict)
    canonical_comp_id: Optional[str] = None  # Unresolved at Stage 4.2

    @classmethod
    def from_raw(
        cls,
        raw_name: str,
        sport: str = "Football",
        country: Optional[str] = None,
        category_id: Optional[str] = None,
        tournament_id: Optional[str] = None,
        provider: Optional[str] = None,
        provider_competition_id: Optional[str] = None,
        external_ids: Optional[Dict[str, str]] = None,
        canonical_comp_id: Optional[str] = None,
    ) -> "CompetitionReference":
        """Factory creating a normalized CompetitionReference from raw competition metadata."""
        norm_name, tokens = normalize_competition_name(raw_name)
        return cls(
            raw_name=raw_name,
            normalized_name=norm_name,
            tokens=tokens,
            sport=sport,
            country=country,
            category_id=category_id,
            tournament_id=tournament_id,
            provider=provider,
            provider_competition_id=provider_competition_id,
            external_ids=dict(external_ids or {}),
            canonical_comp_id=canonical_comp_id,
        )





@dataclass(frozen=True)
class TeamComparison:
    """Deterministic comparison metrics between two TeamReference objects (Evidence only)."""
    exact_name: bool
    token_overlap: float  # Jaccard index: len(intersection) / len(union)
    provider_id_match: Optional[bool]
    external_id_match: Optional[bool]
    reasons: Tuple[str, ...]


def compare_teams(team_a: TeamReference, team_b: TeamReference) -> TeamComparison:
    """Computes deterministic comparison evidence between two TeamReferences without making merge decisions."""
    exact_name = (
        bool(team_a.normalized_name)
        and team_a.normalized_name == team_b.normalized_name
    )

    # 1. Canonical alias resolution
    canon_a_name, canon_a_tokens = resolve_canonical_team_name(team_a.normalized_name, team_a.tokens)
    canon_b_name, canon_b_tokens = resolve_canonical_team_name(team_b.normalized_name, team_b.tokens)
    if canon_a_name and canon_a_name == canon_b_name:
        exact_name = True

    set_a: Set[str] = set(canon_a_tokens)
    set_b: Set[str] = set(canon_b_tokens)
    union = set_a | set_b
    intersection = set_a & set_b

    token_overlap = 1.0 if exact_name else (len(intersection) / len(union) if union else 0.0)

    # 2. Distinguishing entity check (e.g. Manchester City vs Manchester United)
    mod_a = set_a & DISTINGUISHING_MODIFIERS
    mod_b = set_b & DISTINGUISHING_MODIFIERS
    has_conflicting_modifiers = bool(mod_a and mod_b and mod_a != mod_b)
    if has_conflicting_modifiers:
        token_overlap = 0.0

    # 3. Core significant tokens (filtering out common prefixes/descriptors in WEAK_TOKENS and year numbers)
    core_a = {t for t in set_a if t not in WEAK_TOKENS and not is_year_or_number_noise(t)}
    core_b = {t for t in set_b if t not in WEAK_TOKENS and not is_year_or_number_noise(t)}

    if not core_a:
        core_a = {t for t in set_a if t not in WEAK_TOKENS} or set_a
    if not core_b:
        core_b = {t for t in set_b if t not in WEAK_TOKENS} or set_b

    if core_a and core_b and not has_conflicting_modifiers:
        core_union = core_a | core_b
        core_intersection = core_a & core_b
        core_overlap = len(core_intersection) / len(core_union) if core_union else 0.0

        if core_intersection == core_a == core_b:
            token_overlap = max(token_overlap, 0.95)
        elif core_overlap >= 0.5:
            # Weighted blend of core overlap and full token overlap
            token_overlap = max(token_overlap, round(0.7 * core_overlap + 0.3 * token_overlap, 4))

    # Provider ID match is only meaningful if both come from the same provider
    provider_id_match: Optional[bool] = None
    if (
        team_a.provider
        and team_a.provider == team_b.provider
        and team_a.provider_team_id
        and team_b.provider_team_id
    ):
        provider_id_match = team_a.provider_team_id == team_b.provider_team_id

    # External ID match (e.g. Betradar / Sportradar team IDs)
    external_id_match: Optional[bool] = None
    common_ext_keys = set(team_a.external_ids.keys()) & set(team_b.external_ids.keys())
    if common_ext_keys:
        matches = [team_a.external_ids[k] == team_b.external_ids[k] for k in common_ext_keys]
        external_id_match = all(matches)

    reasons = []
    if exact_name:
        reasons.append("exact_normalized_name_match")
    if token_overlap > 0.0:
        reasons.append(f"token_jaccard_overlap_{round(token_overlap, 2)}")
    if provider_id_match is True:
        reasons.append("provider_team_id_match")
    elif provider_id_match is False:
        reasons.append("provider_team_id_conflict")
    if external_id_match is True:
        reasons.append("external_id_match")
    elif external_id_match is False:
        reasons.append("external_id_conflict")

    return TeamComparison(
        exact_name=exact_name,
        token_overlap=round(token_overlap, 4),
        provider_id_match=provider_id_match,
        external_id_match=external_id_match,
        reasons=tuple(reasons),
    )


class AliasResolver(ABC):
    """Abstract extension point for future team and competition alias resolution registries."""

    @abstractmethod
    def resolve_team(self, team_ref: TeamReference) -> Optional[str]:
        """Resolves team reference to canonical team ID if a verified alias exists."""
        pass

    @abstractmethod
    def resolve_competition(self, comp_ref: CompetitionReference) -> Optional[str]:
        """Resolves competition reference to canonical competition ID if a verified alias exists."""
        pass
