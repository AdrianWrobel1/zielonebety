"""
Superbet Provider Normalizer Implementation
"""

import functools
import re
from typing import List, Dict, Optional, Tuple
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import BaseNormalizer, NormalizedGraph
from normalization.exceptions import NormalizationError
from normalization.identity import normalize_team_name
from normalization.aliases import resolve_canonical_team_name, ALL_IDENTITY_SUFFIXES
from normalization.competitions import resolve_canonical_competition
from normalization.market_scope import is_allowed_market_family, get_market_family_name
from providers.superbet.models import SuperbetEvent, SuperbetMarket, SuperbetSelection

# Precompiled regular expressions for SuperbetNormalizer
_RE_SB_TOTALS_1 = re.compile(r'^(?:liczba\s+goli|suma\s+goli|gole\s+powy[zż]ej/poni[zż]ej|over/under)(?:\s+[-+]?[0-9]+\.?[0-9]*)?$')
_RE_SB_TOTALS_TEAM = re.compile(r'[-–]\s+(?:liczba|suma)\s+(?:goli|rzut[oó]w\s+ro[zż]nych|kartek|spalonych|fauli|strza[lł][oó]w|celnych\s+strza[lł][oó]w)')
_RE_SB_MKT_LINE = re.compile(r'(?:goli|kartek|ro[zż]nych|spalonych|fauli|strza[lł][oó]w|total|over|under|liczba|powyżej|poniżej)\s*([0-9]+\.?[0-9]*)', re.IGNORECASE)
_RE_SB_PAREN_LINE = re.compile(r'\(([-+]?[0-9]+\.?[0-9]*)\)')
_RE_SB_SEL_TOTALS_LINE = re.compile(r'(?:poniżej|ponizej|powyżej|powyzej|over|under)\s+([0-9]+\.?[0-9]*)', re.IGNORECASE)


@functools.lru_cache(maxsize=16384)
def _get_team_patterns_superbet(team_name: str) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    """Caches candidate strings and pattern checks for Superbet team matching in market names."""
    if not team_name or not team_name.strip():
        return (), (), ()
    norm_team, tok_team = normalize_team_name(team_name)
    canon_team, ctok_team = resolve_canonical_team_name(norm_team, tok_team)
    candidates = set()
    if norm_team:
        candidates.add(norm_team)
    if canon_team:
        candidates.add(canon_team)
    raw_t = team_name.strip().lower()
    if raw_t:
        candidates.add(raw_t)

    valid_cands = tuple(c for c in candidates if len(c) >= 2)
    patterns = []
    for cand in valid_cands:
        patterns.extend([
            f"{cand} -",
            f"{cand}:",
            f"- {cand}",
            f": {cand}",
            f"({cand})",
            f" {cand} ",
        ])
    token_phrases = (" ".join(ctok_team),) if len(ctok_team) >= 2 else ()
    return valid_cands, tuple(patterns), token_phrases


class SuperbetNormalizer(BaseNormalizer):
    """Normalizes Superbet-specific provider models into the canonical domain graph."""

    MARKET_TYPE_MAP: Dict[str, str] = {
        # Superbet market names (Polish) -> canonical types
        "mecz": "1X2",
        "wynik meczu": "1X2",
        "1x2": "1X2",
        "liczba goli": "TOTALS",
        "over/under": "TOTALS",
        "200734": "TOTALS",
        "suma goli": "TOTALS",
        "gole powyżej/poniżej": "TOTALS",
        "gole powyzej/ponizej": "TOTALS",
        "obie drużyny strzelą": "BTTS",
        "obie druzyny strzela": "BTTS",  # without diacritics
        "obie drużyny strzelą gola": "BTTS",
        "obie druzyny strzela gola": "BTTS",
        "oba zespoły strzelą": "BTTS",
        "oba zespoly strzela": "BTTS",
        "btts": "BTTS",
        "handicap": "HANDICAP",
        "handicap 1x2": "HANDICAP",
        "handicap europejski": "HANDICAP",
        "200736": "HANDICAP",
        "handicap azjatycki": "ASIAN_HANDICAP",
        "asian handicap": "ASIAN_HANDICAP",
        "podwójna szansa": "DOUBLE_CHANCE",
        "podwojna szansa": "DOUBLE_CHANCE",  # without diacritics
        "double chance": "DOUBLE_CHANCE",
        "zakład bez remisu": "DRAW_NO_BET",
        "zaklad bez remisu": "DRAW_NO_BET",  # without diacritics
        "remis bez zakładu": "DRAW_NO_BET",
        "remis bez zakladu": "DRAW_NO_BET",  # without diacritics
        "draw no bet": "DRAW_NO_BET",
        "555": "DRAW_NO_BET",
        "wynik dokładny": "CORRECT_SCORE",
        "wynik dokladny": "CORRECT_SCORE",  # without diacritics
        "correct score": "CORRECT_SCORE",
        "wynik 1. połowy": "HALF_TIME_RESULT",
        "wynik 1. polowy": "HALF_TIME_RESULT",  # without diacritics
        "1. połowa - wynik": "HALF_TIME_RESULT",
        "1. polowa - wynik": "HALF_TIME_RESULT",
        "half time result": "HALF_TIME_RESULT",
        "parzyste/nieparzyste": "ODD_EVEN",
        "odd/even": "ODD_EVEN",
        # Player Props
        "zawodnik - strzeli gola": "PLAYER_GOALS",
        "zawodnik - strzeli 1. gola": "PLAYER_FIRST_GOAL",
        "zawodnik - strzeli 2+ gole": "PLAYER_GOALS",
        "zawodnik - strzeli 3+ gole": "PLAYER_GOALS",
        "zawodnik - strzeli gola w 1. połowie": "PLAYER_GOALS_FIRST_HALF",
        "zawodnik - strzeli gola w 1. polowie": "PLAYER_GOALS_FIRST_HALF",
        "zawodnik - strzeli gola w 2. połowie": "PLAYER_GOALS_SECOND_HALF",
        "zawodnik - strzeli gola w 2. polowie": "PLAYER_GOALS_SECOND_HALF",
        "zawodnik - liczba celnych strzałów": "PLAYER_SHOTS_ON_TARGET",
        "zawodnik - liczba celnych strzalow": "PLAYER_SHOTS_ON_TARGET",
        "zawodnik - liczba strzałów": "PLAYER_SHOTS",
        "zawodnik - liczba strzalow": "PLAYER_SHOTS",
        "zawodnik - liczba asyst": "PLAYER_ASSISTS",
        "zawodnik - otrzyma kartkę": "PLAYER_CARDS",
        "zawodnik - otrzyma kartke": "PLAYER_CARDS",
        "zawodnik - otrzyma czerwoną kartkę": "PLAYER_RED_CARDS",
        "zawodnik - otrzyma czerwona kartke": "PLAYER_RED_CARDS",
        "zawodnik - liczba popełnionych fauli": "PLAYER_FOULS",
        "zawodnik - liczba popelnionych fauli": "PLAYER_FOULS",
        "zawodnik - liczba podań": "PLAYER_PASSES",
        "zawodnik - liczba podan": "PLAYER_PASSES",
        "zawodnik - liczba odbiorów": "PLAYER_TACKLES",
        "zawodnik - liczba odbiorow": "PLAYER_TACKLES",
    }

    SELECTION_TYPE_MAP: Dict[str, str] = {
        # Superbet selection names -> canonical types
        "1": "HOME",
        "x": "DRAW",
        "2": "AWAY",
        "over": "OVER",
        "powyżej": "OVER",
        "powyzej": "OVER",  # without diacritics
        "under": "UNDER",
        "poniżej": "UNDER",
        "ponizej": "UNDER",  # without diacritics
        "tak": "YES",
        "yes": "YES",
        "nie": "NO",
        "no": "NO",
        "1x": "HOME_DRAW",
        "12": "HOME_AWAY",
        "x2": "DRAW_AWAY",
        "nieparzyste": "ODD",
        "odd": "ODD",
        "parzyste": "EVEN",
        "even": "EVEN",
    }

    def normalize_event(self, provider_event: SuperbetEvent) -> NormalizedGraph:
        """Transforms a SuperbetEvent into a canonical entity graph."""
        if not isinstance(provider_event, SuperbetEvent):
            raise NormalizationError(
                f"Expected SuperbetEvent instance, got {type(provider_event)}"
            )

        # 1. Normalize Competition via Canonical Registry
        comp_provider_ids = {}
        if provider_event.tournament_id:
            comp_provider_ids["superbet"] = str(provider_event.tournament_id).strip()

        # 2. Extract Participants
        home = provider_event.home_team
        away = provider_event.away_team

        comp_res = resolve_canonical_competition(
            raw_name=provider_event.competition_name,
            home_team=home,
            away_team=away,
            provider_ids=comp_provider_ids,
        )

        comp_metadata = {
            "canonical_id": comp_res.canonical_id,
            "competition_type": comp_res.competition_type,
            "tier": comp_res.tier,
            "provenance": comp_res.provenance,
            "confidence": comp_res.confidence,
            "matched_by": comp_res.matched_by,
        }
        if provider_event.category_id:
            comp_metadata["superbet"] = {"category_id": provider_event.category_id}

        competition = Competition(
            name=comp_res.canonical_name,
            sport=provider_event.sport_name or "Football",
            country=comp_res.country,
            provider_ids=comp_provider_ids,
            metadata=comp_metadata,
        )

        # 3. Normalize Event
        event_external_ids = {}
        if provider_event.betradar_id:
            event_external_ids["betradar"] = provider_event.betradar_id

        event_meta_superbet = {}
        if provider_event.home_team_id:
            event_meta_superbet["home_team_id"] = provider_event.home_team_id
        if provider_event.away_team_id:
            event_meta_superbet["away_team_id"] = provider_event.away_team_id
        if provider_event.tournament_id:
            event_meta_superbet["tournament_id"] = provider_event.tournament_id
        if provider_event.category_id:
            event_meta_superbet["category_id"] = provider_event.category_id
        if provider_event.name:
            event_meta_superbet["raw_event_name"] = provider_event.name

        event_metadata = {"superbet": event_meta_superbet} if event_meta_superbet else {}

        event = Event(
            competition_id=competition.internal_id,
            home_participant=home,
            away_participant=away,
            scheduled_start=provider_event.start_time,
            provider_ids={"superbet": provider_event.event_id},
            external_ids=event_external_ids,
            metadata=event_metadata,
        )

        markets: List[Market] = []
        selections: List[Selection] = []
        odds_list: List[Odds] = []

        # 4. Normalize Markets, Selections, and Odds
        for sm in provider_event.markets:
            if not sm.is_active:
                continue

            canonical_mkt_type = self._resolve_market_type(sm)
            market_metadata = self._extract_market_metadata(sm, canonical_mkt_type, home, away)
            player_name = None
            if canonical_mkt_type.startswith("PLAYER_"):
                player_name = self._extract_player_name_from_market(sm)
                if player_name:
                    market_metadata["player_name"] = player_name
                    market_metadata["scope"] = "PLAYER"

            # Stage 50: Centralized Market Allowlist Filter
            metric = market_metadata.get("metric", "GOALS")
            scope = market_metadata.get("scope", "MATCH")
            if not is_allowed_market_family(canonical_mkt_type, metric=metric, scope=scope, raw_name=sm.name):
                continue

            market_line = self._extract_market_line(sm)

            market = Market(
                event_id=event.internal_id,
                market_type=canonical_mkt_type,
                line=market_line,
                status="OPEN" if sm.is_active else "CLOSED",
                provider_ids={"superbet": sm.market_id} if sm.market_id else {},
                metadata=market_metadata,
            )
            markets.append(market)

            for ss in sm.selections:
                if not ss.is_active:
                    continue

                canonical_sel_type = self._resolve_selection_type(ss, home, away, canonical_mkt_type)
                selection_line = self._extract_selection_line(ss) or market_line

                participant = player_name
                if not participant:
                    if canonical_sel_type == "HOME":
                        participant = home
                    elif canonical_sel_type == "AWAY":
                        participant = away

                sel_metadata = {}
                if ss.outcome_id:
                    sel_metadata["superbet"] = {"outcome_id": ss.outcome_id}

                selection = Selection(
                    market_id=market.internal_id,
                    selection_type=canonical_sel_type,
                    line=selection_line,
                    participant=participant,
                    provider_ids={"superbet": ss.selection_id} if ss.selection_id else {},
                    metadata=sel_metadata,
                )
                selections.append(selection)

                if ss.odds and ss.odds.decimal_odds > 1.0:
                    odds = Odds(
                        selection_id=selection.internal_id,
                        bookmaker="superbet",
                        decimal_odds=ss.odds.decimal_odds,
                    )
                    odds_list.append(odds)

        return NormalizedGraph(
            competition=competition,
            event=event,
            markets=markets,
            selections=selections,
            odds_list=odds_list,
        )

    def _extract_player_name_from_market(self, market: SuperbetMarket) -> Optional[str]:
        """Extracts player name from Superbet market specifiers or selections, prioritizing player_name over player_id."""
        if market.specifiers:
            # 1. First priority: explicit player_name key
            if "player_name" in market.specifiers and market.specifiers["player_name"]:
                val_str = str(market.specifiers["player_name"]).strip()
                if "," in val_str:
                    parts = [p.strip() for p in val_str.split(",") if p.strip()]
                    if len(parts) == 2:
                        return f"{parts[1]} {parts[0]}"
                return val_str

            # 2. Second priority: any player name specifier (excluding ID fields)
            for k, v in market.specifiers.items():
                k_low = k.lower()
                if ("player" in k_low or k_low.startswith("ss_p")) and "id" not in k_low and v:
                    val_str = str(v).strip()
                    if "," in val_str:
                        parts = [p.strip() for p in val_str.split(",") if p.strip()]
                        if len(parts) == 2:
                            return f"{parts[1]} {parts[0]}"
                    return val_str

            # 3. Third priority: general fallback on specifiers
            for k, v in market.specifiers.items():
                if ("player" in k.lower() or k.startswith("ss_p")) and v:
                    val_str = str(v).strip()
                    if "," in val_str:
                        # Convert "Lewandowski, Robert" -> "Robert Lewandowski"
                        parts = [p.strip() for p in val_str.split(",") if p.strip()]
                        if len(parts) == 2:
                            return f"{parts[1]} {parts[0]}"
                    return val_str
        if market.selections:
            for s in market.selections:
                if s.specifiers:
                    if "player_name" in s.specifiers and s.specifiers["player_name"]:
                        val_str = str(s.specifiers["player_name"]).strip()
                        if "," in val_str:
                            parts = [p.strip() for p in val_str.split(",") if p.strip()]
                            if len(parts) == 2:
                                return f"{parts[1]} {parts[0]}"
                        return val_str
                    for k, v in s.specifiers.items():
                        k_low = k.lower()
                        if ("player" in k_low or k_low.startswith("ss_p")) and "id" not in k_low and v:
                            val_str = str(v).strip()
                            if "," in val_str:
                                parts = [p.strip() for p in val_str.split(",") if p.strip()]
                                if len(parts) == 2:
                                    return f"{parts[1]} {parts[0]}"
                            return val_str
                    for k, v in s.specifiers.items():
                        if ("player" in k.lower() or k.startswith("ss_p")) and v:
                            val_str = str(v).strip()
                            if "," in val_str:
                                parts = [p.strip() for p in val_str.split(",") if p.strip()]
                                if len(parts) == 2:
                                    return f"{parts[1]} {parts[0]}"
                            return val_str
                # Parse from selection name if format is 'Player Name - powyżej 0.5'
                if " - " in s.name:
                    p_candidate = s.name.split(" - ")[0].strip()
                    if p_candidate and not any(k in p_candidate.lower() for k in ("powyżej", "poniżej", "over", "under")):
                        if "," in p_candidate:
                            parts = [p.strip() for p in p_candidate.split(",") if p.strip()]
                            if len(parts) == 2:
                                return f"{parts[1]} {parts[0]}"
                        return p_candidate
                elif not any(k in s.name.lower() for k in ("1", "x", "2", "over", "under", "powyżej", "poniżej", "tak", "nie", "yes", "no", "remis", "draw")):
                    clean_s = s.name.strip()
                    if "," in clean_s:
                        parts = [p.strip() for p in clean_s.split(",") if p.strip()]
                        if len(parts) == 2:
                            return f"{parts[1]} {parts[0]}"
                    return clean_s
        return None

    def _extract_market_metadata(
        self,
        market: SuperbetMarket,
        canonical_mkt_type: str,
        home: Optional[str] = None,
        away: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extracts period, scope, and participant metadata from Superbet market."""
        meta: Dict[str, Any] = {"raw_name": market.name}
        name_lower = (market.name or "").strip().lower()

        # 1. Metric extraction
        if any(k in name_lower for k in ("rzutów rożnych", "rzutow roznych", "rz.rożnych", "rz.roznych", "rożne", "rozne", "corner")):
            meta["metric"] = "CORNERS"
        elif any(k in name_lower for k in ("punkt", "punkty", "punktow", "punktów", "booking point", "card point")) and any(k in name_lower for k in ("kartk", "card")):
            meta["metric"] = "CARD_POINTS"
        elif any(k in name_lower for k in ("kartk", "kartki", "kartek", "card")):
            meta["metric"] = "CARDS"
        elif any(k in name_lower for k in ("spalonych", "spalone", "spalony", "offside")):
            meta["metric"] = "OFFSIDES"
        elif any(k in name_lower for k in ("celnych strzałów", "celnych strzalow")):
            meta["metric"] = "SHOTS_ON_TARGET"
        elif any(k in name_lower for k in ("strzałów", "strzalow", "strzały", "strzaly", "shot")):
            meta["metric"] = "SHOTS"
        elif any(k in name_lower for k in ("fauli", "faule", "faul")):
            meta["metric"] = "FOULS"
        elif any(k in name_lower for k in ("podań", "podan", "podania")):
            meta["metric"] = "PASSES"
        else:
            meta["metric"] = "GOALS"

        # 2. Period extraction
        if any(k in name_lower for k in ("1. połowa", "1. polowa", "1st half", "first half", "do przerwy", "1.połowa", "1.polowa", "pierwsza połowa", "pierwsza polowa")):
            meta["period"] = "FIRST_HALF"
        elif any(k in name_lower for k in ("2. połowa", "2. polowa", "2nd half", "second half", "2.połowa", "2.polowa", "druga połowa", "druga polowa")):
            meta["period"] = "SECOND_HALF"
        else:
            meta["period"] = "FULL_TIME"

        # 3. Scope & Participant Role extraction
        is_home_team = False
        is_away_team = False

        if any(k in name_lower for k in ("gospodarze", "gospodarzy", "drużyna 1", "druzyna 1", "team 1")):
            is_home_team = True
        elif any(k in name_lower for k in ("goście", "goscie", "gości", "gosci", "drużyna 2", "druzyna 2", "team 2")):
            is_away_team = True
        else:
            # Check team identity with Stage 26 canonical alias resolution
            h_match = self._matches_team_in_market_name(name_lower, home)
            a_match = self._matches_team_in_market_name(name_lower, away)

            if h_match and not a_match:
                is_home_team = True
            elif a_match and not h_match:
                is_away_team = True

        if is_home_team:
            meta["scope"] = "TEAM"
            meta["participant_role"] = "HOME"
        elif is_away_team:
            meta["scope"] = "TEAM"
            meta["participant_role"] = "AWAY"
        else:
            meta["scope"] = "MATCH"
            meta["participant_role"] = None

        return meta

    def _matches_team_in_market_name(self, market_name_lower: str, team_name: Optional[str]) -> bool:
        """Deterministically checks if team identity is referenced in the market name using Stage 26 aliases."""
        if not team_name or not market_name_lower:
            return False

        valid_cands, patterns, token_phrases = _get_team_patterns_superbet(team_name)
        if not valid_cands:
            return False

        spaced_name = f" {market_name_lower} "
        if any(p in spaced_name for p in patterns):
            return True

        for cand in valid_cands:
            if market_name_lower.startswith((f"{cand} ", f"{cand}-", f"{cand}:")):
                return True
            if market_name_lower.endswith((f" {cand}", f"-{cand}", f":{cand}")):
                return True

        for phrase in token_phrases:
            if phrase in market_name_lower:
                return True

        return False

    def _resolve_market_type(self, market: SuperbetMarket) -> str:
        """Resolve Superbet market to canonical market type."""
        # Try mapping by market_type_id first if available
        if market.market_type_id:
            type_id_lower = market.market_type_id.strip().lower()
            mapped = self.MARKET_TYPE_MAP.get(type_id_lower)
            if mapped:
                return mapped

        name_lower = market.name.strip().lower()
        mapped = self.MARKET_TYPE_MAP.get(name_lower)
        if mapped:
            return mapped

        # Substring matching for player props (specific qualifiers first)
        if ("1. gola" in name_lower or "pierwszego gola" in name_lower or "1. gol" in name_lower or "pierwszy gol" in name_lower) and "zawodnik" in name_lower:
            return "PLAYER_FIRST_GOAL"
        if ("ostatniego gola" in name_lower or "ostatni gol" in name_lower) and "zawodnik" in name_lower:
            return "PLAYER_LAST_GOAL"
        if ("w 1. po" in name_lower or "w 1.po" in name_lower) and "zawodnik" in name_lower:
            return "PLAYER_GOALS_FIRST_HALF"
        if ("w 2. po" in name_lower or "w 2.po" in name_lower) and "zawodnik" in name_lower:
            return "PLAYER_GOALS_SECOND_HALF"
        if "czerwon" in name_lower and "kartk" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_RED_CARDS"
        if "strzeli gola" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_GOALS"
        if "celnych strza" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_SHOTS_ON_TARGET"
        if "strza" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_SHOTS"
        if "asyst" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_ASSISTS"
        if "kartk" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_CARDS"
        if "faul" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_FOULS"
        if "poda" in name_lower and "zawodnik" in name_lower:
            return "PLAYER_PASSES"

        # Exact and prefix matching for standard markets
        if name_lower in ("mecz", "wynik meczu", "1x2", "1 x 2", "zwycięzca meczu", "zwyciezca meczu", "zwycięzca", "zwyciezca"):
            return "1X2"
        if name_lower in ("obie drużyny strzelą", "obie druzyny strzela", "oba zespoły strzelą", "oba zespoly strzela", "btts", "obie drużyny strzelą gola", "obie druzyny strzela gola"):
            return "BTTS"
        if _RE_SB_TOTALS_1.match(name_lower):
            return "TOTALS"

        # Statistical Over/Under totals (Cards, Corners, Offsides, Fouls, Shots)
        if "zawodnik" not in name_lower and ";" not in name_lower and "handicap" not in name_lower and "każda" not in name_lower and "kazda" not in name_lower and "&" not in name_lower and " lub " not in name_lower:
            if any(stat in name_lower for stat in ("liczba rzutów rożnych", "liczba rzutow roznych", "suma rzutów rożnych", "suma rzutow roznych", "rzuty rożne powyżej", "rzuty rozne powyzej", "rzuty rożne poniżej", "rzuty rozne ponizej")) or name_lower in ("rzuty rożne", "rzuty rozne"):
                return "TOTALS"
            if any(stat in name_lower for stat in ("liczba kartek", "suma kartek", "kartki powyżej", "kartki powyzej", "liczba żółtych kartek", "liczba zoltych kartek", "żółte kartki powyżej", "żółte kartki poniżej")) or name_lower in ("żółte kartki", "zolte kartki", "kartki"):
                return "TOTALS"
            if any(stat in name_lower for stat in ("liczba spalonych", "suma spalonych", "spalone powyżej", "spalone powyzej", "spalone -")) or name_lower == "spalone":
                return "TOTALS"
            if any(stat in name_lower for stat in ("liczba fauli", "suma fauli", "faule powyżej", "faule poniżej", "liczba celnych strzałów", "liczba celnych strzalow", "liczba strzałów", "liczba strzalow")):
                return "TOTALS"

            # Handle team-level statistical totals (e.g. "Real Madrid - liczba goli", "Wolfsburg - liczba rzutów rożnych", "Portland Timbers - liczba kartek")
            if (
                "&" not in name_lower
                and " lub " not in name_lower
                and "dokładna" not in name_lower
                and "dokladna" not in name_lower
                and "minuty" not in name_lower
                and "samobójcz" not in name_lower
                and "samobojcz" not in name_lower
                and "parzysta" not in name_lower
                and _RE_SB_TOTALS_TEAM.search(name_lower)
            ):
                return "TOTALS"

        if name_lower in ("podwójna szansa", "podwojna szansa", "double chance"):
            return "DOUBLE_CHANCE"
        if name_lower in ("zakład bez remisu", "zaklad bez remisu", "remis bez zakładu", "remis bez zakladu", "draw no bet"):
            return "DRAW_NO_BET"
        if name_lower in ("wynik 1. połowy", "wynik 1. polowy", "1. połowa - wynik", "1. polowa - wynik", "half time result"):
            return "HALF_TIME_RESULT"
        if "handicap azjatycki" in name_lower:
            return "ASIAN_HANDICAP"
        if name_lower.startswith("handicap"):
            return "HANDICAP"

        return name_lower.upper().replace(" ", "_")

    def _resolve_selection_type(
        self,
        selection: SuperbetSelection,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        canonical_mkt_type: Optional[str] = None,
    ) -> str:
        """Resolve Superbet selection to canonical selection type."""
        name_lower = selection.name.strip().lower()

        # 1. Exact match against known selection keywords
        mapped = self.SELECTION_TYPE_MAP.get(name_lower)
        if mapped:
            return mapped

        # 2. Player to score / get card / make assist simple selection
        if canonical_mkt_type in ("PLAYER_GOALS", "PLAYER_CARDS", "PLAYER_ASSISTS") and not any(k in name_lower for k in ("poniżej", "ponizej", "powyżej", "powyzej", "under", "over")):
            return "YES"

        # 3. Totals / Prop prefix matching
        if name_lower.startswith(("poniżej", "ponizej", "under")):
            return "UNDER"
        if name_lower.startswith(("powyżej", "powyzej", "over")):
            return "OVER"

        # 4. Participant name matching for DNB and Handicap
        if home_team:
            h_clean = home_team.strip().lower()
            if name_lower == h_clean or name_lower.startswith(h_clean + " ") or name_lower.startswith(h_clean + "("):
                return "HOME"

        if away_team:
            a_clean = away_team.strip().lower()
            if name_lower == a_clean or name_lower.startswith(a_clean + " ") or name_lower.startswith(a_clean + "("):
                return "AWAY"

        # 5. Draw match
        if name_lower.startswith(("remis ", "remis(", "draw ", "draw(")) or name_lower in ("remis", "draw"):
            return "DRAW"

        # Fallback: return uppercased name
        return name_lower.upper() if name_lower else "UNKNOWN"

    def _extract_market_line(self, market: SuperbetMarket) -> Optional[float]:
        """Extract line value from Superbet market specifiers or selections."""
        # 1. Check market specifiers dict
        if market.specifiers:
            for key in ("total", "handicap", "line", "hcp", "goalnr"):
                if key in market.specifiers:
                    try:
                        return float(market.specifiers[key])
                    except (ValueError, TypeError):
                        pass
        # 2. Check selections special_bet_value or specifiers
        if market.selections:
            for s in market.selections:
                sel_line = self._extract_selection_line(s)
                if sel_line is not None:
                    return sel_line
        # 3. Regex on market name for totals or multi-goal player props
        if market.name:
            name_lower = market.name.strip().lower()
            if "strzeli 1. gola" in name_lower or "pierwszego gola" in name_lower or "ostatniego gola" in name_lower:
                return None
            if "strzeli 2+ gole" in name_lower or "2+ gole" in name_lower or "2 lub więcej" in name_lower:
                return 1.5
            if "strzeli 3+ gole" in name_lower or "3+ gole" in name_lower or "3 lub więcej" in name_lower:
                return 2.5
            match = _RE_SB_MKT_LINE.search(market.name)
            if match:
                try:
                    return float(match.group(1))
                except (ValueError, TypeError):
                    pass
        return None

    def _extract_selection_line(self, selection: SuperbetSelection) -> Optional[float]:
        """Extract line value from Superbet selection."""
        # 1. Try extracting float from parentheses in selection name (e.g. "Portland Timbers (2.5)", "Chicago Fire (-2.5)")
        match = _RE_SB_PAREN_LINE.search(selection.name)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, TypeError):
                pass

        # 2. Try extracting float from totals selection name (e.g. "Poniżej 2.5", "Powyżej 1.5")
        match = _RE_SB_SEL_TOTALS_LINE.search(selection.name)
        if match:
            try:
                return float(match.group(1))
            except (ValueError, TypeError):
                pass

        # 3. Check special_bet_value (e.g. "2.5", "+1.5")
        if selection.special_bet_value:
            try:
                return float(selection.special_bet_value)
            except (ValueError, TypeError):
                pass

        # 4. Check selection specifiers
        if selection.specifiers:
            for key in ("total", "handicap", "line", "hcp", "goalnr"):
                if key in selection.specifiers:
                    try:
                        return float(selection.specifiers[key])
                    except (ValueError, TypeError):
                        pass
        return None

