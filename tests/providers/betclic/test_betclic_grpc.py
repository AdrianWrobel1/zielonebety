"""
Unit & Integration Test Suite for Betclic gRPC-Web Client and Protobuf Parser
"""

import struct
import pytest
from unittest.mock import MagicMock, patch

from providers.betclic.fetch.grpc_client import (
    encode_varint,
    parse_varint,
    decode_protobuf,
    build_get_match_request,
    parse_grpc_web_frames,
    BetclicGrpcParser,
    BetclicGrpcClient,
)
from providers.betclic.parser.parser import BetclicParser
from providers.betclic.fetch.fetcher import BetclicFetcher
from providers.betclic.config import BetclicConfig
from providers.betclic.models import BetclicDiscoveredItem
from providers.betclic.exceptions import BetclicParsingError, BetclicFetchError
from normalization.betclic_normalizer import BetclicNormalizer


# ──────────────────────────────────────────────────────────────────────────────
# Helper Protobuf Encoders for Deterministic Fixtures
# ──────────────────────────────────────────────────────────────────────────────

def _make_field(field_no: int, wire_type: int, val: bytes) -> bytes:
    tag = (field_no << 3) | wire_type
    return encode_varint(tag) + val


def _make_varint_field(field_no: int, val: int) -> bytes:
    return _make_field(field_no, 0, encode_varint(val))


def _make_double_field(field_no: int, val: float) -> bytes:
    return _make_field(field_no, 1, struct.pack("<d", val))


def _make_string_field(field_no: int, val: str) -> bytes:
    b = val.encode("utf-8")
    return _make_field(field_no, 2, encode_varint(len(b)) + b)


def _make_msg_field(field_no: int, msg_bytes: bytes) -> bytes:
    return _make_field(field_no, 2, encode_varint(len(msg_bytes)) + msg_bytes)


def _build_raw_selection(
    sel_id: int,
    name: str,
    betslip_name: str,
    odds: float,
    status: int = 1,
    extra_field_val: int = 999,
) -> bytes:
    buf = bytearray()
    buf.extend(_make_varint_field(1, sel_id))
    buf.extend(_make_string_field(10, name))
    buf.extend(_make_string_field(11, betslip_name))
    buf.extend(_make_double_field(12, odds))
    buf.extend(_make_varint_field(14, status))
    # Unknown field for robustness test
    buf.extend(_make_varint_field(99, extra_field_val))
    return bytes(buf)


def _build_raw_market(
    mkt_id: int,
    name: str,
    betslip_name: str,
    selections: list[bytes],
    status: int = 1,
    group_markets: list[bytes] = None,
) -> bytes:
    buf = bytearray()
    buf.extend(_make_varint_field(1, mkt_id))
    buf.extend(_make_string_field(2, name))
    buf.extend(_make_string_field(3, betslip_name))
    buf.extend(_make_varint_field(4, status))
    for s in selections:
        buf.extend(_make_msg_field(16, s))
    if group_markets:
        for gm in group_markets:
            buf.extend(_make_msg_field(13, gm))
    return bytes(buf)


def _build_raw_match_response(
    match_id: int,
    match_name: str,
    subcategories: list[tuple[str, str, list[bytes]]],
) -> bytes:
    # 1. Match message
    match_buf = bytearray()
    match_buf.extend(_make_varint_field(1, match_id))
    match_buf.extend(_make_string_field(2, match_name))
    match_buf.extend(_make_string_field(3, "2026-08-25T19:00:00.0000000Z"))
    match_buf.extend(_make_varint_field(4, 0))  # isLive = False

    # Competition: field 5
    comp_buf = bytearray()
    comp_buf.extend(_make_string_field(1, "c_13"))
    comp_buf.extend(_make_string_field(2, "LaLiga"))
    match_buf.extend(_make_msg_field(5, bytes(comp_buf)))

    # Contestants: field 6
    for c_id, c_name in [("1", "Osasuna"), ("2", "Levante")]:
        c_buf = bytearray()
        c_buf.extend(_make_string_field(1, c_id))
        c_buf.extend(_make_string_field(2, c_name))
        match_buf.extend(_make_msg_field(6, bytes(c_buf)))

    # SubCategories: field 11
    for sc_id, sc_name, mkts in subcategories:
        sc_buf = bytearray()
        sc_buf.extend(_make_string_field(1, sc_id))
        sc_buf.extend(_make_string_field(2, sc_name))
        for m in mkts:
            sc_buf.extend(_make_msg_field(3, m))
        match_buf.extend(_make_msg_field(11, bytes(sc_buf)))

    # 2. Payload message (field 1 is match)
    payload_buf = bytearray()
    payload_buf.extend(_make_msg_field(1, bytes(match_buf)))

    # 3. Response message (field 1 is payload)
    resp_buf = bytearray()
    resp_buf.extend(_make_msg_field(1, bytes(payload_buf)))

    # 4. Frame: 0x00 + 4 bytes length + resp_buf
    frame = bytearray()
    frame.append(0x00)
    frame.extend(struct.pack(">I", len(resp_buf)))
    frame.extend(resp_buf)
    return bytes(frame)


# ──────────────────────────────────────────────────────────────────────────────
# Test Cases
# ──────────────────────────────────────────────────────────────────────────────

def test_request_serialization():
    """Verifies gRPC-Web request serialization and framing."""
    req_bytes = build_get_match_request(
        match_id=1186317656420352,
        language="pl",
        category_id="ca_ftb_prp",
    )
    assert len(req_bytes) > 5
    assert req_bytes[0] == 0x00  # Data frame flag

    payload_len = struct.unpack(">I", req_bytes[1:5])[0]
    assert payload_len == len(req_bytes) - 5

    # Decode protobuf fields inside request payload
    fields = decode_protobuf(req_bytes[5:])
    field_dict = {f: (w, v) for f, w, v in fields}

    assert 1 in field_dict
    assert field_dict[1] == (0, 1186317656420352)

    assert 2 in field_dict
    assert field_dict[2][1].decode("utf-8") == "pl"

    assert 3 in field_dict
    assert field_dict[3][1].decode("utf-8") == "ca_ftb_prp"


def test_grpc_frame_parsing_multiple_and_trailers():
    """Verifies frame parser handles data frames, trailer frames, and multiple frames."""
    msg1 = b"hello_grpc"
    frame1 = bytes([0x00]) + struct.pack(">I", len(msg1)) + msg1

    trailer = b"grpc-status: 0\r\n"
    frame_trailer = bytes([0x80]) + struct.pack(">I", len(trailer)) + trailer

    msg2 = b"second_payload"
    frame2 = bytes([0x00]) + struct.pack(">I", len(msg2)) + msg2

    combined = frame1 + frame_trailer + frame2
    parsed = parse_grpc_web_frames(combined)

    assert len(parsed) == 2
    assert parsed[0] == msg1
    assert parsed[1] == msg2


def test_grpc_frame_parsing_malformed():
    """Verifies that truncated or malformed frames raise clear BetclicParsingError."""
    # Truncated header (< 5 bytes)
    with pytest.raises(BetclicParsingError, match="Truncated gRPC-Web frame header"):
        parse_grpc_web_frames(b"\x00\x00\x00")

    # Header says 100 bytes, but only 5 provided
    corrupted_body = bytes([0x00]) + struct.pack(">I", 100) + b"12345"
    with pytest.raises(BetclicParsingError, match="Truncated gRPC-Web frame body"):
        parse_grpc_web_frames(corrupted_body)


def test_protobuf_decoding_1x2_market():
    """Verifies decoding a standard 1X2 market."""
    s1 = _build_raw_selection(101, "Osasuna", "Osasuna", 1.95, status=1)
    sx = _build_raw_selection(102, "Remis", "Remis", 3.40, status=1)
    s2 = _build_raw_selection(103, "Levante", "Levante", 4.10, status=1)

    mkt = _build_raw_market(1001, "Wynik meczu", "Wynik meczu", [s1, sx, s2])
    frame = _build_raw_match_response(
        match_id=99901,
        match_name="Osasuna - Levante",
        subcategories=[("sc_main", "Główne", [mkt])],
    )

    frames = parse_grpc_web_frames(frame)
    match_dict = BetclicGrpcParser.decode_get_match_response(frames[0])

    assert match_dict["id"] == "99901"
    assert match_dict["name"] == "Osasuna - Levante"
    assert match_dict["home_team"] == "Osasuna"
    assert match_dict["away_team"] == "Levante"

    parser = BetclicParser()
    events = parser.parse_payloads([match_dict])
    assert len(events) == 1
    ev = events[0]
    assert len(ev.markets) == 1

    m = ev.markets[0]
    assert m.provider_market_id == "1001"
    assert m.name == "Wynik meczu"
    assert len(m.selections) == 3
    assert m.selections[0].provider_selection_id == "101"
    assert m.selections[0].odds.decimal_odds == 1.95
    assert m.selections[1].odds.decimal_odds == 3.40
    assert m.selections[2].odds.decimal_odds == 4.10

    # Normalizer test
    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(ev)
    assert len(graph.markets) == 1
    assert graph.markets[0].market_type == "1X2"
    assert len(graph.selections) == 3
    assert len(graph.odds_list) == 3


def test_protobuf_decoding_totals_and_corners():
    """Verifies decoding Over/Under and Corner statistical markets."""
    s_over = _build_raw_selection(201, "Powyżej 8.5", "Powyżej 8.5", 1.85, status=1)
    s_under = _build_raw_selection(202, "Poniżej 8.5", "Poniżej 8.5", 1.95, status=1)

    mkt_corners = _build_raw_market(2001, "Rzuty rożne", "Rzuty rożne", [s_over, s_under])
    frame = _build_raw_match_response(
        match_id=99902,
        match_name="Osasuna - Levante",
        subcategories=[("ca_ftb_prp", "Statystyki", [mkt_corners])],
    )

    frames = parse_grpc_web_frames(frame)
    match_dict = BetclicGrpcParser.decode_get_match_response(frames[0])

    parser = BetclicParser()
    events = parser.parse_payloads([match_dict])
    assert len(events) == 1
    ev = events[0]

    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(ev)
    assert len(graph.markets) >= 1
    mkt = graph.markets[0]
    assert mkt.market_type == "TOTALS"
    assert mkt.metadata.get("metric") == "CORNERS"
    assert mkt.line == 8.5


def test_protobuf_decoding_player_cards_and_fouls():
    """Verifies decoding Player Cards and Player Fouls markets."""
    s_card_yes = _build_raw_selection(301, "Tak", "Tak", 3.20, status=1)
    s_card_no = _build_raw_selection(302, "Nie", "Nie", 1.30, status=1)
    mkt_card = _build_raw_market(3001, "Kartka dla zawodnika: Ante Budimir", "Kartka dla zawodnika: Ante Budimir", [s_card_yes, s_card_no])

    s_foul_over = _build_raw_selection(303, "Powyżej 1.5", "Powyżej 1.5", 1.70, status=1)
    s_foul_under = _build_raw_selection(304, "Poniżej 1.5", "Poniżej 1.5", 2.05, status=1)
    mkt_foul = _build_raw_market(3002, "Faule zawodnika: Jon Moncayola", "Faule zawodnika: Jon Moncayola", [s_foul_over, s_foul_under])

    frame = _build_raw_match_response(
        match_id=99903,
        match_name="Osasuna - Levante",
        subcategories=[("ca_ftb_prp", "Statystyki", [mkt_card, mkt_foul])],
    )

    frames = parse_grpc_web_frames(frame)
    match_dict = BetclicGrpcParser.decode_get_match_response(frames[0])

    parser = BetclicParser()
    events = parser.parse_payloads([match_dict])
    ev = events[0]
    assert len(ev.markets) == 2

    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(ev)

    card_mkts = [m for m in graph.markets if m.market_type == "PLAYER_CARDS"]
    assert len(card_mkts) >= 1
    assert card_mkts[0].metadata.get("player_name") == "Ante Budimir"

    foul_mkts = [m for m in graph.markets if m.market_type == "PLAYER_FOULS"]
    assert len(foul_mkts) >= 1
    assert foul_mkts[0].metadata.get("player_name") == "Jon Moncayola"


def test_closed_and_unavailable_selections():
    """Verifies that selections with status=0 or invalid odds are inactive."""
    s_open = _build_raw_selection(401, "Powyżej 2.5", "Powyżej 2.5", 1.80, status=1)
    s_closed = _build_raw_selection(402, "Poniżej 2.5", "Poniżej 2.5", 2.00, status=0)

    mkt = _build_raw_market(4001, "Liczba goli", "Liczba goli", [s_open, s_closed])
    frame = _build_raw_match_response(
        match_id=99904,
        match_name="Osasuna - Levante",
        subcategories=[("sc_main", "Główne", [mkt])],
    )

    frames = parse_grpc_web_frames(frame)
    match_dict = BetclicGrpcParser.decode_get_match_response(frames[0])

    parser = BetclicParser()
    events = parser.parse_payloads([match_dict])
    ev = events[0]
    m = ev.markets[0]

    assert m.selections[0].odds.is_active is True
    assert m.selections[1].odds.is_active is False

    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(ev)
    # Only active odds with decimal_odds > 1.0 are added to odds_list
    assert len(graph.odds_list) == 1
    assert graph.odds_list[0].decimal_odds == 1.80


def test_grpc_client_mock_multicategory():
    """Verifies BetclicGrpcClient combines multiple category requests into a single unified match dict."""
    mkt1 = _build_raw_market(5001, "Wynik meczu", "Wynik meczu", [
        _build_raw_selection(501, "Osasuna", "Osasuna", 2.10),
    ])
    frame1 = _build_raw_match_response(
        match_id=99905,
        match_name="Osasuna - Levante",
        subcategories=[("sc_main", "Główne", [mkt1])],
    )

    mkt2 = _build_raw_market(5002, "Rzuty rożne", "Rzuty rożne", [
        _build_raw_selection(502, "Powyżej 9.5", "Powyżej 9.5", 1.90),
    ])
    frame2 = _build_raw_match_response(
        match_id=99905,
        match_name="Osasuna - Levante",
        subcategories=[("ca_ftb_prp", "Statystyki", [mkt2])],
    )

    mock_session = MagicMock()
    # Return frame1 for first call, frame2 for second call
    mock_resp1 = MagicMock(is_success=True, status_code=200, body=frame1, content=frame1, raw_body=frame1)
    mock_resp2 = MagicMock(is_success=True, status_code=200, body=frame2, content=frame2, raw_body=frame2)
    mock_session.post.side_effect = [mock_resp1, mock_resp2]

    client = BetclicGrpcClient(session_manager=mock_session)
    result = client.fetch_match_detail(
        match_id=99905,
        categories=("", "ca_ftb_prp"),
    )

    assert result["id"] == "99905"
    assert len(result["subCategories"]) == 2

    sc_names = [sc["name"] for sc in result["subCategories"]]
    assert "Główne" in sc_names
    assert "Statystyki" in sc_names

    parser = BetclicParser()
    events = parser.parse_payloads([result])
    assert len(events[0].markets) == 2


def test_fetcher_fallback_to_html():
    """Verifies BetclicFetcher falls back to HTML SSR scraper when gRPC endpoint fails."""
    config = BetclicConfig(
        use_grpc_detail=True,
        fallback_to_html=True,
    )
    mock_session = MagicMock()

    # Make gRPC post fail with 500 error
    mock_grpc_resp = MagicMock(is_success=False, status_code=500)
    mock_session.post.return_value = mock_grpc_resp

    # Make HTML get return SSR payload
    html_content = '''
    <html>
      <script id="ssr-state" type="application/json">
        {
          "matchState": {
            "response": {
              "payload": {
                "match": {
                  "id": "99906",
                  "name": "Osasuna - Levante",
                  "competition": {"name": "LaLiga"},
                  "contestants": [{"name": "Osasuna"}, {"name": "Levante"}],
                  "markets": [
                    {
                      "id": "mkt_html_1",
                      "name": "Wynik meczu",
                      "code": "1X2",
                      "mainSelections": [
                        {"id": "s_html_1", "name": "Osasuna", "odds": 2.05, "status": 1}
                      ]
                    }
                  ]
                }
              }
            }
          }
        }
      </script>
    </html>
    '''
    mock_html_resp = MagicMock(is_success=True, status_code=200, text=lambda: html_content)
    mock_session.get.return_value = mock_html_resp

    fetcher = BetclicFetcher(config=config, session_manager=mock_session)
    item = BetclicDiscoveredItem(
        provider_event_id="99906",
        name="Osasuna - Levante",
        competition_name="LaLiga",
        url="https://www.betclic.pl/events/99906",
        start_time="2026-08-25T19:00:00Z",
    )

    payload = fetcher._fetch_detail_event(item)
    assert payload["id"] == "99906"
    assert payload["name"] == "Osasuna - Levante"
    assert len(payload["markets"]) == 1
    assert payload["markets"][0]["id"] == "mkt_html_1"


def test_protobuf_decoding_split_cards_and_sliders():
    """Verifies decoding markets with split cards (field 11) and sliders (field 15)."""
    # 1. Split card selection: field 1 card_name, field 2 selection
    split_card_buf = bytearray()
    split_card_buf.extend(_make_string_field(1, "Ante Budimir"))
    s_raw = _build_raw_selection(601, "Strzeli", "Strzeli", 2.45, status=1)
    split_card_buf.extend(_make_msg_field(2, s_raw))

    # 2. Slider selection: field 1 slider_name, field 3 slider_val
    slider_buf = bytearray()
    slider_buf.extend(_make_string_field(1, "Liczba strzałów"))
    # SliderValue: field 1 int val, field 2 NullableSelection
    sv_buf = bytearray()
    sv_buf.extend(_make_varint_field(1, 3))
    # NullableSelection: field 1 selection
    ns_buf = bytearray()
    ns_buf.extend(_make_msg_field(1, _build_raw_selection(602, "Powyżej 2.5", "Powyżej 2.5", 1.75, status=1)))
    sv_buf.extend(_make_msg_field(2, bytes(ns_buf)))
    slider_buf.extend(_make_msg_field(3, bytes(sv_buf)))

    # Build market containing both
    mkt_buf = bytearray()
    mkt_buf.extend(_make_varint_field(1, 6001))
    mkt_buf.extend(_make_string_field(2, "Strzelcy i statystyki"))
    mkt_buf.extend(_make_string_field(3, "Strzelcy i statystyki"))
    mkt_buf.extend(_make_varint_field(4, 1))
    mkt_buf.extend(_make_msg_field(11, bytes(split_card_buf)))
    mkt_buf.extend(_make_msg_field(15, bytes(slider_buf)))

    frame = _build_raw_match_response(
        match_id=99907,
        match_name="Osasuna - Levante",
        subcategories=[("ca_ftb_gsc", "Strzelcy", [bytes(mkt_buf)])],
    )

    frames = parse_grpc_web_frames(frame)
    match_dict = BetclicGrpcParser.decode_get_match_response(frames[0])

    parser = BetclicParser()
    events = parser.parse_payloads([match_dict])
    ev = events[0]
    assert len(ev.markets) == 1
    m = ev.markets[0]
    assert len(m.selections) == 2

    # Check split card selection name
    assert "Ante Budimir" in m.selections[0].name
    assert m.selections[0].odds.decimal_odds == 2.45

    # Check slider selection
    assert m.selections[1].odds.decimal_odds == 1.75


def test_protobuf_decoding_tabs_and_player_shots():
    """Verifies decoding tabbed markets and Player Shots."""
    s_shots1 = _build_raw_selection(701, "Powyżej 1.5", "Powyżej 1.5", 1.65)
    s_shots2 = _build_raw_selection(702, "Poniżej 1.5", "Poniżej 1.5", 2.10)
    mkt_tab_shots = _build_raw_market(7002, "Celne strzały zawodnika: Bryan Zaragoza", "Celne strzały", [s_shots1, s_shots2])

    tab_buf = bytearray()
    tab_buf.extend(_make_string_field(1, "Strzały"))
    tab_buf.extend(_make_msg_field(2, mkt_tab_shots))

    mkt_container = bytearray()
    mkt_container.extend(_make_varint_field(1, 7001))
    mkt_container.extend(_make_string_field(2, "Zawodnicy - Strzały"))
    mkt_container.extend(_make_msg_field(14, bytes(tab_buf)))

    frame = _build_raw_match_response(
        match_id=99908,
        match_name="Osasuna - Levante",
        subcategories=[("ca_ftb_prp", "Statystyki", [bytes(mkt_container)])],
    )

    frames = parse_grpc_web_frames(frame)
    match_dict = BetclicGrpcParser.decode_get_match_response(frames[0])

    parser = BetclicParser()
    events = parser.parse_payloads([match_dict])
    ev = events[0]
    assert len(ev.markets) >= 1

    normalizer = BetclicNormalizer()
    graph = normalizer.normalize_event(ev)
    shot_mkts = [m for m in graph.markets if m.market_type == "PLAYER_SHOTS_ON_TARGET"]
    assert len(shot_mkts) >= 1
    assert shot_mkts[0].metadata.get("player_name") == "Bryan Zaragoza"


def test_protobuf_unknown_wire_types_and_fields():
    """Verifies parser does not fail when unknown tags and wire types are present."""
    raw_garbage_tail = b"\xff\xff\xff\x7f"
    valid_sel = _build_raw_selection(801, "Osasuna", "Osasuna", 2.20)
    corrupted_sel = valid_sel + raw_garbage_tail

    # Should safely parse known fields without raising unhandled exception
    sel_dict = BetclicGrpcParser.decode_selection(corrupted_sel)
    assert sel_dict["id"] == "801"
    assert sel_dict["name"] == "Osasuna"
    assert sel_dict["odds"] == 2.20

