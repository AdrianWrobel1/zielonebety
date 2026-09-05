"""
Canonical Domain Models for Zielone Bety Platform
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any, Tuple, Set
import hashlib
import itertools
import time
from datetime import datetime, timezone

_id_counter = itertools.count(1)
_last_ts_second: int = 0
_cached_iso_ts: str = ""


def _get_current_iso_ts() -> str:
    """Return cached ISO timestamp string, refreshed once per second."""
    global _last_ts_second, _cached_iso_ts
    current_sec = int(time.time())
    if current_sec != _last_ts_second:
        _last_ts_second = current_sec
        _cached_iso_ts = datetime.fromtimestamp(current_sec, tz=timezone.utc).isoformat()
    return _cached_iso_ts


def generate_canonical_id(prefix: str = "") -> str:
    """Generate permanent unique internal canonical identifier."""
    raw_id = str(next(_id_counter))
    if prefix:
        return f"{prefix}_{raw_id}"
    return raw_id


@dataclass
class Competition:
    name: str
    sport: str = "Football"
    country: str = "International"
    season: Optional[str] = None
    internal_id: str = field(default_factory=lambda: generate_canonical_id("comp"))
    provider_ids: Dict[str, str] = field(default_factory=dict)
    external_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)


@dataclass
class Event:
    competition_id: str
    home_participant: str
    away_participant: str
    scheduled_start: Optional[str] = None
    status: str = "SCHEDULED"
    internal_id: str = field(default_factory=lambda: generate_canonical_id("ev"))
    provider_ids: Dict[str, str] = field(default_factory=dict)
    external_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)


@dataclass
class Market:
    event_id: str
    market_type: str
    line: Optional[float] = None
    status: str = "OPEN"
    internal_id: str = field(default_factory=lambda: generate_canonical_id("mkt"))
    provider_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)


@dataclass
class Selection:
    market_id: str
    selection_type: str
    line: Optional[float] = None
    participant: Optional[str] = None
    canonical_participant_id: Optional[str] = None
    internal_id: str = field(default_factory=lambda: generate_canonical_id("sel"))
    provider_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)


@dataclass(frozen=True)
class Odds:
    """Immutable Odds Snapshot."""
    selection_id: str
    bookmaker: str
    decimal_odds: float
    timestamp: str = field(default_factory=_get_current_iso_ts)
    internal_id: str = field(default_factory=lambda: generate_canonical_id("odds"))


def _normalize_kickoff_for_identity(scheduled_start_utc: Optional[str]) -> Optional[str]:
    """Normalizes a kickoff string for canonical event identity (P1-NEW-002).

    - Parses ISO-8601 (tolerates trailing 'Z', numeric offsets, naive UTC).
    - Truncates seconds/microseconds and floors minutes to a 15-minute grid
      so harmless provider jitter (seconds, small provider-time skew,
      earliest-of-subset differences within one bucket) cannot fork identity.
    - Returns ``None`` when kickoff is missing or unparseable; callers must
      keep such identities explicitly dateless (never silently coarsen).
    """
    if not scheduled_start_utc:
        return None
    raw = str(scheduled_start_utc).strip()
    if not raw:
        return None
    try:
        iso = raw.replace("Z", "+00:00") if raw.endswith(("Z", "z")) else raw
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        bucket_min = (dt.minute // 15) * 15
        floored = dt.replace(minute=bucket_min, second=0, microsecond=0)
        return floored.strftime("%Y-%m-%dT%H:%M:00Z")
    except (ValueError, TypeError, OverflowError):
        return None


def generate_deterministic_canonical_event_id(
    sport: str,
    home_team_norm: str,
    away_team_norm: str,
    scheduled_start_utc: Optional[str] = None,
    competition_norm: Optional[str] = None,
) -> str:
    """Generate a deterministic, provider-independent canonical event identifier.

    Derivation (identity scheme v2, P1-NEW-002):
    - sport (lowercased)
    - normalized home participant name
    - normalized away participant name (orientation preserved: home/away
      semantics are required by TEAM roles/participant logic, so teams are
      NOT sorted; swapped-orientation provider pairs still aggregate into a
      single component/ID within one scan via ORIENTATION_SWAP matching)
    - kickoff floored to a 15-minute UTC bucket (or 'no_start')
    - competition slug included ONLY in the dateless branch so missing
      kickoff cannot silently collapse unrelated fixtures across competitions
    Format: cev_<16-char sha256 hex digest>

    Migration scope: dated IDs change once versus the previous exact-second
    scheme (one-time expiry/re-alert bounded by max_misses=2); dateless IDs
    change only when a competition context is supplied.
    """
    sport_clean = (sport or "football").strip().lower()
    home_clean = home_team_norm.strip().lower()
    away_clean = away_team_norm.strip().lower()
    bucket = _normalize_kickoff_for_identity(scheduled_start_utc)
    if bucket is not None:
        start_clean = bucket
    elif competition_norm and str(competition_norm).strip():
        comp_clean = str(competition_norm).strip().lower()
        start_clean = f"no_start:{comp_clean}"
    else:
        start_clean = "no_start"

    key_str = f"v2:{sport_clean}:{home_clean}:{away_clean}:{start_clean}"
    digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    return f"cev_{digest}"


def generate_deterministic_canonical_player_id(
    sport: str,
    player_name_norm: str,
    team_context: Optional[str] = None,
) -> str:
    """Generate a deterministic, provider-independent canonical player identifier.

    Derivation:
    - sport (lowercased)
    - normalized player name
    - optional team context (e.g. normalized team name)
    Format: cplr_<16-char sha256 hex digest>
    """
    sport_clean = (sport or "football").strip().lower()
    player_clean = (player_name_norm or "").strip().lower()
    team_clean = (team_context or "").strip().lower()
    key_str = f"{sport_clean}:{player_clean}"
    if team_clean:
        key_str = f"{key_str}:{team_clean}"
    digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    return f"cplr_{digest}"


def generate_deterministic_canonical_team_id(
    sport: str,
    team_name_norm: str,
) -> str:
    """Generate a deterministic, provider-independent canonical team identifier.

    Derivation:
    - sport (lowercased)
    - normalized team name
    Format: cteam_<16-char sha256 hex digest>
    """
    sport_clean = (sport or "football").strip().lower()
    team_clean = (team_name_norm or "").strip().lower()
    key_str = f"{sport_clean}:{team_clean}"
    digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    return f"cteam_{digest}"


def generate_deterministic_canonical_competition_id(
    sport: str,
    comp_name_norm: str,
    country: Optional[str] = None,
) -> str:
    """Generate a deterministic, provider-independent canonical competition identifier.

    Derivation:
    - sport (lowercased)
    - normalized competition name
    - country (lowercased)
    Format: ccomp_<16-char sha256 hex digest>
    """
    sport_clean = (sport or "football").strip().lower()
    comp_clean = (comp_name_norm or "").strip().lower()
    country_clean = (country or "international").strip().lower()
    key_str = f"{sport_clean}:{comp_clean}:{country_clean}"
    digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    return f"ccomp_{digest}"


@dataclass
class CanonicalPlayer:
    """Provider-independent canonical sports player identity."""
    canonical_player_id: str
    canonical_name: str
    normalized_name: str
    sport: str = "Football"
    team_name: Optional[str] = None
    canonical_team_id: Optional[str] = None
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    provider_player_ids: Dict[str, str] = field(default_factory=dict)
    external_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)


@dataclass
class CanonicalTeam:
    """Provider-independent canonical sports team identity."""
    canonical_team_id: str
    canonical_name: str
    normalized_name: str
    sport: str = "Football"
    country: Optional[str] = None
    aliases: Tuple[str, ...] = field(default_factory=tuple)
    provider_team_ids: Dict[str, str] = field(default_factory=dict)
    external_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)


@dataclass
class EventSource:
    """Represents a single provider's contribution and lineage to a CanonicalEvent."""
    provider: str
    provider_event_id: str
    internal_event_id: str
    home_participant: str
    away_participant: str
    scheduled_start: Optional[str] = None
    competition_name: Optional[str] = None
    provider_competition_id: Optional[str] = None
    external_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_orientation_swapped: bool = False
    original_event: Optional[Any] = None


@dataclass
class MatchEvidence:
    """Auditable preservation of Stage 4.4 match decision and decomposed signals."""
    source_provider: str
    target_provider: str
    source_event_id: str
    target_event_id: str
    decision: str
    total_score: float
    orientation: str
    signals: Dict[str, Any] = field(default_factory=dict)
    veto_reasons: Tuple[str, ...] = field(default_factory=tuple)
    warnings: Tuple[str, ...] = field(default_factory=tuple)
    blocking_keys: Tuple[str, ...] = field(default_factory=tuple)
    evidence: Dict[str, Any] = field(default_factory=dict)
    home_team: Optional[str] = None
    away_team: Optional[str] = None
    competition_name: Optional[str] = None
    start_time: Optional[str] = None


@dataclass
class CanonicalCompetition:
    """Canonical representation of a competition across aggregated sources."""
    name: str
    sport: str = "Football"
    country: Optional[str] = None
    competition_id: Optional[str] = None
    canonical_comp_id: Optional[str] = None
    competition_type: Optional[str] = None
    tier: int = 2
    provenance: str = "PROVIDER_METADATA"
    confidence: float = 1.0
    provider_competition_ids: Dict[str, str] = field(default_factory=dict)
    external_ids: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.canonical_comp_id and self.competition_id:
            self.canonical_comp_id = self.competition_id
        elif not self.competition_id and self.canonical_comp_id:
            self.competition_id = self.canonical_comp_id


@dataclass
class CanonicalEvent:
    """Provider-independent aggregated canonical sporting event."""
    canonical_event_id: str
    sport: str
    home_team: str
    away_team: str
    canonical_home_team_id: Optional[str] = None
    canonical_away_team_id: Optional[str] = None
    canonical_competition_id: Optional[str] = None
    scheduled_start: Optional[str] = None
    competition: Optional[CanonicalCompetition] = None
    sources: Dict[str, EventSource] = field(default_factory=dict)
    match_evidence: List[MatchEvidence] = field(default_factory=list)
    status: str = "SCHEDULED"
    provenance: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)


def generate_deterministic_player_prop_id(
    event_id: str,
    player_name_norm: str,
    stat_type: str,
    line: float,
    side: str,
    period: str = "FULL_TIME",
) -> str:
    """Generate a collision-safe deterministic player prop canonical identifier.

    Derivation:
    - event_id
    - normalized player name
    - stat_type (lowercased)
    - line (e.g. 0.5, 1.5)
    - side (OVER / UNDER)
    - period (e.g. FULL_TIME)
    Format: cpp_<16-char sha256 hex digest>
    """
    import hashlib
    ev_clean = (event_id or "").strip().lower()
    pl_clean = (player_name_norm or "").strip().lower()
    st_clean = (stat_type or "shots").strip().lower()
    line_clean = f"{float(line):.1f}"
    side_clean = (side or "over").strip().upper()
    period_clean = (period or "FULL_TIME").strip().upper()

    key_str = f"{ev_clean}:{pl_clean}:{st_clean}:{line_clean}:{side_clean}:{period_clean}"
    digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    return f"cpp_{digest}"


@dataclass
class PlayerPropMarket:
    """Canonical representation of a player proposition market (e.g., Over 0.5 Shots)."""
    event_id: str
    player_name: str
    team_name: str
    opponent_name: str
    stat_type: str
    line: float
    side: str = "OVER"
    period: str = "FULL_TIME"
    status: str = "OPEN"
    internal_id: str = field(default_factory=lambda: generate_canonical_id("ppm"))
    canonical_prop_id: str = ""
    hit_rate_pct: float = 0.0
    hit_rate_count: int = 0
    sample_size: int = 0
    stat_average: float = 0.0
    last_5_avg: Optional[float] = None
    last_10_avg: Optional[float] = None
    last_15_avg: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)

    def __post_init__(self):
        if not self.canonical_prop_id:
            self.canonical_prop_id = generate_deterministic_player_prop_id(
                event_id=self.event_id,
                player_name_norm=self.player_name,
                stat_type=self.stat_type,
                line=self.line,
                side=self.side,
                period=self.period,
            )


@dataclass(frozen=True)
class PlayerPropOdds:
    """Immutable Bookmaker Odds for a canonical PlayerPropMarket (Reference Data)."""
    prop_market_id: str
    bookmaker: str
    decimal_odds: float
    source: str = "statshub"
    timestamp: str = field(default_factory=_get_current_iso_ts)
    internal_id: str = field(default_factory=lambda: generate_canonical_id("pp_odds"))


def generate_deterministic_team_prop_id(
    event_id: str,
    team_name_norm: str,
    stat_type: str,
    line: float,
    side: str,
    participant_role: str = "HOME",
    period: str = "FULL_TIME",
) -> str:
    """Generate a collision-safe deterministic team prop canonical identifier.

    Derivation:
    - event_id
    - normalized team name
    - stat_type (lowercased)
    - line (e.g. 1.5, 4.5)
    - side (OVER / UNDER)
    - participant_role (HOME / AWAY)
    - period (e.g. FULL_TIME)
    Format: ctp_<16-char sha256 hex digest>
    """
    import hashlib
    ev_clean = (event_id or "").strip().lower()
    tm_clean = (team_name_norm or "").strip().lower()
    st_clean = (stat_type or "shots").strip().lower()
    line_clean = f"{float(line):.1f}"
    side_clean = (side or "over").strip().upper()
    role_clean = (participant_role or "HOME").strip().upper()
    period_clean = (period or "FULL_TIME").strip().upper()

    key_str = f"{ev_clean}:{tm_clean}:{st_clean}:{line_clean}:{side_clean}:{role_clean}:{period_clean}"
    digest = hashlib.sha256(key_str.encode("utf-8")).hexdigest()[:16]
    return f"ctp_{digest}"


@dataclass
class TeamPropMarket:
    """Canonical representation of a team proposition market (e.g., Arsenal Over 4.5 Corners)."""
    event_id: str
    team_name: str
    opponent_name: str
    stat_type: str
    line: float
    side: str = "OVER"
    participant_role: str = "HOME"
    period: str = "FULL_TIME"
    status: str = "OPEN"
    internal_id: str = field(default_factory=lambda: generate_canonical_id("tpm"))
    canonical_prop_id: str = ""
    hit_rate_pct: float = 0.0
    hit_rate_count: int = 0
    sample_size: int = 0
    stat_average: float = 0.0
    last_5_avg: Optional[float] = None
    last_10_avg: Optional[float] = None
    last_15_avg: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_get_current_iso_ts)
    updated_at: str = field(default_factory=_get_current_iso_ts)

    def __post_init__(self):
        if not self.canonical_prop_id:
            self.canonical_prop_id = generate_deterministic_team_prop_id(
                event_id=self.event_id,
                team_name_norm=self.team_name,
                stat_type=self.stat_type,
                line=self.line,
                side=self.side,
                participant_role=self.participant_role,
                period=self.period,
            )


@dataclass(frozen=True)
class TeamPropOdds:
    """Immutable Bookmaker Odds for a canonical TeamPropMarket (Reference Data)."""
    prop_market_id: str
    bookmaker: str
    decimal_odds: float
    source: str = "statshub"
    timestamp: str = field(default_factory=_get_current_iso_ts)
    internal_id: str = field(default_factory=lambda: generate_canonical_id("tp_odds"))



