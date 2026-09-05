"""P1-007 regression: real authentication, protected control plane, explicit CORS.

Intended behavior encoded here (not implementation details):

  - wrong / arbitrary passwords are rejected (401), never issued an Admin token
  - valid credentials are accepted (200 + bearer token, no password echoed)
  - control-plane POST routes require authentication (missing/invalid -> 401)
  - authorization is enforced where roles apply (non-admin -> 403)
  - CORS never combines wildcard origins with credentials
  - passwords / tokens are never logged
"""

import logging

import pytest

from api.exceptions import UnauthorizedError
from api.routes import APIRouter
from api.services import PlatformAPIService
from database.config import DatabaseConfig
from database.connection import DatabaseManager

TEST_ADMIN_PASSWORD = "p1007-test-admin-password"


@pytest.fixture()
def service(monkeypatch):
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    db_mgr = DatabaseManager(DatabaseConfig.default_sqlite_in_memory())
    db_mgr.create_tables()
    svc = PlatformAPIService(db_manager=db_mgr)
    try:
        yield svc
    finally:
        try:
            svc.scheduler.stop()
        except Exception:
            pass
        try:
            db_mgr.dispose()
        except Exception:
            pass


@pytest.fixture()
def router(service):
    return APIRouter(service=service)


def test_wrong_password_rejected_at_service(service):
    with pytest.raises(UnauthorizedError) as exc_info:
        service.authenticate_user(username="admin", password="WRONG PASSWORD")
    assert exc_info.value.status_code == 401


def test_arbitrary_password_rejected_at_service(service):
    with pytest.raises(UnauthorizedError):
        service.authenticate_user(username="admin", password="anything-at-all")
    with pytest.raises(UnauthorizedError):
        service.authenticate_user(username="admin", password="")


def test_valid_credentials_accepted_at_service(service):
    envelope = service.authenticate_user(username="admin", password=TEST_ADMIN_PASSWORD)
    assert envelope["user"]["username"] == "admin"
    assert envelope["user"]["role"] == "Admin"
    assert envelope["token_type"] == "bearer"
    assert isinstance(envelope["access_token"], str) and len(envelope["access_token"]) >= 16
    assert "password" not in str(envelope).lower()


def test_login_route_returns_401_envelope_for_bad_credentials(router):
    res = router.handle_post_auth_login(username="admin", password="not-the-password")
    assert res.status_code == 401
    assert not (res.data or {}).get("access_token")


def test_login_route_returns_200_for_valid_credentials(router):
    res = router.handle_post_auth_login(username="admin", password=TEST_ADMIN_PASSWORD)
    assert res.status_code == 200
    assert "access_token" in res.data


def _protected_post_routes():
    from api.fastapi_app import app

    control_prefixes = (
        "/api/v1/scan",
        "/api/scan",
        "/api/v1/providers/",
        "/api/v1/telegram/",
        "/api/v1/settings",
        "/api/v1/props/scan",
        "/api/v1/props/global-scan",
        "/api/v1/team-props/scan",
    )
    found = []
    for route in app.routes:
        methods = getattr(route, "methods", None) or set()
        if "POST" not in methods:
            continue
        path = getattr(route, "path", "")
        if path in ("/api/v1/auth/login",):
            continue
        if path.startswith(control_prefixes):
            found.append(route)
    assert found, "expected control-plane POST routes to exist"
    return found


def test_control_post_routes_require_authentication():
    from api.auth import require_admin

    missing = []
    for route in _protected_post_routes():
        calls = [d.call for d in getattr(getattr(route, "dependant", None), "dependencies", [])]
        if require_admin not in calls:
            missing.append(getattr(route, "path", "?"))
    assert not missing, f"control POST routes without auth dependency: {missing}"


def test_auth_login_route_stays_public():
    from api.fastapi_app import app
    from api.auth import require_admin

    login_routes = [r for r in app.routes if getattr(r, "path", "") == "/api/v1/auth/login"]
    assert login_routes, "login route must exist"
    for route in login_routes:
        calls = [d.call for d in getattr(getattr(route, "dependant", None), "dependencies", [])]
        assert require_admin not in calls


def test_valid_token_accepted_by_dependency(service):
    from api.auth import get_current_user
    from types import SimpleNamespace

    envelope = service.authenticate_user(username="admin", password=TEST_ADMIN_PASSWORD)
    token = envelope["access_token"]
    req = SimpleNamespace(headers={"authorization": f"Bearer {token}"})
    user = get_current_user(req)
    assert user["username"] == "admin"
    assert user["role"] == "Admin"


def test_missing_token_rejected_by_dependency():
    from fastapi import HTTPException

    from api.auth import get_current_user
    from types import SimpleNamespace

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(SimpleNamespace(headers={}))
    assert exc_info.value.status_code == 401


def test_invalid_token_rejected_by_dependency():
    from fastapi import HTTPException

    from api.auth import get_current_user
    from types import SimpleNamespace

    with pytest.raises(HTTPException) as exc_info:
        get_current_user(SimpleNamespace(headers={"authorization": "Bearer bogus"}))
    assert exc_info.value.status_code == 401


def test_non_admin_token_forbidden_by_dependency(monkeypatch):
    from fastapi import HTTPException

    from api import auth as api_auth
    from types import SimpleNamespace

    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", TEST_ADMIN_PASSWORD)
    token = api_auth.create_session("viewer", role="viewer")
    with pytest.raises(HTTPException) as exc_info:
        api_auth.require_admin(SimpleNamespace(headers={"authorization": f"Bearer {token}"}))
    assert exc_info.value.status_code == 403


def test_cors_never_wildcard_with_credentials():
    from api.fastapi_app import app

    cors = [m for m in app.user_middleware if m.cls.__name__ == "CORSMiddleware"]
    assert cors, "CORS middleware must be registered"
    for entry in cors:
        origins = entry.kwargs.get("allow_origins", [])
        credentials = entry.kwargs.get("allow_credentials", False)
        assert not ("*" in origins and credentials), "wildcard CORS with credentials is forbidden"
        assert "*" not in origins, "CORS origins must be explicit"


def test_secrets_never_logged(service, caplog):
    secret_pw = "super-secret-pw-xyz-123"
    with caplog.at_level(logging.INFO, logger="zielonebety.api"):
        try:
            service.authenticate_user(username="admin", password=secret_pw)
        except UnauthorizedError:
            pass
        envelope = service.authenticate_user(username="admin", password=TEST_ADMIN_PASSWORD)
    assert secret_pw not in caplog.text
    assert envelope["access_token"] not in caplog.text
