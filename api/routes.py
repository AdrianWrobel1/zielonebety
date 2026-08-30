"""
API Endpoint Router & Controller Definitions
"""

import time
from typing import Dict, Any, Optional, Union
from api.models import APIResponse
from api.services import PlatformAPIService
from api.exceptions import APIError, ResourceNotFoundError


class APIRouter:
    """Lightweight API router definitions formatting standardized envelopes."""

    def __init__(self, service: Optional[PlatformAPIService] = None):
        self.service = service or PlatformAPIService()

    def handle_get_health(self) -> APIResponse:
        """GET /health or /api/v1/health"""
        start = time.perf_counter()
        health_data = self.service.get_health()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return APIResponse(
            status_code=200,
            data=health_data,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_get_liveness(self) -> APIResponse:
        """GET /health/liveness or /api/v1/health/liveness"""
        return APIResponse(
            status_code=200,
            data={"status": "alive"},
            execution_time_ms=0.1
        )

    def handle_get_readiness(self) -> APIResponse:
        """GET /health/readiness or /api/v1/health/readiness"""
        start = time.perf_counter()
        db_ok = self.service.db_manager.check_health()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if db_ok:
            return APIResponse(
                status_code=200,
                data={"status": "ready", "database": "ok", "scanner": self.service._scanner_status},
                execution_time_ms=round(elapsed_ms, 2)
            )
        else:
            return APIResponse(
                status_code=503,
                data={"status": "not_ready", "database": "error", "scanner": self.service._scanner_status},
                errors=["Database connection unavailable."],
                execution_time_ms=round(elapsed_ms, 2)
            )

    def handle_get_providers(self) -> APIResponse:
        """GET /api/v1/providers"""
        start = time.perf_counter()
        providers_data = self.service.get_providers()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return APIResponse(
            status_code=200,
            data=providers_data,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_get_events(
        self,
        sport: Optional[str] = None,
        competition: Optional[str] = None,
        provider: Optional[str] = None,
        search: Optional[str] = None,
        matched: Optional[Union[bool, str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> APIResponse:
        """GET /api/v1/events"""
        start = time.perf_counter()
        events = self.service.list_events(
            sport=sport,
            competition=competition,
            provider=provider,
            search=search,
            matched=matched,
            limit=limit,
            offset=offset,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return APIResponse(
            status_code=200,
            data=events,
            metadata={"limit": limit, "offset": offset, "count": len(events)},
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_post_trigger_provider(self, provider_name: str) -> APIResponse:
        """POST /api/v1/providers/{name}/run"""
        start = time.perf_counter()
        try:
            run_result = self.service.trigger_provider_run(provider_name)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=200,
                data=run_result,
                execution_time_ms=round(elapsed_ms, 2)
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=400,
                errors=[str(e)],
                execution_time_ms=round(elapsed_ms, 2)
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Production Scanner Control Routes
    # ──────────────────────────────────────────────────────────────────────────

    def handle_post_run_scan(self, payload: Optional[Dict[str, Any]] = None) -> APIResponse:
        """POST /api/v1/scan/run"""
        start = time.perf_counter()
        try:
            from orchestration.models import ScanConfig
            config_override = None
            if payload and isinstance(payload, dict):
                scan_mode = str(payload.get("scan_mode", "NORMAL")).upper()
                max_details = payload.get("max_detail_requests")
                config_override = ScanConfig(
                    scan_mode=scan_mode,
                    max_detail_requests=int(max_details) if max_details is not None else None,
                )

            scan_data = self.service.run_scan(config=config_override)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=200,
                data=scan_data,
                metadata={"execution_id": scan_data.get("execution_id")},
                execution_time_ms=round(elapsed_ms, 2),
            )
        except APIError as api_err:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=api_err.status_code,
                errors=[str(api_err)],
                execution_time_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=500,
                errors=[f"Scan execution error: {str(exc)}"],
                execution_time_ms=round(elapsed_ms, 2),
            )

    def handle_get_latest_scan(self) -> APIResponse:
        """GET /api/v1/scan/latest"""
        start = time.perf_counter()
        latest = self.service.get_latest_scan()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        if latest is None:
            return APIResponse(
                status_code=200,
                data=None,
                metadata={"status": "NOT_RUN", "message": "No scan has been executed yet."},
                execution_time_ms=round(elapsed_ms, 2),
            )

        return APIResponse(
            status_code=200,
            data=latest,
            metadata={"status": latest.get("cycle_status")},
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_latest_trace(self) -> APIResponse:
        """GET /api/v1/scan/trace/latest"""
        start = time.perf_counter()
        trace = self.service.get_latest_trace()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        if trace is None:
            return APIResponse(
                status_code=404,
                data=None,
                errors=["No profiler trace available yet."],
                execution_time_ms=round(elapsed_ms, 2),
            )

        return APIResponse(
            status_code=200,
            data=trace,
            metadata={"trace_id": trace.get("trace_id")},
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_trace_by_id(self, trace_id: str) -> APIResponse:
        """GET /api/v1/scan/trace/{trace_id}"""
        start = time.perf_counter()
        trace = self.service.get_trace_by_id(trace_id)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        if trace is None:
            return APIResponse(
                status_code=404,
                data=None,
                errors=[f"Trace '{trace_id}' not found."],
                execution_time_ms=round(elapsed_ms, 2),
            )

        return APIResponse(
            status_code=200,
            data=trace,
            metadata={"trace_id": trace.get("trace_id")},
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_scan_status(self) -> APIResponse:
        """GET /api/v1/scan/status"""
        start = time.perf_counter()
        status_data = self.service.get_scan_status()
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return APIResponse(
            status_code=200,
            data=status_data,
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_scan_history(self, limit: int = 10) -> APIResponse:
        """GET /api/v1/scan/history"""
        start = time.perf_counter()
        history = self.service.get_scan_history(limit=limit)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        return APIResponse(
            status_code=200,
            data=history,
            metadata={"count": len(history), "limit": limit},
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_scheduler_status(self) -> APIResponse:
        """GET /api/v1/scan/scheduler"""
        start = time.perf_counter()
        status = self.service.get_scheduler_status()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=status,
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_post_scheduler_configure(self, payload: Dict[str, Any]) -> APIResponse:
        """POST /api/v1/scan/scheduler/configure"""
        start = time.perf_counter()
        try:
            new_status = self.service.configure_scheduler(
                enabled=payload.get("enabled"),
                interval_minutes=payload.get("interval_minutes"),
                scan_scope=payload.get("scan_scope"),
                hours_ahead=payload.get("hours_ahead"),
                event_limit=payload.get("event_limit"),
            )
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=200,
                data=new_status,
                execution_time_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=400,
                errors=[str(exc)],
                execution_time_ms=round(elapsed_ms, 2),
            )

    def handle_post_scheduler_run_now(self) -> APIResponse:
        """POST /api/v1/scan/scheduler/run-now — trigger one automated scan immediately."""
        start = time.perf_counter()
        try:
            result = self.service.scheduler.run_scan_now()
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=200,
                data=result,
                metadata={"execution_id": result.get("execution_id"), "scan_source": "AUTOMATED"},
                execution_time_ms=round(elapsed_ms, 2),
            )
        except APIError as api_err:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=api_err.status_code,
                errors=[str(api_err)],
                execution_time_ms=round(elapsed_ms, 2),
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=500,
                errors=[f"Scheduler run-now error: {str(exc)}"],
                execution_time_ms=round(elapsed_ms, 2),
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Events, Opportunities & User Configuration
    # ──────────────────────────────────────────────────────────────────────────

    def handle_get_event_detail(self, event_id: str) -> APIResponse:
        """GET /api/v1/events/{id}"""
        start = time.perf_counter()
        detail = self.service.get_event_detail(event_id)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if detail is None:
            return APIResponse(
                status_code=404,
                errors=[f"Event with ID '{event_id}' not found."],
                execution_time_ms=round(elapsed_ms, 2)
            )
        return APIResponse(
            status_code=200,
            data=detail,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_get_explorer_opportunities(
        self,
        opp_type: Optional[str] = None,
        status: Optional[str] = None,
        bookmaker: Optional[str] = None,
        sport: Optional[str] = None,
        competition: Optional[str] = None,
        search: Optional[str] = None,
        min_score: float = 0.0,
        min_execution_edge: Optional[float] = None,
        min_ev: Optional[float] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        sort: str = "score",
        order: str = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> APIResponse:
        """GET /api/v1/opportunities/explorer — Unified Opportunity Explorer."""
        start = time.perf_counter()
        explorer_data = self.service.get_unified_explorer_opportunities(
            opp_type=opp_type,
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
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=explorer_data,
            metadata={
                "total": explorer_data.get("total", 0),
                "limit": limit,
                "offset": offset,
                "counts_by_type": explorer_data.get("counts_by_type", {}),
            },
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_opportunities(
        self,
        opportunity_type: Optional[str] = None,
        min_roi: float = 0.0,
        min_ev: float = 0.0,
        sport: Optional[str] = None,
        provider: Optional[str] = None,
        status: Optional[str] = None,
        scope: Optional[str] = None,
        competition_tier: Optional[int] = None,
        market_type: Optional[str] = None,
        min_quality_score: float = 0.0,
    ) -> APIResponse:
        """GET /api/v1/opportunities"""
        start = time.perf_counter()
        opps = self.service.list_opportunities(
            opportunity_type=opportunity_type,
            min_roi=min_roi,
            min_ev=min_ev,
            sport=sport,
            provider=provider,
            status=status,
            scope=scope,
            competition_tier=competition_tier,
            market_type=market_type,
            min_quality_score=min_quality_score,
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=opps,
            metadata={"count": len(opps)},
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_get_opportunity_detail(self, opportunity_id: str) -> APIResponse:
        """GET /api/v1/opportunities/{id}"""
        start = time.perf_counter()
        detail = self.service.get_opportunity_detail(opportunity_id)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if detail is None:
            return APIResponse(
                status_code=404,
                errors=[f"Opportunity with ID '{opportunity_id}' not found."],
                execution_time_ms=round(elapsed_ms, 2)
            )
        return APIResponse(
            status_code=200,
            data=detail,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_get_notifications(self, limit: int = 50, offset: int = 0) -> APIResponse:
        """GET /api/v1/notifications"""
        start = time.perf_counter()
        notifs = self.service.list_notifications(limit=limit, offset=offset)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=notifs,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_get_odds_history(self, event_id: str = "ev-real-barca-01", period: str = "24h") -> APIResponse:
        """GET /api/v1/history/odds"""
        start = time.perf_counter()
        history = self.service.get_odds_history(event_id=event_id, period=period)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=history,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_get_settings(self) -> APIResponse:
        """GET /api/v1/settings"""
        start = time.perf_counter()
        settings = self.service.get_user_settings()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=settings,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_post_settings(self, new_settings: Dict[str, Any]) -> APIResponse:
        """POST /api/v1/settings"""
        start = time.perf_counter()
        updated = self.service.update_user_settings(new_settings)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=updated,
            execution_time_ms=round(elapsed_ms, 2)
        )

    def handle_post_auth_login(self, username: str = "admin", password: str = "") -> APIResponse:
        """POST /api/v1/auth/login"""
        start = time.perf_counter()
        auth_data = self.service.authenticate_user(username=username, password=password)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=auth_data,
            execution_time_ms=round(elapsed_ms, 2)
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Player Props Route Handlers
    # ──────────────────────────────────────────────────────────────────────────

    def handle_post_scan_props(self, config_params: Optional[Dict[str, Any]] = None) -> APIResponse:
        """POST /api/v1/props/scan"""
        start = time.perf_counter()
        try:
            results = self.service.scan_player_props(config_params=config_params)
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=200,
                data=results,
                execution_time_ms=round(elapsed_ms, 2),
            )
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            return APIResponse(
                status_code=500,
                data=None,
                errors=[f"Player props scan failed: {str(e)}"],
                execution_time_ms=round(elapsed_ms, 2),
            )

    def handle_get_props_results(
        self,
        category: Optional[str] = None,
        stat: Optional[str] = None,
        search: Optional[str] = None,
        min_odds: float = 1.0,
        min_hit_rate: float = 0.0,
        min_score: float = 0.0,
        min_execution_edge: Optional[float] = None,
        min_statistical_edge: Optional[float] = None,
        min_ev: Optional[float] = None,
        execution_status: Optional[str] = None,
        bookmaker: Optional[str] = None,
        position: Optional[str] = None,
        line: Optional[float] = None,
        sort_by: str = "score",
        limit: int = 500,
        offset: int = 0,
    ) -> APIResponse:
        """GET /api/v1/props/results"""
        start = time.perf_counter()
        results = self.service.get_props_results(
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
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=results,
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_prop_detail(self, prop_id: str) -> APIResponse:
        """GET /api/v1/props/{prop_id}"""
        start = time.perf_counter()
        detail = self.service.get_prop_detail(prop_id)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if not detail:
            return APIResponse(
                status_code=404,
                data=None,
                errors=[f"Player prop '{prop_id}' not found."],
                execution_time_ms=round(elapsed_ms, 2),
            )
        return APIResponse(
            status_code=200,
            data=detail,
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_props_health(self) -> APIResponse:
        """GET /api/v1/props/health"""
        start = time.perf_counter()
        health_data = self.service.get_props_health()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=health_data,
            execution_time_ms=round(elapsed_ms, 2),
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Team Props Subsystem Handlers
    # ──────────────────────────────────────────────────────────────────────────

    def handle_scan_team_props(self, config_params: Optional[Dict[str, Any]] = None) -> APIResponse:
        """POST /api/v1/team-props/scan"""
        start = time.perf_counter()
        scan_data = self.service.scan_team_props(config_params=config_params)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=scan_data,
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_team_props_results(
        self,
        stat: Optional[str] = None,
        page: int = 1,
        limit: int = 50,
        search: str = "",
        min_odds: float = 1.0,
        min_hit_rate: float = 0.0,
        min_score: float = 0.0,
        min_edge: Optional[float] = None,
        min_ev: Optional[float] = None,
        execution_status: Optional[str] = None,
        line: Optional[float] = None,
        side: Optional[str] = None,
        sort_by: str = "score",
        sort_direction: str = "desc",
    ) -> APIResponse:
        """GET /api/v1/team-props"""
        start = time.perf_counter()
        results = self.service.get_team_props_results(
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
        )
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=results,
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_team_prop_detail(self, prop_id: str) -> APIResponse:
        """GET /api/v1/team-props/{prop_id}"""
        start = time.perf_counter()
        detail = self.service.get_team_prop_detail(prop_id)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        if not detail:
            return APIResponse(
                status_code=404,
                data=None,
                errors=[f"Team prop '{prop_id}' not found."],
                execution_time_ms=round(elapsed_ms, 2),
            )
        return APIResponse(
            status_code=200,
            data=detail,
            execution_time_ms=round(elapsed_ms, 2),
        )

    def handle_get_team_props_health(self) -> APIResponse:
        """GET /api/v1/team-props/health"""
        start = time.perf_counter()
        health_data = self.service.get_team_props_health()
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        return APIResponse(
            status_code=200,
            data=health_data,
            execution_time_ms=round(elapsed_ms, 2),
        )


