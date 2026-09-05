"""P1-NEW-011 regression: security hardening (offline).

- trace export requires admin;
- login brute-force throttled (429 path helpers);
- logout revokes the bearer session;
- CORS never wildcard with credentials; methods/headers explicit;
- 500 envelope is generic (no exception text to clients).
"""
from api import auth as auth_mod
from api.auth import (
    clear_login_attempts,
    clear_sessions,
    create_session,
    is_login_rate_limited,
    register_login_attempt,
    revoke_token,
    verify_token,
)


class _Req:
    def __init__(self, host="10.9.9.9"):
        self.client = type("C", (), {"host": host})()


def setup_function(_):
    clear_login_attempts()
    clear_sessions()


def teardown_function(_):
    clear_login_attempts()
    clear_sessions()


def test_login_throttle_blocks_after_window():
    import os

    old_max, old_win = os.environ.get("LOGIN_RATE_LIMIT_MAX"), os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS")
    os.environ["LOGIN_RATE_LIMIT_MAX"] = "3"
    os.environ["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = "60"
    try:
        req = _Req()
        assert is_login_rate_limited(req) is False
        register_login_attempt(req, success=False)
        register_login_attempt(req, success=False)
        assert is_login_rate_limited(req) is False
        register_login_attempt(req, success=False)
        assert is_login_rate_limited(req) is True
        # Success resets the window.
        register_login_attempt(req, success=True)
        assert is_login_rate_limited(req) is False
    finally:
        if old_max is None:
            os.environ.pop("LOGIN_RATE_LIMIT_MAX", None)
        else:
            os.environ["LOGIN_RATE_LIMIT_MAX"] = old_max
        if old_win is None:
            os.environ.pop("LOGIN_RATE_LIMIT_WINDOW_SECONDS", None)
        else:
            os.environ["LOGIN_RATE_LIMIT_WINDOW_SECONDS"] = old_win


def test_logout_revokes_session():
    token = create_session("admin", role="Admin")
    assert verify_token(token) is not None
    assert revoke_token(token) is True
    assert verify_token(token) is None
    assert revoke_token(token) is False


def test_trace_export_requires_admin():
    from api.fastapi_app import app

    targets = [r for r in app.routes
               if getattr(r, "path", "") in ("/api/v1/scan/trace/{trace_id}/export",)
               and "GET" in getattr(r, "methods", set())]
    assert targets, "trace export route missing"
    dep_names = set()
    for route in targets:
        for dep in getattr(route, "dependant", None).dependencies if getattr(route, "dependant", None) else []:
            fn = getattr(dep, "call", None)
            dep_names.add(getattr(fn, "__name__", str(fn)))
    assert "require_admin" in dep_names


def test_cors_explicit_no_wildcard_with_credentials():
    from api.fastapi_app import app
    from fastapi.middleware.cors import CORSMiddleware

    mw = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert mw, "CORS middleware missing"
    opts = mw[0].kwargs
    assert "*" not in (opts.get("allow_origins") or [])
    assert "*" not in (opts.get("allow_methods") or [])
    assert "*" not in (opts.get("allow_headers") or [])


def test_500_envelope_is_generic():
    import asyncio

    from api.fastapi_app import global_exception_handler

    class _URL:
        path = "/api/v1/secret-path"

    class _Req2:
        method = "GET"
        url = _URL()

    resp = asyncio.run(global_exception_handler(_Req2(), RuntimeError("db password=hunter2 at /etc/x")))
    assert resp.status_code == 500
    import json

    body = json.loads(resp.body.decode())
    assert body["errors"] == ["Internal server error."]
    assert "hunter2" not in resp.body.decode()
