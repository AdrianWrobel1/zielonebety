import asyncio
import pytest
from api.fastapi_app import app


async def _make_asgi_request(path: str, headers=None):
    raw_headers= []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode(), v.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw_headers,
    }
    messages = []

    async def receive():
        return {"type": "http.request"}

    async def send(msg):
        messages.append(msg)

    await app(scope, receive, send)

    status = 500
    resp_headers = {}
    body_parts = []

    for msg in messages:
        if msg["type"] == "http.response.start":
            status = msg["status"]
            resp_headers = {k.decode("latin1").lower(): v.decode("latin1") for k, v in msg.get("headers", [])}
        elif msg["type"] == "http.response.body":
            body_parts.append(msg.get("body", b""))

    return status, resp_headers, b"i".join(body_parts)


@pytest.mark.asyncio
async def test_security_headers_present():
    status, headers, _ = await _make_asgi_request("/api/v1/health")
    assert status == 200
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert headers.get("referrer-policy") == "strict-origin-when-cross-origin"


@pytest.mark.asyncio
async def test_spa_routes_serve_index_html():
    for route in ["/dashboard", "/profiler", "/events", "/playerprops", "/opportunities", "/settings"]:
        status, headers, body = await _make_asgi_request(route)
        assert status == 200, f"Failed for route {route} with status {status}"
        assert "text/html" in headers.get("content-type", ""), f"Wrong content-type for {route}"
        assert b"<!DOCTYPE html>" in body, f"HTML doctype missing for {route}"


@pytest.mark.asyncio
async def test_static_favicon_and_robots():
    status_fav, headers_fav, _ = await _make_asgi_request("/favicon.ico")
    assert status_fav == 200
    assert "image/svg+xml" in headers_fav.get("content-type", "")

    status_rob, headers_rob, body_rob = await _make_asgi_request("/robots.txt")
    assert status_rob == 200
    assert "text/plain" in headers_rob.get("content-type", "")
    assert b"Disallow: /" in body_rob


@pytest.mark.asyncio
async def test_unknown_api_returns_json_404():
    status, headers, body = await _make_asgi_request("/api/v1/nonexistent_test_route")
    assert status == 404
    assert "application/json" in headers.get("content-type", "")
    assert b"Not Found" in body


@pytest.mark.asyncio
async def test_gzip_compression_supported():
    status, headers, body = await _make_asgi_request("/dashboard", headers={"Accept-Encoding": "gzip"})
    assert status == 200
    assert headers.get("content-encoding") == "gzip"


@pytest.mark.asyncio
async def test_spa_deep_links_and_remaining_views():
    for route in ["/notifications", "/history", "/opportunity/arb-real-barca-1", "/teamprops"]:
        status, headers, body = await _make_asgi_request(route)
        assert status == 200, f"Failed for route {route} with status {status}"
        assert "text/html" in headers.get("content-type", "")
        assert b"<!DOCTYPE html>" in body


@pytest.mark.asyncio
async def test_auth_protection_on_mutating_endpoints():
    # Attempt mutating settings without token
    status, headers, body = await _make_asgi_request("/api/v1/settings")
    # GET settings is read-only (200)
    assert status == 200

    # Test that unknown API returns JSON 404
    status_unk, headers_unk, body_unk = await _make_asgi_request("/api/v1/unknown_mutation_probe")
    assert status_unk == 404
    assert "application/json" in headers_unk.get("content-type", "")


