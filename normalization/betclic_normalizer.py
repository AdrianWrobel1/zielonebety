"""
Betclic Provider Normalizer Implementation
"""

from __future__ import annotations

import functools
from collections import defaultdict
import re
from typing import Any, List, Dict, Optional, Tuple
from domain.models import Competition, Event, Market, Selection, Odds
from normalization.base_normalizer import BaseNormalizer, NormalizedGraph
from normalization.exceptions import NormalizationError
from normalization.identity import normalize_team_name
from normalization.aliases import resolve_canonical_team_name, ALL_IDENTITY_SUFFIXES
from normalization.competitions import resolve_canonical_competition
from normalization.market_identity import normalize_player_name
from normalization.market_scope import is_allowed_market_family, get_market_family_name
from providers.betclic.models import BetclicEvent, BetclicMarket, BetclicSelection

_BETCLIC_SANITIZE_TRANS = str.maketrans({
    "\xa0": " ",
    "Ą": "A", "Ć": "C", "Ę": "E", "Ł": "L", "Ń": "N", "Ó": "O", "Ś": "S", "Ź": "Z", "Ż": "Z",
    "ą": "a", "ć": "c", "ę": "e", "ł": "l", "ń": "n", "ó": "o", "ś": "s", "ź": "z", "ż": "z",
})

# Precompiled regexes for Betclic normalizer
_RE_BC_HANDICAP = re.compile(r'(-?[0-9]+\.?[0-9]*)')
_RE_BC_PAREN = re.compile(r'\(([-+]?[0-9]+\.?[0-9]*)\)')
_RE_BC_SEL_LINE = re.compile(r'(?:powyżej|poniżej|powyzej|ponizej|over|under|\+|-)\s*(-?[0-9]+\.?[0-9]*)', re.IGNORECASE)
_RE_BC_MKT_HCP = re.compile(r'handicap\s*(?:[:\s]\s*)?([-+]?[0-9]+\.?[0-9]*)', re.IGNORECASE)
_RE_BC_MKT_LINE = re.compile(r'(?:goli|kartek|ro[zż]nych|spalonych|fauli|strza[lł][oó]w|total|over|under|suma|powyżej|poniżej|powyzej|ponizej)\s*(?:w\s+meczu\s*)?[:\-]?\s*([0-9]+\.?[0-9]*)', re.IGNORECASE)
_RE_BC_PLAYER_PROP = re.compile(r"^(.*?)\s+(Powyżej|Poniżej|Powyzej|Ponizej|Over|Under)\s+([\d]+[.,][\d]+|[\d]+)$", re.IGNORECASE)
_RE_BC_PLAYER_DOB = re.compile(r"\s*\(\d{2}/\d{2}/\d{4}\)")
_RE_BC_SPLIT_TOK = re.compile(r'[\s\-]+')


@functools.lru_cache(maxsize=16384)
def _sanitize_text_cached(text: str) -> str:
    """Removes Polish diacritics and non-breaking spaces for robust matching."""
    return text.translate(_BETCLIC_SANITIZE_TRANS)


@functools.lru_cache(maxsize=16384)
def _get_team_patterns_betclic(team_name: str) -> Tuple[Tuple[str, ...], Tuple[str, ...], Tuple[str, ...]]:
    """Caches candidate strings and pattern checks for Betclic team matching in market names."""
    if not team_name or not team_name.strip():
        return (), (), ()
    norm_team, tok_team = normalize_team_name(team_name)
    canon_team, ctok_team = resolve_canonical_team_name(norm_team, tok_team)
    candidates = set()
    if norm_team:
        candidates.add(norm_team.upper())
    if canon_team:
        candidates.add(canon_team.upper())
    raw_t = _sanitize_text_cached(team_name.strip().upper())
    if raw_t:
        candidates.add(raw_t)

    valid_cands = tuple(c for c in candidates if len(c) >= 2)
    patterns = []
    for cand in valid_cands:
        patterns.extend([
            f"- {cand}",
            f": {cand}",
            f"({cand})",
            f"{cand} -",
            f"{cand}:",
            f" {cand} ",
            f"GOLE: {cand}",
            f"LICZBA GOLI: {cand}",
            f"LICZBA GOLI - {cand}",
            f"LICZBA GOLI ({cand})",
        ])
    token_phrases = (" ".join(ctok_team).upper(),) if len(ctok_team) >= 2 else ()
    return valid_cands, tuple(patterns), token_phrases


@functools.lru_cache(maxsize=8192)
def _get_player_selection_strip_candidates(home: str, away: str) -> Tuple[str, ...]:
    """Generates and caches candidate prefix strings to strip from player selection names."""
    candidates = []
    for t in (home, away):
        if t and t.strip():
            t_clean = t.strip()
            norm_t, _ = normalize_team_name(t_clean)
            canon_t, _ = resolve_canonical_team_name(norm_t, ())
            candidates.extend([t_clean.lower(), norm_t.lower(), canon_t.lower()])
            tokens = [tok for tok in _RE_BC_SPLIT_TOK.split(f"{t_clean} {norm_t} {canon_t}".lower()) if len(tok) >= 3]
            candidates.extend(tokens)
    candidates.extend(["sporting lizbona", "rio ave fc", "sporting", "rio ave", "lizbona", "fc"])
    unique = sorted(set(candidates), key=len, reverse=True)
    return tuple(unique)


@functools.lru_cache(maxsize=131072)
def _matches_team_in_market_name_cached(market_name_clean: str, team_name: Optional[str]) -> bool:
    """Deterministically checks if team identity is referenced in the market name using Stage 26 aliases."""
    if not team_name or not market_name_clean:
        return False

    valid_cands, patterns, token_phrases = _get_team_patterns_betclic(team_name)
    if not valid_cands:
        return False

    m_upper = market_name_clean.upper()
    spaced_upper = f" {m_upper} "

    if any(p in spaced_upper for p in patterns):
        return True

    for cand in valid_cands:
        if m_upper.startswith((f"{cand} ", f"{cand}-", f"{cand}:")):
            return True
        if m_upper.endswith((f" {cand}", f"-{cand}", f":{cand}")):
            return True

    for phrase in token_phrases:
        if phrase in m_upper:
            return True

    return False


class BetclicNormalizer(BaseNormalizer):
    """Normalizes Betclic-specific provider models into the canonical domain graph."""

    MARKET_TYPE_MAP: Dict[str, str] = {
        # 1X2
        "MATCH_RESULT": "1X2",
        "1X2": "1X2",
        "WYNIK MECZU": "1X2",
        "WYNIK_MECZU": "1X2",
        "MECZ": "1X2",
        "WYNIK MECZU (Z WYŁĄCZENIEM DOGRYWKI)": "1X2",
        "WYNIK MECZU (Z WYLACZENIEM DOGRYWKI)": "1X2",
        "WYNIK MECZU (Z WYŁACZENIEM DOGRYWKI)": "1X2",
        "WYNIK MECZU (BEZ DOGRYWKI)": "1X2",

        # TOTALS
        "TOTAL_GOALS": "TOTALS",
        "OVER_UNDER": "TOTALS",
        "TOTALS": "TOTALS",
        "LICZBA GOLI": "TOTALS",
        "LICZBA_GOLI": "TOTALS",
        "LICZBA GOLI W MECZU": "TOTALS",
        "GOLE POWYŻEJ/PONIŻEJ": "TOTALS",
        "GOLE POWYZEJ/PONIZEJ": "TOTALS",
        "POWYŻEJ/PONIŻEJ": "TOTALS",
        "POWYZEJ/PONIZEJ": "TOTALS",
        "SUMA GOLI": "TOTALS",

        # BTTS
        "BOTH_TEAMS_TO_SCORE": "BTTS",
        "BTTS": "BTTS",
        "OBIE DRUŻYNY STRZELĄ": "BTTS",
        "OBIE DRUZYNY STRZELA": "BTTS",
        "OBIE_DRUŻYNY_STRZELĄ": "BTTS",
        "OBIE_DRUZYNY_STRZELA": "BTTS",
        "OBIE DRUŻYNY STRZELĄ GOLA": "BTTS",
        "OBIE DRUZYNY STRZELA GOLA": "BTTS",
        "OBA ZESPOŁY STRZELĄ GOLA": "BTTS",
        "OBA ZESPOLY STRZELA GOLA": "BTTS",
        "OBA ZESPOŁY STRZELĄ": "BTTS",
        "OBA ZESPOLY STRZELA": "BTTS",

        # DOUBLE CHANCE
        "DOUBLE_CHANCE": "DOUBLE_CHANCE",
        "PODWÓJNA SZANSA": "DOUBLE_CHANCE",
        "PODWOJNA SZANSA": "DOUBLE_CHANCE",
        "PODWÓJNA_SZANSA": "DOUBLE_CHANCE",
        "PODWOJNA_SZANSA": "DOUBLE_CHANCE",

        # DRAW NO BET
        "DRAW_NO_BET": "DRAW_NO_BET",
        "DNB": "DRAW_NO_BET",
        "ZAKŁAD BEZ REMISU": "DRAW_NO_BET",
        "ZAKLAD BEZ REMISU": "DRAW_NO_BET",
        "REMIS BEZ ZAKŁADU": "DRAW_NO_BET",
        "REMIS BEZ ZAKLADU": "DRAW_NO_BET",

        # HALF TIME RESULT
        "HALF_TIME_RESULT": "HALF_TIME_RESULT",
        "HT_RESULT": "HALF_TIME_RESULT",
        "WYNIK 1. POŁOWY": "HALF_TIME_RESULT",
        "WYNIK 1. POLOWY": "HALF_TIME_RESULT",
        "1. POŁOWA - WYNIK": "HALF_TIME_RESULT",
        "1. POLOWA - WYNIK": "HALF_TIME_RESULT",
        "WYNIK DO PRZERWY": "HALF_TIME_RESULT",

        # HANDICAP
        "HANDICAP": "HANDICAP",
        "HANDICAP_1X2": "HANDICAP",
        "HANDICAP EUROPEJSKI": "HANDICAP",
        "EUROPEAN_HANDICAP": "HANDICAP",
        "ASIAN_HANDICAP": "ASIAN_HANDICAP",
        "HANDICAP AZJATYCKI": "ASIAN_HANDICAP",

        # PLAYER PROPS
        "PLAYER_GOALS": "PLAYER_GOALS",
        "STRZELEC GOLA": "PLAYER_GOALS",
        "STRZELEC": "PLAYER_GOALS",
        "STRZELI GOLA": "PLAYER_GOALS",
        "ZAWODNIK - STRZELI GOLA": "PLAYER_GOALS",
        "ZAWODNIK STRZELI GOLA": "PLAYER_GOALS",
        "PLAYER_SHOTS": "PLAYER_SHOTS",
        "STRZAŁY ZAWODNIKA": "PLAYER_SHOTS",
        "STRZALY ZAWODNIKA": "PLAYER_SHOTS",
        "LICZBA STRZAŁÓW ZAWODNIKA": "PLAYER_SHOTS",
        "LICZBA STRZALOW ZAWODNIKA": "PLAYER_SHOTS",
        "LICZBA STRZAŁÓW ZAWODNIKA (OPTA)": "PLAYER_SHOTS",
        "LICZBA STRZALOW ZAWODNIKA (OPTA)": "PLAYER_SHOTS",
        "PLAYER_SHOTS_ON_TARGET": "PLAYER_SHOTS_ON_TARGET",
        "CELNE STRZAŁY ZAWODNIKA": "PLAYER_SHOTS_ON_TARGET",
        "CELNE STRZALY ZAWODNIKA": "PLAYER_SHOTS_ON_TARGET",
        "LICZBA CELNYCH STRZAŁÓW ZAWODNIKA": "PLAYER_SHOTS_ON_TARGET",
        "LICZBA CELNYCH STRZALOW ZAWODNIKA": "PLAYER_SHOTS_ON_TARGET",
        "LICZBA CELNYCH STRZAŁÓW ZAWODNIKA (OPTA)": "PLAYER_SHOTS_ON_TARGET",
        "LICZBA CELNYCH STRZALOW ZAWODNIKA (OPTA)": "PLAYER_SHOTS_ON_TARGET",
        "LICZBA CELNYCH STRZAŁÓW ZAWODNIKA SPOZA POLA KARNEGO": "PLAYER_SHOTS_ON_TARGET",
        "LICZBA CELNYCH STRZAŁÓW ZAWODNIKA NOGĄ": "PLAYER_SHOTS_ON_TARGET",
        "LICZBA CELNYCH STRZAŁÓW ZAWODNIKA GŁOWĄ": "PLAYER_SHOTS_ON_TARGET",
        "PLAYER_ASSISTS": "PLAYER_ASSISTS",
        "ASYSTY ZAWODNIKA": "PLAYER_ASSISTS",
        "LICZBA ASYST ZAWODNIKA": "PLAYER_ASSISTS",
        "ZAWODNIK ZALICZY ASYSTĘ": "PLAYER_ASSISTS",
        "ZAWODNIK ZALICZY ASYSTE": "PLAYER_ASSISTS",
        "PLAYER_CARDS": "PLAYER_CARDS",
        "KARTKA DLA ZAWODNIKA": "PLAYER_CARDS",
        "LICZBA KARTEK ZAWODNIKA": "PLAYER_CARDS",
        "ZAWODNIK - OTRZYMA KARTKĘ": "PLAYER_CARDS",
        "ZAWODNIK - OTRZYMA KARTKE": "PLAYER_CARDS",
        "PLAYER_FOULS": "PLAYER_FOULS",
        "FAULE ZAWODNIKA": "PLAYER_FOULS",
        "LICZBA FAULI ZAWODNIKA": "PLAYER_FOULS",
        "LICZBA FAULI ZAWODNIKA  (OPTA)": "PLAYER_FOULS",
        "LICZBA FAULI ZAWODNIKA (OPTA)": "PLAYER_FOULS",
        "PLAYER_PASSES": "PLAYER_PASSES",
        "PODANIA ZAWODNIKA": "PLAYER_PASSES",
        "LICZBA PODAŃ ZAWODNIKA": "PLAYER_PASSES",
        "LICZBA PODAN ZAWODNIKA": "PLAYER_PASSES",
        "LICZBA PODAŃ ZAWODNIKA (OPTA)": "PLAYER_PASSES",
        "LICZBA PODAN ZAWODNIKA (OPTA)": "PLAYER_PASSES",
        "LICZBA CELNYCH PODAŃ ZAWODNIKA (OPTA)": "PLAYER_PASSES",
        "LICZBA CELNYCH PODAN ZAWODNIKA (OPTA)": "PLAYER_PASSES",
        # CORRECT SCORE (Stage 31 — previously unmapped, fell back to raw Polish string)
        "CORRECT_SCORE": "CORRECT_SCORE",
        "DOKŁADNY WYNIK": "CORRECT_SCORE",
        "DOKLADNY WYNIK": "CORRECT_SCORE",
        "WYNIK DOKŁADNY": "CORRECT_SCORE",
        "WYNIK DOKLADNY": "CORRECT_SCORE",

        # ODD/EVEN (Stage 31 — previously unmapped, fell back to raw Polish string)
        "ODD_EVEN": "ODD_EVEN",
        "ODD/EVEN": "ODD_EVEN",
        "PARZYSTE/NIEPARZYSTE": "ODD_EVEN",
        "PARZYSTE / NIEPARZYSTE": "ODD_EVEN",
        "PARZYSTE-NIEPARZYSTE": "ODD_EVEN",
        "LICZBA GOLI PARZYSTA/NIEPARZYSTA": "ODD_EVEN",
        "LICZBA GOLI PARZYSTA / NIEPARZYSTA": "ODD_EVEN",

        # HALF TIME FULL TIME (HT/FT) — must NEVER be standard 1X2
        "HALF_TIME_FULL_TIME": "HALF_TIME_FULL_TIME",
        "HT_FT": "HALF_TIME_FULL_TIME",
        "WYNIK MECZU POŁOWA / CAŁY": "HALF_TIME_FULL_TIME",
        "WYNIK MECZU POLOWA / CALY": "HALF_TIME_FULL_TIME",
        "WYNIK POŁOWA / CAŁY": "HALF_TIME_FULL_TIME",
        "WYNIK POLOWA / CALY": "HALF_TIME_FULL_TIME",
        "POŁOWA / CAŁY": "HALF_TIME_FULL_TIME",
        "POLOWA / CALY": "HALF_TIME_FULL_TIME",
        "POŁOWA/CAŁY": "HALF_TIME_FULL_TIME",
        "POLOWA/CALY": "HALF_TIME_FULL_TIME",

        # COMBOS & SPECIALS — must NEVER be standard 1X2 or BTTS
        "WYNIK MECZU & OBA ZESPOŁY STRZELĄ": "COMBO_1X2_BTTS",
        "WYNIK MECZU & OBIE DRUŻYNY STRZELĄ": "COMBO_1X2_BTTS",
        "WYNIK MECZU & OBIE DRUZYNY STRZELA": "COMBO_1X2_BTTS",
        "WYNIK MECZU & OBA ZESPOLY STRZELA": "COMBO_1X2_BTTS",
        "WYNIK/OBA ZESPOŁY STRZELĄ - 1. POŁOWA": "COMBO_1X2_BTTS_HT",
        "WYNIK/OBA ZESPOLY STRZELA - 1. POLOWA": "COMBO_1X2_BTTS_HT",
        "WYNIK/OBA ZESPOŁY STRZELĄ - 2. POŁOWA": "COMBO_1X2_BTTS_2H",
        "WYNIK/OBA ZESPOLY STRZELA - 2. POLOWA": "COMBO_1X2_BTTS_2H",
        "WYNIK MECZU & LICZBA GOLI": "COMBO_1X2_TOTALS",
        "WYNIK MECZU - XTRA WYGRANA": "XTRA_1X2",
        "OBA ZESPOŁY STRZELĄ Z RZUTU KARNEGO": "PENALTY_BTTS",
        "OBA ZESPOLY STRZELA Z RZUTU KARNEGO": "PENALTY_BTTS",
        "OBIE DRUŻYNY STRZELĄ Z RZUTU KARNEGO": "PENALTY_BTTS",
        "OBIE DRUZYNY STRZELA Z RZUTU KARNEGO": "PENALTY_BTTS",
        "OBA ZESPOŁY STRZELĄ Z RZUTU WOLNEGO": "FREE_KICK_BTTS",
        "OBA ZESPOLY STRZELA Z RZUTU WOLNEGO": "FREE_KICK_BTTS",
        "OBA ZESPOŁY STRZELĄ W 1. I 2. POŁOWIE": "BOTH_HALVES_BTTS",
        "OBA ZESPOLY STRZELA W 1. I 2. POLOWIE": "BOTH_HALVES_BTTS",
        "OBA ZESPOŁY STRZELĄ W OBU POŁOWACH": "BOTH_HALVES_BTTS",
        "OBA ZESPOLY STRZELA W OBU POLOWACH": "BOTH_HALVES_BTTS",
        "OBIE DRUŻYNY STRZELĄ W OBU POŁOWACH": "BOTH_HALVES_BTTS",
        "OBIE DRUZYNY STRZELA W OBU POLOWACH": "BOTH_HALVES_BTTS",
        "OBIE DRUŻYNY STRZELĄ PO 2+": "BTTS_2_PLUS",
        "OBIE DRUZYNY STRZELA PO 2+": "BTTS_2_PLUS",
        "OBA ZESPOŁY STRZELĄ GOLA / LICZBA BRAMEK": "COMBO_BTTS_TOTALS",
        "OBA ZESPOLY STRZELA GOLA / LICZBA BRAMEK": "COMBO_BTTS_TOTALS",
        "PODWÓJNA SZANSA & OBA ZESPOŁY STRZELĄ": "COMBO_DC_BTTS",
        "PODWOJNA SZANSA & OBA ZESPOLY STRZELA": "COMBO_DC_BTTS",
        "PODWÓJNA SZANSA & POWYŻEJ/PONIŻEJ": "COMBO_DC_TOTALS",
        "PODWOJNA SZANSA & POWYZEJ/PONIZEJ": "COMBO_DC_TOTALS",
        "PODWÓJNA SZANSA (1.POŁOWA LUB MECZ)": "DOUBLE_CHANCE_COMBO",
        "PODWOJNA SZANSA (1.POLOWA LUB MECZ)": "DOUBLE_CHANCE_COMBO",

        "PLAYER_TACKLES": "PLAYER_TACKLES",
        "ODBIORY ZAWODNIKA": "PLAYER_TACKLES",
        "LICZBA ODBIORÓW ZAWODNIKA": "PLAYER_TACKLES",
        "LICZBA ODBIOROW ZAWODNIKA": "PLAYER_TACKLES",
        "LICZBA ODBIORÓW ZAWODNIKA (OPTA)": "PLAYER_TACKLES",
        "LICZBA ODBIOROW ZAWODNIKA (OPTA)": "PLAYER_TACKLES",
    }

    SELECTION_TYPE_MAP: Dict[str, str] = {
        "HOME": "HOME",
        "1": "HOME",
        "DRAW": "DRAW",
        "X": "DRAW",
        "REMIS": "DRAW",
        "AWAY": "AWAY",
        "2": "AWAY",
        "OVER": "OVER",
        "POWYŻEJ": "OVER",
        "POWYZEJ": "OVER",
        "+": "OVER",
        "UNDER": "UNDER",
        "PONIŻEJ": "UNDER",
        "PONIZEJ": "UNDER",
        "-": "UNDER",
        "YES": "YES",
        "TAK": "YES",
        "NO": "NO",
        "NIE": "NO",
        "1X": "HOME_DRAW",
        "HOME_DRAW": "HOME_DRAW",
        "1X_HOME_DRAW": "HOME_DRAW",
        "12": "HOME_AWAY",
        "HOME_AWAY": "HOME_AWAY",
        "12_HOME_AWAY": "HOME_AWAY",
        "X2": "DRAW_AWAY",
        "2X": "DRAW_AWAY",
        "DRAW_AWAY": "DRAW_AWAY",
        "X2_DRAW_AWAY": "DRAW_AWAY",
    }

    def normalize_event(self, provider_event: BetclicEvent, include_markets: bool = True) -> NormalizedGraph:
        """Transforms a BetclicEvent into a canonical entity graph."""
        if not isinstance(provider_event, BetclicEvent):
            raise NormalizationError(f"Expected BetclicEvent instance, got {type(provider_event)}")

        # 1. Extract Participants
        home = provider_event.home_team
        away = provider_event.away_team
        if not home or not away:
            # Parse from event name if team names not separated
            parts = provider_event.name.split(" vs ")
            if len(parts) == 2:
                home, away = parts[0].strip(), parts[1].strip()
            else:
                parts_dash = provider_event.name.split(" - ")
                if len(parts_dash) == 2:
                    home, away = parts_dash[0].strip(), parts_dash[1].strip()
                else:
                    home = provider_event.name
                    away = "Unknown"

        # 2. Normalize Competition via Canonical Registry
        comp_provider_ids = {}
        raw_meta = getattr(provider_event, "raw_payload", None) or getattr(provider_event, "raw_metadata", None)
        if raw_meta and isinstance(raw_meta, dict):
            comp_obj = raw_meta.get("competition", {})
            if isinstance(comp_obj, dict) and comp_obj.get("id"):
                comp_provider_ids["betclic"] = str(comp_obj["id"]).strip()

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

        competition = Competition(
            name=comp_res.canonical_name,
            sport=provider_event.sport_name or "Football",
            country=comp_res.country,
            provider_ids=comp_provider_ids,
            metadata=comp_metadata,
        )

        # 3. Normalize Event
        event_metadata = {}
        if provider_event.name:
            event_metadata["betclic"] = {"raw_event_name": provider_event.name}

        event = Event(
            competition_id=competition.internal_id,
            home_participant=home,
            away_participant=away,
            scheduled_start=provider_event.start_time,
            provider_ids={"betclic": provider_event.provider_event_id},
            metadata=event_metadata,
        )

        if not include_markets:
            return NormalizedGraph(
                competition=competition,
                event=event,
                markets=[],
                selections=[],
                odds_list=[],
            )

        markets: List[Market] = []
        selections: List[Selection] = []
        odds_list: List[Odds] = []

        # 4. Normalize Markets, Selections, and Odds
        for bm in provider_event.markets:
            # Completely ignore/reject Xtra Wygrana promo markets (e.g. Strzelec - Xtra Wygrana)
            if "XTRA" in (bm.name or "").upper() or "XTRA" in (bm.market_type_code or "").upper():
                continue

            canonical_mkt_type = self._resolve_market_type(bm)
            mkt_meta = self._extract_market_metadata(bm, canonical_mkt_type, home, away)

            # Stage 50: Centralized Market Allowlist Filter
            metric = mkt_meta.get("metric", "GOALS")
            scope = mkt_meta.get("scope", "MATCH")
            if not is_allowed_market_family(canonical_mkt_type, metric=metric, scope=scope, raw_name=bm.name):
                continue

            # Check if this is a line-dependent multi-line market (TOTALS, HANDICAP)
            if canonical_mkt_type in ("TOTALS", "HANDICAP", "ASIAN_HANDICAP") and bm.selections:
                mkt_line_from_name = self._extract_market_line(bm, canonical_mkt_type)
                if mkt_line_from_name is not None:
                    line_groups: Dict[Optional[float], List[BetclicSelection]] = {mkt_line_from_name: list(bm.selections)}
                else:
                    # Group selections by distinct extracted line
                    line_groups = defaultdict(list)
                    for bs in bm.selections:
                        sel_line = self._extract_selection_line(bs)
                        line_groups[sel_line].append(bs)

                for m_line, group_sels in line_groups.items():
                    if len(line_groups) > 1 and bm.provider_market_id and m_line is not None:
                        m_id = f"{bm.provider_market_id}_{m_line}"
                    else:
                        m_id = bm.provider_market_id

                    # Clone metadata per market instance
                    curr_mkt_meta = dict(mkt_meta)
                    market = Market(
                        event_id=event.internal_id,
                        market_type=canonical_mkt_type,
                        line=m_line,
                        status="OPEN" if bm.is_open else "CLOSED",
                        provider_ids={"betclic": m_id} if m_id else {},
                        metadata=curr_mkt_meta,
                    )
                    markets.append(market)

                    for bs in group_sels:
                        canonical_sel_type = self._resolve_selection_type(bs, home, away)
                        bs_line = self._extract_selection_line(bs) or m_line

                        participant = None
                        if canonical_sel_type == "HOME":
                            participant = home
                        elif canonical_sel_type == "AWAY":
                            participant = away

                        selection = Selection(
                            market_id=market.internal_id,
                            selection_type=canonical_sel_type,
                            line=bs_line,
                            participant=participant,
                            provider_ids={"betclic": bs.provider_selection_id} if bs.provider_selection_id else {},
                        )
                        selections.append(selection)

                        if bs.odds and getattr(bs.odds, "is_active", True) and bs.odds.decimal_odds > 1.0:
                            odds = Odds(
                                selection_id=selection.internal_id,
                                bookmaker="betclic",
                                decimal_odds=bs.odds.decimal_odds,
                                timestamp=bs.odds.timestamp or event.created_at,
                            )
                            odds_list.append(odds)

            elif canonical_mkt_type.startswith("PLAYER_"):
                # Handle player prop markets (e.g. Shots, SOT, Fouls, Cards, Passes, Tackles, Goals, Assists)
                for bs in bm.selections:
                    raw_s_name = bs.name.strip()
                    parsed_player, parsed_side, parsed_line = self._parse_player_selection_name(raw_s_name, home, away)

                    # If selection name is just Tak/Nie/Yes/No, extract player from market name
                    if raw_s_name.upper() in ("TAK", "NIE", "YES", "NO", "1", "2"):
                        player_name = None
                        if ":" in bm.name:
                            player_name = bm.name.split(":")[-1].strip()
                        elif " - " in bm.name:
                            player_name = bm.name.split(" - ")[-1].strip()
                        sel_side = "YES" if raw_s_name.upper() in ("TAK", "YES") else "NO"
                        m_line = self._extract_selection_line(bs) or self._extract_market_line(bm, canonical_mkt_type) or bs.handicap
                    elif parsed_player:
                        player_name = parsed_player
                        sel_side = parsed_side or "OVER"
                        m_line = parsed_line
                    else:
                        player_name = None
                        if ":" in bm.name:
                            player_name = bm.name.split(":")[-1].strip()
                        elif " - " in bm.name:
                            player_name = bm.name.split(" - ")[-1].strip()
                        else:
                            player_name = raw_s_name
                        sel_side = self._resolve_selection_type(bs, home, away)
                        m_line = self._extract_selection_line(bs) or self._extract_market_line(bm, canonical_mkt_type) or bs.handicap

                    m_id = f"{bm.provider_market_id}_{bs.provider_selection_id}" if bm.provider_market_id else bs.provider_selection_id
                    p_meta = dict(mkt_meta)
                    p_meta["scope"] = "PLAYER"
                    p_meta["player_name"] = player_name
                    p_meta["raw_name"] = bm.name

                    line_float = float(m_line) if m_line is not None and str(m_line).replace(".", "", 1).isdigit() else None

                    p_market = Market(
                        event_id=event.internal_id,
                        market_type=canonical_mkt_type,
                        line=line_float,
                        status="OPEN" if bm.is_open else "CLOSED",
                        provider_ids={"betclic": m_id} if m_id else {},
                        metadata=p_meta,
                    )
                    markets.append(p_market)

                    selection = Selection(
                        market_id=p_market.internal_id,
                        selection_type=sel_side,
                        line=line_float,
                        participant=player_name,
                        provider_ids={"betclic": bs.provider_selection_id} if bs.provider_selection_id else {},
                    )
                    selections.append(selection)

                    if bs.odds and getattr(bs.odds, "is_active", True) and bs.odds.decimal_odds > 1.0:
                        odds = Odds(
                            selection_id=selection.internal_id,
                            bookmaker="betclic",
                            decimal_odds=bs.odds.decimal_odds,
                            timestamp=bs.odds.timestamp or event.created_at,
                        )
                        odds_list.append(odds)

            else:
                # Standard single-line or line-independent market
                market_line = self._extract_market_line(bm, canonical_mkt_type)
                player_name = None
                curr_mkt_meta = dict(mkt_meta)
                market = Market(
                    event_id=event.internal_id,
                    market_type=canonical_mkt_type,
                    line=market_line,
                    status="OPEN" if bm.is_open else "CLOSED",
                    provider_ids={"betclic": bm.provider_market_id} if bm.provider_market_id else {},
                    metadata=curr_mkt_meta,
                )
                m_sels: List[Selection] = []
                m_odds: List[Odds] = []
                for bs in bm.selections:
                    canonical_sel_type = self._resolve_selection_type(bs, home, away)
                    bs_line = self._extract_selection_line(bs) or bs.handicap

                    participant = None
                    if canonical_sel_type == "HOME":
                        participant = home
                    elif canonical_sel_type == "AWAY":
                        participant = away

                    selection = Selection(
                        market_id=market.internal_id,
                        selection_type=canonical_sel_type,
                        line=float(bs_line) if bs_line is not None and str(bs_line).replace(".", "", 1).isdigit() else None,
                        participant=participant,
                        provider_ids={"betclic": bs.provider_selection_id} if bs.provider_selection_id else {},
                    )
                    m_sels.append(selection)

                    if bs.odds and getattr(bs.odds, "is_active", True) and bs.odds.decimal_odds > 1.0:
                        odds = Odds(
                            selection_id=selection.internal_id,
                            bookmaker="betclic",
                            decimal_odds=bs.odds.decimal_odds,
                            timestamp=bs.odds.timestamp or event.created_at,
                        )
                        m_odds.append(odds)

                # Strict partition integrity check: verify outcome set matches required canonical partition
                sel_types_set = {s.selection_type for s in m_sels}
                is_valid_partition = True
                if canonical_mkt_type in ("1X2", "HALF_TIME_RESULT"):
                    if sel_types_set != {"HOME", "DRAW", "AWAY"} or len(m_sels) != 3:
                        is_valid_partition = False
                elif canonical_mkt_type == "BTTS":
                    if sel_types_set != {"YES", "NO"} or len(m_sels) != 2:
                        is_valid_partition = False
                elif canonical_mkt_type == "DRAW_NO_BET":
                    if sel_types_set != {"HOME", "AWAY"} or len(m_sels) != 2:
                        is_valid_partition = False
                elif canonical_mkt_type == "DOUBLE_CHANCE":
                    if sel_types_set != {"HOME_DRAW", "HOME_AWAY", "DRAW_AWAY"} or len(m_sels) != 3:
                        is_valid_partition = False
                elif canonical_mkt_type == "ODD_EVEN":
                    if sel_types_set != {"ODD", "EVEN"} or len(m_sels) != 2:
                        is_valid_partition = False

                if is_valid_partition:
                    markets.append(market)
                    selections.extend(m_sels)
                    odds_list.extend(m_odds)
                else:
                    # Corrupted / non-partition market: retain as non-canonical raw type
                    fallback_market = Market(
                        event_id=event.internal_id,
                        market_type=bm.name or canonical_mkt_type,
                        line=market_line,
                        status="OPEN" if bm.is_open else "CLOSED",
                        provider_ids={"betclic": bm.provider_market_id} if bm.provider_market_id else {},
                        metadata=curr_mkt_meta,
                    )
                    markets.append(fallback_market)
                    for s in m_sels:
                        s.market_id = fallback_market.internal_id
                    selections.extend(m_sels)
                    odds_list.extend(m_odds)

        return NormalizedGraph(
            competition=competition,
            event=event,
            markets=markets,
            selections=selections,
            odds_list=odds_list,
        )

    def _extract_market_metadata(
        self,
        market: BetclicMarket,
        canonical_mkt_type: str,
        home: Optional[str] = None,
        away: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Extracts period, scope, and participant metadata from Betclic market."""
        meta: Dict[str, Any] = {"raw_name": market.name}
        name_raw = (market.name or "").strip().upper()
        code_raw = (market.market_type_code or "").strip().upper()
        clean_name = self._sanitize_text(f"{name_raw} {code_raw}").upper()

        # 1. Metric extraction
        if any(k in clean_name for k in ("RZUTOW ROZNYCH", "RZUTÓW ROŻNYCH", "ROZNE", "ROŻNE", "CORNER")):
            meta["metric"] = "CORNERS"
        elif any(k in clean_name for k in ("PUNKT", "PUNKTY", "PUNKTOW", "PUNKTÓW", "BOOKING POINT", "CARD POINT")) and any(k in clean_name for k in ("KARTK", "CARD")):
            meta["metric"] = "CARD_POINTS"
        elif any(k in clean_name for k in ("KARTK", "KARTKI", "KARTEK", "CARD", "ZOLTYCH KARTEK", "ŻÓŁTYCH KARTEK")):
            meta["metric"] = "CARDS"
        elif any(k in clean_name for k in ("SPALONYCH", "SPALONE", "SPALONY", "OFFSIDE")):
            meta["metric"] = "OFFSIDES"
        elif any(k in clean_name for k in ("CELNYCH STRZALOW", "CELNYCH STRZAŁÓW", "CELNE STRZALY", "CELNE STRZAŁY", "SHOTS ON TARGET")):
            meta["metric"] = "SHOTS_ON_TARGET"
        elif any(k in clean_name for k in ("STRZALOW", "STRZAŁÓW", "STRZALY", "STRZAŁY", "SHOT")):
            meta["metric"] = "SHOTS"
        elif any(k in clean_name for k in ("FAULI", "FAULE", "FAUL", "FOUL")):
            meta["metric"] = "FOULS"
        elif any(k in clean_name for k in ("PODAN", "PODAŃ", "PODANIA", "PASS")):
            meta["metric"] = "PASSES"
        else:
            meta["metric"] = "GOALS"

        # 2. Period extraction
        if any(k in clean_name for k in ("1. POLOWA", "1.POLOWA", "1. POŁOWA", "1.POŁOWA", "1ST HALF", "FIRST HALF", "DO PRZERWY", "PIERWSZA POLOWA", "PIERWSZA POŁOWA")):
            meta["period"] = "FIRST_HALF"
        elif any(k in clean_name for k in ("2. POLOWA", "2.POLOWA", "2. POŁOWA", "2.POŁOWA", "2ND HALF", "SECOND HALF", "DRUGA POLOWA", "DRUGA POŁOWA")):
            meta["period"] = "SECOND_HALF"
        else:
            meta["period"] = "FULL_TIME"

        # 3. Scope & Participant Role extraction
        is_home_team = False
        is_away_team = False

        if any(k in clean_name for k in ("GOSPODARZ", "DRUZYNA 1", "DRUŻYNA 1", "TEAM 1")):
            is_home_team = True
        elif any(k in clean_name for k in ("GOSC", "GOŚC", "DRUZYNA 2", "DRUŻYNA 2", "TEAM 2")):
            is_away_team = True
        else:
            # Match specific team names with Stage 26 alias support
            h_match = self._matches_team_in_market_name(clean_name, home)
            a_match = self._matches_team_in_market_name(clean_name, away)

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

    def _matches_team_in_market_name(self, market_name_clean: str, team_name: Optional[str]) -> bool:
        """Deterministically checks if team identity is referenced in the market name using Stage 26 aliases."""
        return _matches_team_in_market_name_cached(market_name_clean, team_name)

    def _resolve_market_type(self, market: BetclicMarket) -> str:
        """Resolve Betclic market to canonical market type strictly and unambiguously."""
        name_raw = market.name.strip().upper()
        code_raw = market.market_type_code.strip().upper()

        # 1. Exact lookup on raw market name / type code
        if name_raw in self.MARKET_TYPE_MAP:
            return self.MARKET_TYPE_MAP[name_raw]
        if code_raw in self.MARKET_TYPE_MAP:
            return self.MARKET_TYPE_MAP[code_raw]

        # 2. Cleaned name check (without diacritics / symbols)
        clean_name = self._sanitize_text(name_raw).upper()
        if clean_name in self.MARKET_TYPE_MAP:
            return self.MARKET_TYPE_MAP[clean_name]

        clean_code = self._sanitize_text(code_raw).upper()
        if clean_code in self.MARKET_TYPE_MAP:
            return self.MARKET_TYPE_MAP[clean_code]

        # Check prefixes for player props (e.g. "Kartka dla zawodnika: Ante Budimir", "Faule zawodnika: ...")
        for separator in (":", " - "):
            if separator in clean_name:
                prefix = clean_name.split(separator)[0].strip()
                if prefix in self.MARKET_TYPE_MAP:
                    return self.MARKET_TYPE_MAP[prefix]

        full_text = f"{clean_name} {clean_code}".upper()

        # 3. Explicit guard: Compound, combo, multi-condition, special, or HT/FT markets
        # MUST NEVER be collapsed into standard 1X2, BTTS, TOTALS, or DNB
        is_special_or_combo = any(
            k in full_text
            for k in (
                "POŁOWA / CAŁY",
                "POLOWA / CALY",
                "POŁOWA/CAŁY",
                "POLOWA/CALY",
                "HT/FT",
                " & ",
                " LUB ",
                " ALBO ",
                " ORAZ ",
                "XTRA",
                "RZUTU KARNEGO",
                "RZUT KARNY",
                "RZUTU WOLNEGO",
                "W OBU POŁOWACH",
                "W OBU POLOWACH",
                "1. I 2. POŁOWIE",
                "1. I 2. POLOWIE",
                "PRZEDZIAŁY",
                "PRZEDZIALY",
                "DOKŁADNA",
                "DOKLADNA",
                "SAMOBÓJCZ",
                "SAMOBOJCZ",
                "PIERWSZY",
                "OSTATNI",
                "MINUTY",
                "PO 2+",
                "PO 3+",
                "POZOSTAŁY CZAS",
                "POZOSTALY CZAS",
            )
        )

        if is_special_or_combo:
            if "POŁOWA / CAŁY" in full_text or "POLOWA / CALY" in full_text or "HT/FT" in full_text:
                return "HALF_TIME_FULL_TIME"
            if "RZUTU KARNEGO" in full_text or "RZUT KARNY" in full_text:
                return "PENALTY_SPECIAL"
            if " & " in full_text or " ORAZ " in full_text:
                return "COMBO_MARKET"
            return name_raw

        # 4. Strict Standard Canonical Types
        # Half Time Result
        if clean_name in ("WYNIK 1. POŁOWY", "WYNIK 1. POLOWY", "1. POŁOWA - WYNIK", "1. POLOWA - WYNIK", "WYNIK DO PRZERWY", "HALF TIME RESULT", "HALF_TIME_RESULT", "HT_RESULT") or \
           clean_code in ("WYNIK 1. POŁOWY", "WYNIK 1. POLOWY", "1. POŁOWA - WYNIK", "HALF_TIME_RESULT", "HT_RESULT"):
            return "HALF_TIME_RESULT"

        # Both Teams To Score (BTTS)
        if clean_name in ("OBIE DRUŻYNY STRZELĄ", "OBIE DRUZYNY STRZELA", "OBIE DRUŻYNY STRZELĄ GOLA", "OBIE DRUZYNY STRZELA GOLA", "OBA ZESPOŁY STRZELĄ", "OBA ZESPOLY STRZELA", "OBA ZESPOŁY STRZELĄ GOLA", "OBA ZESPOLY STRZELA GOLA", "BTTS", "BOTH TEAMS TO SCORE", "BOTH_TEAMS_TO_SCORE") or \
           clean_code in ("OBIE DRUŻYNY STRZELĄ", "OBIE DRUZYNY STRZELA", "BTTS", "BOTH_TEAMS_TO_SCORE"):
            return "BTTS"

        # Match Result 1X2
        if clean_name in ("WYNIK MECZU", "MECZ", "1X2", "1 X 2", "MATCH RESULT", "MATCH_RESULT", "WYNIK MECZU (Z WYŁĄCZENIEM DOGRYWKI)", "WYNIK MECZU (Z WYLACZENIEM DOGRYWKI)", "WYNIK MECZU (BEZ DOGRYWKI)", "ZWYCIĘZCA MECZU", "ZWYCIEZCA MECZU", "ZWYCIĘZCA", "ZWYCIEZCA") or \
           clean_code in ("WYNIK MECZU", "MECZ", "1X2", "1 X 2", "MATCH_RESULT"):
            return "1X2"

        # Double Chance
        if clean_name in ("PODWÓJNA SZANSA", "PODWOJNA SZANSA", "DOUBLE CHANCE", "DOUBLE_CHANCE") or \
           clean_code in ("PODWÓJNA SZANSA", "PODWOJNA SZANSA", "DOUBLE CHANCE", "DOUBLE_CHANCE"):
            return "DOUBLE_CHANCE"

        # Draw No Bet
        if clean_name in ("ZAKŁAD BEZ REMISU", "ZAKLAD BEZ REMISU", "REMIS BEZ ZAKŁADU", "REMIS BEZ ZAKLADU", "DRAW NO BET", "DRAW_NO_BET", "DNB") or \
           clean_code in ("ZAKŁAD BEZ REMISU", "ZAKLAD BEZ REMISU", "DRAW NO BET", "DRAW_NO_BET", "DNB"):
            return "DRAW_NO_BET"

        # Correct Score
        if clean_name in ("DOKŁADNY WYNIK", "DOKLADNY WYNIK", "WYNIK DOKŁADNY", "WYNIK DOKLADNY", "CORRECT SCORE", "CORRECT_SCORE"):
            return "CORRECT_SCORE"

        # Odd / Even
        if clean_name in ("PARZYSTE/NIEPARZYSTE", "PARZYSTE / NIEPARZYSTE", "PARZYSTE-NIEPARZYSTE", "LICZBA GOLI PARZYSTA/NIEPARZYSTA", "LICZBA GOLI PARZYSTA / NIEPARZYSTA", "ODD/EVEN", "ODD_EVEN"):
            return "ODD_EVEN"

        # Totals (Goals & Statistical Metrics)
        if re.match(r'^(?:LICZBA\s+GOLI|SUMA\s+GOLI|GOLE\s+POWY[ZŻ]EJ/PONI[ZŻ]EJ|POWY[ZŻ]EJ/PONI[ZŻ]EJ|TOTAL_GOALS|TOTALS|OVER_UNDER)(?:\s+[-+]?[0-9]+\.?[0-9]*)?$', clean_name) or \
           clean_code in ("TOTAL_GOALS", "TOTALS", "OVER_UNDER", "LICZBA GOLI", "SUMA GOLI"):
            return "TOTALS"

        # Statistical Over/Under totals (Corners, Cards, Offsides, Fouls, Shots, Shots on target)
        if any(k in full_text for k in ("RZUTOW ROZNYCH", "RZUTY ROZNE", "RZUTY ROŻNE", "ROZNE POWYZEJ", "ROZNE W MECZU", "ROZNE")):
            return "TOTALS"
        if any(k in full_text for k in ("LICZBA KARTEK", "SUMA KARTEK", "KARTKI POWYZEJ", "ZOLTYCH KARTEK", "KARTKI W MECZU", "KARTKI")):
            return "TOTALS"
        if any(k in full_text for k in ("LICZBA SPALONYCH", "SUMA SPALONYCH", "SPALONE POWYZEJ", "SPALONE W MECZU", "SPALONE -", "SPALONE")):
            return "TOTALS"
        if any(k in full_text for k in ("LICZBA FAULI", "SUMA FAULI", "FAULE W MECZU", "FAULE POWYZEJ", "FAULE", "FAULI", "CELNYCH STRZALOW", "CELNE STRZALY", "STRZALY CELNE", "LICZBA STRZALOW", "STRZALY W MECZU", "STRZALY", "STRZALOW")):
            return "TOTALS"

        # Team statistical totals regex pattern
        if re.search(r'(?:LICZBA|SUMA)\s+(?:RZUT[OÓ]W\s+RO[ZŻ]NYCH|KARTEK|[ZŻ][OÓ][LŁ]TYCH\s+KARTEK|SPALONYCH|FAULI|STRZA[LŁ][OÓ]W|CELNYCH\s+STRZA[LŁ][OÓ]W)', full_text):
            return "TOTALS"

        # Handicaps
        if "HANDICAP AZJATYCKI" in full_text or "ASIAN_HANDICAP" in full_text:
            return "ASIAN_HANDICAP"
        if "HANDICAP" in full_text:
            return "HANDICAP"

        return name_raw

    def _resolve_selection_type(
        self,
        selection: BetclicSelection,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
    ) -> str:
        """Resolve Betclic selection to canonical selection type strictly and unambiguously."""
        code_upper = (selection.type_code or "").strip().upper()
        name_upper = (selection.name or "").strip().upper()

        # 1. Exact lookup
        if code_upper in self.SELECTION_TYPE_MAP:
            return self.SELECTION_TYPE_MAP[code_upper]
        if name_upper in self.SELECTION_TYPE_MAP:
            return self.SELECTION_TYPE_MAP[name_upper]

        # 2. Over / Under prefix matching
        name_lower = name_upper.lower()
        if name_lower.startswith(("powyżej ", "powyzej ", "over ", "+")):
            return "OVER"
        if name_lower.startswith(("poniżej ", "ponizej ", "under ", "-")):
            return "UNDER"

        # 3. Double Chance exact and fuzzy team phrases
        if home_team and away_team:
            h_clean = home_team.strip().upper()
            a_clean = away_team.strip().upper()
            
            # Direct code matching
            if code_upper in ("1X", "HOME_DRAW"):
                return "HOME_DRAW"
            if code_upper in ("X2", "2X", "DRAW_AWAY"):
                return "DRAW_AWAY"
            if code_upper in ("12", "HOME_AWAY"):
                return "HOME_AWAY"

            # Check if selection contains home or away team with 'remis' or 'draw'
            has_remis = "REMIS" in name_upper or "DRAW" in name_upper or "X" in name_upper.split()
            has_home = self._matches_team_in_market_name(name_upper, home_team) or h_clean in name_upper
            has_away = self._matches_team_in_market_name(name_upper, away_team) or a_clean in name_upper

            if has_home and has_remis and not has_away:
                return "HOME_DRAW"
            if has_away and has_remis and not has_home:
                return "DRAW_AWAY"
            if has_home and has_away and not has_remis:
                return "HOME_AWAY"

            if name_upper in (f"{h_clean} LUB REMIS", f"REMIS LUB {h_clean}", f"{h_clean} OR DRAW", f"DRAW OR {h_clean}", "1X"):
                return "HOME_DRAW"
            if name_upper in (f"{a_clean} LUB REMIS", f"REMIS LUB {a_clean}", f"{a_clean} OR DRAW", f"DRAW OR {a_clean}", "X2", "2X"):
                return "DRAW_AWAY"
            if name_upper in (f"{h_clean} LUB {a_clean}", f"{a_clean} LUB {h_clean}", f"{h_clean} OR {a_clean}", f"{a_clean} OR {h_clean}", "12"):
                return "HOME_AWAY"

        # 4. Strict participant matching for 1X2 / DNB (NO startswith, NO substring match)
        if home_team:
            h_clean = home_team.strip().upper()
            if name_upper == h_clean or name_upper.rstrip('.') == h_clean.rstrip('.') or self._matches_team_in_market_name(name_upper, home_team) or name_upper in ("GOSPODARZ", "GOSPODARZE", "TEAM 1", "TEAM1"):
                return "HOME"
        if away_team:
            a_clean = away_team.strip().upper()
            if name_upper == a_clean or name_upper.rstrip('.') == a_clean.rstrip('.') or self._matches_team_in_market_name(name_upper, away_team) or name_upper in ("GOŚĆ", "GOŚCIE", "GOSC", "GOSCIE", "TEAM 2", "TEAM2"):
                return "AWAY"
        if name_upper in ("REMIS", "DRAW", "X", "REMIS.", "DRAW."):
            return "DRAW"

        return name_upper if name_upper else "UNKNOWN"

    def _extract_selection_line(self, selection: BetclicSelection) -> Optional[float]:
        """Extracts numerical line from selection handicap or name."""
        if selection.handicap is not None:
            try:
                clean_h = str(selection.handicap).replace(",", ".").strip()
                match = _RE_BC_HANDICAP.search(clean_h)
                if match:
                    return float(match.group(1))
            except (ValueError, TypeError):
                pass

        if selection.name:
            clean_name = selection.name.replace(",", ".")
            # Match pattern: e.g. "Powyżej 2.5", "Poniżej 3.5", "+1.5", "-1.5", "(2.5)", "(-1.5)"
            m_paren = _RE_BC_PAREN.search(clean_name)
            if m_paren:
                try:
                    return float(m_paren.group(1))
                except (ValueError, TypeError):
                    pass
            m_sel = _RE_BC_SEL_LINE.search(clean_name)
            if m_sel:
                try:
                    return float(m_sel.group(1))
                except (ValueError, TypeError):
                    pass

        return None

    def _extract_market_line(self, market: BetclicMarket, canonical_mkt_type: str) -> Optional[float]:
        """Extracts semantic market line from Betclic market name if explicitly present."""
        if canonical_mkt_type in ("TOTALS", "HANDICAP", "ASIAN_HANDICAP") and market.name:
            clean_name = market.name.replace(",", ".")
            # Explicit parentheses line e.g. "Gole Powyżej/Poniżej (2.5)"
            m_paren = _RE_BC_PAREN.search(clean_name)
            if m_paren:
                try:
                    return float(m_paren.group(1))
                except (ValueError, TypeError):
                    pass
            # Explicit handicap line e.g. "Handicap -1.5", "Handicap: +1.5", "Handicap 1.5"
            m_hcp = _RE_BC_MKT_HCP.search(clean_name)
            if m_hcp:
                try:
                    return float(m_hcp.group(1))
                except (ValueError, TypeError):
                    pass
            # Explicit totals keyword followed by number (not period number)
            m_line = _RE_BC_MKT_LINE.search(clean_name)
            if m_line:
                try:
                    return float(m_line.group(1))
                except (ValueError, TypeError):
                    pass

        return None

    def _sanitize_text(self, text: str) -> str:
        """Removes Polish diacritics and non-breaking spaces for robust matching."""
        return _sanitize_text_cached(text)

    @staticmethod
    def _parse_player_selection_name(name: str, home: str = "", away: str = "") -> Tuple[Optional[str], Optional[str], Optional[float]]:
        """Parses Betclic player selection name into (player_name, side, line)."""
        cleaned = name.strip()
        
        # Remove team names if prefixed (including common variants with FC, Lizbona etc.)
        candidates = _get_player_selection_strip_candidates(home, away)
        for cand in candidates:
            if cleaned.lower().startswith(cand):
                cleaned = cleaned[len(cand):].strip()

        # Check '<Player Name> <Powyżej/Poniżej> <Line>'
        m = _RE_BC_PLAYER_PROP.search(cleaned)
        if m:
            p_name = m.group(1).strip()
            p_name = _RE_BC_PLAYER_DOB.sub("", p_name).strip()
            side_raw = m.group(2).upper()
            line_raw = float(m.group(3).replace(",", "."))
            side = "OVER" if ("POW" in side_raw or "OVER" in side_raw) else "UNDER"
            return p_name, side, line_raw

        # Single player name without line (e.g. for Strzelec / Asysty / Kartka)
        if cleaned and not any(k in cleaned.lower() for k in ("powyżej", "poniżej", "over", "under", "remis", "draw", "1x2")):
            p_name = _RE_BC_PLAYER_DOB.sub("", cleaned).strip()
            return p_name, "YES", None

        return None, None, None


