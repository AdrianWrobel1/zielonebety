"""
StatsHub Team Props Response Parser Implementation
"""

import logging
from typing import Any, Dict, List, Optional, Union, Set, Tuple

from providers.statshub.parser import normalize_bookmaker_name
from providers.statshub.team_models import (
    StatsHubTeamFixture,
    StatsHubTeamHistoricalMatch,
    StatsHubTeamBookmakerOdds,
    StatsHubTeamStat,
    StatsHubTeamPropResult,
)

logger = logging.getLogger("providers.statshub.team_parser")


STAT_ALIAS_MAP: Dict[str, str] = {
    "shots": "shots",
    "shot": "shots",
    "totalshots": "shots",
    "shotsontarget": "shots_on_target",
    "shots_on_target": "shots_on_target",
    "shotsongoal": "shots_on_target",
    "shots_on_goal": "shots_on_target",
    "sot": "shots_on_target",
    "corners": "corners",
    "corner": "corners",
    "cornerkicks": "corners",
    "corner_kicks": "corners",
    "totalshotsongoal": "shots",
    "total_shots_on_goal": "shots",
    "cards": "cards",
    "card": "cards",
    "yellowcards": "cards",
    "yellow_cards": "cards",
    "fouls": "fouls",
    "foul": "fouls",
    "goals": "goals",
    "goal": "goals",
    "offsides": "offsides",
    "offside": "offsides",
    "passes": "passes",
    "pass": "passes",
}


class StatsHubTeamParser:
    """Parser transforming raw StatsHub team props and trends responses into structured domain models."""

    def parse_payload(self, raw_data: Union[Dict[str, Any], List[Any]], default_stat: str = "") -> List[StatsHubTeamPropResult]:
        """Parse raw response into list of StatsHubTeamPropResult."""
        if not raw_data:
            return []

        items: List[Dict[str, Any]] = []
        fixtures_map: Dict[str, Any] = {}

        if isinstance(raw_data, list):
            items = [item for item in raw_data if isinstance(item, dict)]
        elif isinstance(raw_data, dict):
            if "fixtures" in raw_data and isinstance(raw_data.get("fixtures"), list):
                for fix in raw_data["fixtures"]:
                    if isinstance(fix, dict) and fix.get("id"):
                        fixtures_map[str(fix["id"])] = fix

            if "teams" in raw_data and isinstance(raw_data["teams"], list):
                items = raw_data["teams"]
            elif "data" in raw_data and isinstance(raw_data["data"], list):
                items = raw_data["data"]
            elif "team_props" in raw_data and isinstance(raw_data["team_props"], list):
                items = raw_data["team_props"]
            elif "items" in raw_data and isinstance(raw_data["items"], list):
                items = raw_data["items"]
            elif "props" in raw_data and isinstance(raw_data["props"], list):
                items = raw_data["props"]
            elif "players" in raw_data and isinstance(raw_data["players"], list):
                items = raw_data["players"]
            elif "results" in raw_data and isinstance(raw_data["results"], list):
                items = raw_data["results"]
            elif "teamName" in raw_data or "team" in raw_data or "id" in raw_data:
                items = [raw_data]

        raw_payload_stat = default_stat
        if isinstance(raw_data, dict) and raw_data.get("stat"):
            raw_payload_stat = str(raw_data.get("stat")).strip()

        results: List[StatsHubTeamPropResult] = []
        for item in items:
            try:
                team_prop = self._parse_single_team_item(item, fixtures_map, default_stat=raw_payload_stat)
                if team_prop:
                    results.append(team_prop)
            except Exception as e:
                logger.warning(f"Error parsing StatsHub team prop item: {e}", exc_info=False)
                continue

        logger.info(f"StatsHub parsed {len(results)} team prop items successfully.")
        return results

    def _parse_single_team_item(
        self,
        item: Dict[str, Any],
        fixtures_map: Dict[str, Any],
        default_stat: str = "",
    ) -> Optional[StatsHubTeamPropResult]:
        """Extract team, opponent, fixture, stats, and odds from a single item dict."""
        team_name = str(
            item.get("teamName")
            or item.get("team")
            or (item.get("teamInfo", {}) if isinstance(item.get("teamInfo"), dict) else {}).get("name")
            or (item.get("fixtureInfo", {}) if isinstance(item.get("fixtureInfo"), dict) else {}).get("homeTeamName")
            or ""
        ).strip()

        if not team_name:
            t_obj = item.get("team")
            if isinstance(t_obj, dict):
                team_name = str(t_obj.get("name") or t_obj.get("fullName") or "").strip()

        if not team_name:
            return None

        team_id = item.get("teamId") or (item.get("teamInfo", {}) if isinstance(item.get("teamInfo"), dict) else {}).get("id") or item.get("id")
        team_slug = item.get("teamSlug") or (item.get("teamInfo", {}) if isinstance(item.get("teamInfo"), dict) else {}).get("slug")

        # Fixture / Matchup
        fixture_info = item.get("fixtureInfo") if isinstance(item.get("fixtureInfo"), dict) else {}
        matchup = item.get("matchup") if isinstance(item.get("matchup"), dict) else {}

        home_team = str(
            item.get("homeTeamName")
            or fixture_info.get("homeTeamName")
            or fixture_info.get("home_team")
            or ""
        ).strip()

        away_team = str(
            item.get("awayTeamName")
            or fixture_info.get("awayTeamName")
            or fixture_info.get("away_team")
            or ""
        ).strip()

        opponent_name = str(
            matchup.get("opponentTeamName")
            or item.get("opponentTeamName")
            or item.get("opponentName")
            or item.get("opponent")
            or (away_team if team_name == home_team else home_team)
            or ""
        ).strip()

        if not home_team and team_name:
            home_team = team_name
        if not away_team and opponent_name:
            away_team = opponent_name

        opponent_team_id = item.get("opponentTeamId") or item.get("opponentId") or matchup.get("opponentTeamId")
        opponent_team_slug = item.get("opponentTeamSlug") or item.get("opponentSlug") or matchup.get("opponentTeamSlug")

        fixture_id_val = (
            item.get("eventId")
            or fixture_info.get("id")
            or item.get("fixtureId")
            or item.get("matchId")
            or f"{home_team}_vs_{away_team}"
        )
        fixture_id = str(fixture_id_val)

        parent_fix = fixtures_map.get(fixture_id, {})

        event_internal_id = (
            item.get("eventInternalId")
            or fixture_info.get("internalId")
            or parent_fix.get("internalId")
            or item.get("internalId")
        )

        fixture_slug = (
            fixture_info.get("slug")
            or parent_fix.get("slug")
            or item.get("eventSlug")
            or item.get("fixtureSlug")
        )

        home_team_slug = item.get("homeTeamSlug") or fixture_info.get("homeTeamSlug") or parent_fix.get("homeTeamSlug")
        away_team_slug = item.get("awayTeamSlug") or fixture_info.get("awayTeamSlug") or parent_fix.get("awayTeamSlug")
        home_team_id = item.get("homeTeamId") or fixture_info.get("homeTeamId") or parent_fix.get("homeTeamId")
        away_team_id = item.get("awayTeamId") or fixture_info.get("awayTeamId") or parent_fix.get("awayTeamId")
        tournament_id = item.get("tournamentId") or fixture_info.get("tournamentId") or parent_fix.get("tournamentId")
        unique_tournament_id = item.get("uniqueTournamentId") or fixture_info.get("uniqueTournamentId")

        competition = str(
            item.get("leagueName")
            or fixture_info.get("competitionName")
            or fixture_info.get("tournamentName")
            or item.get("competitionName")
            or item.get("competition")
            or item.get("tournament")
            or ""
        ).strip()

        raw_kickoff = (
            item.get("eventTimestamp")
            or fixture_info.get("kickoff")
            or fixture_info.get("startTime")
            or parent_fix.get("startTime")
            or item.get("kickoff")
            or item.get("startTime")
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

        venue = fixture_info.get("venue") or item.get("venue")

        fixture = StatsHubTeamFixture(
            fixture_id=fixture_id,
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            kickoff=kickoff_str,
            venue=str(venue) if venue else None,
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

        # Participant role (HOME or AWAY)
        raw_role = str(item.get("participantRole") or item.get("role") or item.get("venue") or "").upper()
        if raw_role in ("HOME", "AWAY"):
            role = raw_role
        elif home_team and team_name.lower() == home_team.lower():
            role = "HOME"
        elif away_team and team_name.lower() == away_team.lower():
            role = "AWAY"
        else:
            role = "HOME"

        # Stat type & semantics
        raw_stat = str(item.get("stat") or item.get("statType") or default_stat or "shots").strip()
        stat_clean = raw_stat.lower().replace("_", "").replace(" ", "")
        stat_type = STAT_ALIAS_MAP.get(stat_clean, raw_stat.lower())
        stat_display = item.get("statDisplay")

        odds_type = str(item.get("oddsType") or "over").lower()
        target_line = float(item.get("line") or item.get("total") or item.get("statThreshold") or 0.5)

        # Statistical Metrics
        stat_val = int(item.get("statValue") or item.get("stat_value") or item.get("value") or 0)
        avg_val = float(item.get("average") or item.get("statAvg") or item.get("avg") or item.get("trendAvg") or 0.0)
        hit_rate_cnt = int(item.get("hitRateCount") or item.get("hits") or item.get("trendHits") or 0)
        sample_sz = int(item.get("sampleSize") or item.get("sample_size") or item.get("trendWindow") or item.get("totalMatches") or 0)
        hit_rate_pct = float(item.get("hitRatePct") or item.get("hitRate") or 0.0)

        # Trends metadata
        trend_hits = int(item["trendHits"]) if "trendHits" in item and item["trendHits"] is not None else (hit_rate_cnt if hit_rate_cnt > 0 else None)
        trend_window = int(item["trendWindow"]) if "trendWindow" in item and item["trendWindow"] is not None else (sample_sz if sample_sz > 0 else None)
        trend_total = int(item["trendTotal"]) if "trendTotal" in item and item["trendTotal"] is not None else None
        trend_avg = float(item["trendAvg"]) if "trendAvg" in item and item["trendAvg"] is not None else (avg_val if avg_val > 0 else None)
        opponent_hit_rate = float(item["opponentHitRate"]) if "opponentHitRate" in item and item["opponentHitRate"] is not None else None
        league_name = item.get("leagueName") or competition

        if hit_rate_pct == 0.0 and trend_hits is not None and trend_window and trend_window > 0:
            hit_rate_pct = round((trend_hits / trend_window) * 100.0, 1)

        last_5 = float(item.get("last5Avg")) if item.get("last5Avg") is not None else None
        last_10 = float(item.get("last10Avg")) if item.get("last10Avg") is not None else None
        last_15 = float(item.get("last15Avg")) if item.get("last15Avg") is not None else None

        # Historical Matches
        raw_hist = item.get("recentGames") or item.get("historicalMatches") or item.get("history") or item.get("matches") or []
        hist_matches: List[StatsHubTeamHistoricalMatch] = []
        if isinstance(raw_hist, list):
            for m in raw_hist:
                if isinstance(m, dict):
                    h_opp = str(m.get("opponent") or m.get("opponentName") or "").strip()
                    h_opp_slug = m.get("opponentSlug")
                    h_opp_id = m.get("opponentId")
                    match_ts = m.get("eventTimestamp") or m.get("timestamp")
                    h_date = str(m.get("date") or m.get("matchDate") or "").strip()
                    if not h_date and match_ts:
                        from datetime import datetime, timezone
                        try:
                            h_date = datetime.fromtimestamp(match_ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        except Exception:
                            h_date = str(match_ts)

                    h_val = int(m.get("statValue") or m.get("stat_value") or m.get("value") or 0)
                    is_h = m.get("isHome")
                    h_venue = ("H" if is_h else "A") if is_h is not None else str(m.get("venue") or m.get("homeAway") or m.get("home_away") or "").strip().upper()
                    h_comp = str(m.get("competition") or m.get("tournament") or "").strip()
                    h_t_score = int(m.get("teamScore")) if m.get("teamScore") is not None else None
                    h_o_score = int(m.get("opponentScore")) if m.get("opponentScore") is not None else None
                    is_hit = m.get("isHit")

                    hist_matches.append(
                        StatsHubTeamHistoricalMatch(
                            opponent=h_opp,
                            opponent_slug=h_opp_slug,
                            opponent_id=h_opp_id,
                            date=h_date,
                            timestamp=int(match_ts) if match_ts else None,
                            stat_value=h_val,
                            venue=h_venue,
                            competition=h_comp,
                            team_score=h_t_score,
                            opponent_score=h_o_score,
                            is_hit=is_hit,
                            event_id=m.get("eventId"),
                        )
                    )

        if hist_matches and sample_sz == 0:
            sample_sz = len(hist_matches)
            if avg_val == 0.0:
                avg_val = round(sum(m.stat_value for m in hist_matches) / sample_sz, 2)
            if last_5 is None and len(hist_matches) >= 5:
                last_5 = round(sum(m.stat_value for m in hist_matches[:5]) / 5.0, 2)
            if last_10 is None and len(hist_matches) >= 10:
                last_10 = round(sum(m.stat_value for m in hist_matches[:10]) / 10.0, 2)

        # Bookmaker Odds
        bm_odds_list: List[StatsHubTeamBookmakerOdds] = []
        available_lines: Set[float] = {target_line}
        best_odds_map: Dict[str, StatsHubTeamBookmakerOdds] = {}

        # A. Parse item['bookmakers'] = [{"bookmakerId": 2, "bookmakerName": "Bet365", "oddsValue": 1.615}]
        if "bookmakers" in item and isinstance(item["bookmakers"], list):
            for b in item["bookmakers"]:
                if isinstance(b, dict):
                    bm_name = normalize_bookmaker_name(str(b.get("bookmakerName") or b.get("name") or "Unknown"))
                    b_price = float(b.get("oddsValue") or b.get("odds") or 0.0)
                    b_id = b.get("bookmakerId") or b.get("id")
                    if b_price > 1.0:
                        bm_obj = StatsHubTeamBookmakerOdds(
                            bookmaker=bm_name,
                            line=target_line,
                            side=odds_type,
                            decimal_odds=round(b_price, 4),
                            bookmaker_id=int(b_id) if b_id is not None else None,
                            metadata={"raw": b},
                        )
                        bm_odds_list.append(bm_obj)
                        available_lines.add(target_line)

        # B. Fallback: Parse bookmakerOdds / odds / markets list
        if not bm_odds_list:
            raw_odds = item.get("bookmakerOdds") or item.get("odds") or item.get("markets") or []
            if isinstance(raw_odds, list):
                for od in raw_odds:
                    if isinstance(od, dict):
                        bm_name = normalize_bookmaker_name(str(od.get("bookmaker") or od.get("bookmakerName") or od.get("provider") or ""))
                        line_f = float(od.get("line") or od.get("total") or target_line)
                        side_s = str(od.get("side") or od.get("selection") or od.get("selectionType") or odds_type).lower()
                        dec_odds = float(od.get("decimalOdds") or od.get("odds") or od.get("decimal_odds") or od.get("price") or od.get("oddsValue") or 0.0)
                        b_id = od.get("bookmakerId") or od.get("id")

                        if dec_odds > 1.0:
                            team_bm_odd = StatsHubTeamBookmakerOdds(
                                bookmaker=bm_name,
                                line=line_f,
                                side=side_s,
                                decimal_odds=round(dec_odds, 4),
                                bookmaker_id=int(b_id) if b_id is not None else None,
                                metadata={"raw": od},
                            )
                            bm_odds_list.append(team_bm_odd)
                            available_lines.add(line_f)

        # Deduplication and best_odds_map construction
        dedup_map: Dict[Tuple[str, float, str], StatsHubTeamBookmakerOdds] = {}
        for o in bm_odds_list:
            key_t = (o.bookmaker.lower(), round(o.line, 2), o.side.lower())
            if key_t not in dedup_map or o.decimal_odds > dedup_map[key_t].decimal_odds:
                dedup_map[key_t] = o

        final_odds_list = list(dedup_map.values())
        for o in final_odds_list:
            best_key = f"{o.side}_{o.line}"
            if best_key not in best_odds_map or o.decimal_odds > best_odds_map[best_key].decimal_odds:
                best_odds_map[best_key] = o

        team_stat = StatsHubTeamStat(
            team_name=team_name,
            opponent_name=opponent_name,
            fixture=fixture,
            stat_type=stat_type,
            team_id=team_id,
            team_slug=team_slug,
            opponent_team_id=opponent_team_id,
            opponent_team_slug=opponent_team_slug,
            stat_display=stat_display,
            odds_type=odds_type,
            line=target_line,
            participant_role=role,
            stat_value=stat_val,
            average=avg_val,
            hit_rate_count=hit_rate_cnt,
            sample_size=sample_sz,
            hit_rate_pct=hit_rate_pct,
            trend_hits=trend_hits,
            trend_window=trend_window,
            trend_total=trend_total,
            trend_avg=trend_avg,
            opponent_hit_rate=opponent_hit_rate,
            league_name=league_name,
            last_5_avg=last_5,
            last_10_avg=last_10,
            last_15_avg=last_15,
            historical_matches=hist_matches,
            bookmaker_odds=final_odds_list,
            raw_data=item,
        )

        return StatsHubTeamPropResult(
            team_stat=team_stat,
            available_lines=sorted(list(available_lines)),
            best_odds_by_line=best_odds_map,
            total_bookmaker_count=len({o.bookmaker for o in final_odds_list}),
        )

