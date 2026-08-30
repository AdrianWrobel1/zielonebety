"""
StatsHub Response Parser Implementation
"""

import logging
from typing import Any, Dict, List, Optional, Union

from providers.statshub.models import (
    StatsHubFixture,
    StatsHubHistoricalMatch,
    StatsHubBookmakerOdds,
    StatsHubPlayerStat,
    StatsHubPropResult,
)

logger = logging.getLogger("providers.statshub.parser")


BOOKMAKER_NAME_MAP: Dict[str, str] = {
    "bet365": "Bet365",
    "paddy power": "Paddy Power",
    "skybet": "Skybet",
    "william hill": "William Hill",
    "betfred": "Betfred",
    "ladbrokes": "Ladbrokes",
    "boylesports": "BoyleSports",
    "betvictor": "BetVictor",
    "betmgm uk": "BetMGM UK",
    "10bet": "10bet",
    "kambi": "Kambi",
    "unibet": "Unibet",
    "all british casino": "All British Casino",
    "superbet": "Superbet",
    "betclic": "Betclic",
}


def normalize_bookmaker_name(raw_name: str) -> str:
    """Normalize bookmaker string into standardized title/display name."""
    if not raw_name:
        return "Unknown"
    cleaned = raw_name.strip()
    lowered = cleaned.lower()
    return BOOKMAKER_NAME_MAP.get(lowered, cleaned)


class StatsHubParser:
    """Parser transforming raw StatsHub props response into structured domain models."""

    def parse_payload(self, raw_data: Union[Dict[str, Any], List[Any]]) -> List[StatsHubPropResult]:
        """Parse raw response from StatsHub /api/props/hunter into list of StatsHubPropResult."""
        if not raw_data:
            return []

        items: List[Dict[str, Any]] = []
        fixtures_map: Dict[str, Any] = {}

        if isinstance(raw_data, list):
            items = [item for item in raw_data if isinstance(item, dict)]
        elif isinstance(raw_data, dict):
            # Parse top-level fixtures if present
            if "fixtures" in raw_data and isinstance(raw_data["fixtures"], list):
                for fix in raw_data["fixtures"]:
                    if isinstance(fix, dict) and fix.get("id"):
                        fixtures_map[str(fix["id"])] = fix

            # StatsHub primary key is 'players'
            if "players" in raw_data and isinstance(raw_data["players"], list):
                items = raw_data["players"]
            elif "data" in raw_data and isinstance(raw_data["data"], list):
                items = raw_data["data"]
            elif "items" in raw_data and isinstance(raw_data["items"], list):
                items = raw_data["items"]
            elif "props" in raw_data and isinstance(raw_data["props"], list):
                items = raw_data["props"]
            elif "results" in raw_data and isinstance(raw_data["results"], list):
                items = raw_data["results"]
            elif "name" in raw_data or "playerName" in raw_data or "id" in raw_data:
                items = [raw_data]

        raw_payload_stat = ""
        if isinstance(raw_data, dict) and raw_data.get("stat"):
            raw_payload_stat = str(raw_data.get("stat")).strip()

        results: List[StatsHubPropResult] = []
        for item in items:
            try:
                prop_result = self._parse_single_prop_item(item, fixtures_map, default_stat=raw_payload_stat)
                if prop_result:
                    results.append(prop_result)
            except Exception as e:
                logger.warning(f"Error parsing StatsHub prop item: {e}", exc_info=False)
                continue

        logger.info(f"StatsHub parsed {len(results)} player prop items successfully.")
        return results

    def _parse_single_prop_item(
        self,
        item: Dict[str, Any],
        fixtures_map: Dict[str, Any],
        default_stat: str = "",
    ) -> Optional[StatsHubPropResult]:
        """Extract player, fixture, stats, and odds from a single item dict."""
        player_name = (
            item.get("name")
            or item.get("playerName")
            or item.get("player")
            or (item.get("playerInfo", {}) if isinstance(item.get("playerInfo"), dict) else {}).get("name")
            or ""
        ).strip()

        if not player_name:
            p_obj = item.get("player")
            if isinstance(p_obj, dict):
                player_name = (p_obj.get("name") or p_obj.get("fullName") or "").strip()

        if not player_name:
            return None

        # Team and Opponent
        fixture_info = item.get("fixtureInfo") if isinstance(item.get("fixtureInfo"), dict) else {}
        matchup = item.get("matchup") if isinstance(item.get("matchup"), dict) else {}

        team = str(
            item.get("teamName")
            or item.get("team")
            or (item.get("playerInfo", {}) if isinstance(item.get("playerInfo"), dict) else {}).get("teamName")
            or fixture_info.get("homeTeamName")
            or ""
        ).strip()

        opponent = str(
            matchup.get("opponentTeamName")
            or item.get("opponentName")
            or item.get("opponent")
            or fixture_info.get("awayTeamName")
            or ""
        ).strip()

        # Fixture / Match info
        fixture_id = str(
            fixture_info.get("id")
            or item.get("fixtureId")
            or item.get("eventId")
            or item.get("matchId")
            or f"{team}_vs_{opponent}"
        )

        competition = str(
            fixture_info.get("tournamentName")
            or item.get("competitionName")
            or item.get("tournamentName")
            or item.get("league")
            or ""
        ).strip()

        kickoff = fixture_info.get("startTime") or item.get("kickoff") or item.get("startTime") or item.get("date")
        if isinstance(kickoff, (int, float)):
            from datetime import datetime, timezone
            try:
                kickoff = datetime.fromtimestamp(kickoff, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                kickoff = str(kickoff)

        venue = "home" if matchup.get("isHome") or fixture_info.get("isHome") else "away"

        home_team = fixture_info.get("homeTeamName") or (team if venue == "home" else opponent)
        away_team = fixture_info.get("awayTeamName") or (opponent if venue == "home" else team)

        fixture = StatsHubFixture(
            fixture_id=fixture_id,
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            kickoff=str(kickoff) if kickoff else None,
            venue=venue,
            metadata={"fixtureInfo": fixture_info},
        )

        # Determine stat key: item['stat'] / item['statType'] -> stats dict keys -> default_stat -> 'shots'
        stats_dict = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        averages_dict = item.get("averages") if isinstance(item.get("averages"), dict) else {}
        hit_rates_dict = item.get("hitRates") if isinstance(item.get("hitRates"), dict) else {}

        detected_stat = item.get("stat") or item.get("statType")
        if not detected_stat and stats_dict:
            detected_stat = next(iter(stats_dict.keys()), None)
        if not detected_stat and averages_dict:
            detected_stat = next(iter(averages_dict.keys()), None)
        if not detected_stat and hit_rates_dict:
            detected_stat = next(iter(hit_rates_dict.keys()), None)
        if not detected_stat:
            detected_stat = default_stat or "shots"

        raw_stat = str(detected_stat).strip()
        stat_type_map = {
            "shots": "shots",
            "shotsontarget": "shots_on_target",
            "shots_on_target": "shots_on_target",
            "goals": "goals",
            "assists": "assists",
            "passes": "passes",
            "tackles": "tackles",
            "fouls": "fouls",
            "cards": "cards",
            "corners": "corners",
            "offsides": "offsides",
        }
        stat_type = stat_type_map.get(raw_stat.lower().replace("_", "").replace(" ", ""), raw_stat.lower())
        position = str(item.get("position") or "").upper()
        minutes_played = int(item.get("minutesPlayed") or item.get("minutes") or 0)
        substituted_in = bool(item.get("substitutedIn") or item.get("isSub", False))

        # Stats metrics from dicts or direct numbers
        stats_dict = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        stat_value = int(stats_dict.get(raw_stat) or stats_dict.get(stat_type) or item.get("statValue") or item.get("value") or 0)

        averages_dict = item.get("averages") if isinstance(item.get("averages"), dict) else {}
        average = float(averages_dict.get(raw_stat) or averages_dict.get(stat_type) or item.get("average") or item.get("avg") or item.get("statAvg") or 0.0)

        hit_rates_dict = item.get("hitRates") if isinstance(item.get("hitRates"), dict) else {}
        hit_rate_pct = float(hit_rates_dict.get(raw_stat) or hit_rates_dict.get(stat_type) or item.get("hitRatePct") or item.get("hitRate") or 0.0)

        # Recent games / sample size
        recent_games = item.get("recentGames") or item.get("historicalMatches") or item.get("lastGamesData") or []
        sample_size = int(item.get("sampleSize") or item.get("sample") or (len(recent_games) if recent_games else item.get("count") or 10))
        hit_rate_count = int(item.get("hitRateCount") or item.get("hits") or round(hit_rate_pct * sample_size / 100.0)) if sample_size > 0 else 0

        # Calculate splits
        last_5_avg = float(item["last5Avg"]) if "last5Avg" in item and item["last5Avg"] is not None else None
        last_10_avg = float(item["last10Avg"]) if "last10Avg" in item and item["last10Avg"] is not None else None
        last_15_avg = float(item["last15Avg"]) if "last15Avg" in item and item["last15Avg"] is not None else None

        # Historical matches
        historical_matches: List[StatsHubHistoricalMatch] = []
        if isinstance(recent_games, list):
            for m in recent_games:
                if isinstance(m, dict):
                    ev = m.get("event") if isinstance(m.get("event"), dict) else {}
                    ps = m.get("playerStats") if isinstance(m.get("playerStats"), dict) else {}

                    opp_name = ev.get("awayTeamName") if ev.get("homeTeamName") == team else ev.get("homeTeamName") or str(m.get("opponent") or "")
                    match_ts = m.get("timestamp") or ev.get("timeStartTimestamp")
                    date_str = ""
                    if match_ts:
                        from datetime import datetime, timezone
                        try:
                            date_str = datetime.fromtimestamp(match_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        except Exception:
                            date_str = str(match_ts)

                    # Extract match stat value specifically for this stat type without accidental fallbacks
                    stat_keys = [stat_type, raw_stat, raw_stat.lower(), stat_type.lower()]
                    if stat_type == "shots_on_target":
                        stat_keys.extend(["shotsOnTarget", "shotsontarget", "sot", "shots_on_target"])
                    elif stat_type == "fouls":
                        stat_keys.extend(["fouls", "foul", "playerFouls", "foulsCommitted"])
                    elif stat_type == "cards":
                        stat_keys.extend(["cards", "card", "yellowCards", "playerCards", "yellow_cards"])
                    elif stat_type == "assists":
                        stat_keys.extend(["assists", "assist", "playerAssists"])
                    elif stat_type == "goals":
                        stat_keys.extend(["goals", "goal", "playerGoals"])
                    elif stat_type == "passes":
                        stat_keys.extend(["passes", "pass", "playerPasses", "totalPasses"])
                    elif stat_type == "tackles":
                        stat_keys.extend(["tackles", "tackle", "playerTackles"])
                    elif stat_type == "shots":
                        stat_keys.extend(["shots", "totalShots", "playerShots"])

                    m_stat_val = None
                    for k in stat_keys:
                        if k in ps and ps[k] is not None:
                            m_stat_val = ps[k]
                            break
                    if m_stat_val is None:
                        m_stat_val = m.get("statValue") or m.get("value")

                    historical_matches.append(
                        StatsHubHistoricalMatch(
                            opponent=opp_name,
                            date=date_str,
                            minutes_played=int(ps.get("minutesPlayed") or m.get("minutes") or 0),
                            stat_value=int(m_stat_val or 0),
                            home_away="H" if ev.get("homeTeamName") == team else "A",
                            started=(ps.get("substitutedIn") is None),
                            competition=str(ev.get("roundSlug") or m.get("competition") or ""),
                        )
                    )

        # Compute L5 / L10 averages from recent matches if not directly provided
        if historical_matches:
            if last_5_avg is None and len(historical_matches) >= 1:
                l5_matches = historical_matches[:5]
                last_5_avg = round(sum(m.stat_value for m in l5_matches) / len(l5_matches), 2)
            if last_10_avg is None and len(historical_matches) >= 1:
                l10_matches = historical_matches[:10]
                last_10_avg = round(sum(m.stat_value for m in l10_matches) / len(l10_matches), 2)

        # Bookmaker odds parsing & deterministic deduplication
        raw_bookmaker_odds: List[StatsHubBookmakerOdds] = []

        # 1. Parse oddsByLine dictionary (e.g. {"0.5": {"over": [{"bookmakerName": "Bet365", "oddsValue": 1.833}], "under": []}})
        odds_by_line = item.get("oddsByLine")
        if isinstance(odds_by_line, dict):
            for line_key, side_data in odds_by_line.items():
                try:
                    line_val = float(line_key)
                except ValueError:
                    continue

                if isinstance(side_data, dict):
                    # Process over
                    for o in side_data.get("over", []):
                        if isinstance(o, dict):
                            b_name = normalize_bookmaker_name(str(o.get("bookmakerName") or o.get("name") or "Unknown"))
                            b_price = float(o.get("oddsValue") or o.get("odds") or 1.0)
                            if b_price > 1.0:
                                raw_bookmaker_odds.append(
                                    StatsHubBookmakerOdds(
                                        bookmaker=b_name,
                                        line=line_val,
                                        side="over",
                                        decimal_odds=round(b_price, 4),
                                    )
                                )
                    # Process under
                    for o in side_data.get("under", []):
                        if isinstance(o, dict):
                            b_name = normalize_bookmaker_name(str(o.get("bookmakerName") or o.get("name") or "Unknown"))
                            b_price = float(o.get("oddsValue") or o.get("odds") or 1.0)
                            if b_price > 1.0:
                                raw_bookmaker_odds.append(
                                    StatsHubBookmakerOdds(
                                        bookmaker=b_name,
                                        line=line_val,
                                        side="under",
                                        decimal_odds=round(b_price, 4),
                                    )
                                )

        # 2. Fallback: Parse bookmakerOdds / odds array or dict
        if not raw_bookmaker_odds:
            raw_odds = item.get("bookmakerOdds") or item.get("odds") or item.get("lines")
            if isinstance(raw_odds, list):
                for o in raw_odds:
                    if isinstance(o, dict):
                        bookmaker_name = normalize_bookmaker_name(str(o.get("bookmaker") or o.get("bookie") or o.get("name") or "Unknown"))
                        line_val = float(o.get("line") or o.get("threshold") or item.get("statThreshold") or 0.5)
                        side_val = str(o.get("side") or o.get("overUnder") or "over").lower()
                        decimal_val = float(o.get("odds") or o.get("decimalOdds") or o.get("price") or 1.0)

                        if decimal_val > 1.0:
                            raw_bookmaker_odds.append(
                                StatsHubBookmakerOdds(
                                    bookmaker=bookmaker_name,
                                    line=line_val,
                                    side=side_val,
                                    decimal_odds=round(decimal_val, 4),
                                    metadata={"raw": o},
                                )
                            )
            elif isinstance(raw_odds, dict):
                for b_name, price in raw_odds.items():
                    try:
                        if isinstance(price, (int, float)) and float(price) > 1.0:
                            raw_bookmaker_odds.append(
                                StatsHubBookmakerOdds(
                                    bookmaker=normalize_bookmaker_name(str(b_name)),
                                    line=float(item.get("statThreshold") or 0.5),
                                    side="over",
                                    decimal_odds=round(float(price), 4),
                                )
                            )
                    except Exception:
                        pass

        # Deterministic deduplication: preserve unique (bookmaker, line, side) keeping highest odds
        dedup_map: Dict[Tuple[str, float, str], StatsHubBookmakerOdds] = {}
        for o in raw_bookmaker_odds:
            key = (o.bookmaker.lower(), round(o.line, 2), o.side.lower())
            if key not in dedup_map or o.decimal_odds > dedup_map[key].decimal_odds:
                dedup_map[key] = o
        bookmaker_odds: List[StatsHubBookmakerOdds] = list(dedup_map.values())

        player_stat = StatsHubPlayerStat(
            player_name=player_name,
            team=team,
            opponent=opponent,
            fixture=fixture,
            stat_type=stat_type,
            position=position,
            minutes_played=minutes_played,
            substituted_in=substituted_in,
            stat_value=stat_value,
            average=average,
            hit_rate_count=hit_rate_count,
            sample_size=sample_size,
            hit_rate_pct=hit_rate_pct,
            last_5_avg=last_5_avg,
            last_10_avg=last_10_avg,
            last_15_avg=last_15_avg,
            historical_matches=historical_matches,
            bookmaker_odds=bookmaker_odds,
            raw_data=item,
        )

        available_lines = sorted(list({o.line for o in bookmaker_odds}))
        best_odds_by_line: Dict[str, StatsHubBookmakerOdds] = {}
        for o in bookmaker_odds:
            key = f"{o.side}_{o.line}"
            if key not in best_odds_by_line or o.decimal_odds > best_odds_by_line[key].decimal_odds:
                best_odds_by_line[key] = o

        return StatsHubPropResult(
            player_stat=player_stat,
            available_lines=available_lines,
            best_odds_by_line=best_odds_by_line,
            total_bookmaker_count=len({o.bookmaker for o in bookmaker_odds}),
        )
