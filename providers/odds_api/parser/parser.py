"""
Odds API.io Payload Parser Module
"""

import logging
from typing import Any, Dict, List, Optional
from providers.odds_api.models import (
    OddsApiEvent,
    OddsApiMarket,
    OddsApiSelection,
    OddsApiOdds,
)
from providers.odds_api.exceptions import OddsApiParsingError

logger = logging.getLogger("zielonebety.provider.odds_api")


class OddsApiParser:
    """Parses Odds API.io payloads for Bet365 and Unibet into domain event models."""

    SUPPORTED_BMS = {"bet365": "bet365", "unibet": "unibet"}

    def parse_payloads(
        self,
        raw_responses: List[Dict[str, Any]],
        target_bookmakers: Optional[List[str]] = None,
    ) -> List[OddsApiEvent]:
        """Parses a list of raw event payloads into a list of OddsApiEvent objects."""
        parsed_events: List[OddsApiEvent] = []
        target_bm_lower = [b.lower() for b in (target_bookmakers or ["bet365", "unibet"])]

        for payload in raw_responses:
            if not isinstance(payload, dict):
                continue

            try:
                ev_id = str(payload.get("id", "")).strip()
                home = str(payload.get("home", "")).strip()
                away = str(payload.get("away", "")).strip()
                if not ev_id or not home or not away:
                    continue

                league_info = payload.get("league", {}) if isinstance(payload.get("league"), dict) else {}
                comp_name = str(league_info.get("name") or "Unknown Competition").strip()
                start_time = payload.get("date")

                bookmakers_data = payload.get("bookmakers", {})
                if not isinstance(bookmakers_data, dict):
                    continue

                for raw_bm_name, mkt_list in bookmakers_data.items():
                    bm_clean = raw_bm_name.lower().replace(" (no latency)", "").replace(" ", "")
                    canonical_bm = self.SUPPORTED_BMS.get(bm_clean)
                    if not canonical_bm or canonical_bm not in target_bm_lower:
                        continue

                    if not isinstance(mkt_list, list):
                        continue

                    parsed_markets = self._parse_bookmaker_markets(ev_id, canonical_bm, mkt_list)
                    if parsed_markets:
                        ev_model = OddsApiEvent(
                            provider_event_id=f"{ev_id}_{canonical_bm}",
                            bookmaker_name=canonical_bm,
                            name=f"{home} vs {away}",
                            home_team=home,
                            away_team=away,
                            competition_name=comp_name,
                            start_time=start_time,
                            sport_name="Football",
                            markets=parsed_markets,
                            raw_payload=payload,
                        )
                        parsed_events.append(ev_model)

            except Exception as e:
                logger.warning(f"Error parsing Odds API event payload: {e}")

        return parsed_events

    def _parse_bookmaker_markets(
        self,
        event_id: str,
        bookmaker_name: str,
        mkt_list: List[Dict[str, Any]],
    ) -> List[OddsApiMarket]:
        markets: List[OddsApiMarket] = []

        for m_idx, m_dict in enumerate(mkt_list):
            if not isinstance(m_dict, dict):
                continue

            raw_name = str(m_dict.get("name", "")).strip()
            if not raw_name:
                continue

            odds_entries = m_dict.get("odds", [])
            if not isinstance(odds_entries, list) or not odds_entries:
                continue

            # Check if multi-line totals / spread (list of items with hdp/line)
            if raw_name in ("Totals", "Goals Over/Under", "Alternative Total Goals", "Spread", "Alternative Asian Handicap", "European Handicap", "Totals HT", "Spread HT"):
                for entry_idx, entry in enumerate(odds_entries):
                    if not isinstance(entry, dict):
                        continue
                    hdp_line = entry.get("hdp")
                    try:
                        line_val = float(hdp_line) if hdp_line is not None else None
                    except (ValueError, TypeError):
                        line_val = None

                    selections = self._extract_selections_from_entry(f"{event_id}_{bookmaker_name}_{m_idx}_{entry_idx}", entry, line_val)
                    if selections:
                        m_id = f"{event_id}_{bookmaker_name}_{raw_name}_{line_val}_{entry_idx}"
                        markets.append(
                            OddsApiMarket(
                                provider_market_id=m_id,
                                name=raw_name,
                                market_type_code=raw_name,
                                is_open=True,
                                line=line_val,
                                selections=selections,
                            )
                        )
            else:
                # Single line market (ML, Draw No Bet, Double Chance, Both Teams To Score, ML HT, etc.)
                selections: List[OddsApiSelection] = []
                for entry_idx, entry in enumerate(odds_entries):
                    if isinstance(entry, dict):
                        selections.extend(self._extract_selections_from_entry(f"{event_id}_{bookmaker_name}_{m_idx}_{entry_idx}", entry, None))

                if selections:
                    m_id = f"{event_id}_{bookmaker_name}_{raw_name}_{m_idx}"
                    markets.append(
                        OddsApiMarket(
                            provider_market_id=m_id,
                            name=raw_name,
                            market_type_code=raw_name,
                            is_open=True,
                            line=None,
                            selections=selections,
                        )
                    )

        return markets

    def _extract_selections_from_entry(
        self,
        prefix: str,
        entry: Dict[str, Any],
        line_val: Optional[float],
    ) -> List[OddsApiSelection]:
        selections: List[OddsApiSelection] = []

        # 1. 1X2 / ML / HT: 'home', 'draw', 'away'
        if "home" in entry:
            try:
                dec = float(entry["home"])
                if dec > 1.0:
                    selections.append(OddsApiSelection(
                        provider_selection_id=f"{prefix}_home",
                        name="1",
                        type_code="HOME",
                        handicap=line_val,
                        odds=OddsApiOdds(decimal_odds=dec),
                    ))
            except (ValueError, TypeError):
                pass

        if "draw" in entry:
            try:
                dec = float(entry["draw"])
                if dec > 1.0:
                    selections.append(OddsApiSelection(
                        provider_selection_id=f"{prefix}_draw",
                        name="X",
                        type_code="DRAW",
                        odds=OddsApiOdds(decimal_odds=dec),
                    ))
            except (ValueError, TypeError):
                pass

        if "away" in entry:
            try:
                dec = float(entry["away"])
                if dec > 1.0:
                    selections.append(OddsApiSelection(
                        provider_selection_id=f"{prefix}_away",
                        name="2",
                        type_code="AWAY",
                        handicap=-line_val if line_val is not None else None,
                        odds=OddsApiOdds(decimal_odds=dec),
                    ))
            except (ValueError, TypeError):
                pass

        # 2. Totals: 'over', 'under'
        if "over" in entry:
            try:
                dec = float(entry["over"])
                if dec > 1.0:
                    selections.append(OddsApiSelection(
                        provider_selection_id=f"{prefix}_over",
                        name=f"Over {line_val}",
                        type_code="OVER",
                        handicap=line_val,
                        odds=OddsApiOdds(decimal_odds=dec),
                    ))
            except (ValueError, TypeError):
                pass

        if "under" in entry:
            try:
                dec = float(entry["under"])
                if dec > 1.0:
                    selections.append(OddsApiSelection(
                        provider_selection_id=f"{prefix}_under",
                        name=f"Under {line_val}",
                        type_code="UNDER",
                        handicap=line_val,
                        odds=OddsApiOdds(decimal_odds=dec),
                    ))
            except (ValueError, TypeError):
                pass

        # 3. BTTS: 'yes', 'no'
        if "yes" in entry:
            try:
                dec = float(entry["yes"])
                if dec > 1.0:
                    selections.append(OddsApiSelection(
                        provider_selection_id=f"{prefix}_yes",
                        name="Yes",
                        type_code="YES",
                        odds=OddsApiOdds(decimal_odds=dec),
                    ))
            except (ValueError, TypeError):
                pass

        if "no" in entry:
            try:
                dec = float(entry["no"])
                if dec > 1.0:
                    selections.append(OddsApiSelection(
                        provider_selection_id=f"{prefix}_no",
                        name="No",
                        type_code="NO",
                        odds=OddsApiOdds(decimal_odds=dec),
                    ))
            except (ValueError, TypeError):
                pass

        # 4. Double Chance: '1X', '12', 'X2'
        for dc_key in ("1X", "12", "X2"):
            if dc_key in entry:
                try:
                    dec = float(entry[dc_key])
                    if dec > 1.0:
                        selections.append(OddsApiSelection(
                            provider_selection_id=f"{prefix}_{dc_key}",
                            name=dc_key,
                            type_code=dc_key,
                            odds=OddsApiOdds(decimal_odds=dec),
                        ))
                except (ValueError, TypeError):
                    pass

        return selections
