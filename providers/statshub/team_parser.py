"""
StatsHub Team Props Response Parser Implementation
"""

import logging
from typing import Any, Dict, List, Optional, Union, Set

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
    "shotsontarget": "shotsOnTarget",
    "shots_on_target": "shotsOnTarget",
    "corners": "corners",
    "corner": "corners",
    "cards": "cards",
    "card": "cards",
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
    """Parser transforming raw StatsHub team props responses into structured domain models."""

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
            elif "team_props" in raw_data and isinstance(raw_data["team_props"], list):
                items = raw_data["team_props"]
            elif "data" in raw_data and isinstance(raw_data["data"], list):
                items = raw_data["data"]
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

        # Fixture / Matchup
        fixture_info = item.get("fixtureInfo") if isinstance(item.get("fixtureInfo"), dict) else {}
        matchup = item.get("matchup") if isinstance(item.get("matchup"), dict) else {}

        home_team = str(fixture_info.get("homeTeamName") or fixture_info.get("home_team") or "").strip()
        away_team = str(fixture_info.get("awayTeamName") or fixture_info.get("away_team") or "").strip()

        opponent_name = str(
            matchup.get("opponentTeamName")
            or item.get("opponentName")
            or item.get("opponent")
            or (away_team if team_name == home_team else home_team)
            or ""
        ).strip()

        if not home_team and team_name:
            home_team = team_name
        if not away_team and opponent_name:
            away_team = opponent_name

        fixture_id = str(
            fixture_info.get("id")
            or item.get("fixtureId")
            or item.get("eventId")
            or item.get("matchId")
            or f"{home_team}_vs_{away_team}"
        )

        competition = str(
            fixture_info.get("competitionName")
            or fixture_info.get("tournamentName")
            or item.get("competitionName")
            or item.get("competition")
            or item.get("tournament")
            or ""
        ).strip()

        kickoff = fixture_info.get("kickoff") or fixture_info.get("startTime") or item.get("kickoff") or item.get("startTime")
        venue = fixture_info.get("venue") or item.get("venue")


        fixture = StatsHubTeamFixture(
            fixture_id=fixture_id,
            home_team=home_team,
            away_team=away_team,
            competition=competition,
            kickoff=str(kickoff) if kickoff else None,
            venue=str(venue) if venue else None,
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


        # Stat type
        raw_stat = str(item.get("stat") or item.get("statType") or default_stat or "shots").strip()
        stat_type = STAT_ALIAS_MAP.get(raw_stat.lower(), raw_stat.lower())

        # Statistical Metrics
        stat_val = int(item.get("statValue") or item.get("stat_value") or item.get("value") or 0)
        avg_val = float(item.get("average") or item.get("statAvg") or item.get("avg") or 0.0)
        hit_rate_cnt = int(item.get("hitRateCount") or item.get("hits") or 0)
        sample_sz = int(item.get("sampleSize") or item.get("sample_size") or item.get("totalMatches") or 0)
        hit_rate_pct = float(item.get("hitRatePct") or item.get("hitRate") or 0.0)

        last_5 = float(item.get("last5Avg")) if item.get("last5Avg") is not None else None
        last_10 = float(item.get("last10Avg")) if item.get("last10Avg") is not None else None
        last_15 = float(item.get("last15Avg")) if item.get("last15Avg") is not None else None

        # Historical Matches
        raw_hist = item.get("historicalMatches") or item.get("history") or item.get("matches") or []
        hist_matches: List[StatsHubTeamHistoricalMatch] = []
        if isinstance(raw_hist, list):
            for m in raw_hist:
                if isinstance(m, dict):
                    h_opp = str(m.get("opponent") or m.get("opponentName") or "").strip()
                    h_date = str(m.get("date") or m.get("matchDate") or "").strip()
                    h_val = int(m.get("statValue") or m.get("stat_value") or m.get("value") or 0)
                    h_venue = str(m.get("venue") or m.get("homeAway") or m.get("home_away") or "").strip().upper()
                    h_comp = str(m.get("competition") or m.get("tournament") or "").strip()
                    h_t_score = int(m.get("teamScore")) if m.get("teamScore") is not None else None
                    h_o_score = int(m.get("opponentScore")) if m.get("opponentScore") is not None else None

                    hist_matches.append(
                        StatsHubTeamHistoricalMatch(
                            opponent=h_opp,
                            date=h_date,
                            stat_value=h_val,
                            venue=h_venue,
                            competition=h_comp,
                            team_score=h_t_score,
                            opponent_score=h_o_score,
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
        raw_odds = item.get("bookmakerOdds") or item.get("odds") or item.get("markets") or []
        bm_odds_list: List[StatsHubTeamBookmakerOdds] = []
        available_lines: Set[float] = set()
        best_odds_map: Dict[str, StatsHubTeamBookmakerOdds] = {}

        if isinstance(raw_odds, list):
            for od in raw_odds:
                if isinstance(od, dict):
                    bm_name = normalize_bookmaker_name(str(od.get("bookmaker") or od.get("bookmakerName") or od.get("provider") or ""))
                    line_f = float(od.get("line") or od.get("total") or 0.5)
                    side_s = str(od.get("side") or od.get("selection") or od.get("selectionType") or "over").lower()
                    dec_odds = float(od.get("decimalOdds") or od.get("odds") or od.get("decimal_odds") or od.get("price") or 0.0)

                    if dec_odds > 1.0:
                        team_bm_odd = StatsHubTeamBookmakerOdds(
                            bookmaker=bm_name,
                            line=line_f,
                            side=side_s,
                            decimal_odds=dec_odds,
                        )
                        bm_odds_list.append(team_bm_odd)
                        available_lines.add(line_f)

                        key = f"{side_s}{line_f}"
                        if key not in best_odds_map or dec_odds > best_odds_map[key].decimal_odds:
                            best_odds_map[key] = team_bm_odd

        team_stat = StatsHubTeamStat(
            team_name=team_name,
            opponent_name=opponent_name,
            fixture=fixture,
            stat_type=stat_type,
            participant_role=role,
            stat_value=stat_val,
            average=avg_val,
            hit_rate_count=hit_rate_cnt,
            sample_size=sample_sz,
            hit_rate_pct=hit_rate_pct,
            last_5_avg=last_5,
            last_10_avg=last_10,
            last_15_avg=last_15,
            historical_matches=hist_matches,
            bookmaker_odds=bm_odds_list,
            raw_data=item,
        )


        return StatsHubTeamPropResult(
            team_stat=team_stat,
            available_lines=sorted(list(available_lines)),
            best_odds_by_line=best_odds_map,
            total_bookmaker_count=len(bm_odds_list),
        )
