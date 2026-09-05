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
from datetime import datetime, timezone, timedelta
from datetime import tzinfo as _tzinfo_base
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

    Robustness (P1-NEW-001): non-string inputs never raise. Epoch int/float
    (seconds, or milliseconds when >1e12) and datetime objects are accepted;
    anything else yields None (unknown).

    Returns:
        timezone-aware datetime in UTC, or None if invalid/unparseable.
    """
    if timestamp_str is None:
        return None
    if isinstance(timestamp_str, datetime):
        try:
            if timestamp_str.tzinfo is None:
                if default_tz == "UTC":
                    return timestamp_str.replace(tzinfo=timezone.utc)
                return None
            return timestamp_str.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            return None
    if isinstance(timestamp_str, bool):
        return None
    if isinstance(timestamp_str, (int, float)):
        try:
            epoch = float(timestamp_str)
            if epoch > 1e12:
                epoch /= 1000.0
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except (ValueError, TypeError, OverflowError, OSError):
            return None
    if not isinstance(timestamp_str, str):
        return None
    if not timestamp_str.strip():
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

    # 4. Handle StatsHub-emitted 'YYYY-MM-DD HH:MM UTC' format (explicit UTC
    # suffix, e.g. '2026-09-01 18:00 UTC' from providers/statshub/parser.py).
    # Additive only: previously unparseable -> None callers are unaffected
    # except that genuinely-known kickoffs now compare correctly.
    try:
        if clean_str.endswith(" UTC"):
            dt = datetime.strptime(clean_str[:-4].strip(), "%Y-%m-%d %H:%M")
            return dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass

    # 5. Handle 'YYYY-MM-DD HH:MM' naive format (UTC only when explicitly allowed)
    try:
        dt = datetime.strptime(clean_str, "%Y-%m-%d %H:%M")
        if default_tz == "UTC":
            return dt.replace(tzinfo=timezone.utc)
        return None
    except (ValueError, TypeError):
        pass

    return None


#: Maximum kickoff separation for two observations to be the same fixture.
#: Mirrors the settlement layer's ±24h alignment window
#: (scanner/player_shots_settler.py) so valuation and settlement share one
#: time model instead of two.
FIXTURE_KICKOFF_COMPATIBILITY_WINDOW_HOURS = 24.0


def are_kickoffs_compatible(
    kickoff_a: Optional[str],
    kickoff_b: Optional[str],
    max_diff_hours: float = FIXTURE_KICKOFF_COMPATIBILITY_WINDOW_HOURS,
) -> Optional[bool]:
    """Determines whether two kickoff strings can describe the same fixture.

    Returns:
        True when both parse and differ by at most ``max_diff_hours``;
        False when both parse but differ by more;
        None when either side is missing/unparseable (unknown — the caller
        must fall back to names-only logic, never treat unknown as a match
        nor as a mismatch).
    """
    try:
        dt_a = parse_kickoff_to_utc(kickoff_a)
        dt_b = parse_kickoff_to_utc(kickoff_b)
    except Exception:
        return None
    if dt_a is None or dt_b is None:
        return None
    try:
        return abs((dt_a - dt_b).total_seconds()) <= max_diff_hours * 3600.0
    except Exception:
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


# ─────────────────────────────────────────────────────────────────────────────
# Europe/Warsaw timezone (P1-NEW-009)
# ─────────────────────────────────────────────────────────────────────────────
#
# Single source of truth for Warsaw wall-clock conversions (scheduler daily
# triggers, ultra-scan day boundaries, digest windows). Prefers IANA tzdata;
# the fallback implements the EU daylight-saving rule explicitly so slim
# containers without tzdata do NOT silently run winter schedules one hour
# late (the previous fixed +02:00 fallback did exactly that).

_WARSAW_TZ_CACHED: Optional[Any] = None
_WARSAW_TZ_FROM_FALLBACK: bool = False


class _EuropeWarsawFallbackTz(_tzinfo_base):
    """DST-aware Europe/Warsaw fallback for environments without tzdata.

    EU rule: CEST (+02:00) from the last Sunday of March 01:00 UTC until the
    last Sunday of October 01:00 UTC, otherwise CET (+01:00).
    """

    @staticmethod
    def _last_sunday(year: int, month: int) -> int:
        import calendar

        last_day = calendar.monthrange(year, month)[1]
        for day in range(last_day, 0, -1):
            if calendar.weekday(year, month, day) == 6:
                return day
        return last_day

    @classmethod
    def _is_dst_utc(cls, dt: datetime) -> bool:
        ref = dt
        try:
            if ref.tzinfo is not None:
                ref = (ref - ref.utcoffset()).replace(tzinfo=None)
        except Exception:
            return False
        year = ref.year
        try:
            dst_start = datetime(year, 3, cls._last_sunday(year, 3), 1, 0, 0)
            dst_end = datetime(year, 10, cls._last_sunday(year, 10), 1, 0, 0)
        except Exception:
            return False
        return dst_start <= ref < dst_end

    @classmethod
    def _is_ambiguous_wall(cls, wall: datetime) -> bool:
        """True for wall times occurring twice (autumn fallback hour)."""
        try:
            w = wall.replace(tzinfo=None)
            return (
                w.month == 10
                and w.day == cls._last_sunday(w.year, 10)
                and datetime(w.year, 10, w.day, 2, 0, 0) <= w < datetime(w.year, 10, w.day, 3, 0, 0)
            )
        except Exception:
            return False

    @classmethod
    def _is_dst_wall(cls, wall: datetime) -> bool:
        """DST decision from a naive wall time (fold-aware for ambiguous walls)."""
        w = wall.replace(tzinfo=None)
        if cls._is_ambiguous_wall(w):
            return getattr(wall, "fold", 0) == 0
        try:
            spring = datetime(w.year, 3, cls._last_sunday(w.year, 3), 3, 0, 0)
            autumn = datetime(w.year, 10, cls._last_sunday(w.year, 10), 3, 0, 0)
        except Exception:
            return False
        return spring <= w < autumn

    def utcoffset(self, dt: Optional[datetime]) -> Optional[timedelta]:
        if dt is None:
            return timedelta(hours=1)
        try:
            return timedelta(hours=2) if self._is_dst_wall(dt) else timedelta(hours=1)
        except Exception:
            return timedelta(hours=1)

    def dst(self, dt: Optional[datetime]) -> Optional[timedelta]:
        try:
            off = self.utcoffset(dt)
            return timedelta(hours=1) if off == timedelta(hours=2) else timedelta(0)
        except Exception:
            return timedelta(0)

    def tzname(self, dt: Optional[datetime]) -> str:
        try:
            return "CEST" if self.utcoffset(dt) == timedelta(hours=2) else "CET"
        except Exception:
            return "CET"

    def fromutc(self, dt: datetime) -> datetime:
        # Decide the offset from the UTC instant itself (unambiguous), not
        # from the wall time: the default tzinfo.fromutc re-evaluates the
        # offset at the converted wall time, which shifts the instant by one
        # hour inside the autumn ambiguous hour. fold marks which occurrence
        # a wall time in that hour represents (0 = CEST, 1 = CET).
        if dt.tzinfo is not self:
            raise ValueError("fromutc: dt.tzinfo is not self")
        try:
            utc_dt = dt.replace(tzinfo=timezone.utc)
            dst_now = self._is_dst_utc(utc_dt)
            off = timedelta(hours=2) if dst_now else timedelta(hours=1)
            wall = (utc_dt + off).replace(tzinfo=None)
            fold = 0
            if self._is_ambiguous_wall(wall):
                fold = 0 if dst_now else 1
            return wall.replace(tzinfo=self, fold=fold)
        except Exception:
            return dt.replace(tzinfo=self)


def get_warsaw_tz() -> Any:
    """Returns the Europe/Warsaw tzinfo (IANA when available, DST-aware fallback otherwise)."""
    global _WARSAW_TZ_CACHED, _WARSAW_TZ_FROM_FALLBACK
    if _WARSAW_TZ_CACHED is None:
        try:
            from zoneinfo import ZoneInfo

            _WARSAW_TZ_CACHED = ZoneInfo("Europe/Warsaw")
            _WARSAW_TZ_FROM_FALLBACK = False
        except Exception:
            _WARSAW_TZ_CACHED = _EuropeWarsawFallbackTz()
            _WARSAW_TZ_FROM_FALLBACK = True
    return _WARSAW_TZ_CACHED


def is_warsaw_tz_fallback() -> bool:
    """True when the Warsaw tzinfo currently in use is the DST fallback (no tzdata)."""
    get_warsaw_tz()
    return _WARSAW_TZ_FROM_FALLBACK


# ─────────────────────────────────────────────────────────────────────────────
# Team Identity Resolver (canonical team registry with provider bindings)
# ─────────────────────────────────────────────────────────────────────────────
#
# Restored production-shadowing contract: the resolver NEVER force-matches.
# Steps 1–2 consult trusted bindings; step 3 deterministically registers the
# canonical form (auto-bind for future O(1) lookup), so every non-empty input
# resolves while ambiguous/empty inputs stay explicit. Consumers that need
# recall gating (e.g. market matching) must use their own conservative rules.


@dataclass
class TeamResolutionResult:
    """Auditable outcome of a team identity resolution attempt."""
    status: Any
    canonical_team: Optional[Any] = None
    canonical_team_id: Optional[str] = None
    canonical_name: Optional[str] = None
    confidence: float = 0.0
    method: str = "UNRESOLVED"
    reasons: Tuple[str, ...] = ()


class TeamIdentityResolver:
    """Deterministic registry-based team identity resolver with provider bindings."""

    def __init__(self) -> None:
        self._canonical_teams: Dict[str, Any] = {}
        self._provider_bindings: Dict[Tuple[str, str], str] = {}
        self._external_bindings: Dict[Tuple[str, str], str] = {}

    # -- registry primitives -------------------------------------------------
    def register_canonical_team(self, team: Any) -> Any:
        self._canonical_teams[team.canonical_team_id] = team
        return team

    def bind_provider_team(self, canonical_team_id: str, provider: str, provider_team_id: str) -> None:
        if provider and provider_team_id:
            self._provider_bindings[(str(provider).lower(), str(provider_team_id))] = canonical_team_id

    # -- resolution ----------------------------------------------------------
    def resolve_team(self, team_ref: TeamReference) -> Optional[str]:
        """Resolves a reference to a canonical team ID, or None when unknown."""
        res = self.resolve_team_detailed(team_ref)
        return res.canonical_team_id

    def resolve_team_detailed(self, team_ref: TeamReference) -> TeamResolutionResult:
        """Resolves with full audit trail. Non-empty inputs always RESOLVE."""
        from normalization.player_identity import ResolutionStatus
        from domain.models import CanonicalTeam
        from domain.models import generate_deterministic_canonical_team_id

        norm_name = (team_ref.normalized_name or "").strip()
        provider = (team_ref.provider or "unknown").lower()
        prov_id = str(team_ref.provider_team_id) if team_ref.provider_team_id else None

        if not norm_name:
            return TeamResolutionResult(
                status=ResolutionStatus.UNRESOLVED,
                confidence=0.0,
                method="UNRESOLVED",
                reasons=("empty_team_name",),
            )

        # 1. Trusted provider-ID binding.
        if prov_id:
            bound_id = self._provider_bindings.get((provider, prov_id))
            if bound_id and bound_id in self._canonical_teams:
                bound = self._canonical_teams[bound_id]
                return TeamResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    canonical_team=bound,
                    canonical_team_id=bound.canonical_team_id,
                    canonical_name=bound.canonical_name,
                    confidence=1.0,
                    method="PROVIDER_BINDING",
                    reasons=(f"provider_team_id_bound:{provider}:{prov_id}",),
                )

        # 2. External-ID binding (e.g. Betradar / Sportradar).
        for ext_key, ext_val in (team_ref.external_ids or {}).items():
            bound_id = self._external_bindings.get((str(ext_key).lower(), str(ext_val)))
            if bound_id and bound_id in self._canonical_teams:
                bound = self._canonical_teams[bound_id]
                return TeamResolutionResult(
                    status=ResolutionStatus.RESOLVED,
                    canonical_team=bound,
                    canonical_team_id=bound.canonical_team_id,
                    canonical_name=bound.canonical_name,
                    confidence=1.0,
                    method="EXTERNAL_ID",
                    reasons=(f"external_team_id_bound:{ext_key}:{ext_val}",),
                )

        # 3. Deterministic canonical registration (never ambiguous: one input
        # name yields exactly one canonical form via alias resolution).
        canon_norm, _ = resolve_canonical_team_name(norm_name, team_ref.tokens)
        canon_id = generate_deterministic_canonical_team_id("football", canon_norm)
        team = self._canonical_teams.get(canon_id)
        if team is None:
            team = CanonicalTeam(
                canonical_team_id=canon_id,
                canonical_name=" ".join(t.capitalize() for t in canon_norm.split()) or norm_name,
                normalized_name=canon_norm,
                sport="Football",
            )
            self.register_canonical_team(team)
        if prov_id:
            self.bind_provider_team(canon_id, provider, prov_id)
        exact = canon_norm == norm_name
        return TeamResolutionResult(
            status=ResolutionStatus.RESOLVED,
            canonical_team=team,
            canonical_team_id=canon_id,
            canonical_name=team.canonical_name,
            confidence=0.98 if exact else 0.95,
            method="EXACT_NORMALIZED_NAME" if exact else "CANONICAL_ALIAS",
            reasons=("exact_normalized_name_match",) if exact else ("canonical_alias_registration",),
        )


_TEAM_RESOLVER_SINGLETON: Optional[TeamIdentityResolver] = None


def get_team_resolver() -> TeamIdentityResolver:
    """Returns the shared platform TeamIdentityResolver instance."""
    global _TEAM_RESOLVER_SINGLETON
    if _TEAM_RESOLVER_SINGLETON is None:
        _TEAM_RESOLVER_SINGLETON = TeamIdentityResolver()
    return _TEAM_RESOLVER_SINGLETON
