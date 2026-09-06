"""
Control-Plane Authentication (P1-007).

Smallest architecture-compatible real authentication for the genuinely
single-user / local-only deployment model of this repository:

  - Same-origin serving (nginx reverse-proxies /api/ to the backend and
    serves the static frontend; local dev runs API + static on one origin).
  - No user store, no OAuth/Redis/external IdP in the architecture.
  - One administrator role; credentials come from the environment.

Behavior contract:

  - invalid credentials  -> rejected (401 via UnauthorizedError / HTTPException)
  - missing credentials  -> rejected (401)
  - valid token, wrong role -> rejected (403)
  - valid admin token    -> allowed

Secrets discipline:

  - The production password is NEVER hardcoded and NEVER logged.
  - Comparison uses hmac.compare_digest (constant-time).
  - Sessions are opaque random tokens (secrets.token_urlsafe) held only in
    process memory with a configurable TTL. Nothing identifying is logged.

Environment:

  - ADMIN_USERNAME        (default "admin")
  - ADMIN_PASSWORD        (no default in production; dev-only fallback below)
  - TOKEN_EXPIRE_MINUTES  (default 1440)
  - CORS_ALLOWED_ORIGINS / FRONTEND_ORIGINS (comma-separated explicit list)
"""

import hmac
import logging
import os
import secrets
import threading
import time
from typing import Dict, Optional

from fastapi import HTTPException, Request

logger = logging.getLogger("zielonebety.api.auth")

# Dev-only fallback used when ADMIN_PASSWORD is not configured AND the
# process is not running with ENVIRONMENT=production. Production without an
# explicit ADMIN_PASSWORD is fail-closed (all logins rejected) so a missing
# secret can never silently become an open or default-credential deployment.
DEV_DEFAULT_PASSWORD = "changeme-local-dev-only"

_DEFAULT_TOKEN_TTL_MINUTES = 1440

_sessions_lock = threading.Lock()
_sessions: Dict[str, Dict[str, object]] = {}

# P1-NEW-011: login brute-force throttle. Per-client sliding window; failures
# accumulate, success clears. Conservative defaults, env-overridable.
_LOGIN_ATTEMPTS_LOCK = threading.Lock()
_login_attempts: Dict[str, list] = {}


def _login_throttle_limits() -> tuple:
    try:
        max_attempts = int(os.environ.get("LOGIN_RATE_LIMIT_MAX", "10"))
    except (TypeError, ValueError):
        max_attempts = 10
    try:
        window_seconds = float(os.environ.get("LOGIN_RATE_LIMIT_WINDOW_SECONDS", "60"))
    except (TypeError, ValueError):
        window_seconds = 60.0
    return max(1, max_attempts), max(1.0, window_seconds)


def _client_id_from_request(request: object) -> str:
    try:
        client = getattr(request, "client", None)
        host = getattr(client, "host", None)
        if host:
            return str(host)
    except Exception:
        pass
    return "unknown"


def is_login_rate_limited(request: object) -> bool:
    """True when the client exhausted the login attempt window (P1-NEW-011)."""
    max_attempts, window = _login_throttle_limits()
    now = time.time()
    with _LOGIN_ATTEMPTS_LOCK:
        attempts = [t for t in _login_attempts.get(_client_id_from_request(request), []) if now - t < window]
        _login_attempts[_client_id_from_request(request)] = attempts
        return len(attempts) >= max_attempts


def register_login_attempt(request: object, success: bool) -> None:
    """Records a login outcome; success resets the client's window."""
    key = _client_id_from_request(request)
    with _LOGIN_ATTEMPTS_LOCK:
        if success:
            _login_attempts.pop(key, None)
        else:
            _login_attempts.setdefault(key, []).append(time.time())


def clear_login_attempts() -> None:
    """Drop all login throttle state (tests / administrative reset)."""
    with _LOGIN_ATTEMPTS_LOCK:
        _login_attempts.clear()


def get_admin_username() -> str:
    """Configured administrator username (never a secret)."""
    return os.environ.get("ADMIN_USERNAME") or "admin"


def _expected_password() -> Optional[str]:
    configured = os.environ.get("ADMIN_PASSWORD")
    if configured:
        return configured
    if os.environ.get("ENVIRONMENT", "").lower() == "production":
        return None
    return DEV_DEFAULT_PASSWORD


def is_auth_configured() -> bool:
    """True when an explicit ADMIN_PASSWORD is configured."""
    return bool(os.environ.get("ADMIN_PASSWORD"))


def verify_credentials(username: object, password: object) -> bool:
    """Constant-time verification of username + password."""
    expected_pw = _expected_password()
    if expected_pw is None:
        return False
    if not isinstance(username, str) or not isinstance(password, str):
        return False
    if not username or not password:
        return False
    expected_user = get_admin_username()
    return hmac.compare_digest(username, expected_user) and hmac.compare_digest(password, expected_pw)


def _token_ttl_seconds() -> float:
    try:
        minutes = int(os.environ.get("TOKEN_EXPIRE_MINUTES", str(_DEFAULT_TOKEN_TTL_MINUTES)))
    except (TypeError, ValueError):
        minutes = _DEFAULT_TOKEN_TTL_MINUTES
    return float(max(1, minutes)) * 60.0


def create_session(username: str, role: str = "Admin") -> str:
    """Mint an opaque bearer token for an already-authenticated user."""
    token = secrets.token_urlsafe(32)
    with _sessions_lock:
        _sessions[token] = {
            "username": username,
            "role": role,
            "expires_at": time.time() + _token_ttl_seconds(),
        }
    return token


def verify_token(token: object) -> Optional[Dict[str, str]]:
    """Return the session identity for a valid non-expired token, else None."""
    if not isinstance(token, str) or not token:
        return None
    with _sessions_lock:
        sess = _sessions.get(token)
        if sess is None:
            return None
        if float(sess.get("expires_at", 0.0)) < time.time():
            _sessions.pop(token, None)
            return None
        return {"username": str(sess.get("username")), "role": str(sess.get("role"))}


def clear_sessions() -> None:
    """Drop all sessions (tests / administrative reset)."""
    with _sessions_lock:
        _sessions.clear()


def revoke_token(token: object) -> bool:
    """Revoke a single bearer session (logout). Returns True if one existed."""
    if not isinstance(token, str) or not token:
        return False
    with _sessions_lock:
        return _sessions.pop(token, None) is not None


def _bearer_token_from_request(request: object) -> Optional[str]:
    try:
        headers = getattr(request, "headers", None) or {}
        auth = headers.get("authorization") if hasattr(headers, "get") else None
        if auth is None and hasattr(headers, "get"):
            auth = headers.get("Authorization")
    except Exception:
        return None
    if not isinstance(auth, str) or not auth:
        return None
    scheme, _, token = auth.partition(" ")
    if scheme.lower() != "bearer":
        return None
    token = token.strip()
    return token or None


def is_auth_enabled() -> bool:
    """True when control-plane authentication is enforced.
    
    Can be explicitly disabled via AUTH_DISABLED=true or REQUIRE_AUTH=false
    for private/personal single-operator deployments without a login UI
    (per Product Requirement #1). Defaults to True to maintain security regression safety.
    """
    if os.environ.get("AUTH_DISABLED", "").lower() in ("true", "1", "yes"):
        return False
    if os.environ.get("REQUIRE_AUTH", "").lower() in ("false", "0", "no"):
        return False
    return True


def get_current_user(request: Request) -> Dict[str, str]:
    """FastAPI dependency: valid bearer session or 401 (never logs the token)."""
    if not is_auth_enabled():
        return {"username": get_admin_username(), "role": "Admin"}
    token = _bearer_token_from_request(request)
    identity = verify_token(token) if token else None
    if identity is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return identity


def require_admin(request: Request) -> Dict[str, str]:
    """FastAPI dependency: admin session or 401/403."""
    user = get_current_user(request)
    if user.get("role") != "Admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions.")
    return user


def get_cors_origins() -> list:
    """Explicit origin allowlist; a wildcard can never enable credentialed CORS.

    Same-origin deployments (nginx in front, or uvicorn serving the bundled
    static frontend) need no CORS entry at all. This list only covers split
    local development plus operator-configured production origins.
    """
    raw = os.environ.get("CORS_ALLOWED_ORIGINS") or os.environ.get("FRONTEND_ORIGINS") or ""
    origins: list = []
    for part in raw.split(","):
        origin = part.strip()
        if not origin or origin == "*":
            continue
        origin = origin.rstrip("/")
        if origin and origin not in origins:
            origins.append(origin)
    for default in (
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ):
        if default not in origins:
            origins.append(default)
    return origins
