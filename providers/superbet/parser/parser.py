"""
Superbet Payload Parser Module (Tier 1 Overview & Tier 2 Full Market Parsing)
"""

import json
from typing import List, Dict, Any, Tuple
from providers.base.models import (
    DETAIL_FETCH_ERROR_KEY,
    DETAIL_FETCH_ERROR_TYPE_KEY,
    DETAIL_FETCH_FAILED_KEY,
    OVERVIEW_NOT_ACQUIRED_KEY,
)
from providers.superbet.models import (
    SuperbetEvent,
    SuperbetMarket,
    SuperbetSelection,
    SuperbetOdds,
)
from providers.superbet.exceptions import SuperbetParsingError


class SuperbetParser:
    """Parses raw Superbet JSON payloads (Tier 1 overview & Tier 2 full detail) into domain models."""

    TEAM_SEPARATORS = [
        "·",     # Unicode middle dot \u00b7
        " vs ",
        " VS ",
        " - ",
        " – ",   # En dash
        " — ",   # Em dash
    ]

    def parse_payloads(self, raw_responses: List[Dict[str, Any]], include_markets: bool = True) -> List[SuperbetEvent]:
        """Parses a list of raw response dicts into SuperbetEvent objects."""
        parsed_events: List[SuperbetEvent] = []

        for payload in raw_responses:
            try:
                if not isinstance(payload, dict):
                    raise SuperbetParsingError(f"Expected dict payload, got {type(payload)}")

                # Check if payload is wrapped in {"error": false, "data": [ {...} ]} (Tier 2 envelope)
                if "data" in payload and isinstance(payload["data"], list) and len(payload["data"]) > 0:
                    for ev_dict in payload["data"]:
                        if isinstance(ev_dict, dict):
                            parsed_events.append(self._parse_single_event(ev_dict, include_markets=include_markets))
                else:
                    parsed_events.append(self._parse_single_event(payload, include_markets=include_markets))

            except Exception as e:
                if isinstance(e, SuperbetParsingError):
                    raise
                raise SuperbetParsingError(f"Error parsing Superbet payload: {e}") from e

        return parsed_events

    def _parse_single_event(self, payload: Dict[str, Any], include_markets: bool = True) -> SuperbetEvent:
        """Parses an individual event dictionary."""
        fixture = payload.get("fixture", {}) if isinstance(payload.get("fixture"), dict) else {}

        event_id = str(
            payload.get("eventId")
            or payload.get("event_id")
            or payload.get("id", "")
        ).strip()

        name = str(
            payload.get("matchName")
            or fixture.get("event_name")
            or payload.get("name")
            or payload.get("eventName", "")
        ).strip()

        if not event_id or not name:
            raise SuperbetParsingError(f"Missing mandatory event fields in payload: {payload}")

        # Identifiers
        betradar_id = str(payload.get("betradarId") or fixture.get("betradar_id") or "") or None
        home_team_id = str(payload.get("homeTeamId") or fixture.get("home_team_id") or "") or None
        away_team_id = str(payload.get("awayTeamId") or fixture.get("away_team_id") or "") or None
        tournament_id = str(payload.get("tournamentId") or fixture.get("tournament_id") or "") or None
        category_id = str(payload.get("categoryId") or fixture.get("category_id") or "") or None

        comp = str(
            payload.get("tournamentName")
            or payload.get("competitionName")
            or payload.get("competition")
            or (f"Tournament {tournament_id}" if tournament_id else "")
        ).strip()

        # Extract home/away team names
        home_team = payload.get("homeTeamName") or payload.get("homeTeam")
        away_team = payload.get("awayTeamName") or payload.get("awayTeam")
        if not home_team or not away_team:
            home_team, away_team = self._split_teams(name)

        start_time = str(
            payload.get("matchDate")
            or payload.get("utcDate")
            or fixture.get("utc_date")
            or fixture.get("event_date")
            or payload.get("startDate")
            or payload.get("start_date")
            or ""
        )

        markets: List[SuperbetMarket] = []
        if include_markets:
            raw_markets = payload.get("markets", [])

            # 1. Tier 2 flat odds structure (e.g. from /v2/pl-PL/events/{event_id})
            if "odds" in payload and isinstance(payload["odds"], list):
                markets = self._parse_flat_odds_markets(event_id, payload["odds"])
            # 2. Tier 1 / standard hierarchical markets structure (e.g. from /v3/pl-PL/events)
            elif isinstance(raw_markets, list) and len(raw_markets) > 0:
                markets = self._parse_hierarchical_markets(event_id, raw_markets)

        # P1-003: propagate explicit detail-acquisition failure state so a
        # failed request is never equivalent to a legitimate empty response.
        fetch_failed = payload.get(DETAIL_FETCH_FAILED_KEY) is True
        fetch_error = payload.get(DETAIL_FETCH_ERROR_KEY) if fetch_failed else None
        fetch_error_type = payload.get(DETAIL_FETCH_ERROR_TYPE_KEY) if fetch_failed else None
        # P1-NEW-010: propagate Tier-1 overview NOT_ACQUIRED state so an
        # event whose markets were never acquired is never equivalent to a
        # legitimate zero-market detail response.
        overview_only = payload.get(OVERVIEW_NOT_ACQUIRED_KEY) is True

        return SuperbetEvent(
            event_id=event_id,
            name=name,
            home_team=home_team,
            away_team=away_team,
            sport_name="Football",
            competition_name=comp or None,
            start_time=start_time or None,
            betradar_id=betradar_id,
            home_team_id=home_team_id,
            away_team_id=away_team_id,
            tournament_id=tournament_id,
            category_id=category_id,
            markets=markets,
            raw_metadata=payload,
            fetch_failed=fetch_failed,
            fetch_error=str(fetch_error) if fetch_error else None,
            fetch_error_type=str(fetch_error_type) if fetch_error_type else None,
            overview_only=overview_only,
        )

    @staticmethod
    def _is_raw_market_in_scope(m_name: str, m_type_id: str = "") -> bool:
        """
        Early filtration for Superbet markets to avoid creating heavy SuperbetMarket / SuperbetSelection objects
        for market families that are outside central market_scope.

        Preserves 100% of platform market_scope:
        - 1X2 / Match Winner, Double Chance, BTTS, Totals (Goals, Corners, Cards, Fouls, Shots, Shots on Target, Offsides)
        - Draw No Bet, Handicap, Asian Handicap, Half Time Result
        - Player Goals, First/Last Goal, Half Goals, Player Shots, Shots on Target, Cards, Fouls, Tackles, Assists
        - Combo BTTS + O/U 2.5
        """
        if not m_name:
            return False
        name_lower = m_name.strip().lower()

        # 1. Reject 'xtra', 'strzelec xtra', 'superbets', 'superkursy', 'super przewaga', 'hit dnia'
        if any(k in name_lower for k in ("xtra", "superbets", "super bets", "super kursy", "superkursy", "super przewaga", "hit dnia")):
            return False

        # 2. Reject multiline / combos separated by semicolons
        if ";" in name_lower:
            return False

        # 3. Reject disallowed statistical metrics (Saves, Intercepts, Woodwork, etc.)
        if any(k in name_lower for k in ("obronionych", "obron", "przechwyt", "słupek", "slupek", "poprzeczk")):
            return False

        # 4. Reject specific body part / sub-variant player markets not in canonical scope
        if "zawodnik" in name_lower or "strzelec" in name_lower:
            if any(k in name_lower for k in (
                "spoza pola", "prawą nogą", "prawa noga", "lewą nogą", "lewa noga",
                "głową", "glowa", "glową", "w obu połowach", "w obu polowach",
                "lub zaliczy", "& zaliczy", "oraz zaliczy", "fauli na zawodniku",
                "1. kartk", "1.kartk", "spalonych", "dla "
            )):
                return False
            if "którykolwiek" in name_lower or "ktorykolwiek" in name_lower:
                return False
            # Player props must match allowed canonical player metrics
            if not any(k in name_lower for k in (
                "strzeli", "strzelec", "celnych strza", "strza", "asyst", "kartk", "faul", "odbior"
            )):
                return False
            return True

        # 5. Reject match combos with '&' or 'lub' (except combo BTTS + Totals)
        if (" & " in name_lower or " lub " in name_lower):
            # Only allow combo BTTS + Totals (e.g. 'liczba goli & obie drużyny strzelą')
            is_btts_totals = ("obie" in name_lower or "btts" in name_lower) and ("goli" in name_lower or "liczba" in name_lower)
            if not is_btts_totals:
                return False

        # 6. Reject disallowed time intervals, minutowe, exact score, or non-standard specials
        if any(k in name_lower for k in (
            "dokładny wynik", "dokladny wynik", "- do ", " do x minuty",
            "minuty", "minutę", "minucie", "samobójcz", "samobojcz",
            "kto pierwszy", "kto wygra resztę", "kto strzeli nastepnego", "kto strzeli następnego",
            "połowa z większą", "polowa z wieksza", "połowa / mecz", "polowa / mecz", "połowa/mecz", "polowa/mecz",
            "1.połowa / 2.połowa", "awans", "dogrywka", "obaj zawodnicy", "obydwaj zawodnicy",
            "każda z drużyn", "kazda z druzyn", "obie drużyny powyżej", "obie druzyny powyzej",
            "wygra do zera", "czyste konto", "wygra obie połowy", "wygra obie polowy",
            "strzeli w obu połowach", "strzeli w obu polowach", "multigol", "multi-gol", "multiwynik",
            "kombinacja", "wygra i poniżej", "wygra i powyżej", "wygra i over", "wygra i under",
            "przedział goli", "przedzial goli", "czas 1. gola", "połowa z 1. golem",
        )):
            return False

        return True

    def _parse_flat_odds_markets(self, event_id: str, odds_list: List[Dict[str, Any]]) -> List[SuperbetMarket]:
        """Groups flat odds list into distinct market variants using composite keys."""
        # Key: (market_type_id, market_name, specifiers_json, special_bet_value) -> list of selections
        market_groups: Dict[Tuple[str, str, str, str], List[SuperbetSelection]] = {}
        market_metadata: Dict[Tuple[str, str, str, str], Dict[str, Any]] = {}

        for s_idx, o in enumerate(odds_list):
            if not isinstance(o, dict):
                continue

            m_type_id = str(o.get("marketId") or o.get("marketUuid") or "0")
            m_name = str(o.get("marketName") or o.get("name") or "Unknown Market")

            # Early filtration: Skip out-of-scope markets before instantiating objects
            if not self._is_raw_market_in_scope(m_name, m_type_id):
                continue

            # Specifiers and lines
            specifiers = o.get("specifiers") if isinstance(o.get("specifiers"), dict) else None
            spec_str = json.dumps(specifiers, sort_keys=True) if specifiers else ""
            sp_val = str(o.get("specialBetValue", "")) if o.get("specialBetValue") is not None else ""

            group_key = (m_type_id, m_name, spec_str, sp_val)

            # Extract selection fields
            s_id = str(
                o.get("uuid")
                or o.get("id")
                or o.get("outcomeId")
                or f"{event_id}_s_{s_idx}"
            )
            s_name = str(
                o.get("selectionName")
                or o.get("name")
                or o.get("info")
                or f"Selection {s_idx + 1}"
            )
            price_val = float(o.get("price", o.get("odds", 1.0)))

            status = o.get("status")
            is_active = (status in [1, "active", "ACTIVE", True]) and (o.get("display", True) is not False)
            if price_val <= 1.0:
                is_active = False

            sel = SuperbetSelection(
                selection_id=s_id,
                name=s_name,
                odds=SuperbetOdds(decimal_odds=price_val),
                outcome_id=str(o.get("outcomeId", "")) if o.get("outcomeId") is not None else None,
                specifiers=specifiers,
                special_bet_value=sp_val or None,
                is_active=is_active,
                raw_metadata=o,
            )

            market_groups.setdefault(group_key, []).append(sel)
            if group_key not in market_metadata:
                market_metadata[group_key] = {
                    "market_type_id": m_type_id,
                    "specifiers": specifiers,
                    "raw_sample": o,
                }

        markets: List[SuperbetMarket] = []
        for m_idx, (group_key, selections) in enumerate(market_groups.items()):
            m_type_id, m_name, _, sp_val = group_key
            meta = market_metadata[group_key]

            # Construct clean market ID: marketTypeId_index
            market_id = f"{event_id}_m_{m_type_id}_{m_idx}" if m_type_id != "0" else f"{event_id}_m_{m_idx}"

            markets.append(
                SuperbetMarket(
                    market_id=market_id,
                    name=m_name,
                    market_type_id=m_type_id if m_type_id != "0" else None,
                    specifiers=meta["specifiers"],
                    selections=selections,
                    is_active=any(s.is_active for s in selections),
                    raw_metadata=meta["raw_sample"],
                )
            )

        return markets

    def _parse_hierarchical_markets(self, event_id: str, raw_markets: List[Dict[str, Any]]) -> List[SuperbetMarket]:
        """Parses hierarchical market list (e.g. from Tier 1 overview)."""
        markets: List[SuperbetMarket] = []

        for m_idx, m in enumerate(raw_markets):
            if not isinstance(m, dict):
                continue

            m_id = str(m.get("id", m.get("marketId", f"{event_id}_m_{m_idx}")))
            m_name = str(m.get("name", m.get("marketName", "Unknown Market")))
            is_open = bool(m.get("is_open", m.get("active", True)))

            selections: List[SuperbetSelection] = []
            raw_selections = m.get("odds", m.get("selections", []))

            if isinstance(raw_selections, list):
                for s_idx, s in enumerate(raw_selections):
                    if not isinstance(s, dict):
                        continue
                    meta = s.get("metadata", {}) if isinstance(s.get("metadata"), dict) else {}
                    s_id = str(
                        s.get("uuid")
                        or meta.get("outcome_id")
                        or s.get("selectionId")
                        or s.get("id", f"{m_id}_s_{s_idx}")
                    )
                    s_name = str(
                        meta.get("name")
                        or meta.get("info")
                        or s.get("name")
                        or s.get("selectionName", "")
                    )
                    decimal_odds = float(s.get("price", s.get("odds", 1.0)))
                    is_sel_active = (
                        (s.get("status") == 1 and s.get("display", True) is not False)
                        if "status" in s
                        else bool(s.get("active", is_open))
                    )
                    if decimal_odds <= 1.0:
                        is_sel_active = False

                    selections.append(
                        SuperbetSelection(
                            selection_id=s_id,
                            name=s_name,
                            odds=SuperbetOdds(decimal_odds=decimal_odds),
                            outcome_id=str(meta.get("outcome_id", "")) if "outcome_id" in meta else None,
                            is_active=is_sel_active,
                            raw_metadata=s,
                        )
                    )

            markets.append(
                SuperbetMarket(
                    market_id=m_id,
                    name=m_name,
                    market_type_id=m_id,
                    selections=selections,
                    is_active=is_open and any(s.is_active for s in selections),
                    raw_metadata=m,
                )
            )

        return markets

    def _split_teams(self, name: str) -> tuple[str, str]:
        """Splits an event name into home and away teams using known delimiters."""
        for sep in self.TEAM_SEPARATORS:
            if sep in name:
                parts = name.split(sep, 1)
                if len(parts) == 2 and parts[0].strip() and parts[1].strip():
                    return parts[0].strip(), parts[1].strip()

        return name, "Opponent"
