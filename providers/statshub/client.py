"""
StatsHub HTTP Client Implementation
"""

import json
import logging
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Union
import requests

from providers.statshub.config import StatsHubConfig
from providers.statshub.constants import STATSHUB_BASE_URL, STATSHUB_PROVIDER_NAME

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
    """HTTP Client for StatsHub Props Hunter API."""

    def __init__(self, config: Optional[StatsHubConfig] = None, session: Optional[requests.Session] = None):
        self.config = config or StatsHubConfig()
        self.session = session or requests.Session()
        self._cached_discovery_context: Optional[Dict[str, Any]] = None

    def discover_active_context(self, days_ahead: Optional[int] = None) -> Dict[str, Any]:
        """Discover active startOfDay, endOfDay and tournament IDs from StatsHub homepage."""
        horizon_days = days_ahead if days_ahead is not None else getattr(self.config, "days_ahead", 7)
        if self._cached_discovery_context and self._cached_discovery_context.get("_horizon_days") == horizon_days:
            return self._cached_discovery_context

        try:
            r = self.session.get("https://www.statshub.com/", headers=self.config.headers, timeout=self.config.request_timeout)
            if r.status_code == 200:
                match = re.search(r'<script id="__NEXT_DATA__" type="application/json">([^<]+)</script>', r.text)
                if match:
                    nd = json.loads(match.group(1))
                    pp = nd.get("props", {}).get("pageProps", {})
                    s_start = pp.get("serverStartOfDay")

                    init_events = pp.get("initialEvents", {}).get("data", [])
                    t_ids = set()
                    for ev in init_events:
                        t = ev.get("tournaments", {}) or ev.get("tournament", {})
                        if isinstance(t, dict) and t.get("id"):
                            t_ids.add(str(t["id"]))
                        if ev.get("events", {}).get("tournamentId"):
                            t_ids.add(str(ev["events"]["tournamentId"]))

                    if s_start and t_ids:
                        computed_end = int(s_start) + (max(1, horizon_days) * 86400) - 1
                        self._cached_discovery_context = {
                            "startOfDay": int(s_start),
                            "endOfDay": computed_end,
                            "tournaments": ",".join(sorted(list(t_ids))),
                            "_horizon_days": horizon_days,
                        }
                        logger.info(f"Discovered {len(t_ids)} active tournaments from StatsHub homepage (horizon: {horizon_days} days).")
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
            "_horizon_days": horizon_days,
        }
        return self._cached_discovery_context

    def fetch_props(self, config_override: Optional[StatsHubConfig] = None) -> Union[Dict[str, Any], list]:
        """Fetch player props data from StatsHub API."""
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
        url = cfg.base_url or STATSHUB_BASE_URL
        headers = cfg.headers

        logger.info(f"StatsHub requesting: {url} with stat={cfg.stat}, page={cfg.page}, limit={cfg.limit}")

        last_exception: Optional[Exception] = None
        retries = max(1, cfg.max_retries)

        for attempt in range(1, retries + 1):
            try:
                response = self.session.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=cfg.request_timeout,
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
                last_exception = StatsHubTimeoutError(f"Timeout after {cfg.request_timeout}s: {timeout_err}")
                if attempt < retries:
                    time.sleep(1.0 * attempt)
                    continue
            except requests.RequestException as req_err:
                logger.error(f"StatsHub network error: {req_err}")
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
