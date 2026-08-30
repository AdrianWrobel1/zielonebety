"""
Canonical Competition Identity & Normalization Layer

Provides an extensible, authoritative registry and resolution engine for sporting competitions.
Ensures competition metadata (canonical ID, display name, country, type, tier, provenance, confidence)
is preserved deterministically across all stages of the Main Scan pipeline.
"""

from __future__ import annotations

import re
import unicodedata
import functools
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple, Union

from domain.models import CanonicalCompetition


@functools.lru_cache(maxsize=4096)
def _clean_text(text: str) -> str:
    """Normalize text by lowercasing, removing diacritics, and stripping punctuation."""
    if not text:
        return ""
    char_trans = {
        "ł": "l", "Ł": "l", "ø": "o", "Ø": "o", "æ": "ae", "Æ": "ae",
        "œ": "oe", "Œ": "oe", "ß": "ss", "đ": "d", "Đ": "d",
    }
    for k, v in char_trans.items():
        text = text.replace(k, v)
    norm = unicodedata.normalize("NFKD", text)
    clean = "".join(c for c in norm if unicodedata.category(c) != "Mn").lower()
    clean = re.sub(r"[\-/.,_()\[\]'\"`~:;+*]", " ", clean)
    clean = re.sub(r"[^a-z0-9\s]", " ", clean)
    return " ".join(clean.split())


@dataclass(frozen=True)
class CanonicalCompetitionDefinition:
    """Immutable definition of a recognized canonical competition."""
    canonical_id: str
    canonical_name: str
    country: str
    competition_type: str  # "LEAGUE", "INTERNATIONAL_CUP", "DOMESTIC_CUP", "INTERNATIONAL"
    tier: int              # 0 (Top International/UCL/UEL/UECL), 1 (Big European Top-Flights), 2 (Standard/Cups)
    aliases: Tuple[str, ...]
    regex_patterns: Tuple[str, ...] = ()
    qualifier_for_id: Optional[str] = None
    known_teams: Tuple[str, ...] = ()
    superbet_tournament_ids: Tuple[str, ...] = ()
    betclic_competition_ids: Tuple[str, ...] = ()


# ──────────────────────────────────────────────────────────────────────────────
# CANONICAL COMPETITIONS REPO / CATALOG
# ──────────────────────────────────────────────────────────────────────────────

COMPETITION_DEFINITIONS: Tuple[CanonicalCompetitionDefinition, ...] = (
    # ─── TIER 0: UEFA TOURNAMENTS (SENIOR & QUALIFIERS) ──────────────────────
    CanonicalCompetitionDefinition(
        canonical_id="comp_uefa_cl",
        canonical_name="UEFA Champions League",
        country="Europe",
        competition_type="INTERNATIONAL_CUP",
        tier=0,
        aliases=(
            "champions league", "uefa champions league", "ucl", "liga mistrzow",
            "ligi mistrzow", "liga mistrzow uefa", "uefa liga mistrzow", "cl",
            "liga de campeones", "ligue des champions", "championsleague",
        ),
        regex_patterns=(
            r"\b(champions\s+league|liga\s+mistrzow|ligi\s+mistrzow|uefa\s+cl)\b",
        ),
        betclic_competition_ids=("8",),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_uefa_cl_qual",
        canonical_name="UEFA Champions League (Qualifiers)",
        country="Europe",
        competition_type="INTERNATIONAL_CUP",
        tier=0,
        aliases=(
            "uefa champions league qualification", "champions league qualification",
            "champions league qualifiers", "liga mistrzow kwalifikacje",
            "ligi mistrzow kwalifikacje", "liga mistrzow eliminacje",
            "ligi mistrzow eliminacje", "uefa champions league kwalifikacje",
            "liga mistrzow play off", "champions league preliminary",
            "eliminacje ligi mistrzow", "kwalifikacje ligi mistrzow",
        ),
        regex_patterns=(
            r"(?=.*\b(champions\s+league|liga\s+mistrzow|ligi\s+mistrzow|ucl)\b)(?=.*\b(kwal|qual|elim|prelim|play[\s-]*off|runda|round)\b)",
        ),
        qualifier_for_id="comp_uefa_cl",
    ),

    CanonicalCompetitionDefinition(
        canonical_id="comp_uefa_el",
        canonical_name="UEFA Europa League",
        country="Europe",
        competition_type="INTERNATIONAL_CUP",
        tier=0,
        aliases=(
            "europa league", "uefa europa league", "uel", "liga europy",
            "ligi europy", "liga europy uefa", "uefa liga europy", "liga europa",
        ),
        regex_patterns=(
            r"\b(europa\s+league|liga\s+europy|ligi\s+europy|uefa\s+el)\b",
        ),
        betclic_competition_ids=("9",),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_uefa_el_qual",
        canonical_name="UEFA Europa League (Qualifiers)",
        country="Europe",
        competition_type="INTERNATIONAL_CUP",
        tier=0,
        aliases=(
            "uefa europa league qualification", "europa league qualification",
            "europa league qualifiers", "liga europy kwalifikacje",
            "ligi europy kwalifikacje", "liga europy eliminacje",
            "ligi europy eliminacje", "uefa europa league kwalifikacje",
            "liga europy play off", "eliminacje ligi europy", "kwalifikacje ligi europy",
        ),
        regex_patterns=(
            r"(?=.*\b(europa\s+league|liga\s+europy|ligi\s+europy|uel)\b)(?=.*\b(kwal|qual|elim|prelim|play[\s-]*off|runda|round)\b)",
        ),
        qualifier_for_id="comp_uefa_el",
    ),

    CanonicalCompetitionDefinition(
        canonical_id="comp_uefa_ecl",
        canonical_name="UEFA Conference League",
        country="Europe",
        competition_type="INTERNATIONAL_CUP",
        tier=0,
        aliases=(
            "conference league", "uefa conference league", "uefa europa conference league",
            "europa conference league", "uecl", "liga konferencji", "ligi konferencji",
            "liga konferencji uefa", "uefa liga konferencji", "liga konferencji europy",
            "liga konferencji europy uefa",
        ),
        regex_patterns=(
            r"\b(conference\s+league|liga\s+konferencji|ligi\s+konferencji|uefa\s+ecl)\b",
        ),
        betclic_competition_ids=("53",),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_uefa_ecl_qual",
        canonical_name="UEFA Conference League (Qualifiers)",
        country="Europe",
        competition_type="INTERNATIONAL_CUP",
        tier=0,
        aliases=(
            "uefa conference league qualification", "conference league qualification",
            "conference league qualifiers", "liga konferencji kwalifikacje",
            "ligi konferencji kwalifikacje", "liga konferencji eliminacje",
            "ligi konferencji eliminacje", "uefa conference league kwalifikacje",
            "uefa europa conference league qualification", "liga konferencji europy kwalifikacje",
            "liga konferencji play off", "eliminacje ligi konferencji", "kwalifikacje ligi konferencji",
        ),
        regex_patterns=(
            r"(?=.*\b(conference\s+league|liga\s+konferencji|ligi\s+konferencji|uecl)\b)(?=.*\b(kwal|qual|elim|prelim|play[\s-]*off|runda|round)\b)",
        ),
        qualifier_for_id="comp_uefa_ecl",
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_intl_world_cup",
        canonical_name="World Cup",
        country="International",
        competition_type="INTERNATIONAL",
        tier=0,
        aliases=(
            "world cup", "fifa world cup", "mistrzostwa swiata", "mistrzostwa swiata fifa",
            "wc", "mundial", "fifa world cup qualification", "mistrzostwa swiata kwalifikacje",
            "eliminacje mistrzostw swiata",
        ),
        regex_patterns=(
            r"\b(world\s+cup|fifa\s+world\s+cup|mistrzostwa\s+swiata|mundial)\b",
        ),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_intl_euro",
        canonical_name="Euro",
        country="Europe",
        competition_type="INTERNATIONAL",
        tier=0,
        aliases=(
            "euro", "uefa euro", "mistrzostwa europy", "mistrzostwa europy uefa",
            "euro qualification", "mistrzostwa europy kwalifikacje",
            "eliminacje mistrzostw europy",
        ),
        regex_patterns=(
            r"\b(uefa\s+euro|mistrzostwa\s+europy)\b",
            r"^euro\s*(?:20\d\d)?$",
        ),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_intl_nations_league",
        canonical_name="Nations League",
        country="Europe",
        competition_type="INTERNATIONAL",
        tier=0,
        aliases=(
            "nations league", "uefa nations league", "liga narodow", "liga narodow uefa",
            "uefa liga narodow", "unl",
        ),
        regex_patterns=(
            r"\b(nations\s+league|liga\s+narodow|uefa\s+nations\s+league)\b",
        ),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_intl_copa_america",
        canonical_name="Copa America",
        country="South America",
        competition_type="INTERNATIONAL",
        tier=0,
        aliases=(
            "copa america", "copa américa", "conmebol copa america",
        ),
        regex_patterns=(
            r"\bcopa\s+america\b",
        ),
    ),

    # ─── TIER 1: BIG 5 + EKSTRAKLASA + EREDIVISIE + PRIMEIRA LIGA ─────────────
    # 1. Premier League
    CanonicalCompetitionDefinition(
        canonical_id="comp_eng_pl",
        canonical_name="Premier League",
        country="England",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "premier league", "english premier league", "epl", "anglia 1",
            "anglia premier league", "anglia 1 liga", "anglia 1. liga",
            "anglia: premier league", "premier league anglia", "england premier league",
        ),
        regex_patterns=(
            r"^(?:anglia\s+|england\s+|english\s+)?premier\s+league$",
            r"\benglish\s+premier\s+league\b",
            r"\banglia\s+(?:1|premier\s+league)\b",
        ),
        known_teams=(
            "arsenal", "aston villa", "bournemouth", "brentford", "brighton",
            "chelsea", "crystal palace", "everton", "fulham", "ipswich",
            "leicester", "liverpool", "manchester city", "manchester united",
            "newcastle", "nottingham forest", "southampton", "tottenham",
            "west ham", "wolves", "wolverhampton",
        ),
        betclic_competition_ids=("11",),
    ),

    # 2. La Liga
    CanonicalCompetitionDefinition(
        canonical_id="comp_esp_laliga",
        canonical_name="La Liga",
        country="Spain",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "laliga", "la liga", "la liga ea sports", "primera division",
            "hiszpania 1", "hiszpania laliga", "hiszpania la liga",
            "hiszpania 1 liga", "hiszpania 1. liga", "hiszpania: laliga",
            "hiszpania primera division", "spain la liga", "spain laliga",
        ),
        regex_patterns=(
            r"\bla\s*liga\b",
            r"\bprimera\s+division\b",
            r"\bhiszpania\s+(?:1|laliga|la\s*liga)\b",
        ),
        known_teams=(
            "alaves", "athletic bilbao", "atletico madrid", "barcelona", "celta vigo",
            "espanyol", "getafe", "girona", "las palmas", "leganes", "mallorca",
            "osasuna", "rayo vallecano", "real betis", "real madrid", "real sociedad",
            "sevilla", "valencia", "valladolid", "villarreal",
        ),
        betclic_competition_ids=("7",),
    ),

    # 3. Serie A
    CanonicalCompetitionDefinition(
        canonical_id="comp_ita_serie_a",
        canonical_name="Serie A",
        country="Italy",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "serie a", "italy serie a", "italia serie a", "wlochy 1", "wlochy serie a",
            "wlochy 1 liga", "wlochy 1. liga", "wlochy: serie a", "italy 1",
            "serie a wlochy", "serie a enilive",
        ),
        regex_patterns=(
            r"\bserie\s+a\b",
            r"\bwlochy\s+(?:1|serie\s+a)\b",
        ),
        known_teams=(
            "atalanta", "bologna", "cagliari", "como", "empoli", "fiorentina",
            "genoa", "inter", "juventus", "lazio", "lecce", "milan", "monza",
            "napoli", "parma", "roma", "torino", "udinese", "venezia", "verona",
        ),
        betclic_competition_ids=("6",),
    ),

    # 4. Bundesliga
    CanonicalCompetitionDefinition(
        canonical_id="comp_ger_bundesliga",
        canonical_name="Bundesliga",
        country="Germany",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "bundesliga", "german bundesliga", "1 bundesliga", "1. bundesliga",
            "niemcy 1", "niemcy bundesliga", "niemcy 1 liga", "niemcy 1. liga",
            "niemcy: bundesliga", "germany bundesliga", "germany 1",
        ),
        regex_patterns=(
            r"\b1\.?\s*bundesliga\b",
            r"\bbundesliga\b",
            r"\bniemcy\s+(?:1|bundesliga)\b",
        ),
        known_teams=(
            "augsburg", "bayer leverkusen", "bayern munich", "bochum", "borussia dortmund",
            "borussia monchengladbach", "eintracht frankfurt", "freiburg", "heidenheim",
            "hoffenheim", "holstein kiel", "mainz", "rb leipzig", "st pauli",
            "stuttgart", "union berlin", "werder bremen", "wolfsburg",
        ),
        betclic_competition_ids=("5",),
    ),

    # 5. Ligue 1
    CanonicalCompetitionDefinition(
        canonical_id="comp_fra_ligue_1",
        canonical_name="Ligue 1",
        country="France",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "ligue 1", "french ligue 1", "ligue 1 mcdonalds", "francja 1",
            "francja ligue 1", "francja 1 liga", "francja 1. liga",
            "francja: ligue 1", "france ligue 1", "france 1",
        ),
        regex_patterns=(
            r"\bligue\s+1\b",
            r"\bfrancja\s+(?:1|ligue\s+1)\b",
        ),
        known_teams=(
            "angers", "auxerre", "brest", "le havre", "lens", "lille", "lyon",
            "marseille", "monaco", "montpellier", "nantes", "nice", "psg",
            "paris saint germain", "reims", "rennes", "saint etienne",
            "strasbourg", "toulouse",
        ),
        betclic_competition_ids=("4",),
    ),

    # 6. Ekstraklasa
    CanonicalCompetitionDefinition(
        canonical_id="comp_pol_ekstraklasa",
        canonical_name="Ekstraklasa",
        country="Poland",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "ekstraklasa", "pko bp ekstraklasa", "pko ekstraklasa", "polska ekstraklasa",
            "polska ekstraklasa 1", "polska 1", "polska liga", "polska: ekstraklasa",
            "poland ekstraklasa", "ekstraklasa polska",
        ),
        regex_patterns=(
            r"\bekstraklasa\b",
            r"\bpko\s+bp\b",
            r"\bpolska\s+ekstraklasa\b",
        ),
        known_teams=(
            "cracovia", "gks katowice", "gornik zabrze", "jagiellonia", "korona kielce",
            "lech poznan", "lechia gdansk", "legia warszawa", "motor lublin", "piast gliwice",
            "pogon szczecin", "puszcza niepolomice", "radomiak radom", "rakow czestochowa",
            "stal mielec", "slask wroclaw", "widzew lodz", "zaglebie lubin",
        ),
        betclic_competition_ids=("31",),
    ),

    # 7. Eredivisie
    CanonicalCompetitionDefinition(
        canonical_id="comp_ned_eredivisie",
        canonical_name="Eredivisie",
        country="Netherlands",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "eredivisie", "dutch eredivisie", "holandia 1", "holandia eredivisie",
            "holandia 1 liga", "holandia 1. liga", "netherlands eredivisie",
            "holenderska eredivisie",
        ),
        regex_patterns=(
            r"\beredivisie\b",
            r"\bholandia\s+(?:1|eredivisie)\b",
        ),
        known_teams=(
            "ajax", "almere city", "az alkmaar", "feyenoord", "fortuna sittard",
            "go ahead eagles", "groningen", "heerenveen", "heracles", "nac breda",
            "nec nijmegen", "pec zwolle", "psv", "psv eindhoven", "rkc waalwijk",
            "sparta rotterdam", "twente", "utrecht", "willem ii",
        ),
    ),

    # 8. Primeira Liga
    CanonicalCompetitionDefinition(
        canonical_id="comp_por_primeira_liga",
        canonical_name="Primeira Liga",
        country="Portugal",
        competition_type="LEAGUE",
        tier=1,
        aliases=(
            "primeira liga", "liga portugal", "liga portugal betclic", "portugalia 1",
            "portugalia primeira liga", "portugalia liga portugal", "portugal primeira liga",
            "portugalia 1 liga", "portugalia 1. liga",
        ),
        regex_patterns=(
            r"\bprimeira\s+liga\b",
            r"\bliga\s+portugal\b",
            r"\bportugalia\s+(?:1|primeira\s+liga|liga\s+portugal)\b",
        ),
        known_teams=(
            "arouca", "avs", "benfica", "boavista", "braga", "casa pia",
            "estoril", "estrela amadora", "famalicao", "farense", "gil vicente",
            "moreirense", "nacional", "porto", "rio ave", "santa clara",
            "sporting cp", "sporting lizbona", "vitoria guimaraes",
        ),
    ),

    # ─── TIER 2: SECONDARY LEAGUES & DOMESTIC CUPS ───────────────────────────
    # England 2nd (Championship)
    CanonicalCompetitionDefinition(
        canonical_id="comp_eng_championship",
        canonical_name="Championship",
        country="England",
        competition_type="LEAGUE",
        tier=2,
        aliases=(
            "championship", "efl championship", "anglia 2", "anglia championship",
            "anglia 2 liga", "anglia 2. liga", "england championship",
        ),
        regex_patterns=(
            r"\bchampionship\b",
            r"\banglia\s+(?:2|championship)\b",
        ),
    ),
    # Spain 2nd (LaLiga 2)
    CanonicalCompetitionDefinition(
        canonical_id="comp_esp_laliga2",
        canonical_name="LaLiga 2",
        country="Spain",
        competition_type="LEAGUE",
        tier=2,
        aliases=(
            "laliga 2", "la liga 2", "segunda division", "laliga hypermotion",
            "hiszpania 2", "hiszpania laliga 2", "hiszpania 2 liga", "hiszpania 2. liga",
        ),
        regex_patterns=(
            r"\bla\s*liga\s*2\b",
            r"\bsegunda\s+division\b",
            r"\bhypermotion\b",
        ),
    ),
    # Italy 2nd (Serie B)
    CanonicalCompetitionDefinition(
        canonical_id="comp_ita_serie_b",
        canonical_name="Serie B",
        country="Italy",
        competition_type="LEAGUE",
        tier=2,
        aliases=(
            "serie b", "wlochy 2", "wlochy serie b", "wlochy 2 liga", "wlochy 2. liga",
            "italy serie b",
        ),
        regex_patterns=(
            r"\bserie\s+b\b",
            r"\bwlochy\s+(?:2|serie\s+b)\b",
        ),
    ),
    # Germany 2nd (2. Bundesliga)
    CanonicalCompetitionDefinition(
        canonical_id="comp_ger_bundesliga2",
        canonical_name="2. Bundesliga",
        country="Germany",
        competition_type="LEAGUE",
        tier=2,
        aliases=(
            "2 bundesliga", "2. bundesliga", "niemcy 2", "niemcy 2 bundesliga",
            "niemcy 2 liga", "niemcy 2. liga", "germany 2 bundesliga",
        ),
        regex_patterns=(
            r"\b2\.?\s*bundesliga\b",
            r"\bniemcy\s+(?:2|2\.?\s*bundesliga)\b",
        ),
    ),
    # France 2nd (Ligue 2)
    CanonicalCompetitionDefinition(
        canonical_id="comp_fra_ligue_2",
        canonical_name="Ligue 2",
        country="France",
        competition_type="LEAGUE",
        tier=2,
        aliases=(
            "ligue 2", "francja 2", "francja ligue 2", "francja 2 liga", "francja 2. liga",
            "france ligue 2",
        ),
        regex_patterns=(
            r"\bligue\s+2\b",
            r"\bfrancja\s+(?:2|ligue\s+2)\b",
        ),
    ),
    # Poland 2nd (1. Liga)
    CanonicalCompetitionDefinition(
        canonical_id="comp_pol_1_liga",
        canonical_name="1. Liga",
        country="Poland",
        competition_type="LEAGUE",
        tier=2,
        aliases=(
            "1 liga", "1. liga", "polska 1 liga", "polska 1. liga", "betclic 1 liga",
            "betclic 1. liga", "polska 2", "poland 1 liga",
        ),
        regex_patterns=(
            r"\bbetclic\s+1\.?\s*liga\b",
            r"\bpolska\s+1\.?\s*liga\b",
        ),
        betclic_competition_ids=("32",),
    ),
    # Cups
    CanonicalCompetitionDefinition(
        canonical_id="comp_eng_fa_cup",
        canonical_name="FA Cup",
        country="England",
        competition_type="DOMESTIC_CUP",
        tier=2,
        aliases=("fa cup", "puchar anglii", "the fa cup", "the emirates fa cup"),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_eng_efl_cup",
        canonical_name="EFL Cup",
        country="England",
        competition_type="DOMESTIC_CUP",
        tier=2,
        aliases=("efl cup", "carabao cup", "puchar ligi angielskiej"),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_esp_copa_del_rey",
        canonical_name="Copa del Rey",
        country="Spain",
        competition_type="DOMESTIC_CUP",
        tier=2,
        aliases=("copa del rey", "puchar krola", "puchar hiszpanii"),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_ita_coppa_italia",
        canonical_name="Coppa Italia",
        country="Italy",
        competition_type="DOMESTIC_CUP",
        tier=2,
        aliases=("coppa italia", "puchar wloch"),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_ger_dfb_pokal",
        canonical_name="DFB-Pokal",
        country="Germany",
        competition_type="DOMESTIC_CUP",
        tier=2,
        aliases=("dfb pokal", "dfb-pokal", "puchar niemiec"),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_fra_coupe_de_france",
        canonical_name="Coupe de France",
        country="France",
        competition_type="DOMESTIC_CUP",
        tier=2,
        aliases=("coupe de france", "puchar francji"),
    ),
    CanonicalCompetitionDefinition(
        canonical_id="comp_pol_puchar_polski",
        canonical_name="Puchar Polski",
        country="Poland",
        competition_type="DOMESTIC_CUP",
        tier=2,
        aliases=("puchar polski", "polish cup", "poland cup"),
        betclic_competition_ids=("33",),
    ),
)


@dataclass
class CanonicalCompetitionResolution:
    """Detailed result of a competition resolution query."""
    canonical_id: str
    canonical_name: str
    country: str
    competition_type: str
    tier: int
    provenance: str  # "PROVIDER_METADATA", "CROSS_BOOKMAKER_MATCH", "TEAM_INFERENCE", "FALLBACK"
    confidence: float
    matched_by: str  # "EXACT_ALIAS", "REGEX", "QUALIFIER_REGEX", "PROVIDER_ID", "TEAM_INFERENCE", "RAW_PASSTHROUGH", "FALLBACK"
    original_input: Optional[str] = None


class CanonicalCompetitionRegistry:
    """
    Authoritative, deterministic competition registry and resolution engine.
    """

    def __init__(self, definitions: Tuple[CanonicalCompetitionDefinition, ...] = COMPETITION_DEFINITIONS):
        self._definitions = definitions
        self._alias_map: Dict[str, CanonicalCompetitionDefinition] = {}
        self._id_map: Dict[str, CanonicalCompetitionDefinition] = {}
        self._betclic_id_map: Dict[str, CanonicalCompetitionDefinition] = {}
        self._superbet_id_map: Dict[str, CanonicalCompetitionDefinition] = {}
        self._compiled_regexes: List[Tuple[re.Pattern, CanonicalCompetitionDefinition]] = []
        self._team_to_comp: Dict[str, List[CanonicalCompetitionDefinition]] = {}

        self._build_indexes()

    def _build_indexes(self) -> None:
        for defn in self._definitions:
            self._id_map[defn.canonical_id] = defn
            for alias in defn.aliases:
                clean_al = _clean_text(alias)
                if clean_al:
                    self._alias_map[clean_al] = defn

            for pat in defn.regex_patterns:
                self._compiled_regexes.append((re.compile(pat, re.IGNORECASE), defn))

            for bid in defn.betclic_competition_ids:
                self._betclic_id_map[str(bid).strip()] = defn

            for sbid in defn.superbet_tournament_ids:
                self._superbet_id_map[str(sbid).strip()] = defn

            for tm in defn.known_teams:
                clean_tm = _clean_text(tm)
                if clean_tm:
                    self._team_to_comp.setdefault(clean_tm, []).append(defn)

    def resolve(
        self,
        raw_name: Optional[str] = None,
        home_team: Optional[str] = None,
        away_team: Optional[str] = None,
        provider_ids: Optional[Dict[str, str]] = None,
        country: Optional[str] = None,
    ) -> CanonicalCompetitionResolution:
        """
        Deterministically resolves a competition from raw name, provider IDs, or team participants.

        Resolution Precedence:
        1. Authoritative Provider Competition ID (e.g. Betclic comp ID '11' -> Premier League)
        2. Direct Alias Exact Match on cleaned raw_name
        3. Regex Pattern Match (including Qualifiers detection)
        4. Team-based Controlled Fallback (only if raw_name is missing, generic or unrecognized)
        5. Cleaned Raw Name Passthrough (if raw_name is not generic)
        6. Fallback Unknown Competition (confidence 0.0)
        """
        raw_str = (raw_name or "").strip()
        clean_raw = _clean_text(raw_str)

        is_generic_raw = (
            not clean_raw
            or clean_raw in (
                "unknown competition", "unknown", "football", "pilka nozna",
                "superbet football", "tournament", "football competition",
            )
        )

        # 1. Provider ID match
        if provider_ids:
            if "betclic" in provider_ids:
                bc_id = str(provider_ids["betclic"]).strip()
                if bc_id in self._betclic_id_map:
                    defn = self._betclic_id_map[bc_id]
                    # Check if raw_name specifies qualification
                    if not is_generic_raw and any(k in clean_raw for k in ("kwalifik", "qualif", "elim", "prelim", "play off")):
                        qual_defn = self._find_qualifier_def(defn.canonical_id)
                        if qual_defn:
                            defn = qual_defn
                    return CanonicalCompetitionResolution(
                        canonical_id=defn.canonical_id,
                        canonical_name=defn.canonical_name,
                        country=defn.country,
                        competition_type=defn.competition_type,
                        tier=defn.tier,
                        provenance="PROVIDER_METADATA",
                        confidence=1.0,
                        matched_by="PROVIDER_ID",
                        original_input=raw_name,
                    )

            if "superbet" in provider_ids:
                sb_id = str(provider_ids["superbet"]).strip()
                if sb_id in self._superbet_id_map:
                    defn = self._superbet_id_map[sb_id]
                    return CanonicalCompetitionResolution(
                        canonical_id=defn.canonical_id,
                        canonical_name=defn.canonical_name,
                        country=defn.country,
                        competition_type=defn.competition_type,
                        tier=defn.tier,
                        provenance="PROVIDER_METADATA",
                        confidence=1.0,
                        matched_by="PROVIDER_ID",
                        original_input=raw_name,
                    )

        # 2. Authoritative string alias matching if not generic
        if not is_generic_raw:
            # Check direct alias table
            if clean_raw in self._alias_map:
                defn = self._alias_map[clean_raw]
                return CanonicalCompetitionResolution(
                    canonical_id=defn.canonical_id,
                    canonical_name=defn.canonical_name,
                    country=defn.country,
                    competition_type=defn.competition_type,
                    tier=defn.tier,
                    provenance="PROVIDER_METADATA",
                    confidence=1.0,
                    matched_by="EXACT_ALIAS",
                    original_input=raw_name,
                )

            # Check regex patterns (with priority to qualifier regexes)
            # 1st pass: check qualifier definitions
            for pat, defn in self._compiled_regexes:
                if defn.qualifier_for_id and pat.search(clean_raw):
                    return CanonicalCompetitionResolution(
                        canonical_id=defn.canonical_id,
                        canonical_name=defn.canonical_name,
                        country=defn.country,
                        competition_type=defn.competition_type,
                        tier=defn.tier,
                        provenance="PROVIDER_METADATA",
                        confidence=1.0,
                        matched_by="QUALIFIER_REGEX",
                        original_input=raw_name,
                    )

            # 2nd pass: general regexes
            for pat, defn in self._compiled_regexes:
                if not defn.qualifier_for_id and pat.search(clean_raw):
                    # Check if text also contains qualifier hints
                    if any(k in clean_raw for k in ("kwalifik", "qualif", "elim", "prelim", "play off")):
                        qual_defn = self._find_qualifier_def(defn.canonical_id)
                        if qual_defn:
                            defn = qual_defn
                    return CanonicalCompetitionResolution(
                        canonical_id=defn.canonical_id,
                        canonical_name=defn.canonical_name,
                        country=defn.country,
                        competition_type=defn.competition_type,
                        tier=defn.tier,
                        provenance="PROVIDER_METADATA",
                        confidence=1.0,
                        matched_by="REGEX",
                        original_input=raw_name,
                    )

        # 3. Team-based Controlled Fallback
        if home_team or away_team:
            inferred = self._infer_competition_from_teams(home_team, away_team, clean_raw)
            if inferred:
                return CanonicalCompetitionResolution(
                    canonical_id=inferred.canonical_id,
                    canonical_name=inferred.canonical_name,
                    country=inferred.country,
                    competition_type=inferred.competition_type,
                    tier=inferred.tier,
                    provenance="TEAM_INFERENCE",
                    confidence=0.85,
                    matched_by="TEAM_INFERENCE",
                    original_input=raw_name,
                )

        # 4. Cleaned Raw Name Passthrough (if specific non-generic raw name existed)
        if not is_generic_raw and len(raw_str) > 2:
            return CanonicalCompetitionResolution(
                canonical_id=f"comp_custom_{_clean_text(raw_str).replace(' ', '_')}",
                canonical_name=raw_str,
                country=country or "International",
                competition_type="LEAGUE",
                tier=2,
                provenance="PROVIDER_METADATA",
                confidence=0.70,
                matched_by="RAW_PASSTHROUGH",
                original_input=raw_name,
            )

        # 5. Genuine Unknown Competition Fallback
        return CanonicalCompetitionResolution(
            canonical_id="comp_unknown",
            canonical_name="Unknown Competition",
            country=country or "International",
            competition_type="LEAGUE",
            tier=2,
            provenance="FALLBACK",
            confidence=0.0,
            matched_by="FALLBACK",
            original_input=raw_name,
        )

    def _find_qualifier_def(self, base_id: str) -> Optional[CanonicalCompetitionDefinition]:
        for defn in self._definitions:
            if defn.qualifier_for_id == base_id:
                return defn
        return None

    def _infer_competition_from_teams(
        self,
        home_team: Optional[str],
        away_team: Optional[str],
        clean_raw: str,
    ) -> Optional[CanonicalCompetitionDefinition]:
        """Infers domestic competition when both teams belong to the same domestic league."""
        clean_h = _clean_text(home_team or "")
        clean_a = _clean_text(away_team or "")

        comps_h = self._match_team_to_comps(clean_h)
        comps_a = self._match_team_to_comps(clean_a)

        # Case 1: Both teams uniquely belong to the SAME league definition
        if comps_h and comps_a:
            common = [c for c in comps_h if c in comps_a and c.competition_type == "LEAGUE"]
            if len(common) == 1:
                return common[0]

        # Case 2: One known domestic team vs another team from the same country or explicit context
        # (e.g. if home is 'rakow czestochowa' and away is 'puszcza niepolomice')
        if comps_h and len(comps_h) == 1 and not comps_a:
            if comps_h[0].competition_type == "LEAGUE":
                # Only infer if there's no international indicator
                if not any(k in clean_raw for k in ("uefa", "conference", "champions", "europa", "qualif")):
                    return comps_h[0]

        if comps_a and len(comps_a) == 1 and not comps_h:
            if comps_a[0].competition_type == "LEAGUE":
                if not any(k in clean_raw for k in ("uefa", "conference", "champions", "europa", "qualif")):
                    return comps_a[0]

        return None

    def _match_team_to_comps(self, clean_team_name: str) -> List[CanonicalCompetitionDefinition]:
        if not clean_team_name:
            return []
        matched = []
        for team_key, defn_list in self._team_to_comp.items():
            if team_key == clean_team_name or f" {team_key} " in f" {clean_team_name} ":
                matched.extend(defn_list)
        return matched


# Global singleton instance
_GLOBAL_REGISTRY: Optional[CanonicalCompetitionRegistry] = None


def get_canonical_competition_registry() -> CanonicalCompetitionRegistry:
    """Returns global shared CanonicalCompetitionRegistry instance."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = CanonicalCompetitionRegistry()
    return _GLOBAL_REGISTRY


def resolve_canonical_competition(
    raw_name: Optional[str] = None,
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    provider_ids: Optional[Dict[str, str]] = None,
    country: Optional[str] = None,
) -> CanonicalCompetitionResolution:
    """Convenience function to resolve a competition using the global registry."""
    registry = get_canonical_competition_registry()
    return registry.resolve(
        raw_name=raw_name,
        home_team=home_team,
        away_team=away_team,
        provider_ids=provider_ids,
        country=country,
    )
