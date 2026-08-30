"""
Odds API.io Normalizer Implementation (Bet365 & Unibet)
"""

from typing import Dict, List, Optional
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import BaseNormalizer, NormalizedGraph
from normalization.exceptions import NormalizationError
from providers.odds_api.models import OddsApiEvent, OddsApiMarket, OddsApiSelection


class OddsApiNormalizer(BaseNormalizer):
    """Normalizes OddsApiEvent models into the canonical domain graph."""

    MARKET_TYPE_MAP: Dict[str, str] = {
        "ML": "1X2",
        "MATCH_RESULT": "1X2",
        "1X2": "1X2",
        "DRAW NO BET": "DRAW_NO_BET",
        "DRAW_NO_BET": "DRAW_NO_BET",
        "DOUBLE CHANCE": "DOUBLE_CHANCE",
        "DOUBLE_CHANCE": "DOUBLE_CHANCE",
        "TOTALS": "TOTALS",
        "GOALS OVER/UNDER": "TOTALS",
        "ALTERNATIVE TOTAL GOALS": "TOTALS",
        "ALTERNATIVE GOAL LINE": "TOTALS",
        "BOTH TEAMS TO SCORE": "BTTS",
        "BTTS": "BTTS",
        "SPREAD": "ASIAN_HANDICAP",
        "ALTERNATIVE ASIAN HANDICAP": "ASIAN_HANDICAP",
        "EUROPEAN HANDICAP": "HANDICAP",
        "HALF TIME RESULT": "HALF_TIME_RESULT",
        "ML HT": "HALF_TIME_RESULT",
        "SPREAD HT": "ASIAN_HANDICAP",
        "TOTALS HT": "TOTALS",
        "ALTERNATIVE 1ST HALF GOAL LINE": "TOTALS",
        "ALTERNATIVE 1ST HALF ASIAN HANDICAP": "ASIAN_HANDICAP",
    }

    SELECTION_TYPE_MAP: Dict[str, str] = {
        "HOME": "HOME",
        "1": "HOME",
        "DRAW": "DRAW",
        "X": "DRAW",
        "AWAY": "AWAY",
        "2": "AWAY",
        "OVER": "OVER",
        "UNDER": "UNDER",
        "YES": "YES",
        "NO": "NO",
        "1X": "HOME_DRAW",
        "12": "HOME_AWAY",
        "X2": "DRAW_AWAY",
    }

    def normalize_event(self, provider_event: OddsApiEvent) -> NormalizedGraph:
        """Transforms an OddsApiEvent into a canonical entity graph."""
        if not isinstance(provider_event, OddsApiEvent):
            raise NormalizationError(f"Expected OddsApiEvent instance, got {type(provider_event)}")

        bm_name = provider_event.bookmaker_name.lower()
        comp_name = provider_event.competition_name or "Unknown Competition"

        competition = Competition(
            name=comp_name,
            sport=provider_event.sport_name or "Football",
        )

        home = provider_event.home_team
        away = provider_event.away_team

        event = Event(
            competition_id=competition.internal_id,
            home_participant=home,
            away_participant=away,
            scheduled_start=provider_event.start_time,
            provider_ids={bm_name: provider_event.provider_event_id},
            metadata={"odds_api": {"bookmaker": bm_name}},
        )

        markets: List[Market] = []
        selections: List[Selection] = []
        odds_list: List[Odds] = []

        for m in provider_event.markets:
            if not m.is_open or not m.selections:
                continue

            canonical_mkt_type = self._resolve_market_type(m)
            mkt_meta = {}

            # Time period check (Second half vs First half vs Full time)
            name_upper = m.name.strip().upper()
            code_upper = (m.market_type_code or "").strip().upper()
            combined_upper = f"{name_upper} {code_upper}"

            import re
            if re.search(r'\b(2H|2ND HALF|SECOND HALF)\b', combined_upper) or " 2H" in combined_upper or "- 2H" in combined_upper:
                mkt_meta["period"] = "SECOND_HALF"
            elif any(k in combined_upper for k in (" 1H", "1ST HALF", "FIRST HALF")) or re.search(r'\b(HT|1H)\b', combined_upper) or " 1H" in combined_upper or "- 1H" in combined_upper:
                mkt_meta["period"] = "FIRST_HALF"

            market = Market(
                event_id=event.internal_id,
                market_type=canonical_mkt_type,
                line=m.line,
                status="OPEN" if m.is_open else "CLOSED",
                provider_ids={bm_name: m.provider_market_id},
                metadata=mkt_meta,
            )
            markets.append(market)

            for s in m.selections:
                canonical_sel_type = self._resolve_selection_type(s)

                participant = None
                if canonical_sel_type == "HOME":
                    participant = home
                elif canonical_sel_type == "AWAY":
                    participant = away

                selection = Selection(
                    market_id=market.internal_id,
                    selection_type=canonical_sel_type,
                    line=s.handicap or m.line,
                    participant=participant,
                    provider_ids={bm_name: s.provider_selection_id},
                )
                selections.append(selection)

                if s.odds and s.odds.decimal_odds > 1.0:
                    odds = Odds(
                        selection_id=selection.internal_id,
                        bookmaker=bm_name,
                        decimal_odds=s.odds.decimal_odds,
                        timestamp=s.odds.timestamp or event.created_at,
                    )
                    odds_list.append(odds)

        return NormalizedGraph(
            competition=competition,
            event=event,
            markets=markets,
            selections=selections,
            odds_list=odds_list,
        )

    def _resolve_market_type(self, market: OddsApiMarket) -> str:
        name_upper = market.name.strip().upper()
        if name_upper in self.MARKET_TYPE_MAP:
            return self.MARKET_TYPE_MAP[name_upper]

        code_upper = market.market_type_code.strip().upper()
        if code_upper in self.MARKET_TYPE_MAP:
            return self.MARKET_TYPE_MAP[code_upper]

        if "TOTAL" in name_upper or "OVER/UNDER" in name_upper:
            return "TOTALS"
        if "SPREAD" in name_upper or "ASIAN HANDICAP" in name_upper:
            return "ASIAN_HANDICAP"
        if "HANDICAP" in name_upper:
            return "HANDICAP"
        if "BOTH TEAMS" in name_upper or "BTTS" in name_upper:
            return "BTTS"
        if "DOUBLE CHANCE" in name_upper:
            return "DOUBLE_CHANCE"
        if "DRAW NO BET" in name_upper:
            return "DRAW_NO_BET"

        return name_upper.replace(" ", "_")

    def _resolve_selection_type(self, selection: OddsApiSelection) -> str:
        code_upper = (selection.type_code or "").strip().upper()
        if code_upper in self.SELECTION_TYPE_MAP:
            return self.SELECTION_TYPE_MAP[code_upper]

        name_upper = (selection.name or "").strip().upper()
        if name_upper in self.SELECTION_TYPE_MAP:
            return self.SELECTION_TYPE_MAP[name_upper]

        if name_upper.startswith("OVER"):
            return "OVER"
        if name_upper.startswith("UNDER"):
            return "UNDER"

        return name_upper if name_upper else "UNKNOWN"
