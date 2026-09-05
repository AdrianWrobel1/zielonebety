"""
FastAPI Server Entry Point for Zielone Bety Platform REST API & Web Frontend
"""

from contextlib import asynccontextmanager
import logging
import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, HTTPException, Query, Body, Response, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

from api.auth import get_cors_origins, require_admin
from api.routes import APIRouter
from api.services import PlatformAPIService, _sanitize_text
from database.connection import DatabaseManager

logger = logging.getLogger("zielonebety.api")

db_manager_instance = DatabaseManager()
db_manager_instance.create_tables()

service_instance = PlatformAPIService(db_manager=db_manager_instance)
router_instance = APIRouter(service=service_instance)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Graceful startup and shutdown lifecycle context manager."""
    logger.info("Application starting up...")
    db_manager_instance.create_tables()
    yield
    # Graceful Shutdown
    logger.info("Application initiating graceful shutdown...")
    if hasattr(service_instance, "scheduler") and service_instance.scheduler is not None:
        service_instance.scheduler.stop()
    if hasattr(db_manager_instance, "dispose"):
        db_manager_instance.dispose()
    logger.info("Application graceful shutdown completed.")


app = FastAPI(
    title="Zielone Bety REST API",
    description="Single REST interface serving platform data to presentation layer clients.",
    version="1.0.0",
    lifespan=lifespan,
)

# Explicit origin allowlist (P1-007): same-origin deployments need no CORS
# entry; split local development and operator-configured origins are listed
# explicitly. A wildcard is never combined with credentials.
# P1-NEW-011: methods/headers are explicit (no "*" with credentials).
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_cors_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Map auth/permission failures to the standardized error envelope."""
    safe_detail = _sanitize_text(str(exc.detail))
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "status_code": exc.status_code,
            "data": None,
            "errors": [safe_detail],
            "metadata": {},
            "execution_time_ms": 0.0,
        },
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler returning a generic envelope without leaking internals."""
    safe_error = _sanitize_text(str(exc))
    logger.error(f"Unhandled server error on {request.method} {request.url.path}: {safe_error}", exc_info=True)
    # P1-NEW-011: the exception text is logged server-side only; the client
    # receives a generic message (no path/config/SQL disclosure).
    return JSONResponse(
        status_code=500,
        content={
            "status_code": 500,
            "data": None,
            "errors": ["Internal server error."],
            "metadata": {},
            "execution_time_ms": 0.0,
        },
    )


# ──────────────────────────────────────────────────────────────────────────────
# Health, Liveness & Readiness Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/health")
@app.get("/api/v1/health")
def get_health():
    """Aggregated health status."""
    return router_instance.handle_get_health().to_dict()


@app.get("/health/liveness")
@app.get("/api/v1/health/liveness")
def get_liveness():
    """Process liveness probe."""
    return router_instance.handle_get_liveness().to_dict()


@app.get("/health/readiness")
@app.get("/api/v1/health/readiness")
def get_readiness(response: Response):
    """Application readiness probe."""
    api_res = router_instance.handle_get_readiness()
    response.status_code = api_res.status_code
    return api_res.to_dict()


# ──────────────────────────────────────────────────────────────────────────────
# Production Scanner Control & Cycle Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/v1/scan/run")
@app.post("/api/scan/run")
def trigger_scan(response: Response, payload: Optional[Dict[str, Any]] = Body(default=None), admin: dict = Depends(require_admin)):
    """Trigger a production scan cycle across registered providers."""
    api_res = router_instance.handle_post_run_scan(payload=payload)
    response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/scan/latest")
@app.get("/api/scan/latest")
def get_latest_scan():
    """Fetch structured results of the most recent scan cycle."""
    return router_instance.handle_get_latest_scan().to_dict()


@app.post("/api/v1/scan/ultra")
@app.post("/api/scan/ultra")
def trigger_ultra_scan(response: Response, payload: Optional[Dict[str, Any]] = Body(default=None), admin: dict = Depends(require_admin)):
    """Trigger a comprehensive daily ULTRA SCAN cycle across all today's matches."""
    api_res = router_instance.handle_post_ultra_scan(payload=payload)
    response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/scan/ultra/latest")
@app.get("/api/scan/ultra/latest")
def get_latest_ultra_scan():
    """Fetch structured results of the most recent ULTRA scan cycle."""
    return router_instance.handle_get_latest_ultra_scan().to_dict()


@app.get("/api/v1/scan/trace/latest")
@app.get("/api/scan/trace/latest")
def get_latest_trace(response: Response, mode: str = "main"):
    """Fetch execution trace and worker telemetry of the most recent scan cycle for a given mode."""
    api_res = router_instance.handle_get_latest_trace(mode=mode)
    response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/scan/trace/{trace_id}")
@app.get("/api/scan/trace/{trace_id}")
def get_trace_by_id(trace_id: str, response: Response):
    """Fetch execution trace and worker telemetry by Trace ID or Execution ID."""
    api_res = router_instance.handle_get_trace_by_id(trace_id)
    response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/scan/trace/{trace_id}/export")
@app.get("/api/scan/trace/{trace_id}/export")
def export_trace_by_id(trace_id: str, response: Response, admin: dict = Depends(require_admin)):
    """Download the complete JSON trace payload for external diagnosis.

    P1-NEW-011: trace payloads embed full market/odds snapshots and were
    previously downloadable without authentication.
    """
    api_res = router_instance.handle_get_trace_by_id(trace_id)
    if api_res.status_code != 200 or not api_res.data:
        response.status_code = api_res.status_code
        return api_res.to_dict()
    import json
    return Response(
        content=json.dumps(api_res.data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename=trace_{trace_id}.json"},
    )


@app.get("/api/v1/scan/status")
@app.get("/api/scan/status")
def get_scan_status():
    """Fetch scanner readiness, active execution state, and last cycle status."""
    return router_instance.handle_get_scan_status().to_dict()


@app.get("/api/v1/scan/history")
@app.get("/api/scan/history")
def get_scan_history(limit: int = Query(10, ge=1, le=50)):
    """Fetch summary history of recent scan cycles."""
    return router_instance.handle_get_scan_history(limit=limit).to_dict()


@app.get("/api/v1/scan/scheduler")
@app.get("/api/scan/scheduler")
def get_scheduler_status():
    """Fetch automated scheduler configuration and state."""
    return router_instance.handle_get_scheduler_status().to_dict()


@app.post("/api/v1/scan/scheduler/configure")
@app.post("/api/scan/scheduler/configure")
def configure_scheduler(payload: Dict[str, Any] = Body(default={}), admin: dict = Depends(require_admin)):
    """Configure or toggle the automated scan scheduler."""
    return router_instance.handle_post_scheduler_configure(payload).to_dict()


@app.post("/api/v1/scan/scheduler/run-now")
@app.post("/api/scan/scheduler/run-now")
def scheduler_run_now(response: Response, admin: dict = Depends(require_admin)):
    """Trigger one immediate automated scan cycle (same orchestrator as manual)."""
    api_res = router_instance.handle_post_scheduler_run_now()
    response.status_code = api_res.status_code
    return api_res.to_dict()


# ──────────────────────────────────────────────────────────────────────────────
# Providers, Events, Opportunities & User Configuration
# ──────────────────────────────────────────────────────────────────────────────

@app.get("/api/v1/providers")
def get_providers():
    """Registered providers list and execution stats."""
    return router_instance.handle_get_providers().to_dict()


@app.post("/api/v1/providers/{provider_name}/run")
def trigger_provider(provider_name: str, admin: dict = Depends(require_admin)):
    """Trigger manual execution run for a single provider."""
    return router_instance.handle_post_trigger_provider(provider_name).to_dict()


@app.get("/api/v1/events")
@app.get("/api/events")
def list_events(
    sport: Optional[str] = Query(None),
    competition: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    matched: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Fetch canonical events list with multi-criteria filtering and pagination."""
    return router_instance.handle_get_events(
        sport=sport,
        competition=competition,
        provider=provider,
        search=search,
        matched=matched,
        limit=limit,
        offset=offset,
    ).to_dict()


@app.get("/api/v1/events/{event_id}")
@app.get("/api/events/{event_id}")
def get_event_detail(event_id: str, response: Response):
    """Fetch detailed canonical event info including markets, provider odds matrix, and attached opportunities."""
    api_res = router_instance.handle_get_event_detail(event_id)
    response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/opportunities")
@app.get("/api/opportunities")
def list_opportunities(
    type: Optional[str] = Query(None, alias="type"),
    opportunity_type: Optional[str] = Query(None),
    min_roi: float = Query(0.0),
    min_ev: float = Query(0.0),
    sport: Optional[str] = Query(None),
    provider: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    scope: Optional[str] = Query(None),
    competition_tier: Optional[int] = Query(None),
    market_type: Optional[str] = Query(None),
    min_quality_score: float = Query(0.0),
):
    """List scanned betting opportunities (Surebets and Valuebets) with filters and quality ranking."""
    raw_type = opportunity_type or type
    eff_type = raw_type if isinstance(raw_type, str) else None
    eff_status = status if isinstance(status, str) else None
    eff_sport = sport if isinstance(sport, str) else None
    eff_provider = provider if isinstance(provider, str) else None
    eff_scope = scope if isinstance(scope, str) else None
    eff_market_type = market_type if isinstance(market_type, str) else None
    eff_min_roi = float(min_roi) if isinstance(min_roi, (int, float)) else 0.0
    eff_min_ev = float(min_ev) if isinstance(min_ev, (int, float)) else 0.0
    eff_comp_tier = int(competition_tier) if isinstance(competition_tier, int) else None
    eff_min_quality = float(min_quality_score) if isinstance(min_quality_score, (int, float)) else 0.0

    return router_instance.handle_get_opportunities(
        opportunity_type=eff_type,
        min_roi=eff_min_roi,
        min_ev=eff_min_ev,
        sport=eff_sport,
        provider=eff_provider,
        status=eff_status,
        scope=eff_scope,
        competition_tier=eff_comp_tier,
        market_type=eff_market_type,
        min_quality_score=eff_min_quality,
    ).to_dict()


@app.get("/api/v1/opportunities/explorer")
@app.get("/api/opportunities/explorer")
def list_unified_explorer_opportunities(
    type: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    bookmaker: Optional[str] = Query(None),
    sport: Optional[str] = Query(None),
    competition: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    min_score: float = Query(0.0),
    min_execution_edge: Optional[float] = Query(None),
    min_ev: Optional[float] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort: str = Query("score"),
    order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Unified Opportunity Explorer — aggregated view of Player Props, Valuebets, Surebets, Boosters, and Team Props."""
    return router_instance.handle_get_explorer_opportunities(
        opp_type=type,
        status=status,
        bookmaker=bookmaker,
        sport=sport,
        competition=competition,
        search=search,
        min_score=min_score,
        min_execution_edge=min_execution_edge,
        min_ev=min_ev,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
        order=order,
        limit=limit,
        offset=offset,
    ).to_dict()


@app.get("/api/v1/opportunities/{opportunity_id}")
@app.get("/api/opportunities/{opportunity_id}")
def get_opportunity_detail(opportunity_id: str, response: Response):
    """Fetch complete details, market lines, cross-bookmaker odds, and math breakdown for an opportunity."""
    api_res = router_instance.handle_get_opportunity_detail(opportunity_id)
    response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/notifications")
def list_notifications(limit: int = Query(50, ge=1), offset: int = Query(0, ge=0)):
    """Fetch notification history and channel status."""
    return router_instance.handle_get_notifications(limit=limit, offset=offset).to_dict()


@app.get("/api/v1/telegram/health")
def get_telegram_health():
    """Fetch Telegram channel health, diagnostics, and digest window status."""
    return router_instance.handle_get_telegram_health().to_dict()


@app.post("/api/v1/telegram/test")
def send_telegram_test_message(admin: dict = Depends(require_admin)):
    """Dispatch an administrative diagnostic test message to Telegram."""
    return router_instance.handle_post_telegram_test().to_dict()


@app.post("/api/v1/telegram/configure")
def configure_telegram(payload: Dict[str, Any] = Body(...), admin: dict = Depends(require_admin)):
    """Update safe Telegram administrative toggles."""
    return router_instance.handle_post_telegram_configure(body=payload).to_dict()



@app.get("/api/v1/history/odds")
def get_odds_history(event_id: str = Query("ev-real-barca-01"), period: str = Query("24h")):
    """Fetch time-series odds history for trend visualization."""
    return router_instance.handle_get_odds_history(event_id=event_id, period=period).to_dict()


@app.get("/api/v1/settings")
def get_settings():
    """Fetch client user preferences."""
    return router_instance.handle_get_settings().to_dict()


@app.post("/api/v1/settings")
def update_settings(payload: Dict[str, Any] = Body(...), admin: dict = Depends(require_admin)):
    """Update client user preferences."""
    return router_instance.handle_post_settings(payload).to_dict()


@app.post("/api/v1/auth/login")
def auth_login(request: Request, response: Response, payload: Dict[str, Any] = Body(default={})):
    """Login endpoint with brute-force throttling (P1-NEW-011)."""
    from api.auth import is_login_rate_limited, register_login_attempt

    if is_login_rate_limited(request):
        response.status_code = 429
        return {
            "status_code": 429,
            "data": None,
            "errors": ["Too many login attempts. Try again shortly."],
            "metadata": {},
            "execution_time_ms": 0.0,
        }
    username = payload.get("username", "admin")
    password = payload.get("password", "")
    api_res = router_instance.handle_post_auth_login(username=username, password=password)
    register_login_attempt(request, success=(api_res.status_code == 200))
    response.status_code = api_res.status_code
    return api_res.to_dict()


@app.post("/api/v1/auth/logout")
def auth_logout(request: Request):
    """Revoke the caller's bearer session (P1-NEW-011)."""
    from api.auth import _bearer_token_from_request, revoke_token

    revoked = revoke_token(_bearer_token_from_request(request))
    return {
        "status_code": 200,
        "data": {"revoked": revoked},
        "errors": [],
        "metadata": {},
        "execution_time_ms": 0.0,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Player Props Endpoints (StatsHub Data Provider)
# ──────────────────────────────────────────────────────────────────────────────

@app.post("/api/v1/props/scan")
@app.get("/api/v1/props/scan")
def trigger_props_scan(
    admin: dict = Depends(require_admin),
    stat: Optional[str] = Query("shots"),
    positions: Optional[str] = Query("D,M,F"),
    last_games: int = Query(10, ge=1, le=50),
    hit_rate_threshold: int = Query(0, ge=0, le=100),
    stat_threshold: int = Query(1, ge=0),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=100),
    min_odds: float = Query(1.0),
    line: Optional[float] = Query(None),
    search: Optional[str] = Query(None),
    venue_filter: Optional[str] = Query("both"),
    tournaments: Optional[str] = Query(None),
    fixture_ids: Optional[str] = Query(None),
    start_of_day: Optional[int] = Query(None),
    end_of_day: Optional[int] = Query(None),
    days_ahead: int = Query(7, ge=1, le=30),
    auto_paginate: bool = Query(True),
    max_prop_results: int = Query(500),
    response: Response = None,
):
    """Trigger a player props scan with filter parameters."""
    params = {
        "stat": stat,
        "positions": positions,
        "last_games": last_games,
        "hit_rate_threshold": hit_rate_threshold,
        "stat_threshold": stat_threshold,
        "page": page,
        "limit": limit,
        "min_odds": min_odds,
        "line": line,
        "search": search,
        "venue_filter": venue_filter,
        "tournaments": tournaments,
        "fixture_ids": fixture_ids,
        "start_of_day": start_of_day,
        "end_of_day": end_of_day,
        "days_ahead": days_ahead,
        "auto_paginate": auto_paginate,
        "max_prop_results": max_prop_results,
    }
    api_res = router_instance.handle_post_scan_props(params)
    if response:
        response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/props/results")
def get_props_results(
    category: Optional[str] = Query(None),
    stat: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    min_odds: float = Query(1.0),
    min_hit_rate: float = Query(0.0),
    min_score: float = Query(0.0),
    min_execution_edge: Optional[float] = Query(None),
    min_statistical_edge: Optional[float] = Query(None),
    min_ev: Optional[float] = Query(None),
    execution_status: Optional[str] = Query(None),
    bookmaker: Optional[str] = Query(None),
    position: Optional[str] = Query(None),
    line: Optional[float] = Query(None),
    sort_by: str = Query("score"),
    limit: int = Query(500, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Fetch cached, searched, and ranked player prop opportunities."""
    return router_instance.handle_get_props_results(
        category=category,
        stat=stat,
        search=search,
        min_odds=min_odds,
        min_hit_rate=min_hit_rate,
        min_score=min_score,
        min_execution_edge=min_execution_edge,
        min_statistical_edge=min_statistical_edge,
        min_ev=min_ev,
        execution_status=execution_status,
        bookmaker=bookmaker,
        position=position,
        line=line,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
    ).to_dict()


@app.get("/api/v1/props/health/status")
@app.get("/api/v1/props/health")
def get_props_health():
    """Fetch StatsHub provider health status."""
    return router_instance.handle_get_props_health().to_dict()


@app.post("/api/v1/props/global-scan")
def post_global_props_scan(
    admin: dict = Depends(require_admin),
    time_horizon_days: int = Query(7, ge=1, le=14),
    tournaments: Optional[str] = Query(None),
    props_scope: str = Query("ALL"),
    stat_types: Optional[str] = Query(None),
    min_ev_percent: float = Query(3.0),
    max_results: int = Query(50, ge=1, le=200),
    max_fixtures: int = Query(30, ge=1, le=100),
    max_trends_requests: int = Query(10, ge=1, le=50),
    max_execution_events: int = Query(20, ge=1, le=50),
    response: Response = None,
):
    """Trigger bounded multi-fixture global scan for Player Props and Team Props."""
    params = {
        "time_horizon_days": time_horizon_days,
        "tournaments": tournaments,
        "props_scope": props_scope,
        "stat_types": stat_types,
        "min_ev_percent": min_ev_percent,
        "max_results": max_results,
        "max_fixtures": max_fixtures,
        "max_trends_requests": max_trends_requests,
        "max_execution_events": max_execution_events,
    }
    api_res = router_instance.handle_post_global_props_scan(params)
    if response:
        response.status_code = api_res.status_code
    return api_res.to_dict()


@app.get("/api/v1/props/global-results")
def get_global_props_results(
    props_scope: Optional[str] = Query(None),
    stat: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    min_net_ev: Optional[float] = Query(None),
    status: Optional[str] = Query(None),
    bookmaker: Optional[str] = Query(None),
    view_mode: Optional[str] = Query(None),
    sort_by: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    min_odds: Optional[float] = Query(None),
    competition: Optional[str] = Query(None),
    position: Optional[str] = Query(None),
    threshold: Optional[float] = Query(None),
):
    """Fetch cached global props scan opportunities with filtering and pagination."""
    return router_instance.handle_get_global_props_results(
        props_scope=props_scope,
        stat=stat,
        search=search,
        min_net_ev=min_net_ev,
        status=status,
        bookmaker=bookmaker,
        view_mode=view_mode,
        sort_by=sort_by,
        limit=limit,
        offset=offset,
        min_odds=min_odds,
        competition=competition,
        position=position,
        threshold=threshold,
    ).to_dict()


@app.get("/api/v1/props/taxonomy")
def get_props_taxonomy():
    """Fetch authoritative Props taxonomy metadata including optgroups for UI/API."""
    return router_instance.handle_get_props_taxonomy().to_dict()


@app.get("/api/v1/props/coverage")
def get_props_coverage():
    """Fetch 10-stage Props coverage matrix across all target categories."""
    return router_instance.handle_get_props_coverage().to_dict()


@app.get("/api/v1/props/{prop_id}")
def get_prop_detail(prop_id: str, response: Response):
    """Fetch comprehensive detail, bookmaker odds breakdown, and match history for a prop."""
    api_res = router_instance.handle_get_prop_detail(prop_id)
    response.status_code = api_res.status_code
    return api_res.to_dict()


# ──────────────────────────────────────────────────────────────────────────
# Team Props Endpoints
# ──────────────────────────────────────────────────────────────────────────

@app.post("/api/v1/team-props/scan")
def trigger_team_props_scan(params: Dict[str, Any] = Body(default_factory=dict), admin: dict = Depends(require_admin)):
    """Trigger an on-demand scan of team props data from StatsHub."""
    return router_instance.handle_scan_team_props(config_params=params).to_dict()


@app.get("/api/v1/team-props")
def get_team_props_results(
    stat: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    search: str = Query(""),
    min_odds: float = Query(1.0, ge=1.0),
    min_hit_rate: float = Query(0.0, ge=0.0, le=100.0),
    min_score: float = Query(0.0, ge=0.0, le=100.0),
    min_edge: Optional[float] = Query(None),
    min_ev: Optional[float] = Query(None),
    execution_status: Optional[str] = Query(None),
    line: Optional[float] = Query(None),
    side: Optional[str] = Query(None),
    sort_by: str = Query("score"),
    sort_direction: str = Query("desc"),
):
    """Fetch cached, searched, and ranked team prop opportunities."""
    return router_instance.handle_get_team_props_results(
        stat=stat,
        page=page,
        limit=limit,
        search=search,
        min_odds=min_odds,
        min_hit_rate=min_hit_rate,
        min_score=min_score,
        min_edge=min_edge,
        min_ev=min_ev,
        execution_status=execution_status,
        line=line,
        side=side,
        sort_by=sort_by,
        sort_direction=sort_direction,
    ).to_dict()


@app.get("/api/v1/team-props/health")
def get_team_props_health():
    """Fetch Team Props provider health status."""
    return router_instance.handle_get_team_props_health().to_dict()


@app.get("/api/v1/team-props/{prop_id}")
def get_team_prop_detail(prop_id: str, response: Response):
    """Fetch comprehensive detail, bookmaker odds breakdown, and match history for a team prop."""
    api_res = router_instance.handle_get_team_prop_detail(prop_id)
    response.status_code = api_res.status_code
    return api_res.to_dict()


# Serve static web frontend if directory exists

web_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")
if os.path.exists(web_dir):
    index_file = os.path.join(web_dir, "index.html")

    # Serve SPA routes with index.html fallback if direct path navigation is used
    @app.get("/events")
    @app.get("/playerprops")
    @app.get("/teamprops")
    @app.get("/opportunities")
    @app.get("/providers")
    @app.get("/history")
    @app.get("/notifications")
    @app.get("/settings")
    def get_spa_page():
        from fastapi.responses import FileResponse
        return FileResponse(index_file)

    app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    print("🚀 Uruchamianie serwera Zielone Bety pod adresem http://localhost:8000 ...")
    uvicorn.run("api.fastapi_app:app", host="0.0.0.0", port=8000, reload=True)
