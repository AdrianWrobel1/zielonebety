"""
StatsHub HTTP Client Implementation
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Union
import requests

from providers.statshub.config import StatsHubConfig
from providers.statshub.constants import (
    STATSHUB_BASE_URL,
    STATSHUB_HUNTER_ENDPOINT,
    STATSHUB_PLAYER_TRENDS_ENDPOINT,
    STATSHUB_TEAM_TRENDS_ENDPOINT,
    STATSHUB_PROVIDER_NAME,
)

logger = logging.getLogger("providers.statshub.client")


class StatsHubClientError(Exception):
    """Base exception for StatsHub HTTP client errors."""
    pass


class StatsHubHTTPError(StatsHubClientError):
    """HTTP status error (e.g. 403, 429, 5xx)."""
    def __init__(self, status_code: int, message: str):
        super().__init__(f"StatsHub HTTP {status_code}: {message}")
        self.status_code = status_code


class StatsHubTimeoutError(StatsHubClientError):
    """Request timeout error."""
    pass


class StatsHubMalformedResponseError(StatsHubClientError):
    """Malformed or non-JSON response error."""
    pass


class StatsHubClient:
    """HTTP Client for StatsHub Props Hunter and Trends APIs."""

    def __init__(self, config: Optional[StatsHubConfig] = None, session: Optional[requests.Session] = None):
        self.config = config or StatsHubConfig()
        self.session = session or requests.Session()
        self._cached_discovery_context: Optional[Dict[str, Any]] = None
        self._cached_discovery_context_ts: float = 0.0

    # P1-NEW-007: homepage tournaments/fixtures change intraday; a lifetime
    # cache would serve day-old tournament sets to a long-running process.
    DISCOVERY_CONTEXT_TTL_SECONDS = 3600.0

    def discover_active_context(self, days_ahead: Optional[int] = None) -> Dict[str, Any]:
        """Discover active startOfDay, endOfDay and tournament IDs from StatsHub homepage."""
        horizon_days = days_ahead if days_ahead is not None else getattr(self.config, "days_ahead", 7)
        now_ts = time.time()
        if (
            self._cached_discovery_context
            and self._cached_discovery_context.get("_horizon_days") == horizon_days
            and (now_ts - (self._cached_discovery_context_ts or 0.0)) < self.DISCOVERY_CONTEXT_TTL_SECONDS
        ):
            return self._cached_discovery_context

        try:
            from orchestration.profiler import get_current_scan_profiler
            profiler = get_current_scan_profiler()
            req_start_rel = profiler.elapsed_seconds if profiler else 0.0

            r = self.session.get("https://www.statshub.com/", headers=self.config.headers, timeout=self.config.request_timeout)

            req_end_rel = profiler.elapsed_seconds if profiler else 0.0
            if profiler:
                profiler.record_request(
                    provider="statshub",
                    endpoint_category="homepage_context",
                    worker_id="statshub-client",
                    start_rel_s=req_start_rel,
                    end_rel_s=req_end_rel,
                    http_status=r.status_code,
                    success=(r.status_code == 200),
                    bytes_received=len(r.content) if hasattr(r, "content") and r.content else 0,
                )

            if r.status_code == 200:
                match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', r.text)
                if match:
                    nd = json.loads(match.group(1))
                    pp = nd.get("props", {}).get("pageProps", {})
                    s_start = pp.get("serverStartOfDay")

                    init_events = pp.get("initialEvents", {}).get("data", [])
                    t_ids = set()
                    fixtures_list = []
                    for ev in init_events:
                        t = ev.get("tournaments", {}) or ev.get("tournament", {})
                        if isinstance(t, dict) and t.get("id"):
                            t_ids.add(str(t["id"]))
                        if ev.get("events", {}).get("tournamentId"):
                            t_ids.add(str(ev["events"]["tournamentId"]))

                        e_obj = ev.get("events", {}) if isinstance(ev.get("events"), dict) else {}
                        fid = str(e_obj.get("id") or "")
                        ht = (ev.get("homeTeam", {}) if isinstance(ev.get("homeTeam"), dict) else {}).get("name", "")
                        at = (ev.get("awayTeam", {}) if isinstance(ev.get("awayTeam"), dict) else {}).get("name", "")
                        comp = (ev.get("tournaments", {}) if isinstance(ev.get("tournaments"), dict) else {}).get("name") or (ev.get("tournament", {}) if isinstance(ev.get("tournament"), dict) else {}).get("name") or ""
                        ko = e_obj.get("timeStartTimestamp")
                        st = e_obj.get("status")
                        if fid and ht and at and st not in ("finished", "canceled"):
                            fixtures_list.append({
                                "fixture_id": fid,
                                "home_team": ht,
                                "away_team": at,
                                "competition": comp,
                                "kickoff": ko,
                            })

                    if s_start and t_ids:
                        computed_end = int(s_start) + (max(1, horizon_days) * 86400) - 1
                        self._cached_discovery_context = {
                            "startOfDay": int(s_start),
                            "endOfDay": computed_end,
                            "tournaments": ",".join(sorted(list(t_ids))),
                            "fixtures": fixtures_list,
                            "_horizon_days": horizon_days,
                        }
                        logger.info(f"Discovered {len(t_ids)} active tournaments and {len(fixtures_list)} fixtures from StatsHub homepage (horizon: {horizon_days} days).")
                        self._cached_discovery_context_ts = time.time()
                        return self._cached_discovery_context
        except Exception as e:
            logger.warning(f"StatsHub homepage discovery failed: {e}")

        # Fallback to computing today's UTC bounds and standard top tournaments
        now = datetime.now(timezone.utc)
        start_ts = int(datetime(now.year, now.month, now.day, 0, 0, 0, tzinfo=timezone.utc).timestamp())
        end_ts = int(start_ts + (max(1, horizon_days) * 86400) - 1)
        fallback_tournaments = "1,12,19,24,27,28,33,36,37,40,43,52,53,62,83,102,219,232,280,877,1449,3625,3708,3938,5071,16887,26540,64475,156467,160191,189591"

        self._cached_discovery_context = {
            "startOfDay": start_ts,
            "endOfDay": end_ts,
            "tournaments": fallback_tournaments,
            "fixtures": [],
            "_horizon_days": horizon_days,
        }
        self._cached_discovery_context_ts = time.time()
        return self._cached_discovery_context

    def discover_upcoming_fixtures(self, days_ahead: Optional[int] = None) -> List[Dict[str, Any]]:
        """Discover upcoming active fixtures with IDs from StatsHub homepage."""
        ctx = self.discover_active_context(days_ahead=days_ahead)
        return ctx.get("fixtures", [])

    def _execute_request(
        self,
        url: str,
        params: Dict[str, str],
        cfg: StatsHubConfig,
    ) -> Union[Dict[str, Any], list]:
        """Execute a resilient HTTP GET request with retries, timeouts, and error mappings."""
        headers = cfg.headers
        retries = max(1, cfg.max_retries)
        last_exception: Optional[Exception] = None

        logger.info(f"StatsHub requesting: {url} with params={params}")

        from orchestration.profiler import get_current_scan_profiler
        profiler = get_current_scan_profiler()
        endpoint_cat = "hunter" if "hunter" in url else ("team_trends" if "team-trends" in url else ("player_trends" if "player-trends" in url else "statshub_api"))
        if profiler:
            profiler.register_worker("statshub-client", provider="statshub", role="trends_client")

        for attempt in range(1, retries + 1):
            req_start_rel = profiler.elapsed_seconds if profiler else 0.0
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=cfg.request_timeout,
                )
                req_end_rel = profiler.elapsed_seconds if profiler else 0.0
                resp_bytes = len(response.content) if hasattr(response, "content") and response.content else 0

                if profiler:
                    profiler.record_request(
                        provider="statshub",
                        endpoint_category=endpoint_cat,
                        worker_id="statshub-client",
                        start_rel_s=req_start_rel,
                        end_rel_s=req_end_rel,
                        http_status=response.status_code,
                        retry_count=attempt - 1,
                        success=(response.status_code == 200),
                        bytes_received=resp_bytes,
                    )

                if response.status_code == 200:
                    if not response.text or not response.text.strip():
                        logger.warning("StatsHub returned empty response body.")
                        return {}
                    try:
                        data = response.json()
                        return data
                    except ValueError as json_err:
                        raise StatsHubMalformedResponseError(f"Failed to parse JSON response: {json_err}") from json_err

                elif response.status_code in (429, 500, 502, 503, 504):
                    logger.warning(f"StatsHub temporary failure HTTP {response.status_code} (attempt {attempt}/{retries})")
                    if attempt < retries:
                        time.sleep(1.0 * attempt)
                        continue
                    raise StatsHubHTTPError(response.status_code, response.text[:200])
                else:
                    raise StatsHubHTTPError(response.status_code, response.text[:200])

            except requests.Timeout as timeout_err:
                logger.warning(f"StatsHub request timed out (attempt {attempt}/{retries})")
                if profiler:
                    req_end_rel = profiler.elapsed_seconds
                    profiler.record_request(
                        provider="statshub",
                        endpoint_category=endpoint_cat,
                        worker_id="statshub-client",
                        start_rel_s=req_start_rel,
                        end_rel_s=req_end_rel,
                        http_status=0,
                        retry_count=attempt - 1,
                        success=False,
                        error_type="Timeout",
                        bytes_received=0,
                    )
                last_exception = StatsHubTimeoutError(f"Timeout after {cfg.request_timeout}s: {timeout_err}")
                if attempt < retries:
                    time.sleep(1.0 * attempt)
                    continue
            except requests.RequestException as req_err:
                logger.error(f"StatsHub network error: {req_err}")
                if profiler:
                    req_end_rel = profiler.elapsed_seconds
                    profiler.record_request(
                        provider="statshub",
                        endpoint_category=endpoint_cat,
                        worker_id="statshub-client",
                        start_rel_s=req_start_rel,
                        end_rel_s=req_end_rel,
                        http_status=0,
                        retry_count=attempt - 1,
                        success=False,
                        error_type=type(req_err).__name__,
                        bytes_received=0,
                    )
                last_exception = StatsHubClientError(f"Request failed: {req_err}")
                if attempt < retries:
                    time.sleep(1.0 * attempt)
                    continue
            except StatsHubMalformedResponseError:
                raise
            except StatsHubHTTPError:
                raise

        if last_exception:
            raise last_exception
        return {}

    def fetch_props(self, config_override: Optional[StatsHubConfig] = None) -> Union[Dict[str, Any], list]:
        """Fetch player props data from StatsHub Hunter API (/api/props/hunter)."""
        cfg = config_override or self.config
        if not cfg.enabled:
            logger.info("StatsHub is disabled via configuration.")
            return {}

        # Ensure required startOfDay, endOfDay, and tournaments are present
        if not cfg.start_of_day or not cfg.end_of_day or not cfg.tournaments:
            context = self.discover_active_context(days_ahead=cfg.days_ahead)
            if not cfg.start_of_day:
                cfg.start_of_day = context.get("startOfDay")
            if not cfg.end_of_day:
                cfg.end_of_day = context.get("endOfDay")
            if not cfg.tournaments:
                cfg.tournaments = context.get("tournaments", "")

        params = cfg.build_query_params()
        url = cfg.base_url or STATSHUB_HUNTER_ENDPOINT
        return self._execute_request(url=url, params=params, cfg=cfg)

    def fetch_player_trends(
        self,
        games: Optional[Union[str, List[Union[str, int]]]] = None,
        player_id: Optional[Union[str, int]] = None,
        unique_tournament_id: Optional[Union[str, int]] = None,
        config_override: Optional[StatsHubConfig] = None,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], list]:
        """Fetch player trends data from StatsHub Trends API (/api/props/player-trends)."""
        cfg = config_override or self.config
        if not cfg.enabled:
            logger.info("StatsHub is disabled via configuration.")
            return {}

        # Format games parameter
        games_str = ""
        if games is not None:
            if isinstance(games, list):
                games_str = ",".join(str(g) for g in games if str(g).strip())
            else:
                games_str = str(games).strip()
        elif cfg.games:
            games_str = str(cfg.games).strip()

        p_id = player_id if player_id is not None else cfg.player_id
        tourn_id = unique_tournament_id if unique_tournament_id is not None else cfg.unique_tournament_id

        if not games_str and p_id is None and tourn_id is None:
            raise StatsHubClientError("Either event IDs (games), a playerId, or a uniqueTournamentId is required for player trends")

        # Build query parameters
        params = cfg.build_trends_query_params()
        if games_str:
            params["games"] = games_str
        if p_id is not None:
            params["playerId"] = str(p_id)
        if tourn_id is not None:
            params["uniqueTournamentId"] = str(tourn_id)

        # Allow ad-hoc overrides in kwargs
        for k, v in kwargs.items():
            if v is not None:
                params[k] = str(v)

        url = STATSHUB_PLAYER_TRENDS_ENDPOINT
        return self._execute_request(url=url, params=params, cfg=cfg)

    def fetch_team_trends(
        self,
        games: Optional[Union[str, List[Union[str, int]]]] = None,
        config_override: Optional[StatsHubConfig] = None,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], list]:
        """Fetch team trends data from StatsHub Team Trends API (/api/props/team-trends)."""
        cfg = config_override or self.config
        if not cfg.enabled:
            logger.info("StatsHub is disabled via configuration.")
            return {}

        games_str = ""
        if games is not None:
            if isinstance(games, list):
                games_str = ",".join(str(g) for g in games if str(g).strip())
            else:
                games_str = str(games).strip()
        elif cfg.games:
            games_str = str(cfg.games).strip()

        if not games_str:
            raise StatsHubClientError("Event IDs (games) are required for team trends")

        params = cfg.build_trends_query_params()
        params["games"] = games_str

        for k, v in kwargs.items():
            if v is not None:
                params[k] = str(v)

        url = STATSHUB_TEAM_TRENDS_ENDPOINT
        return self._execute_request(url=url, params=params, cfg=cfg)

