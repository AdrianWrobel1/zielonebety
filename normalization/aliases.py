"""
Stage 26: High-Recall Cross-Bookmaker Team and Competition Alias and Token Resolution

Provides deterministic, provider-independent canonical alias mappings and token helpers:
- Standard club abbreviations, acronyms, and well-known short forms.
- Safe transliteration mappings and European city translations.
- Identity suffix preservation (Age categories, Gender, Reserve teams).
- Distinguishing entity modifiers (preventing false positive cross-entity matches).
"""

import re
from typing import Dict, Optional, Set, Tuple

# Safety sets for suffixes that must NEVER be stripped or aliased away
AGE_GROUPS: Set[str] = {"u17", "u18", "u19", "u20", "u21", "u23"}
GENDER_SUFFIXES: Set[str] = {"women", "w", "fem", "feminino", "ladies", "k", "kobiet"}
RESERVE_SUFFIXES: Set[str] = {"ii", "b", "reserves", "reserve", "2"}
ALL_IDENTITY_SUFFIXES: Set[str] = AGE_GROUPS | GENDER_SUFFIXES | RESERVE_SUFFIXES

# Distinguishing modifiers: if two teams have conflicting non-empty tokens from this set
# (e.g. Manchester City vs Manchester United, Real Madrid vs Atletico Madrid),
# they must NOT be merged or boosted into false-positive matches.
DISTINGUISHING_MODIFIERS: Set[str] = {
    "city", "united", "town", "athletic", "atletico", "sporting",
    "real", "inter", "ac", "lokomotiv", "dinamo", "dynamo",
    "sparta", "slavia", "rapid", "wednesday", "rovers", "wanderers",
    "borussia", "eintracht", "bayer", "bayern", "porto",
}

# Standard European transliterations and phonetic / city name variations
TRANSLITERATION_MAP: Dict[str, str] = {
    "koeln": "koln",
    "fuerth": "furth",
    "muenster": "munster",
    "nuernberg": "nurnberg",
    "duesseldorf": "dusseldorf",
    "luebeck": "lubeck",
    "osnabrueck": "osnabruck",
    "saarbruecken": "saarbrucken",
    "bratyslawa": "bratislava",
    "corunya": "coruna",
    "plzen": "pilsen",
    "zagrzeb": "zagreb",
    "ateny": "athens",
    "monachium": "munich",
    "mediolan": "milan",
    "milano": "milan",
    "turyn": "torino",
    "lizbona": "lisbon",
    "lisboa": "lisbon",
    "kopenhaga": "copenhagen",
    "kobenhavn": "copenhagen",
    "bukareszt": "bucharest",
    "bucuresti": "bucharest",
    "belgrad": "belgrade",
    "beograd": "belgrade",
    "kijow": "kyiv",
    "kiev": "kyiv",
    "neapol": "napoli",
    "marsylia": "marseille",
    "sewilla": "sevilla",
    "seville": "sevilla",
    "antwerpia": "antwerp",
    "praga": "prague",
    "praha": "prague",
    "wieden": "vienna",
    "wien": "vienna",
    "bruksela": "brussels",
    "bruxelles": "brussels",
    "genua": "genoa",
    "paryz": "paris",
    "londyn": "london",
    "rzym": "roma",
    "madryt": "madrid",
    "haga": "hague",
    "solun": "thessaloniki",
}

# Deterministic canonical team aliases (mapped from normalized space to canonical identifier)
CANONICAL_TEAM_ALIASES: Dict[str, str] = {
    # Acronyms and English / French / Spanish / German common forms
    "psg": "paris saint germain",
    "hearts": "heart of midlothian",
    "wolves": "wolverhampton",
    "wolverhampton wanderers": "wolverhampton",
    "qpr": "queens park rangers",
    "inter milan": "inter",
    "inter mediolan": "inter",
    "internazionale": "inter",
    "ac milan": "milan",
    "as roma": "roma",
    "tottenham hotspur": "tottenham",
    "spurs": "tottenham",
    "brighton and hove albion": "brighton",
    "brighton hove albion": "brighton",
    "sheff utd": "sheffield united",
    "sheffield utd": "sheffield united",
    "sheff wed": "sheffield wednesday",
    "sheffield wed": "sheffield wednesday",
    "sheffield wednesday": "sheffield wednesday",
    "oxford utd": "oxford united",
    "oxford united": "oxford united",
    "newcastle utd": "newcastle united",
    "west brom": "west bromwich albion",
    "west bromwich": "west bromwich albion",
    "wba": "west bromwich albion",
    "pne": "preston north end",
    "rotherham utd": "rotherham united",
    "peterborough utd": "peterborough united",
    "cambridge utd": "cambridge united",
    "carlisle utd": "carlisle united",
    "colchester utd": "colchester united",
    "southend utd": "southend united",
    "scunthorpe utd": "scunthorpe united",
    "torquay utd": "torquay united",
    "hartlepool utd": "hartlepool united",
    "west ham utd": "west ham",
    "west ham united": "west ham",
    "man utd": "manchester united",
    "manchester utd": "manchester united",
    "man city": "manchester city",
    "luton town": "luton",
    "charlton athletic": "charlton",
    "preston north end": "preston",
    "borussia mgladbach": "borussia monchengladbach",
    "borussia moenchengladbach": "borussia monchengladbach",
    "mgladbach": "borussia monchengladbach",
    "moenchengladbach": "borussia monchengladbach",
    "athletic club bilbao": "athletic bilbao",
    "ajax amsterdam": "ajax",
    "werder bremen": "werder",
    "rb salzburg": "salzburg",
    "red bull salzburg": "salzburg",
    "rb leipzig": "leipzig",
    "red bull leipzig": "leipzig",
    "slavia praga": "slavia prague",
    "sparta praga": "sparta prague",
    "rapid wieden": "rapid vienna",
    "rapid wien": "rapid vienna",
    "aek ateny": "aek athens",
    "dinamo zagrzeb": "dinamo zagreb",
    "dynamo zagreb": "dinamo zagreb",
    "olympique lyon": "lyon",
    "olympique marsylia": "marseille",
    "olympique de marseille": "marseille",
    "sporting cp": "sporting lizbona",
    "sporting cl": "sporting lizbona",
    "sporting lisbon": "sporting lizbona",
    "sporting lisboa": "sporting lizbona",
    "rio ave fc": "rio ave",
    "le mans fc": "le mans",
    "cf monterrey": "monterrey",
    "fc koeln": "koln",
    "fc koln": "koln",
    "koeln": "koln",
    "koln": "koln",
    "schalke 04": "schalke",
    "paderborn 07": "paderborn",
    "sc paderborn 07": "paderborn",
    "sc paderborn": "paderborn",
    "fc rouen 1899": "rouen",
    "fc rouen": "rouen",
    "fc villefranche beaujolais": "villefranche",
    "villefranche beaujolais": "villefranche",
    "cd nacional": "nacional",
    "nacional madeira": "nacional",
    "estrela amadora": "estrela",
    "estrela da amadora": "estrela",
    "casa pia atletico": "casa pia",
    "casa pia ac": "casa pia",
    "alverca futebol": "alverca",
    "alverca sad": "alverca",
    "iberia 1999": "fc iberia 1999",
    "fc iberia 1999": "fc iberia 1999",
    "qrm": "quevilly rouen",
    "cucuta deportivo": "cucuta",
    "alianza fc valledupar": "alianza valledupar",
    "alianza valledupar": "alianza valledupar",
    "atletico mg": "atletico mineiro",
    "cruzeiro mg": "cruzeiro",
    "bolton wanderers": "bolton",
    "blackburn rovers": "blackburn",
    "derby county": "derby",
    "wycombe wanderers": "wycombe",
    "lincoln city": "lincoln",
    "swansea city": "swansea",
    "stoke city": "stoke",
    "norwich city": "norwich",
    "birmingham city": "birmingham",
    "cardiff city": "cardiff",
    "hull city": "hull",
    "coventry city": "coventry",
    "doncaster rovers": "doncaster",
    "tranmere rovers": "tranmere",
    "burton albion": "burton",
    "bristol city": "bristol city",
    "bristol rovers": "bristol rovers",
}


# Common noise, sport club prefixes, and descriptor tokens
WEAK_TOKENS: Set[str] = {
    "fc", "cf", "sc", "ac", "fk", "sk", "ks", "gks", "if", "club", "clube", "clubul",
    "de", "la", "el", "the", "and", "vs", "of", "del", "le", "da", "do", "du", "d", "a",
    "city", "united", "town", "st", "saint", "afc", "sporting", "ca", "atletico", "athletic", "atletica",
    "wanderers", "rovers", "county", "albion",
    "msk", "tj", "ofk", "gnk", "hnk", "rnk", "nk", "mks", "zks", "lks", "ts",
    "ss", "as", "us", "cd", "ud", "sd", "rc", "rcd", "cs", "bsc", "vfb", "vfl",
    "tsv", "fsv", "spvgg", "sg", "sv", "rb", "bk", "ik", "ff", "aif", "dif",
    "hif", "gif", "uif", "olympique", "ec", "ad", "sad", "csd", "deportivo",
}


import functools
import re
from typing import Dict, Optional, Set, Tuple

_RE_YEAR_NOISE = re.compile(r"^(18\d\d|19\d\d|20\d\d|\d{2})$")


@functools.lru_cache(maxsize=4096)
def is_year_or_number_noise(token: str) -> bool:
    """Returns True if the token represents a numeric year (1800-2099 or 2-digit 00-99)."""
    return bool(_RE_YEAR_NOISE.match(token))


@functools.lru_cache(maxsize=16384)
def resolve_canonical_team_name(normalized_name: str, tokens: Tuple[str, ...]) -> Tuple[str, Tuple[str, ...]]:
    """Resolves a normalized team name to its canonical form while strictly preserving identity suffixes."""
    if not normalized_name or not tokens:
        return normalized_name, tokens

    base_tokens = [t for t in tokens if t not in ALL_IDENTITY_SUFFIXES]
    suffix_tokens = [t for t in tokens if t in ALL_IDENTITY_SUFFIXES]

    base_str = " ".join(base_tokens)
    if base_str in CANONICAL_TEAM_ALIASES:
        canonical_base = CANONICAL_TEAM_ALIASES[base_str]
        canon_tokens = tuple(canonical_base.split() + suffix_tokens)
        return " ".join(canon_tokens), canon_tokens

    # Try non-weak base tokens (e.g. 'fc internazionale' -> 'internazionale')
    strong_base_tokens = [t for t in base_tokens if t not in WEAK_TOKENS and not is_year_or_number_noise(t)]
    strong_base_str = " ".join(strong_base_tokens)
    if strong_base_str and strong_base_str in CANONICAL_TEAM_ALIASES:
        canonical_base = CANONICAL_TEAM_ALIASES[strong_base_str]
        canon_tokens = tuple(canonical_base.split() + suffix_tokens)
        return " ".join(canon_tokens), canon_tokens

    if normalized_name in CANONICAL_TEAM_ALIASES:
        canon_full = CANONICAL_TEAM_ALIASES[normalized_name]
        canon_tokens = tuple(canon_full.split() + suffix_tokens)
        return " ".join(canon_tokens), canon_tokens

    return normalized_name, tokens
