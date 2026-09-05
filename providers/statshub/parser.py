"""
StatsHub Response Parser Implementation
"""

import logging
from typing import Any, Dict, List, Optional, Union, Tuple

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


STAT_TYPE_MAP: Dict[str, str] = {
    "shots": "shots",
    "shot": "shots",
    "totalshots": "shots",
    "shotsontarget": "shots_on_target",
    "shots_on_target": "shots_on_target",
    "shotsongoal": "shots_on_target",
    "shots_on_goal": "shots_on_target",
    "sot": "shots_on_target",
    "goals": "goals",
    "goal": "goals",
    "assists": "assists",
    "assist": "assists",
    "passes": "passes",
    "pass": "passes",
    "tackles": "tackles",
    "tackle": "tackles",
    "fouls": "fouls",
    "foul": "fouls",
    "playerfouls": "fouls",
    "foulscommitted": "fouls",
    "wasfouled": "was_fouled",
    "was_fouled": "was_fouled",
    "foulswon": "was_fouled",
    "fouls_won": "was_fouled",
    "cards": "cards",
    "card": "cards",
    "yellowcards": "cards",
    "yellow_cards": "cards",
    "corners": "corners",
    "corner": "corners",
    "offsides": "offsides",
    "offside": "offsides",
}


class StatsHubParser:
    """Parser transforming raw StatsHub props and trends responses into structured domain models."""

    def parse_payload(self, raw_data: Union[Dict[str, Any], List[Any]], default_stat: str = "") -> List[StatsHubPropResult]:
        """Parse raw response from StatsHub /api/props/hunter or /api/props/player-trends into list of StatsHubPropResult."""
        if not raw_data:
            return []

        items: List[Dict[str, Any]] = []
        fixtures_map: Dict[str, Any] = {}

        if isinstance(raw_data, list):
            items = [item for item in raw_data if isinstance(item, dict)]
        elif isinstance(raw_data, dict):
            # Parse top-level fixtures map if present (Hunter format)
            if "fixtures" in raw_data and isinstance(raw_data["fixtures"], list):
                for fix in raw_data["fixtures"]:
                    if isinstance(fix, dict) and fix.get("id"):
                        fixtures_map[str(fix["id"])] = fix

            # Identify items container
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

        raw_payload_stat = default_stat
        if isinstance(raw_data, dict):
            if raw_data.get("stat"):
                raw_payload_stat = str(raw_data.get("stat")).strip()
            elif raw_data.get("statType"):
                raw_payload_stat = str(raw_data.get("statType")).strip()

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
        # 1. Player identity
        player_name = (
            item.get("playerName")
            or item.get("name")
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

        player_id = item.get("playerId") or item.get("id")
        player_slug = item.get("playerSlug")
        if not player_slug and item.get("slug") and "-vs-" not in str(item.get("slug")):
            player_slug = item.get("slug")

        # 2. Team & Opponent
        fixture_info = item.get("fixtureInfo") if isinstance(item.get("fixtureInfo"), dict) else {}
        matchup = item.get("matchup") if isinstance(item.get("matchup"), dict) else {}

        team_name = str(
            item.get("teamName")
            or item.get("team")
            or (item.get("playerInfo", {}) if isinstance(item.get("playerInfo"), dict) else {}).get("teamName")
            or fixture_info.get("homeTeamName")
            or ""
        ).strip()

        team_id = item.get("teamId") or (item.get("playerInfo", {}) if isinstance(item.get("playerInfo"), dict) else {}).get("teamId")
        team_slug = item.get("teamSlug") or (item.get("playerInfo", {}) if isinstance(item.get("playerInfo"), dict) else {}).get("teamSlug")

        opponent_name = str(
            matchup.get("opponentTeamName")
            or item.get("opponentTeamName")
            or item.get("opponentName")
            or item.get("opponent")
            or fixture_info.get("awayTeamName")
            or ""
        ).strip()

        opponent_team_id = item.get("opponentTeamId") or item.get("opponentId") or matchup.get("opponentTeamId")
        opponent_team_slug = item.get("opponentTeamSlug") or item.get("opponentSlug") or matchup.get("opponentTeamSlug")

        # 3. Fixture / Event Identity & Internal IDs
        fixture_id_val = (
            item.get("eventId")
            or fixture_info.get("id")
            or item.get("fixtureId")
            or item.get("matchId")
            or f"{team_name}_vs_{opponent_name}"
        )
        fixture_id = str(fixture_id_val)

        # Lookup top-level fixture metadata if available
        parent_fix = fixtures_map.get(fixture_id, {})

        event_internal_id = (
            item.get("eventInternalId")
            or fixture_info.get("internalId")
            or parent_fix.get("internalId")
            or item.get("internalId")
        )

        home_team_slug = (
            item.get("homeTeamSlug")
            or fixture_info.get("homeTeamSlug")
            or parent_fix.get("homeTeamSlug")
        )
        away_team_slug = (
            item.get("awayTeamSlug")
            or fixture_info.get("awayTeamSlug")
            or parent_fix.get("awayTeamSlug")
        )

        fixture_slug = (
            fixture_info.get("slug")
            or parent_fix.get("slug")
            or item.get("eventSlug")
            or item.get("fixtureSlug")
        )
        if not fixture_slug and item.get("slug") and ("-vs-" in str(item.get("slug")) or item.get("playerSlug")):
            fixture_slug = item.get("slug")
        if not fixture_slug and home_team_slug and away_team_slug:
            fixture_slug = f"{home_team_slug}-vs-{away_team_slug}"

        home_team_id = item.get("homeTeamId") or fixture_info.get("homeTeamId") or parent_fix.get("homeTeamId")
        away_team_id = item.get("awayTeamId") or fixture_info.get("awayTeamId") or parent_fix.get("awayTeamId")
        tournament_id = item.get("tournamentId") or fixture_info.get("tournamentId") or parent_fix.get("tournamentId")
        unique_tournament_id = item.get("uniqueTournamentId") or fixture_info.get("uniqueTournamentId")

        competition = str(
            item.get("leagueName")
            or fixture_info.get("tournamentName")
            or item.get("competitionName")
            or item.get("tournamentName")
            or item.get("league")
            or ""
        ).strip()

        raw_kickoff = (
            item.get("eventTimestamp")
            or fixture_info.get("startTime")
            or parent_fix.get("startTime")
            or item.get("kickoff")
            or item.get("startTime")
            or item.get("date")
        )

        kickoff_str = None
        if isinstance(raw_kickoff, (int, float)):
            from datetime import datetime, timezone
            try:
                kickoff_str = datetime.fromtimestamp(raw_kickoff, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            except Exception:
                kickoff_str = str(raw_kickoff)
        elif raw_kickoff:
            kickoff_str = str(raw_kickoff)

        venue = "home" if matchup.get("isHome") or fixture_info.get("isHome") or (item.get("homeTeamId") and team_id == item.get("homeTeamId")) else "away"
        home_team = item.get("homeTeamName") or fixture_info.get("homeTeamName") or parent_fix.get("homeTeamName") or (team_name if venue == "home" else opponent_name)
        away_team = item.get("awayTeamName") or fixture_info.get("awayTeamName") or parent_fix.get("awayTeamName") or (opponent_name if venue == "home" else team_name)

        fixture = StatsHubFixture(
            fixture_id=fixture_id,
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            kickoff=kickoff_str,
            venue=venue,
            event_internal_id=event_internal_id,
            slug=fixture_slug,
            home_team_slug=home_team_slug,
            away_team_slug=away_team_slug,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            tournament_id=tournament_id,
            unique_tournament_id=unique_tournament_id,
            metadata={"raw_fixture_info": fixture_info, "parent_fix": parent_fix},
        )

        # 4. Stat & Market detection
        stats_dict = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        averages_dict = item.get("averages") if isinstance(item.get("averages"), dict) else {}
        hit_rates_dict = item.get("hitRates") if isinstance(item.get("hitRates"), dict) else {}

        detected_stat = item.get("statType") or item.get("stat")
        if not detected_stat and stats_dict:
            detected_stat = next(iter(stats_dict.keys()), None)
        if not detected_stat and averages_dict:
            detected_stat = next(iter(averages_dict.keys()), None)
        if not detected_stat and hit_rates_dict:
            detected_stat = next(iter(hit_rates_dict.keys()), None)
        if not detected_stat:
            detected_stat = default_stat or "shots"

        raw_stat = str(detected_stat).strip()
        stat_type_clean = raw_stat.lower().replace("_", "").replace(" ", "")
        stat_type = STAT_TYPE_MAP.get(stat_type_clean, raw_stat.lower())

        market_name = item.get("marketName")
        odds_type = str(item.get("oddsType") or "over").lower()
        target_line = float(item.get("line") or item.get("threshold") or item.get("statThreshold") or 0.5)

        position = str(item.get("position") or "").upper()
        minutes_played = int(item.get("minutesPlayed") or item.get("minutes") or 0)
        substituted_in = bool(item.get("substitutedIn") or item.get("isSub", False))

        # Statistical metrics
        stat_value = int(stats_dict.get(raw_stat) or stats_dict.get(stat_type) or item.get("statValue") or item.get("value") or 0)
        average = float(averages_dict.get(raw_stat) or averages_dict.get(stat_type) or item.get("average") or item.get("avg") or item.get("statAvg") or item.get("trendAvg") or 0.0)
        hit_rate_pct = float(hit_rates_dict.get(raw_stat) or hit_rates_dict.get(stat_type) or item.get("hitRatePct") or item.get("hitRate") or 0.0)

        # Trends specific metadata
        trend_hits = int(item["trendHits"]) if "trendHits" in item and item["trendHits"] is not None else None
        trend_window = int(item["trendWindow"]) if "trendWindow" in item and item["trendWindow"] is not None else None
        trend_total = int(item["trendTotal"]) if "trendTotal" in item and item["trendTotal"] is not None else None
        trend_avg = float(item["trendAvg"]) if "trendAvg" in item and item["trendAvg"] is not None else (average if average > 0 else None)

        opponent_rank = int(item["opponentRank"]) if "opponentRank" in item and item["opponentRank"] is not None else None
        total_ranks = int(item["totalRanks"]) if "totalRanks" in item and item["totalRanks"] is not None else None
        league_average = float(item["leagueAverage"]) if "leagueAverage" in item and item["leagueAverage"] is not None else None
        opponent_average = float(item["opponentAverage"]) if "opponentAverage" in item and item["opponentAverage"] is not None else None

        p90_obj = item.get("p90")
        p90 = float(p90_obj.get(raw_stat) or p90_obj.get(stat_type) or next(iter(p90_obj.values()))) if isinstance(p90_obj, dict) and p90_obj else (float(p90_obj) if isinstance(p90_obj, (int, float)) else None)

        # Recent games / sample size
        recent_games = item.get("recentGames") or item.get("historicalMatches") or item.get("lastGamesData") or []
        sample_size = int(
            item.get("sampleSize")
            or item.get("sample")
            or trend_window
            or (len(recent_games) if recent_games else item.get("count") or 10)
        )
        hit_rate_count = int(
            item.get("hitRateCount")
            or trend_hits
            or item.get("hits")
            or round(hit_rate_pct * sample_size / 100.0)
        ) if sample_size > 0 else 0

        # Calculate hit_rate_pct if only trend_hits/window present
        if hit_rate_pct == 0.0 and trend_hits is not None and trend_window and trend_window > 0:
            hit_rate_pct = round((trend_hits / trend_window) * 100.0, 1)

        # Calculate splits
        last_5_avg = float(item["last5Avg"]) if "last5Avg" in item and item["last5Avg"] is not None else None
        last_10_avg = float(item["last10Avg"]) if "last10Avg" in item and item["last10Avg"] is not None else None
        last_15_avg = float(item["last15Avg"]) if "last15Avg" in item and item["last15Avg"] is not None else None

        # 5. Historical matches parsing
        historical_matches: List[StatsHubHistoricalMatch] = []
        if isinstance(recent_games, list):
            for m in recent_games:
                if isinstance(m, dict):
                    ev = m.get("event") if isinstance(m.get("event"), dict) else {}
                    ps = m.get("playerStats") if isinstance(m.get("playerStats"), dict) else {}

                    opp_name = (
                        m.get("opponentName")
                        or (ev.get("awayTeamName") if ev.get("homeTeamName") == team_name else ev.get("homeTeamName"))
                        or str(m.get("opponent") or "")
                    )
                    opp_slug = m.get("opponentSlug")
                    opp_id = m.get("opponentId")

                    match_ts = m.get("eventTimestamp") or m.get("timestamp") or ev.get("timeStartTimestamp")
                    date_str = ""
                    if match_ts:
                        from datetime import datetime, timezone
                        try:
                            date_str = datetime.fromtimestamp(match_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        except Exception:
                            date_str = str(match_ts)

                    # Extract match stat value specifically for this stat type
                    stat_keys = [stat_type, raw_stat, raw_stat.lower(), stat_type.lower()]
                    if stat_type == "shots_on_target":
                        stat_keys.extend(["shotsOnTarget", "shotsontarget", "shotsOnGoal", "shots_on_goal", "sot", "shots_on_target"])
                    elif stat_type == "fouls":
                        stat_keys.extend(["fouls", "foul", "playerFouls", "foulsCommitted"])
                    elif stat_type == "was_fouled":
                        stat_keys.extend(["wasFouled", "foulsWon", "fouls_won", "was_fouled"])
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

                    is_home_val = m.get("isHome")
                    if is_home_val is None and ev.get("homeTeamName"):
                        is_home_val = (ev.get("homeTeamName") == team_name)

                    home_away_str = "H" if is_home_val else "A"
                    is_hit_val = m.get("isHit")
                    if is_hit_val is None and m_stat_val is not None:
                        if odds_type == "under":
                            is_hit_val = (float(m_stat_val) < target_line)
                        else:
                            is_hit_val = (float(m_stat_val) > target_line)

                    historical_matches.append(
                        StatsHubHistoricalMatch(
                            opponent=opp_name,
                            opponent_slug=opp_slug,
                            opponent_id=opp_id,
                            date=date_str,
                            timestamp=int(match_ts) if match_ts else None,
                            minutes_played=int(ps.get("minutesPlayed") or m.get("minutesPlayed") or m.get("minutes") or 0),
                            stat_value=int(m_stat_val or 0),
                            home_away=home_away_str,
                            started=(ps.get("substitutedIn") is None and not m.get("hasSuperSub", False)),
                            competition=str(ev.get("roundSlug") or m.get("competition") or ""),
                            is_hit=is_hit_val,
                            has_super_sub=m.get("hasSuperSub"),
                            event_id=m.get("eventId") or ev.get("id"),
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

        # 6. Bookmaker odds parsing & multi-bookmaker preservation
        raw_bookmaker_odds: List[StatsHubBookmakerOdds] = []

        # A. Parse Trends API format: item['bookmakers'] = [{"bookmakerId": 2, "bookmakerName": "Bet365", "oddsValue": 1.615}]
        if "bookmakers" in item and isinstance(item["bookmakers"], list):
            for b in item["bookmakers"]:
                if isinstance(b, dict):
                    b_name = normalize_bookmaker_name(str(b.get("bookmakerName") or b.get("name") or "Unknown"))
                    b_price = float(b.get("oddsValue") or b.get("odds") or 0.0)
                    b_id = b.get("bookmakerId") or b.get("id")
                    if b_price > 1.0:
                        raw_bookmaker_odds.append(
                            StatsHubBookmakerOdds(
                                bookmaker=b_name,
                                line=target_line,
                                side=odds_type,
                                decimal_odds=round(b_price, 4),
                                bookmaker_id=int(b_id) if b_id is not None else None,
                                metadata={"raw": b},
                            )
                        )

        # B. Parse oddsByLine dictionary (Hunter format)
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
                            b_price = float(o.get("oddsValue") or o.get("odds") or 0.0)
                            b_id = o.get("bookmakerId") or o.get("id")
                            if b_price > 1.0:
                                raw_bookmaker_odds.append(
                                    StatsHubBookmakerOdds(
                                        bookmaker=b_name,
                                        line=line_val,
                                        side="over",
                                        decimal_odds=round(b_price, 4),
                                        bookmaker_id=int(b_id) if b_id is not None else None,
                                        metadata={"raw": o},
                                    )
                                )
                    # Process under
                    for o in side_data.get("under", []):
                        if isinstance(o, dict):
                            b_name = normalize_bookmaker_name(str(o.get("bookmakerName") or o.get("name") or "Unknown"))
                            b_price = float(o.get("oddsValue") or o.get("odds") or 0.0)
                            b_id = o.get("bookmakerId") or o.get("id")
                            if b_price > 1.0:
                                raw_bookmaker_odds.append(
                                    StatsHubBookmakerOdds(
                                        bookmaker=b_name,
                                        line=line_val,
                                        side="under",
                                        decimal_odds=round(b_price, 4),
                                        bookmaker_id=int(b_id) if b_id is not None else None,
                                        metadata={"raw": o},
                                    )
                                )

        # C. Fallback: Parse bookmakerOdds / odds array or dict
        if not raw_bookmaker_odds:
            raw_odds = item.get("bookmakerOdds") or item.get("odds") or item.get("lines")
            if isinstance(raw_odds, list):
                for o in raw_odds:
                    if isinstance(o, dict):
                        bookmaker_name = normalize_bookmaker_name(str(o.get("bookmaker") or o.get("bookie") or o.get("name") or "Unknown"))
                        line_val = float(o.get("line") or o.get("threshold") or item.get("statThreshold") or target_line)
                        side_val = str(o.get("side") or o.get("overUnder") or odds_type).lower()
                        decimal_val = float(o.get("odds") or o.get("decimalOdds") or o.get("price") or o.get("oddsValue") or 0.0)
                        b_id = o.get("bookmakerId") or o.get("id")

                        if decimal_val > 1.0:
                            raw_bookmaker_odds.append(
                                StatsHubBookmakerOdds(
                                    bookmaker=bookmaker_name,
                                    line=line_val,
                                    side=side_val,
                                    decimal_odds=round(decimal_val, 4),
                                    bookmaker_id=int(b_id) if b_id is not None else None,
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
                                    line=target_line,
                                    side=odds_type,
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
            team=team_name,
            opponent=opponent_name,
            fixture=fixture,
            stat_type=stat_type,
            player_id=player_id,
            player_slug=player_slug,
            team_id=team_id,
            team_slug=team_slug,
            opponent_team_id=opponent_team_id,
            opponent_team_slug=opponent_team_slug,
            market_name=market_name,
            odds_type=odds_type,
            line=target_line,
            position=position,
            minutes_played=minutes_played,
            substituted_in=substituted_in,
            stat_value=stat_value,
            average=average,
            hit_rate_count=hit_rate_count,
            sample_size=sample_size,
            hit_rate_pct=hit_rate_pct,
            trend_hits=trend_hits,
            trend_window=trend_window,
            trend_total=trend_total,
            trend_avg=trend_avg,
            opponent_rank=opponent_rank,
            total_ranks=total_ranks,
            league_average=league_average,
            opponent_average=opponent_average,
            p90=p90,
            last_5_avg=last_5_avg,
            last_10_avg=last_10_avg,
            last_15_avg=last_15_avg,
            historical_matches=historical_matches,
            bookmaker_odds=bookmaker_odds,
            raw_data=item,
        )

        available_lines = sorted(list({o.line for o in bookmaker_odds}))
        if target_line not in available_lines:
            available_lines.append(target_line)
            available_lines.sort()

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
