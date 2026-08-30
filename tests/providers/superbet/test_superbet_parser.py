"""
Unit Tests for Superbet Parser Module (Task 038 / Stage 2.2)
"""

import pytest
from providers.superbet.parser.parser import SuperbetParser
from providers.superbet.exceptions import SuperbetParsingError


def test_superbet_parser_success_legacy():
    parser = SuperbetParser()

    raw_responses = [
        {
            "eventId": "sb_999",
            "matchName": "Legia Warszawa vs Lech Poznan",
            "competitionName": "Ekstraklasa",
            "matchDate": "2026-08-12T19:00:00Z",
            "markets": [
                {
                    "marketId": "m_1X2",
                    "name": "Match Result",
                    "selections": [
                        {"selectionId": "s_1", "name": "Legia Warszawa", "odds": 2.10},
                        {"selectionId": "s_X", "name": "Draw", "odds": 3.40},
                        {"selectionId": "s_2", "name": "Lech Poznan", "odds": 3.10},
                    ],
                }
            ],
        }
    ]

    events = parser.parse_payloads(raw_responses)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_id == "sb_999"
    assert ev.home_team == "Legia Warszawa"
    assert ev.away_team == "Lech Poznan"
    assert len(ev.markets) == 1
    assert len(ev.markets[0].selections) == 3
    assert ev.markets[0].selections[0].odds.decimal_odds == 2.10


def test_superbet_parser_success_native_fastly():
    parser = SuperbetParser()

    raw_responses = [
        {
            "event_id": 13207040,
            "fixture": {
                "event_name": "America MG·Athletic Club MG",
                "utc_date": "2026-08-16T21:30:00Z",
                "tournament_id": 1697,
            },
            "markets": [
                {
                    "id": 547,
                    "name": "Mecz",
                    "odds": [
                        {
                            "uuid": "9da8347f-1ef9-563b-a50e-4ed9b0cba62f",
                            "price": 2.35,
                            "status": 1,
                            "display": True,
                            "metadata": {
                                "outcome_id": 1470,
                                "name": "1",
                                "info": "America MG wygra mecz",
                            },
                        },
                        {
                            "uuid": "2fbe6892-39a3-5e3a-88a0-0782a76ee1ed",
                            "price": 2.85,
                            "status": 1,
                            "display": True,
                            "metadata": {
                                "outcome_id": 1471,
                                "name": "X",
                                "info": "Remis",
                            },
                        },
                        {
                            "uuid": "2c52d972-bea7-5f51-b6c6-58de2b932712",
                            "price": 3.20,
                            "status": 1,
                            "display": True,
                            "metadata": {
                                "outcome_id": 1472,
                                "name": "2",
                                "info": "Athletic Club MG wygra mecz",
                            },
                        },
                    ],
                }
            ],
        }
    ]

    events = parser.parse_payloads(raw_responses)
    assert len(events) == 1
    ev = events[0]
    assert ev.event_id == "13207040"
    assert ev.home_team == "America MG"
    assert ev.away_team == "Athletic Club MG"
    assert ev.start_time == "2026-08-16T21:30:00Z"
    assert len(ev.markets) == 1
    assert len(ev.markets[0].selections) == 3
    assert ev.markets[0].selections[0].selection_id == "9da8347f-1ef9-563b-a50e-4ed9b0cba62f"
    assert ev.markets[0].selections[0].name == "1"
    assert ev.markets[0].selections[0].odds.decimal_odds == 2.35


def test_superbet_parser_tier2_flat_odds_with_specifiers():
    parser = SuperbetParser()

    tier2_payload = {
        "error": False,
        "data": [
            {
                "eventId": 13222121,
                "matchName": "Chicago Fire·Portland Timbers",
                "matchDate": "2026-08-16 22:00:00",
                "betradarId": "sr:match:68932610",
                "homeTeamId": "7466",
                "awayTeamId": "281784",
                "tournamentId": "897",
                "categoryId": "241",
                "odds": [
                    # Market 1: 1X2
                    {
                        "marketId": 547,
                        "marketName": "Mecz",
                        "selectionName": "1",
                        "price": 1.59,
                        "outcomeId": 1470,
                        "status": "active",
                    },
                    {
                        "marketId": 547,
                        "marketName": "Mecz",
                        "selectionName": "X",
                        "price": 4.45,
                        "outcomeId": 1471,
                        "status": "active",
                    },
                    # Market 2: Total Goals Over 1.5
                    {
                        "marketId": 200734,
                        "marketName": "Liczba goli",
                        "selectionName": "Powyżej",
                        "price": 1.18,
                        "outcomeId": 151889,
                        "specifiers": {"total": "1.5"},
                        "specialBetValue": "1.5",
                        "status": "active",
                    },
                    # Market 3: Total Goals Over 2.5 (should be separate market from 1.5!)
                    {
                        "marketId": 200734,
                        "marketName": "Liczba goli",
                        "selectionName": "Powyżej",
                        "price": 1.55,
                        "outcomeId": 151889,
                        "specifiers": {"total": "2.5"},
                        "specialBetValue": "2.5",
                        "status": "active",
                    },
                ],
            }
        ],
    }

    events = parser.parse_payloads([tier2_payload])
    assert len(events) == 1
    ev = events[0]
    assert ev.event_id == "13222121"
    assert ev.betradar_id == "sr:match:68932610"
    assert ev.home_team_id == "7466"
    assert ev.away_team_id == "281784"

    # Crucial assertion: Total Goals 1.5 and Total Goals 2.5 MUST NOT be collapsed!
    # There should be 3 distinct markets: Mecz, Liczba goli (1.5), Liczba goli (2.5)
    assert len(ev.markets) == 3

    mkt_names = [m.name for m in ev.markets]
    assert "Mecz" in mkt_names
    assert mkt_names.count("Liczba goli") == 2

    # Verify specifiers are preserved
    spec_totals = [m.specifiers.get("total") for m in ev.markets if m.specifiers]
    assert "1.5" in spec_totals
    assert "2.5" in spec_totals


def test_superbet_parser_in_scope_and_out_of_scope_filtering():
    """
    Regression test demonstrating:
    1. Needed in-scope markets pass through early filtering (1X2, Totals, BTTS, Player Props, etc.)
    2. Out-of-scope markets (e.g. multi-market combos, exact scores, minutes, passes, saves) are skipped early
    3. Normalization graph remains 100% faithful to the central market scope.
    """
    parser = SuperbetParser()

    payload = {
        "eventId": 9999,
        "matchName": "Team A vs Team B",
        "odds": [
            # 1. In-scope: 1X2
            {
                "marketId": 547,
                "marketName": "Mecz",
                "selectionName": "1",
                "price": 2.10,
                "status": "active",
            },
            # 2. In-scope: Totals
            {
                "marketId": 200734,
                "marketName": "Liczba goli",
                "selectionName": "Powyżej",
                "price": 1.85,
                "specifiers": {"total": "2.5"},
                "status": "active",
            },
            # 3. In-scope: Player Goals
            {
                "marketId": 236226,
                "marketName": "Zawodnik - strzeli gola",
                "selectionName": "Lewandowski, Robert",
                "price": 1.95,
                "specifiers": {"player_name": "Lewandowski, Robert"},
                "status": "active",
            },
            # 4. In-scope: Player Assists
            {
                "marketId": 236230,
                "marketName": "Zawodnik - liczba asyst",
                "selectionName": "Lewandowski, Robert - powyżej 0.5",
                "price": 3.20,
                "specifiers": {"player_name": "Lewandowski, Robert", "total": "0.5"},
                "status": "active",
            },
            # 5. In-scope: Player Shots on Target
            {
                "marketId": 236216,
                "marketName": "Zawodnik - liczba celnych strzałów",
                "selectionName": "Lewandowski, Robert - powyżej 1.5",
                "price": 1.70,
                "specifiers": {"player_name": "Lewandowski, Robert", "total": "1.5"},
                "status": "active",
            },
            # 6. Out-of-scope: Superbets combo with semicolon (MUST BE SKIPPED)
            {
                "marketId": 233953,
                "marketName": "Powyżej 2.5 gola w meczu; Lewandowski strzeli gola; Team A wygra",
                "selectionName": "Tak",
                "price": 3.45,
                "status": "active",
                "extra": {"tag": "superbets"},
            },
            # 7. Out-of-scope: Exact score (MUST BE SKIPPED)
            {
                "marketId": 99901,
                "marketName": "Dokładny wynik",
                "selectionName": "2:1",
                "price": 8.50,
                "status": "active",
            },
            # 8. Out-of-scope: Player Passes (disallowed metric, MUST BE SKIPPED)
            {
                "marketId": 99902,
                "marketName": "Zawodnik - Liczba podań",
                "selectionName": "Lewandowski, Robert - powyżej 30.5",
                "price": 1.80,
                "status": "active",
            },
            # 9. Out-of-scope: Interval / minute market (MUST BE SKIPPED)
            {
                "marketId": 99903,
                "marketName": "Liczba goli - do 30 minuty",
                "selectionName": "Powyżej 0.5",
                "price": 1.90,
                "status": "active",
            },
        ],
    }

    events = parser.parse_payloads([payload])
    assert len(events) == 1
    ev = events[0]

    # Exactly 5 in-scope markets created, 4 out-of-scope skipped!
    assert len(ev.markets) == 5
    m_names = [m.name for m in ev.markets]
    assert "Mecz" in m_names
    assert "Liczba goli" in m_names
    assert "Zawodnik - strzeli gola" in m_names
    assert "Zawodnik - liczba asyst" in m_names
    assert "Zawodnik - liczba celnych strzałów" in m_names
    assert "Powyżej 2.5 gola w meczu; Lewandowski strzeli gola; Team A wygra" not in m_names
    assert "Dokładny wynik" not in m_names
    assert "Zawodnik - Liczba podań" not in m_names
    assert "Liczba goli - do 30 minuty" not in m_names


def test_superbet_parser_team_splitting():
    parser = SuperbetParser()

    h, a = parser._split_teams("Chelsea vs Arsenal")
    assert h == "Chelsea" and a == "Arsenal"

    h, a = parser._split_teams("Real Madrid - Barcelona")
    assert h == "Real Madrid" and a == "Barcelona"

    h, a = parser._split_teams("Bayern Munich – Dortmund")
    assert h == "Bayern Munich" and a == "Dortmund"

    h, a = parser._split_teams("Liverpool·Manchester City")
    assert h == "Liverpool" and a == "Manchester City"


def test_superbet_parser_missing_mandatory_fields():
    parser = SuperbetParser()
    raw_responses = [{"invalid": "data"}]

    with pytest.raises(SuperbetParsingError, match="Missing mandatory event fields"):
        parser.parse_payloads(raw_responses)


def test_superbet_parser_non_dict_payload():
    parser = SuperbetParser()
    with pytest.raises(SuperbetParsingError, match="Expected dict payload"):
        parser.parse_payloads(["not-a-dict"])


def test_superbet_parser_all_allowed_market_families_and_xtra_rejection():
    """
    Regression test confirming:
    1. All allowed market families pass early filtering and normalize properly:
       - 1X2 / Match Winner, Double Chance, BTTS, Totals (Over/Under), Draw No Bet, Handicap, Half Time Result
       - Team Goals, Corners (Match & Team), Cards (Match & Team), Shots (Match & Team), Shots on Target (Match & Team), Fouls, Offsides, Tackles
       - Standard Player Props (Goals, First Goal, Half Goals, SOT, Shots, Assists, Cards, Red Cards, Fouls, Tackles)
       - Combo BTTS + Totals
    2. Disallowed / Xtra markets are strictly rejected before creating objects:
       - Strzelec Xtra, Xtra, Superbets, Superkursy
       - Body parts: strzeli gola głową, celnych strzałów prawą nogą, strzałów spoza pola karnego
       - Combos: strzeli gola lub zaliczy asystę, strzeli gola & zaliczy asystę
       - Disallowed stats: liczba podań, liczba obronionych strzałów
    """
    from normalization.superbet_normalizer import SuperbetNormalizer

    parser = SuperbetParser()
    normalizer = SuperbetNormalizer()

    allowed_raw_odds = [
        # Match Level
        {"marketId": 547, "marketName": "Mecz", "selectionName": "1", "price": 2.10, "status": "active"},
        {"marketId": 548, "marketName": "Podwójna szansa", "selectionName": "1X", "price": 1.30, "status": "active"},
        {"marketId": 549, "marketName": "Obie drużyny strzelą", "selectionName": "Tak", "price": 1.75, "status": "active"},
        {"marketId": 200734, "marketName": "Liczba goli", "selectionName": "Powyżej", "price": 1.85, "specifiers": {"total": "2.5"}, "status": "active"},
        {"marketId": 555, "marketName": "Zakład bez remisu", "selectionName": "1", "price": 1.50, "status": "active"},
        {"marketId": 200736, "marketName": "Handicap", "selectionName": "1 (-1)", "price": 3.40, "status": "active"},
        {"marketId": 557, "marketName": "1. połowa - wynik", "selectionName": "1", "price": 2.60, "status": "active"},
        {"marketId": 558, "marketName": "Liczba rzutów rożnych", "selectionName": "Powyżej", "price": 1.80, "specifiers": {"total": "9.5"}, "status": "active"},
        {"marketId": 559, "marketName": "Liczba kartek", "selectionName": "Powyżej", "price": 1.90, "specifiers": {"total": "3.5"}, "status": "active"},
        {"marketId": 560, "marketName": "Liczba strzałów", "selectionName": "Powyżej", "price": 1.85, "specifiers": {"total": "24.5"}, "status": "active"},
        {"marketId": 561, "marketName": "Liczba celnych strzałów", "selectionName": "Powyżej", "price": 1.75, "specifiers": {"total": "8.5"}, "status": "active"},
        {"marketId": 562, "marketName": "Liczba fauli", "selectionName": "Powyżej", "price": 1.80, "specifiers": {"total": "22.5"}, "status": "active"},
        {"marketId": 563, "marketName": "Liczba spalonych", "selectionName": "Powyżej", "price": 1.95, "specifiers": {"total": "3.5"}, "status": "active"},
        {"marketId": 564, "marketName": "Liczba odbiorów", "selectionName": "Powyżej", "price": 1.85, "specifiers": {"total": "28.5"}, "status": "active"},
        {"marketId": 565, "marketName": "Legia Warszawa - liczba goli", "selectionName": "Powyżej", "price": 1.65, "specifiers": {"total": "1.5"}, "status": "active"},
        {"marketId": 566, "marketName": "Liczba goli & obie drużyny strzelą", "selectionName": "Powyżej 2.5 i Tak", "price": 2.10, "status": "active"},

        # Player Props (Allowed)
        {"marketId": 236226, "marketName": "Zawodnik - strzeli gola", "selectionName": "Lewandowski, Robert", "price": 1.95, "specifiers": {"player_name": "Lewandowski, Robert"}, "status": "active"},
        {"marketId": 236227, "marketName": "Strzelec gola", "selectionName": "Yamal, Lamine", "price": 2.80, "specifiers": {"player_name": "Yamal, Lamine"}, "status": "active"},
        {"marketId": 236228, "marketName": "Zawodnik - strzeli 1. gola", "selectionName": "Lewandowski, Robert", "price": 4.50, "specifiers": {"player_name": "Lewandowski, Robert"}, "status": "active"},
        {"marketId": 236229, "marketName": "Zawodnik - strzeli gola w 1. połowie", "selectionName": "Lewandowski, Robert", "price": 3.40, "specifiers": {"player_name": "Lewandowski, Robert"}, "status": "active"},
        {"marketId": 236216, "marketName": "Zawodnik - liczba celnych strzałów", "selectionName": "Lewandowski, Robert - powyżej 1.5", "price": 1.70, "specifiers": {"player_name": "Lewandowski, Robert", "total": "1.5"}, "status": "active"},
        {"marketId": 236217, "marketName": "Zawodnik - liczba strzałów", "selectionName": "Lewandowski, Robert - powyżej 3.5", "price": 1.85, "specifiers": {"player_name": "Lewandowski, Robert", "total": "3.5"}, "status": "active"},
        {"marketId": 236230, "marketName": "Zawodnik - liczba asyst", "selectionName": "Raphinha - powyżej 0.5", "price": 2.75, "specifiers": {"player_name": "Raphinha", "total": "0.5"}, "status": "active"},
        {"marketId": 236231, "marketName": "Zawodnik - otrzyma kartkę", "selectionName": "Gavi - Tak", "price": 2.20, "specifiers": {"player_name": "Gavi"}, "status": "active"},
        {"marketId": 236232, "marketName": "Zawodnik - otrzyma czerwoną kartkę", "selectionName": "Gavi - Tak", "price": 15.0, "specifiers": {"player_name": "Gavi"}, "status": "active"},
        {"marketId": 236233, "marketName": "Zawodnik - liczba popełnionych fauli", "selectionName": "Gavi - powyżej 1.5", "price": 1.65, "specifiers": {"player_name": "Gavi", "total": "1.5"}, "status": "active"},
        {"marketId": 236234, "marketName": "Zawodnik - liczba odbiorów", "selectionName": "Pedri - powyżej 2.5", "price": 1.90, "specifiers": {"player_name": "Pedri", "total": "2.5"}, "status": "active"},
    ]

    disallowed_raw_odds = [
        # Xtra / Superbets / Superkursy
        {"marketId": 9901, "marketName": "Strzelec Xtra", "selectionName": "Lewandowski, Robert", "price": 2.20, "status": "active"},
        {"marketId": 9902, "marketName": "Zawodnik Xtra - Strzelec", "selectionName": "Yamal, Lamine", "price": 3.10, "status": "active"},
        {"marketId": 9903, "marketName": "Superbets - Powyżej 2.5 gola; Lewandowski strzeli", "selectionName": "Tak", "price": 3.50, "status": "active"},
        {"marketId": 9904, "marketName": "Superkursy - Mecz", "selectionName": "1", "price": 2.30, "status": "active"},
        {"marketId": 9905, "marketName": "Super Przewaga - Mecz", "selectionName": "1", "price": 2.10, "status": "active"},
        {"marketId": 9906, "marketName": "Hit Dnia", "selectionName": "1", "price": 2.15, "status": "active"},

        # Body parts
        {"marketId": 9910, "marketName": "Zawodnik - strzeli gola głową", "selectionName": "Lewandowski, Robert", "price": 4.50, "status": "active"},
        {"marketId": 9911, "marketName": "Zawodnik - strzeli gola prawą nogą", "selectionName": "Lewandowski, Robert", "price": 2.50, "status": "active"},
        {"marketId": 9912, "marketName": "Zawodnik - strzeli gola lewą nogą", "selectionName": "Lewandowski, Robert", "price": 5.00, "status": "active"},
        {"marketId": 9913, "marketName": "Zawodnik - strzeli gola spoza pola karnego", "selectionName": "Yamal, Lamine", "price": 6.00, "status": "active"},
        {"marketId": 9914, "marketName": "Zawodnik - liczba celnych strzałów prawą nogą", "selectionName": "Lewandowski, Robert - powyżej 1.5", "price": 1.95, "status": "active"},
        {"marketId": 9915, "marketName": "Zawodnik - liczba strzałów spoza pola karnego", "selectionName": "Raphinha - powyżej 1.5", "price": 2.10, "status": "active"},

        # Player combos
        {"marketId": 9920, "marketName": "Zawodnik - strzeli gola lub zaliczy asystę", "selectionName": "Lewandowski, Robert", "price": 1.45, "status": "active"},
        {"marketId": 9921, "marketName": "Zawodnik - strzeli gola & zaliczy asystę", "selectionName": "Lewandowski, Robert", "price": 5.50, "status": "active"},
        {"marketId": 9922, "marketName": "Zawodnik - liczba fauli na zawodniku", "selectionName": "Vinicius - powyżej 2.5", "price": 1.75, "status": "active"},
        {"marketId": 9923, "marketName": "Zawodnik - otrzyma 1. kartkę", "selectionName": "Gavi", "price": 4.50, "status": "active"},

        # Disallowed metrics
        {"marketId": 9930, "marketName": "Zawodnik - liczba podań", "selectionName": "Pedri - powyżej 65.5", "price": 1.85, "status": "active"},
        {"marketId": 9931, "marketName": "Zawodnik - liczba obronionych strzałów", "selectionName": "Ter Stegen - powyżej 3.5", "price": 1.90, "status": "active"},

        # Match Disallowed Specials
        {"marketId": 9940, "marketName": "Dokładny wynik", "selectionName": "2:1", "price": 8.50, "status": "active"},
        {"marketId": 9941, "marketName": "Liczba goli - do 15 minuty", "selectionName": "Powyżej 0.5", "price": 2.80, "status": "active"},
        {"marketId": 9942, "marketName": "1.połowa / 2.połowa", "selectionName": "1 / 1", "price": 3.20, "status": "active"},
        {"marketId": 9943, "marketName": "Wygra do zera", "selectionName": "Legia Warszawa", "price": 3.40, "status": "active"},
    ]

    payload = {
        "eventId": "test_ev_01",
        "matchName": "Legia Warszawa vs Lech Poznań",
        "homeTeamName": "Legia Warszawa",
        "awayTeamName": "Lech Poznań",
        "odds": allowed_raw_odds + disallowed_raw_odds,
    }

    events = parser.parse_payloads([payload])
    assert len(events) == 1
    ev = events[0]

    # Exactly all 27 allowed markets created, and 0 of the 20 disallowed markets created
    assert len(ev.markets) == len(allowed_raw_odds)

    mkt_names = [m.name for m in ev.markets]
    # Verify allowed names are present
    assert "Mecz" in mkt_names
    assert "Podwójna szansa" in mkt_names
    assert "Obie drużyny strzelą" in mkt_names
    assert "Liczba goli" in mkt_names
    assert "Zakład bez remisu" in mkt_names
    assert "Handicap" in mkt_names
    assert "1. połowa - wynik" in mkt_names
    assert "Zawodnik - strzeli gola" in mkt_names
    assert "Strzelec gola" in mkt_names
    assert "Zawodnik - liczba celnych strzałów" in mkt_names
    assert "Zawodnik - liczba strzałów" in mkt_names
    assert "Zawodnik - liczba asyst" in mkt_names
    assert "Zawodnik - otrzyma kartkę" in mkt_names
    assert "Zawodnik - liczba popełnionych fauli" in mkt_names
    assert "Zawodnik - liczba odbiorów" in mkt_names

    # Verify disallowed names are NOT present
    assert "Strzelec Xtra" not in mkt_names
    assert "Zawodnik Xtra - Strzelec" not in mkt_names
    assert "Dokładny wynik" not in mkt_names
    assert "Zawodnik - strzeli gola głową" not in mkt_names
    assert "Zawodnik - strzeli gola lub zaliczy asystę" not in mkt_names
    assert "Zawodnik - liczba podań" not in mkt_names

    # Verify normalization into Canonical Domain Graph
    graph = normalizer.normalize_event(ev)
    assert len(graph.markets) >= 20
    norm_types = {m.market_type for m in graph.markets}
    assert "1X2" in norm_types
    assert "DOUBLE_CHANCE" in norm_types
    assert "BTTS" in norm_types
    assert "TOTALS" in norm_types
    assert "DRAW_NO_BET" in norm_types
    assert "HANDICAP" in norm_types
    assert "HALF_TIME_RESULT" in norm_types
    assert "PLAYER_GOALS" in norm_types
    assert "PLAYER_FIRST_GOAL" in norm_types
    assert "PLAYER_SHOTS_ON_TARGET" in norm_types
    assert "PLAYER_SHOTS" in norm_types
    assert "PLAYER_ASSISTS" in norm_types
    assert "PLAYER_CARDS" in norm_types
    assert "PLAYER_FOULS" in norm_types
    assert "PLAYER_TACKLES" in norm_types

