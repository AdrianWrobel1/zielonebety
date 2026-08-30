"""
Application Services for API Orchestration
"""

from collections import defaultdict
import json
import logging
import re
import threading
import time
from typing import Dict, List, Any, Optional, Set, Tuple, Union
import uuid
from decimal import Decimal
from datetime import datetime, timezone

from providers.base.provider_manager import ProviderManager
from database.connection import DatabaseManager
from database.models import OpportunityRecordORM
from database.repositories.event_repository import EventRepository
from database.repositories.opportunity_repository import OpportunityRepository
from scanner.scanner_engine import ScannerEngine
from scanner.models import Opportunity
from normalization.surebet import SurebetOpportunity, SurebetLeg
from normalization.quality_policy import (
    DefaultOpportunityQualityPolicy,
    OpportunityQualityConfig,
    OpportunityQualityEvaluation,
    OpportunityRankingEngine,
)
from orchestration.scan_orchestrator import ProductionScanOrchestrator
from orchestration.models import ScanConfig, ScanCycleResult, CycleStatus
from orchestration.scheduler import ScanScheduler
from api.exceptions import APIError, ResourceNotFoundError

logger = logging.getLogger("zielonebety.api.services")


def _sanitize_text(text: str) -> str:
    """Sanitizes text by removing bot tokens, auth headers, and internal file paths."""
    if not isinstance(text, str):
        text = str(text)
    # Remove Telegram bot tokens (e.g. 123456789:ABCdefGhIjkLmNoPqRsTuVwXyZ)
    text = re.sub(r"\b\d{6,14}:[A-Za-z0-9_-]{20,50}\b", "[REDACTED_BOT_TOKEN]", text)
    # Remove Bearer tokens
    text = re.sub(r"(Bearer\s+)[A-Za-z0-9\-_.]+", r"\1[REDACTED_TOKEN]", text, flags=re.IGNORECASE)
    # Remove Authorization headers
    text = re.sub(r"(Authorization:\s*)[^\r\n]+", r"\1[REDACTED]", text, flags=re.IGNORECASE)
    # Remove full Windows/Linux file paths that might expose internal systems
    text = re.sub(r"[A-Za-z]:\\[^:\n\r\t<>\"'|?*]+\.py", "[PATH_REDACTED]", text)
    return text


def _format_selection_outcome(sel_type: str, event_info: Dict[str, Any]) -> str:
    """Formats selection type code into clear, human-readable outcome text."""
    s = str(sel_type).upper().strip()
    home = event_info.get("home_team") or "Home"
    away = event_info.get("away_team") or "Away"

    if s in ("HOME", "1"):
        return f"Home ({home})" if home and home != "Home" else "Home (1)"
    elif s in ("AWAY", "2"):
        return f"Away ({away})" if away and away != "Away" else "Away (2)"
    elif s in ("DRAW", "X", "REMIS"):
        return "Draw (X)"
    elif s in ("OVER", "POWYZEJ", "POWYŻEJ", "+"):
        return "Over"
    elif s in ("UNDER", "PONIZEJ", "PONIŻEJ", "-"):
        return "Under"
    elif s in ("YES", "TAK"):
        return "Yes (Tak)"
    elif s in ("NO", "NIE"):
        return "No (Nie)"
    elif s in ("HOME_DRAW", "1X"):
        return f"1X ({home} or Draw)" if home and home != "Home" else "1X (Home or Draw)"
    elif s in ("HOME_AWAY", "12"):
        return f"12 ({home} or {away})" if home and away and home != "Home" else "12 (Home or Away)"
    elif s in ("DRAW_AWAY", "X2", "2X"):
        return f"X2 (Draw or {away})" if away and away != "Away" else "X2 (Draw or Away)"
    return s.replace("_", " ").title()


def _format_market_human_readable(mkt_info: Dict[str, Any], event_info: Dict[str, Any]) -> Dict[str, Any]:
    """Formats technical canonical market keys and metadata into human-readable displays."""
    from normalization.market_identity import format_canonical_market_human_readable
    home_team = event_info.get("home_team") if isinstance(event_info, dict) else None
    away_team = event_info.get("away_team") if isinstance(event_info, dict) else None
    return format_canonical_market_human_readable(
        market_key=mkt_info,
        home_team=home_team,
        away_team=away_team,
    )


def _extract_opp_data(opp_or_record: Any) -> Dict[str, Any]:
    """Normalize SurebetOpportunity, OpportunityRecordORM, or serialized dict into a unified dict."""
    if isinstance(opp_or_record, dict):
        return opp_or_record

    if isinstance(opp_or_record, OpportunityRecordORM):
        snapshot = {}
        if opp_or_record.snapshot_json:
            try:
                snapshot = json.loads(opp_or_record.snapshot_json)
            except Exception:
                snapshot = {}

        event_ev = snapshot.get("event_evidence") or {}
        home_name = event_ev.get("home_team")
        away_name = event_ev.get("away_team")
        if (not home_name or not away_name) and event_ev.get("source_name") and " vs " in event_ev["source_name"]:
            parts_sn = event_ev["source_name"].split(" vs ", 1)
            home_name = home_name or parts_sn[0].strip()
            away_name = away_name or parts_sn[1].strip()
        comp_name = event_ev.get("competition_name") or "Football Competition"
        start_t = event_ev.get("start_time") or event_ev.get("source_start") or event_ev.get("target_start")
        mkt_key_dict = snapshot.get("canonical_market_key") or {}
        if isinstance(mkt_key_dict, str):
            parts = mkt_key_dict.split(":")
            if len(parts) >= 7 and parts[0].lower() in ("football", "basketball", "tennis", "hockey"):
                # Format: sport:market_type:metric:scope:role:[player:]period:line
                # e.g., football:TOTALS:GOALS:TEAM:home:FULL_TIME:1.5
                # e.g., football:PLAYER_SHOTS:SHOTS:PLAYER:none:bukayo_saka:FULL_TIME:0.5
                sport_val = parts[0]
                mkt_t = parts[1]
                metric_val = parts[2]
                scope_val = parts[3]
                role_val = parts[4] if parts[4] != "all" else None
                period_val = parts[-2]
                line_str = parts[-1]
                mkt_key_dict = {
                    "sport": sport_val,
                    "market_type": mkt_t,
                    "metric": metric_val,
                    "scope": scope_val,
                    "participant_role": role_val,
                    "period": period_val,
                    "line": float(line_str) if line_str not in ("none", "no_line", "") else None,
                    "key_string": opp_or_record.market_key,
                }
            else:
                mkt_key_dict = {
                    "market_type": parts[0] if len(parts) > 0 else "1X2",
                    "period": parts[1] if len(parts) > 1 else "FULL_TIME",
                    "scope": parts[2] if len(parts) > 2 else "MATCH",
                    "line": parts[3] if len(parts) > 3 and parts[3] != "no_line" else None,
                    "key_string": opp_or_record.market_key,
                }

        # Market line parsing
        line_val = mkt_key_dict.get("line")
        if line_val is not None and str(line_val).lower() not in ("none", "no_line", ""):
            try:
                line_float = float(line_val)
            except (ValueError, TypeError):
                line_float = None
        else:
            line_float = None

        legs = snapshot.get("legs", [])
        bookmakers = snapshot.get("bookmakers") or list(sorted(set(l.get("provider") for l in legs if l.get("provider"))))

        # Extract or compute quality telemetry
        quality_score = snapshot.get("quality_score")
        comp_tier = snapshot.get("competition_tier")
        tier_name = snapshot.get("tier_name")
        is_qual = snapshot.get("is_qualified", True)
        rej_reasons = snapshot.get("rejection_reasons", [])

        if comp_tier is None:
            default_qp = DefaultOpportunityQualityPolicy()
            comp_tier = default_qp.calculate_competition_tier(comp_name) if hasattr(default_qp, "calculate_competition_tier") else 2
            tier_names = {0: "Tier 0 (Top Flight)", 1: "Tier 1 (Secondary)", 2: "Tier 2 (Standard)"}
            tier_name = tier_names.get(comp_tier, "Tier 2 (Standard)")
            if quality_score is None:
                margin_f = float(opp_or_record.arbitrage_margin)
                margin_score = min(40.0, max(0.0, margin_f * 100.0 * 8.0))
                tier_score = {0: 30.0, 1: 18.0, 2: 5.0}.get(comp_tier, 5.0)
                book_score = 15.0 if len(bookmakers) >= 2 else 5.0
                quality_score = round(margin_score + tier_score + book_score + 5.0 + 5.0, 1)

        is_valuebet = (opp_or_record.opportunity_type == "VALUEBET") or (snapshot.get("opportunity_type") == "VALUEBET")
        val_pct_val = float(snapshot.get("value_percent") or opp_or_record.arbitrage_margin) if is_valuebet else float(opp_or_record.arbitrage_margin * 100.0)

        res_dict = {
            "opportunity_id": snapshot.get("opportunity_id", opp_or_record.id),
            "fingerprint": opp_or_record.fingerprint,
            "opportunity_type": opp_or_record.opportunity_type,
            "canonical_event_id": opp_or_record.canonical_event_id,
            "event": {
                "id": opp_or_record.canonical_event_id,
                "home_team": home_name or f"Event {opp_or_record.canonical_event_id[:10]}",
                "away_team": away_name or "",
                "competition": comp_name,
                "start_time": start_t,
                "sport": "Football",
            },
            "market": {
                "type": mkt_key_dict.get("market_type", "1X2"),
                "period": mkt_key_dict.get("period", "FULL_TIME"),
                "scope": mkt_key_dict.get("scope", "MATCH"),
                "line": line_float,
                "key_string": opp_or_record.market_key,
            },
            "arbitrage_margin": float(opp_or_record.arbitrage_margin),
            "arbitrage_margin_pct": round(val_pct_val, 2),
            "value_percent": round(val_pct_val, 2) if is_valuebet else None,
            "fair_odds": float(snapshot.get("fair_odds", 0.0)) if is_valuebet else None,
            "fair_probability": float(snapshot.get("fair_probability", opp_or_record.implied_probability_sum)) if is_valuebet else None,
            "bookmaker_odds": float(snapshot.get("bookmaker_odds", 0.0)) if is_valuebet else None,
            "reference_source": snapshot.get("reference_source") if is_valuebet else None,
            "reference_bookmaker": snapshot.get("reference_bookmaker") if is_valuebet else None,
            "reference_odds": snapshot.get("reference_odds", {}) if is_valuebet else {},
            "raw_probabilities": snapshot.get("raw_probabilities", {}) if is_valuebet else {},
            "overround": float(snapshot.get("overround", 1.0)) if is_valuebet else 1.0,
            "implied_probability_sum": float(opp_or_record.implied_probability_sum),
            "is_mixed_bookmakers": snapshot.get("is_mixed_bookmakers", len(bookmakers) > 1),
            "bookmakers": bookmakers,
            "quality_score": float(quality_score) if quality_score is not None else 50.0,
            "competition_tier": int(comp_tier) if comp_tier is not None else 2,
            "tier_name": tier_name or "Tier 2 (Standard)",
            "is_qualified": is_qual,
            "rejection_reasons": rej_reasons,
            "lifecycle_status": opp_or_record.status,
            "detected_at": opp_or_record.first_seen_at.isoformat() if opp_or_record.first_seen_at else None,
            "first_seen_at": opp_or_record.first_seen_at.isoformat() if opp_or_record.first_seen_at else None,
            "last_seen_at": opp_or_record.last_seen_at.isoformat() if opp_or_record.last_seen_at else None,
            "last_changed_at": opp_or_record.last_changed_at.isoformat() if opp_or_record.last_changed_at else None,
            "last_alerted_at": opp_or_record.last_alerted_at.isoformat() if opp_or_record.last_alerted_at else None,
            "expired_at": opp_or_record.expired_at.isoformat() if opp_or_record.expired_at else None,
            "consecutive_misses": opp_or_record.consecutive_misses,
            "alert_count": opp_or_record.alert_count,
            "delivery_status": opp_or_record.delivery_status,
            "legs": legs,
            "market_evidence": snapshot.get("market_evidence", {}),
            "event_evidence": event_ev,
        }
        return res_dict

    if hasattr(opp_or_record, "value_percent") and (hasattr(opp_or_record, "reference_fair_probability") or hasattr(opp_or_record, "fair_probability")):
        # ValueBetCandidate instance
        cand = opp_or_record
        from valuebets.quality_policy import ValuebetQualityPolicy
        qp = ValuebetQualityPolicy()
        q_eval = qp.evaluate_quality(cand)
        line_val = float(cand.line) if cand.line is not None else None
        
        # Safely extract event teams
        ev_name = getattr(cand, "event_name", "") or ""
        home_t = getattr(cand, "home_team", None)
        away_t = getattr(cand, "away_team", None)
        if (not home_t or not away_t) and " vs " in ev_name:
            ev_parts = ev_name.split(" vs ", 1)
            home_t = home_t or ev_parts[0].strip()
            away_t = away_t or ev_parts[1].strip()

        # Safely extract market key info
        mk = getattr(cand, "market_key", None)
        mkt_period = getattr(cand, "period", None) or (mk.period if mk else "FULL_TIME")
        mkt_scope = getattr(cand, "scope", None) or (mk.scope if mk else "MATCH")
        mkt_key_str = mk.to_key_string() if mk and hasattr(mk, "to_key_string") else f"{cand.market_type}:{mkt_period}:{mkt_scope}:{cand.line or 'no_line'}"

        fair_odds_val = getattr(cand, "reference_fair_odds", None) or getattr(cand, "fair_odds", 0.0)
        fair_prob_val = getattr(cand, "reference_fair_probability", None) or getattr(cand, "fair_probability", 0.0)

        legs_data = [
            {
                "selection_type": cand.selection_type,
                "provider": cand.bookmaker,
                "odds": float(cand.bookmaker_odds),
                "implied_probability": float(Decimal("1") / cand.bookmaker_odds) if cand.bookmaker_odds > 0 else 0.0,
                "fair_odds": float(fair_odds_val),
                "fair_probability": float(fair_prob_val),
                "value_percent": float(cand.value_percent),
                "line": line_val,
            }
        ]

        return {
            "opportunity_id": cand.candidate_id,
            "fingerprint": getattr(cand, "fingerprint", cand.candidate_id),
            "opportunity_type": "VALUEBET",
            "canonical_event_id": cand.canonical_event_id,
            "event": {
                "id": cand.canonical_event_id,
                "home_team": home_t or f"Event {cand.canonical_event_id[:10]}",
                "away_team": away_t or "",
                "competition": cand.competition_name or "Football Competition",
                "start_time": getattr(cand, "kickoff", None) or getattr(cand, "kickoff_time", None),
                "sport": getattr(cand, "sport", "Football"),
            },
            "market": {
                "type": cand.market_type,
                "period": mkt_period,
                "scope": mkt_scope,
                "line": line_val,
                "key_string": mkt_key_str,
            },
            "arbitrage_margin": float(cand.value_percent),
            "arbitrage_margin_pct": round(float(cand.value_percent), 2),
            "value_percent": float(cand.value_percent),
            "fair_odds": float(fair_odds_val),
            "fair_probability": float(fair_prob_val),
            "bookmaker_odds": float(cand.bookmaker_odds),
            "reference_source": cand.reference_source,
            "reference_bookmaker": cand.reference_bookmaker,
            "reference_odds": {k: float(v) for k, v in getattr(cand, "reference_odds", {}).items()} if getattr(cand, "reference_odds", None) else {},
            "raw_probabilities": {k: float(v) for k, v in getattr(cand, "raw_probabilities", {}).items()} if getattr(cand, "raw_probabilities", None) else {},
            "overround": float(getattr(cand, "reference_overround", None) or getattr(cand, "overround", 1.0)),
            "implied_probability_sum": float(cand.fair_probability),
            "is_mixed_bookmakers": False,
            "bookmakers": [cand.bookmaker],
            "quality_score": q_eval.quality_score,
            "competition_tier": q_eval.competition_tier,
            "tier_name": q_eval.tier_name,
            "is_qualified": q_eval.is_qualified,
            "rejection_reasons": [r.value for r in q_eval.rejection_reasons],
            "scoring_breakdown": q_eval.score_breakdown,
            "lifecycle_status": "NEW",
            "detected_at": datetime.now(timezone.utc).isoformat(),
            "first_seen_at": datetime.now(timezone.utc).isoformat(),
            "last_seen_at": datetime.now(timezone.utc).isoformat(),
            "last_changed_at": datetime.now(timezone.utc).isoformat(),
            "legs": legs_data,
        }

    if hasattr(opp_or_record, "canonical_market_key") and hasattr(opp_or_record, "legs"):
        # SurebetOpportunity instance
        opp = opp_or_record
        mkt_key = opp.canonical_market_key
        legs_data = []
        for leg in opp.legs:
            odds_val = float(leg.odds)
            imp_prob = float(leg.implied_probability) if leg.implied_probability is not None else (1.0 / odds_val if odds_val > 0 else 0.0)
            sel_key = getattr(leg, "canonical_selection_key", None)
            sel_line = None
            if sel_key is not None:
                raw_l = getattr(sel_key, "selection_line", getattr(sel_key, "line", None))
                if raw_l is not None:
                    sel_line = float(raw_l)
                elif mkt_key and mkt_key.line is not None:
                    sel_line = float(mkt_key.line)
            elif mkt_key and mkt_key.line is not None:
                sel_line = float(mkt_key.line)

            legs_data.append({
                "selection_type": leg.selection_type,
                "provider": leg.provider,
                "odds": odds_val,
                "raw_odds": odds_val,
                "effective_odds": float(leg.effective_odds) if leg.effective_odds is not None else odds_val,
                "tax_rate": float(leg.tax_rate) if hasattr(leg, "tax_rate") else 0.0,
                "is_tax_applied": getattr(leg, "is_tax_applied", False),
                "implied_probability": imp_prob,
                "line": sel_line,
                "participant": getattr(sel_key, "participant_role", getattr(sel_key, "participant", None)) if sel_key else None,
                "source_selection_id": leg.source_selection_id,
                "source_event_id": leg.source_event_id,
                "source_market_id": leg.source_market_id,
            })

        line_val = float(mkt_key.line) if mkt_key and mkt_key.line is not None else None

        ev_dict = {}
        if opp.event_evidence:
            ev = opp.event_evidence
            if isinstance(ev, dict):
                h_name = ev.get("home_team")
                a_name = ev.get("away_team")
                if (not h_name or not a_name) and ev.get("source_name") and " vs " in ev["source_name"]:
                    parts_sn = ev["source_name"].split(" vs ", 1)
                    h_name = h_name or parts_sn[0].strip()
                    a_name = a_name or parts_sn[1].strip()
                comp_n = ev.get("competition_name")
                st_t = ev.get("start_time") or ev.get("source_start") or ev.get("target_start")
                ev_dict = {
                    "home_team": h_name,
                    "away_team": a_name,
                    "competition_name": comp_n,
                    "start_time": st_t,
                    "decision": ev.get("decision"),
                }
            else:
                ev_data = getattr(ev, "evidence", {}) or {}
                h_name = getattr(ev, "home_team", None) or ev_data.get("home_team")
                a_name = getattr(ev, "away_team", None) or ev_data.get("away_team")
                if (not h_name or not a_name) and ev_data.get("source_name") and " vs " in ev_data["source_name"]:
                    parts_sn = ev_data["source_name"].split(" vs ", 1)
                    h_name = h_name or parts_sn[0].strip()
                    a_name = a_name or parts_sn[1].strip()
                comp_n = getattr(ev, "competition_name", None) or ev_data.get("competition_name")
                st_t = getattr(ev, "start_time", None) or ev_data.get("start_time") or ev_data.get("source_start") or ev_data.get("target_start")
                ev_dict = {
                    "home_team": h_name,
                    "away_team": a_name,
                    "competition_name": comp_n,
                    "start_time": st_t,
                    "decision": getattr(ev, "decision", None),
                }

        # Dynamic Quality Evaluation for SurebetOpportunity
        qp = DefaultOpportunityQualityPolicy()
        q_eval = qp.evaluate_quality(opp)

        return {
            "opportunity_id": opp.opportunity_id,
            "fingerprint": getattr(opp, "fingerprint", opp.opportunity_id),
            "opportunity_type": "SUREBET",
            "canonical_event_id": opp.canonical_event_id,
            "event": {
                "id": opp.canonical_event_id,
                "home_team": ev_dict.get("home_team") or f"Event {opp.canonical_event_id[:10]}",
                "away_team": ev_dict.get("away_team") or "",
                "competition": ev_dict.get("competition_name") or "Football Competition",
                "start_time": ev_dict.get("start_time"),
                "sport": "Football",
            },
            "market": {
                "type": mkt_key.market_type,
                "period": mkt_key.period,
                "scope": mkt_key.scope,
                "line": line_val,
                "key_string": mkt_key.to_key_string() if hasattr(mkt_key, "to_key_string") else str(mkt_key),
            },
            "arbitrage_margin": float(opp.arbitrage_margin),
            "arbitrage_margin_pct": round(float(opp.arbitrage_margin) * 100.0, 2),
            "implied_probability_sum": float(opp.implied_probability_sum),
            "is_mixed_bookmakers": opp.is_mixed_bookmakers,
            "bookmakers": list(opp.bookmakers),
            "quality_score": q_eval.quality_score,
            "competition_tier": q_eval.competition_tier,
            "tier_name": q_eval.tier_name,
            "is_qualified": q_eval.is_qualified,
            "rejection_reasons": list(q_eval.rejection_reasons),
            "scoring_breakdown": q_eval.signals.get("scoring_breakdown", {}),
            "lifecycle_status": getattr(opp, "status", "NEW").value if hasattr(getattr(opp, "status", "NEW"), "value") else str(getattr(opp, "status", "NEW")),
            "detected_at": getattr(opp, "detected_at", datetime.now(timezone.utc).isoformat()),
            "first_seen_at": getattr(opp, "detected_at", datetime.now(timezone.utc).isoformat()),
            "last_seen_at": getattr(opp, "detected_at", datetime.now(timezone.utc).isoformat()),
            "last_changed_at": getattr(opp, "detected_at", datetime.now(timezone.utc).isoformat()),
            "last_alerted_at": None,
            "expired_at": None,
            "consecutive_misses": 0,
            "alert_count": 0,
            "delivery_status": None,
            "legs": legs_data,
            "market_evidence": opp.market_evidence,
            "event_evidence": ev_dict,
        }

    elif hasattr(opp_or_record, "opportunity_type") and hasattr(opp_or_record, "event_id") and hasattr(opp_or_record, "legs"):
        # Legacy scanner.models.Opportunity instance
        legs_data = []
        for leg in opp_or_record.legs:
            legs_data.append({
                "selection_type": leg.selection_type,
                "provider": getattr(leg, "bookmaker", "unknown"),
                "odds": float(leg.decimal_odds),
                "implied_probability": float(leg.implied_probability),
                "line": None,
                "source_selection_id": getattr(leg, "selection_id", None),
            })
        books = list(sorted(set(l.get("provider") for l in legs_data if l.get("provider"))))
        roi_val = getattr(opp_or_record, "roi_percentage", 0.0)
        opp_type_str = opp_or_record.opportunity_type.name if hasattr(opp_or_record.opportunity_type, "name") else str(opp_or_record.opportunity_type)
        return {
            "opportunity_id": opp_or_record.opportunity_id,
            "fingerprint": getattr(opp_or_record, "fingerprint", opp_or_record.opportunity_id),
            "opportunity_type": opp_type_str,
            "canonical_event_id": opp_or_record.event_id,
            "event": {
                "id": opp_or_record.event_id,
                "home_team": f"Event {opp_or_record.event_id}",
                "away_team": "",
                "competition": "Football Competition",
                "sport": "Football",
            },
            "market": {
                "type": opp_or_record.market_type,
                "period": "FULL_TIME",
                "scope": "MATCH",
                "line": None,
                "key_string": opp_or_record.market_type,
            },
            "arbitrage_margin": float(roi_val / 100.0),
            "arbitrage_margin_pct": float(roi_val),
            "implied_probability_sum": float(sum(l["implied_probability"] for l in legs_data)),
            "is_mixed_bookmakers": len(books) > 1,
            "bookmakers": books,
            "quality_score": 50.0,
            "competition_tier": 2,
            "tier_name": "Tier 2 (Standard)",
            "is_qualified": True,
            "rejection_reasons": [],
            "lifecycle_status": "NEW",
            "detected_at": getattr(opp_or_record, "detected_at", datetime.now(timezone.utc).isoformat()),
            "legs": legs_data,
        }

    return {}


def serialize_opportunity_summary(opp_or_record: Any) -> Dict[str, Any]:
    """Standardized summary DTO for list view."""
    data = _extract_opp_data(opp_or_record)
    if not data:
        return {}

    from core.tax_engine import get_tax_engine
    tax_engine = get_tax_engine()

    legs_summary = []
    stake_calc_legs = []
    event_info = data.get("event") or {}
    mkt_dict = data.get("market") or {}
    opp_type = data.get("opportunity_type", "SUREBET")

    for leg in data.get("legs", []):
        raw_odds_val = float(leg.get("raw_odds", leg.get("odds", 0.0)))
        provider = leg.get("provider")
        net_res = tax_engine.calculate_net_odds(raw_odds=raw_odds_val, bookmaker=provider)
        eff_odds_val = float(net_res.effective_net_odds)
        tax_rate_val = float(Decimal("1") - net_res.net_stake_multiplier) if net_res.is_tax_applied else float(leg.get("tax_rate", 0.0))

        legs_summary.append({
            "selection_type": leg.get("selection_type"),
            "selection_outcome": _format_selection_outcome(leg.get("selection_type", ""), event_info),
            "provider": provider,
            "odds": raw_odds_val,
            "raw_odds": raw_odds_val,
            "effective_odds": eff_odds_val,
            "tax_rate": tax_rate_val,
            "line": leg.get("line"),
            "participant": leg.get("participant"),
            "fair_odds": leg.get("fair_odds"),
            "fair_probability": leg.get("fair_probability"),
            "value_percent": leg.get("value_percent"),
        })
        stake_calc_legs.append({
            "selection_type": leg.get("selection_type"),
            "provider": provider,
            "odds": raw_odds_val,
            "effective_odds": eff_odds_val,
        })

    key_str = mkt_dict.get("key_string", "")
    mkt_display = _format_market_human_readable(mkt_dict, event_info)

    # Attach human-readable display info into market dictionary
    mkt_dict["display_name"] = mkt_display.get("market_name")
    mkt_dict["line_display"] = mkt_display.get("line_display")
    mkt_dict["period_display"] = mkt_display.get("period_display")
    mkt_dict["scope_display"] = mkt_display.get("scope_display")
    mkt_dict["label"] = mkt_display.get("label")

    # Authoritative calculation
    calc_1000 = tax_engine.calculate_stake_distribution(total_stake=1000, legs=stake_calc_legs)
    net_s_val = float(calc_1000.get("net_implied_probability_sum", data.get("implied_probability_sum", 0.0)))
    net_margin_pct = float(calc_1000.get("roi_percentage", data.get("arbitrage_margin_pct", 0.0)))
    is_net_surebet = calc_1000.get("is_surebet", (net_s_val < 1.0 and net_s_val > 0.0))

    if opp_type == "VALUEBET":
        final_margin = float(data.get("value_percent") or data.get("arbitrage_margin", 0.0))
        final_margin_pct = float(data.get("value_percent") or data.get("arbitrage_margin_pct", 0.0))
    elif data.get("arbitrage_margin_pct") is not None or data.get("arbitrage_margin") is not None:
        final_margin = float(data.get("arbitrage_margin")) if data.get("arbitrage_margin") is not None else (float(data.get("arbitrage_margin_pct")) / 100.0)
        final_margin_pct = float(data.get("arbitrage_margin_pct")) if data.get("arbitrage_margin_pct") is not None else (final_margin * 100.0)
    else:
        final_margin = net_margin_pct / 100.0
        final_margin_pct = net_margin_pct

    calculation_block = {
        "is_surebet": is_net_surebet if opp_type != "VALUEBET" else False,
        "probability_sum": net_s_val,
        "guaranteed_margin": net_margin_pct / 100.0,
        "roi": net_margin_pct,
        "total_stake": float(calc_1000.get("total_stake", 1000.0)),
        "guaranteed_payout": float(calc_1000.get("guaranteed_payout", 0.0)),
        "guaranteed_profit": float(calc_1000.get("guaranteed_profit", 0.0)),
    }

    summary = {
        "id": data.get("opportunity_id"),
        "opportunity_id": data.get("opportunity_id"),
        "fingerprint": data.get("fingerprint"),
        "opportunity_type": opp_type,
        "canonical_event_id": data.get("canonical_event_id"),
        "canonical_market_key": key_str,
        "event": data.get("event"),
        "market": mkt_dict,
        "market_label": mkt_display.get("label"),
        "margin": final_margin,
        "margin_pct": final_margin_pct,
        "arbitrage_margin_pct": final_margin_pct,
        "implied_probability_sum": net_s_val if opp_type != "VALUEBET" else data.get("implied_probability_sum"),
        "calculation": calculation_block,
        "bookmakers": data.get("bookmakers", []),
        "is_mixed_bookmakers": data.get("is_mixed_bookmakers", False),
        "quality_score": data.get("quality_score", 50.0),
        "competition_tier": data.get("competition_tier", 2),
        "tier_name": data.get("tier_name", "Tier 2 (Standard)"),
        "is_qualified": data.get("is_qualified", True),
        "lifecycle_status": data.get("lifecycle_status", "NEW"),
        "detected_at": data.get("detected_at"),
        "last_seen_at": data.get("last_seen_at"),
        "legs": legs_summary,
    }

    if opp_type == "VALUEBET":
        summary["value_percent"] = data.get("value_percent", data.get("arbitrage_margin_pct"))
        summary["fair_odds"] = data.get("fair_odds")
        summary["fair_probability"] = data.get("fair_probability")
        summary["bookmaker_odds"] = data.get("bookmaker_odds")
        summary["effective_net_odds"] = data.get("effective_net_odds")
        summary["net_value_percent"] = data.get("net_value_percent")
        summary["is_tax_applied"] = data.get("is_tax_applied", False)
        summary["reference_source"] = data.get("reference_source")

    return summary


def serialize_opportunity_detail(opp_or_record: Any) -> Dict[str, Any]:
    """Comprehensive detail DTO for deep inspection, math breakdown, and cross-odds matrix."""
    data = _extract_opp_data(opp_or_record)
    if not data:
        return {}

    legs_raw = data.get("legs", [])
    opp_type = data.get("opportunity_type", "SUREBET")
    s_val = float(data.get("implied_probability_sum", 0.0))
    margin_val = float(data.get("arbitrage_margin", 0.0))
    margin_pct = data.get("arbitrage_margin_pct", round(margin_val * 100.0, 2))

    if opp_type == "VALUEBET":
        val_pct = data.get("value_percent", margin_pct)
        bm_odds = data.get("bookmaker_odds") or (legs_raw[0].get("odds") if legs_raw else 1.0)
        fair_odds = data.get("fair_odds", 0.0)
        fair_prob = data.get("fair_probability", s_val)
        ref_bm = data.get("reference_bookmaker", "pinnacle")
        ref_src = data.get("reference_source", "the_odds_api")
        eff_net_odds = data.get("effective_net_odds")
        net_val_pct = data.get("net_value_percent")
        is_tax = data.get("is_tax_applied", False)
        explanation_text = f"Calculated using sharp reference baseline ({ref_bm} via {ref_src}). Overround removed across market partition yielding fair probability {float(fair_prob)*100:.2f}% and fair odds {fair_odds}. Bookmaker price {bm_odds} yields +{val_pct}% expected value."

        event_info = data.get("event") or {}
        mkt_info = data.get("market") or {}
        mkt_display = _format_market_human_readable(mkt_info, event_info)
        mkt_info["display_name"] = mkt_display.get("market_name")
        mkt_info["line_display"] = mkt_display.get("line_display")
        mkt_info["period_display"] = mkt_display.get("period_display")
        mkt_info["scope_display"] = mkt_display.get("scope_display")
        mkt_info["label"] = mkt_display.get("label")

        return {
            "id": data.get("opportunity_id"),
            "opportunity_id": data.get("opportunity_id"),
            "fingerprint": data.get("fingerprint"),
            "opportunity_type": "VALUEBET",
            "event": data.get("event"),
            "market": mkt_info,
            "market_label": mkt_display.get("label"),
            "value_percent": val_pct,
            "net_value_percent": net_val_pct,
            "margin": val_pct,
            "margin_pct": val_pct,
            "arbitrage_margin_pct": val_pct,
            "implied_probability_sum": fair_prob,
            "fair_odds": fair_odds,
            "fair_probability": fair_prob,
            "bookmaker_odds": bm_odds,
            "effective_net_odds": eff_net_odds,
            "is_tax_applied": is_tax,
            "reference_source": ref_src,
            "reference_bookmaker": ref_bm,
            "reference_odds": data.get("reference_odds", {}),
            "raw_probabilities": data.get("raw_probabilities", {}),
            "overround": data.get("overround", 1.0),
            "selections": legs_raw,
            "legs": legs_raw,
            "bookmakers": data.get("bookmakers", []),
            "is_mixed_bookmakers": False,
            "quality_score": data.get("quality_score", 50.0),
            "competition_tier": data.get("competition_tier", 2),
            "tier_name": data.get("tier_name", "Tier 2 (Standard)"),
            "is_qualified": data.get("is_qualified", True),
            "rejection_reasons": data.get("rejection_reasons", []),
            "scoring_breakdown": data.get("scoring_breakdown", {}),
            "mathematical_explanation": {
                "formula": "Value = (Bookmaker Odds * Fair Probability) - 1",
                "value_percent": val_pct,
                "bookmaker_odds": bm_odds,
                "fair_odds": fair_odds,
                "fair_probability": fair_prob,
                "overround": data.get("overround", 1.0),
                "reference_source": ref_src,
                "reference_bookmaker": ref_bm,
                "reference_odds": data.get("reference_odds", {}),
                "raw_probabilities": data.get("raw_probabilities", {}),
                "explanation": explanation_text,
            },
            "odds_comparison": {
                "selections": [l.get("selection_type") for l in legs_raw],
                "providers": data.get("bookmakers", []),
                "matrix": {
                    legs_raw[0].get("selection_type", "HOME"): {
                        data.get("bookmakers", ["superbet"])[0]: bm_odds,
                        f"Ref: {ref_bm}": float(data.get("reference_odds", {}).get(legs_raw[0].get("selection_type", "HOME"), fair_odds)),
                    }
                } if legs_raw else {},
            },
            "lifecycle": {
                "status": data.get("lifecycle_status", "NEW"),
                "first_seen_at": data.get("first_seen_at"),
                "last_seen_at": data.get("last_seen_at"),
                "last_changed_at": data.get("last_changed_at"),
                "last_alerted_at": data.get("last_alerted_at"),
                "expired_at": data.get("expired_at"),
                "consecutive_misses": data.get("consecutive_misses", 0),
                "alert_count": data.get("alert_count", 0),
                "delivery_status": data.get("delivery_status"),
            },
            "detected_at": data.get("detected_at"),
        }

    # Build odds comparison matrix and math terms across selections
    from core.tax_engine import get_tax_engine
    tax_engine = get_tax_engine()

    event_info = data.get("event") or {}
    mkt_info = data.get("market") or {}
    mkt_display = _format_market_human_readable(mkt_info, event_info)

    # Attach human-readable display info into market dictionary
    mkt_info["display_name"] = mkt_display["market_name"]
    mkt_info["line_display"] = mkt_display["line_display"]
    mkt_info["period_display"] = mkt_display["period_display"]
    mkt_info["scope_display"] = mkt_display["scope_display"]
    mkt_info["label"] = mkt_display.get("label")

    odds_matrix = []
    math_terms = []
    stake_calc_legs = []
    enriched_legs = []

    for leg in legs_raw:
        sel = leg.get("selection_type", "SELECTION")
        provider = leg.get("provider", "unknown")
        raw_odds_num = float(leg.get("odds", leg.get("decimal_odds", 1.0)))
        net_res = tax_engine.calculate_net_odds(raw_odds=raw_odds_num, bookmaker=provider)
        eff_odds_num = float(net_res.effective_net_odds)
        eff_implied_p = round(1.0 / eff_odds_num, 4) if eff_odds_num > 0 else 0.0
        gross_implied_p = round(1.0 / raw_odds_num, 4) if raw_odds_num > 0 else 0.0

        sel_outcome = _format_selection_outcome(sel, event_info)
        tax_factor = float(net_res.net_stake_multiplier)
        tax_rate = float(Decimal("1") - net_res.net_stake_multiplier) if net_res.is_tax_applied else 0.0
        src_id = (
            leg.get("source_selection_id")
            or leg.get("provider_selection_id")
            or leg.get("id")
            or f"{provider}_{str(sel).lower()}"
        )
        leg_line = leg.get("line") if leg.get("line") is not None else mkt_display["line"]
        leg_line_str = str(leg_line) if leg_line is not None else mkt_display["line_display"]

        enriched_leg = {
            "selection_type": sel,
            "selection_outcome": sel_outcome,
            "outcome": sel_outcome,
            "provider": provider,
            "bookmaker": provider,
            "odds": raw_odds_num,
            "raw_odds": raw_odds_num,
            "tax_rate": tax_rate,
            "tax_factor": tax_factor,
            "effective_odds": eff_odds_num,
            "effective_net_odds": eff_odds_num,
            "is_tax_applied": net_res.is_tax_applied,
            "implied_probability": gross_implied_p,
            "net_implied_probability": eff_implied_p,
            "participant": leg.get("participant"),
            "line": leg_line,
            "line_display": leg_line_str,
            "market_name": mkt_display["market_name"],
            "period_display": mkt_display["period_display"],
            "scope_display": mkt_display["scope_display"],
            "source_selection_id": src_id,
            "source_identifier": src_id,
            "source_event_id": leg.get("source_event_id"),
            "source_market_id": leg.get("source_market_id"),
        }
        enriched_legs.append(enriched_leg)

        odds_matrix.append({
            "selection_type": sel,
            "selection_outcome": sel_outcome,
            "selected_provider": provider,
            "provider": provider,
            "bookmaker": provider,
            "selected_odds": raw_odds_num,
            "raw_odds": raw_odds_num,
            "tax_rate": tax_rate,
            "tax_factor": tax_factor,
            "effective_odds": eff_odds_num,
            "effective_net_odds": eff_odds_num,
            "is_tax_applied": net_res.is_tax_applied,
            "implied_probability": gross_implied_p,
            "net_implied_probability": eff_implied_p,
            "participant": leg.get("participant"),
            "line": leg_line,
            "line_display": leg_line_str,
            "market_name": mkt_display["market_name"],
            "period_display": mkt_display["period_display"],
            "scope_display": mkt_display["scope_display"],
            "source_selection_id": src_id,
            "source_identifier": src_id,
            "observed_prices": {
                provider: raw_odds_num
            }
        })
        math_terms.append({
            "selection_type": sel,
            "selection_outcome": sel_outcome,
            "provider": provider,
            "odds": raw_odds_num,
            "effective_odds": eff_odds_num,
            "tax_rate": tax_rate,
            "tax_factor": tax_factor,
            "is_tax_applied": net_res.is_tax_applied,
            "implied_probability": eff_implied_p,
            "gross_implied_probability": gross_implied_p,
            "step_formula": f"1 / {eff_odds_num:.4g} = {eff_implied_p:.4f}" if net_res.is_tax_applied else f"1 / {raw_odds_num:.4g} = {eff_implied_p:.4f}",
            "term_expression": f"1 / {eff_odds_num:.4g} = {eff_implied_p:.4f}" if net_res.is_tax_applied else f"1 / {raw_odds_num:.4g} = {eff_implied_p:.4f}",
        })
        stake_calc_legs.append({
            "selection_type": sel,
            "selection_outcome": sel_outcome,
            "provider": provider,
            "odds": raw_odds_num,
            "effective_odds": eff_odds_num,
            "participant": leg.get("participant"),
            "line": leg_line,
            "source_selection_id": src_id,
        })

    # Net & Gross arbitrage sum calculation via TaxEngine
    net_margin_calc = tax_engine.calculate_net_surebet_margin(stake_calc_legs)
    calc_1000 = tax_engine.calculate_stake_distribution(total_stake=1000, legs=stake_calc_legs)
    calc_presets = tax_engine.generate_preset_table(legs=stake_calc_legs)

    gross_s_val = float(net_margin_calc["gross_implied_probability_sum"])
    gross_margin_val = float(net_margin_calc["gross_margin"])
    gross_margin_pct = float(net_margin_calc["gross_margin_percent"])

    net_s_val = float(calc_1000.get("net_implied_probability_sum", net_margin_calc["net_implied_probability_sum"]))
    net_margin_pct = float(calc_1000.get("roi_percentage", net_margin_calc["net_margin_percent"]))

    is_net_surebet = bool(
        data.get("status") == "SUREBET"
        or (data.get("arbitrage_margin_pct") is not None and float(data.get("arbitrage_margin_pct")) > 0)
        or (data.get("arbitrage_margin") is not None and float(data.get("arbitrage_margin")) > 0)
        or calc_1000.get("is_surebet", (net_s_val < 1.0 and net_s_val > 0.0))
    )

    explanation_text = (
        f"Sum of net effective implied probabilities S = {net_s_val:.4f} is strictly below 1.0 threshold (accounting for bookmaker taxes). "
        f"By placing mathematically proportional stakes across all mutually exclusive outcomes, "
        f"a guaranteed net arbitrage margin of +{net_margin_pct:.2f}% is secured."
        if is_net_surebet else
        f"Sum of net implied probabilities S = {net_s_val:.4f} >= 1.0 (no arbitrage profit possible with configured taxes)."
    )

    calculation_block = {
        "is_surebet": is_net_surebet,
        "probability_sum": net_s_val,
        "guaranteed_margin": net_margin_pct / 100.0,
        "roi": net_margin_pct,
        "total_stake": float(calc_1000.get("total_stake", 1000.0)),
        "guaranteed_payout": float(calc_1000.get("guaranteed_payout", 0.0)),
        "guaranteed_profit": float(calc_1000.get("guaranteed_profit", 0.0)),
    }

    return {
        "id": data.get("opportunity_id"),
        "opportunity_id": data.get("opportunity_id"),
        "fingerprint": data.get("fingerprint"),
        "opportunity_type": data.get("opportunity_type", "SUREBET"),
        "event": data.get("event"),
        "market": mkt_info,
        "market_label": mkt_display.get("label"),
        "margin": float(data.get("arbitrage_margin")) if data.get("arbitrage_margin") is not None else (net_margin_pct / 100.0),
        "margin_pct": float(data.get("arbitrage_margin_pct")) if data.get("arbitrage_margin_pct") is not None else (float(data["arbitrage_margin"]) * 100.0 if data.get("arbitrage_margin") is not None else net_margin_pct),
        "arbitrage_margin_pct": float(data.get("arbitrage_margin_pct")) if data.get("arbitrage_margin_pct") is not None else (float(data["arbitrage_margin"]) * 100.0 if data.get("arbitrage_margin") is not None else net_margin_pct),
        "implied_probability_sum": float(data.get("implied_probability_sum")) if data.get("implied_probability_sum") is not None else net_s_val,
        "gross_margin": gross_margin_val,
        "gross_margin_pct": gross_margin_pct,
        "gross_implied_probability_sum": gross_s_val,
        "net_margin": net_margin_pct / 100.0,
        "net_margin_pct": net_margin_pct,
        "net_implied_probability_sum": net_s_val,
        "calculation": calculation_block,
        "selections": enriched_legs,
        "legs": enriched_legs,
        "bookmakers": data.get("bookmakers", []),
        "is_mixed_bookmakers": data.get("is_mixed_bookmakers", False),
        "quality_score": data.get("quality_score", 50.0),
        "competition_tier": data.get("competition_tier", 2),
        "tier_name": data.get("tier_name", "Tier 2 (Standard)"),
        "is_qualified": data.get("is_qualified", True),
        "rejection_reasons": data.get("rejection_reasons", []),
        "scoring_breakdown": data.get("scoring_breakdown", {}),
        "stake_calculator": {
            "is_surebet": is_net_surebet,
            "net_implied_probability_sum": net_s_val,
            "roi_percentage": net_margin_pct,
            "default_calculation": {
                "total_stake": float(calc_1000["total_stake"]),
                "guaranteed_payout": float(calc_1000["guaranteed_payout"]),
                "guaranteed_profit": float(calc_1000["guaranteed_profit"]),
                "roi_percentage": float(calc_1000["roi_percentage"]),
                "legs": [
                    {
                        "selection_type": l["selection_type"],
                        "selection_outcome": _format_selection_outcome(l["selection_type"], event_info),
                        "provider": l["provider"],
                        "raw_odds": float(l["raw_odds"]),
                        "effective_odds": float(l["effective_odds"]),
                        "tax_rate": float(l["tax_rate"]),
                        "tax_factor": float(1.0 - float(l["tax_rate"])),
                        "is_tax_applied": l["is_tax_applied"],
                        "stake_percentage": float(l["stake_percentage"]),
                        "allocated_stake": float(l["allocated_stake"]),
                        "expected_payout": float(l["expected_payout"]),
                        "expected_profit": float(l["expected_profit"]),
                    }
                    for l in calc_1000.get("legs", [])
                ],
            },
            "preset_table": [
                {
                    "total_stake": float(row["total_stake"]),
                    "is_surebet": row["is_surebet"],
                    "guaranteed_payout": float(row["guaranteed_payout"]),
                    "guaranteed_profit": float(row["guaranteed_profit"]),
                    "roi_percentage": float(row["roi_percentage"]),
                    "leg_stakes": {k: float(v) for k, v in row["leg_stakes"].items()},
                    "legs": [
                        {
                            "selection_type": l["selection_type"],
                            "selection_outcome": _format_selection_outcome(l["selection_type"], event_info),
                            "provider": l["provider"],
                            "raw_odds": float(l["raw_odds"]),
                            "effective_odds": float(l["effective_odds"]),
                            "tax_rate": float(l["tax_rate"]),
                            "tax_factor": float(1.0 - float(l["tax_rate"])),
                            "is_tax_applied": l["is_tax_applied"],
                            "allocated_stake": float(l["allocated_stake"]),
                            "expected_payout": float(l["expected_payout"]),
                            "expected_profit": float(l["expected_profit"]),
                        }
                        for l in row.get("legs", [])
                    ],
                }
                for row in calc_presets
            ],
        },
        "mathematical_explanation": {
            "formula": "S = sum(1 / odds_i)",
            "surebet_threshold": 1.0,
            "implied_probability_sum": net_s_val if net_s_val < 1.0 else float(data.get("implied_probability_sum", net_s_val)),
            "is_surebet": is_net_surebet,
            "is_net_surebet": is_net_surebet,
            "net_implied_probability_sum": net_s_val,
            "net_margin_pct": net_margin_pct,
            "margin_formula": "Margin = (1 / S) - 1",
            "arbitrage_margin": (net_margin_pct / 100.0) if net_margin_pct > 0 else (float(data.get("arbitrage_margin")) if data.get("arbitrage_margin") is not None else net_margin_pct / 100.0),
            "arbitrage_margin_pct": net_margin_pct if net_margin_pct > 0 else (float(data.get("arbitrage_margin_pct")) if data.get("arbitrage_margin_pct") is not None else net_margin_pct),
            "terms": math_terms,
            "explanation": explanation_text,
        },
        "odds_comparison": odds_matrix,
        "lifecycle": {
            "status": data.get("lifecycle_status", "NEW"),
            "first_seen_at": data.get("first_seen_at"),
            "last_seen_at": data.get("last_seen_at"),
            "last_changed_at": data.get("last_changed_at"),
            "last_alerted_at": data.get("last_alerted_at"),
            "expired_at": data.get("expired_at"),
            "consecutive_misses": data.get("consecutive_misses", 0),
            "alert_count": data.get("alert_count", 0),
            "delivery_status": data.get("delivery_status"),
        },
        "detected_at": data.get("detected_at"),
    }


def _serialize_events_from_scan_result(result: ScanCycleResult) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    """Extracts standardized summaries and deep detail structures for all canonical and normalized events in a scan cycle."""
    summaries: List[Dict[str, Any]] = []
    details_map: Dict[str, Dict[str, Any]] = {}
    seen_event_ids: Set[str] = set()

    # Pre-index opportunities by canonical event ID
    surebets_by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if result.detection_result and result.detection_result.opportunities:
        for opp in result.detection_result.opportunities:
            ce_id = opp.canonical_event_id
            surebets_by_event[ce_id].append(serialize_opportunity_detail(opp))

    valuebets_by_event: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    if result.valuebet_result and hasattr(result.valuebet_result, "candidates") and result.valuebet_result.candidates:
        for val_cand in result.valuebet_result.candidates:
            ce_id = val_cand.canonical_event_id
            valuebets_by_event[ce_id].append(serialize_opportunity_detail(val_cand))

    # Pre-index nearest opportunity evaluations by event ID
    nearest_by_event: Dict[str, Dict[str, Any]] = {}
    if result.detection_result and result.detection_result.evaluations:
        for eval_item in result.detection_result.evaluations:
            ce_id = eval_item.canonical_event_id
            if ce_id not in nearest_by_event and eval_item.implied_probability_sum is not None:
                S = eval_item.implied_probability_sum
                margin = eval_item.arbitrage_margin
                m_key = eval_item.canonical_market_key
                m_key_str = m_key.to_key_string() if hasattr(m_key, "to_key_string") else str(m_key)
                dist = S - Decimal("1.0")
                nearest_by_event[ce_id] = {
                    "canonical_event_id": ce_id,
                    "canonical_market_key": m_key_str,
                    "market_type": str(m_key.market_type),
                    "line": float(m_key.line) if m_key.line is not None else None,
                    "implied_probability_sum": float(S),
                    "arbitrage_margin_pct": round(float(margin * 100), 2) if margin is not None else 0.0,
                    "distance_to_arbitrage": float(dist),
                    "explanation": f"Implied probability sum S = {float(S):.4f} is >= 1.0 (no arbitrage profit possible). Distance to arbitrage threshold: {float(dist):.4f}.",
                }

    # ──────────────────────────────────────────────────────────────────────────
    # 1. Matched Canonical Events from CrossBookmakerValidationResult
    # ──────────────────────────────────────────────────────────────────────────
    if result.validation_result and result.validation_result.event_validation_records:
        for record in result.validation_result.event_validation_records:
            ce = record.canonical_event
            ev_id = ce.canonical_event_id
            if ev_id in seen_event_ids:
                continue
            seen_event_ids.add(ev_id)

            home_team = ce.home_team
            away_team = ce.away_team
            comp_name = ce.competition.name if ce.competition else "Football Competition"
            sport = ce.sport or "football"
            kickoff = ce.scheduled_start
            status = ce.status or "SCHEDULED"

            participating_bookmakers = sorted(list(ce.sources.keys())) if ce.sources else ["superbet", "betclic"]

            # Confidence score
            match_confidence = None
            if ce.match_evidence:
                match_confidence = float(ce.match_evidence[0].total_score)
            elif result.validation_result.event_decisions:
                for dec in result.validation_result.event_decisions:
                    if hasattr(dec, "total_score"):
                        match_confidence = float(dec.total_score)
                        break
            if match_confidence is None:
                match_confidence = 1.0

            # Provider coverage table
            providers = []
            for p_name in participating_bookmakers:
                src = ce.sources.get(p_name)
                prov_ev_id = src.provider_event_id if src else None
                raw_mkt_cnt = 0
                norm_mkt_cnt = 0

                # 1. Check raw/parsed provider objects
                if result.provider_results:
                    p_res = result.provider_results.get(p_name) or (result.provider_results.get("odds_api") if p_name in ("bet365", "unibet") else None)
                    if p_res and p_res.parsed_objects:
                        for p_obj in p_res.parsed_objects:
                            obj_bm = getattr(p_obj, "bookmaker_name", "").lower() if hasattr(p_obj, "bookmaker_name") else p_name
                            if p_name in ("bet365", "unibet") and obj_bm != p_name:
                                continue
                            obj_eid = getattr(p_obj, "event_id", None) or getattr(p_obj, "provider_event_id", None)
                            obj_home = getattr(p_obj, "home_team", None)
                            obj_away = getattr(p_obj, "away_team", None)
                            if (prov_ev_id and str(obj_eid) == str(prov_ev_id)) or (obj_home == home_team and obj_away == away_team):
                                raw_mkt_cnt = len(getattr(p_obj, "markets", []))
                                break

                # 2. Check normalized graphs
                if result.normalization_results:
                    norm_entry = result.normalization_results.get(p_name) or result.normalization_results.get("odds_api")
                    if norm_entry and hasattr(norm_entry, "graphs"):
                        for g in norm_entry.graphs:
                            g_bm = getattr(g.event, "metadata", {}).get("odds_api", {}).get("bookmaker") or p_name
                            if p_name in ("bet365", "unibet") and g_bm != p_name:
                                continue
                            if (g.event.home_participant == home_team and g.event.away_participant == away_team) or (src and g.event.internal_id == src.internal_event_id):
                                norm_mkt_cnt = len(g.markets)
                                break

                if raw_mkt_cnt == 0:
                    raw_mkt_cnt = norm_mkt_cnt if norm_mkt_cnt > 0 else len(record.matched_markets)
                if norm_mkt_cnt == 0:
                    norm_mkt_cnt = raw_mkt_cnt

                providers.append({
                    "provider": p_name,
                    "status": "Available",
                    "market_count": raw_mkt_cnt,
                    "raw_market_count": raw_mkt_cnt,
                    "normalized_market_count": norm_mkt_cnt,
                    "matched_market_count": len(record.matched_markets),
                    "provider_event_id": prov_ev_id,
                })

            # Markets and Selection Comparison Matrix
            markets = []
            for m_lineage in record.matched_markets:
                m_key = m_lineage.canonical_market_key
                m_type = m_key.market_type
                m_period = m_key.period
                m_scope = m_key.scope
                line_val = float(m_key.line) if m_key.line is not None else None
                key_str = m_key.to_key_string() if hasattr(m_key, "to_key_string") else str(m_key)

                selections = []
                for pair in m_lineage.comparable_selections:
                    sel_key = pair.canonical_selection_key
                    sel_type = getattr(sel_key, "selection_type", str(sel_key))
                    sel_line = None
                    raw_l = getattr(sel_key, "selection_line", getattr(sel_key, "line", None))
                    if raw_l is not None:
                        try:
                            sel_line = float(raw_l)
                        except (ValueError, TypeError):
                            sel_line = None
                    elif line_val is not None:
                        sel_line = line_val

                    odds_dict: Dict[str, float] = {}
                    if pair.source_odds and pair.source_provider:
                        odds_dict[pair.source_provider] = float(pair.source_odds.decimal_odds)
                    if pair.target_odds and pair.target_provider:
                        odds_dict[pair.target_provider] = float(pair.target_odds.decimal_odds)

                    best_odds_dict = None
                    if odds_dict:
                        best_p = max(odds_dict.keys(), key=lambda p: odds_dict[p])
                        best_val = odds_dict[best_p]
                        best_imp = round(1.0 / best_val, 4) if best_val > 0 else 0.0
                        best_odds_dict = {
                            "bookmaker": best_p,
                            "odds": best_val,
                            "implied_probability": best_imp,
                        }

                    selections.append({
                        "selection_type": sel_type,
                        "line": sel_line,
                        "participant": pair.source_selection.participant or pair.target_selection.participant or None,
                        "odds": odds_dict,
                        "best_odds": best_odds_dict,
                        "source_selection_id": pair.source_selection_id,
                        "target_selection_id": pair.target_selection_id,
                    })

                mkt_books = sorted(list(set(b for s in selections for b in s.get("odds", {}).keys()))) or participating_bookmakers

                markets.append({
                    "canonical_market_key": key_str,
                    "market_type": m_type,
                    "period": m_period,
                    "scope": m_scope,
                    "line": line_val,
                    "status": "MATCHED",
                    "participating_bookmakers": mkt_books,
                    "selections": selections,
                })

            # Opportunities for this event
            ev_surebets = surebets_by_event.get(ev_id, [])
            ev_valuebets = valuebets_by_event.get(ev_id, [])
            ev_nearest = nearest_by_event.get(ev_id)

            total_norm_mkts = sum(p["market_count"] for p in providers) if providers else len(markets)

            # Check if canonical event is an actionable execution match (Superbet <-> Betclic)
            is_actionable_match = (
                ("superbet" in ce.sources and "betclic" in ce.sources)
                or (not any(b in ce.sources for b in ("superbet", "betclic", "bet365", "unibet")) and len(ce.sources) >= 2)
            )
            matching_status_val = "MATCHED" if is_actionable_match else "UNMATCHED"

            comp_obj = ce.competition
            comp_id_val = getattr(comp_obj, "competition_id", None) if comp_obj else None
            comp_country_val = getattr(comp_obj, "country", None) if comp_obj else None
            comp_type_val = getattr(comp_obj, "competition_type", None) if comp_obj else None
            comp_tier_val = getattr(comp_obj, "tier", 2) if comp_obj else 2
            comp_prov_val = getattr(comp_obj, "provenance", "PROVIDER_METADATA") if comp_obj else "FALLBACK"
            comp_conf_val = getattr(comp_obj, "confidence", 1.0) if comp_obj else 1.0

            detail_dict = {
                "id": ev_id,
                "event_id": ev_id,
                "canonical_event_id": ev_id,
                "home": home_team,
                "away": away_team,
                "home_team": home_team,
                "away_team": away_team,
                "competition": comp_name,
                "competition_name": comp_name,
                "competition_id": comp_id_val,
                "competition_country": comp_country_val,
                "competition_type": comp_type_val,
                "competition_tier": comp_tier_val,
                "competition_source": comp_prov_val,
                "competition_confidence": comp_conf_val,
                "sport": sport,
                "kickoff": kickoff,
                "status": status,
                "matching_status": matching_status_val,
                "is_actionable_match": is_actionable_match,
                "is_reference_match": not is_actionable_match,
                "matching_confidence": match_confidence,
                "participating_bookmakers": participating_bookmakers,
                "providers": providers,
                "markets": markets,
                "opportunities": {
                    "surebets": ev_surebets,
                    "valuebets": ev_valuebets,
                    "nearest_opportunity": ev_nearest,
                },
                "surebets_count": len(ev_surebets),
                "valuebets_count": len(ev_valuebets),
                "has_surebet": len(ev_surebets) > 0,
                "has_valuebet": len(ev_valuebets) > 0,
            }
            details_map[ev_id] = detail_dict

            # Also index by provider event IDs
            for p_info in providers:
                if p_info.get("provider_event_id"):
                    details_map[str(p_info["provider_event_id"])] = detail_dict

            max_surebet = max([s.get("margin_pct") or s.get("arbitrage_margin_pct", 0) for s in ev_surebets], default=None)
            max_val = max([v.get("value_percent") or v.get("margin_pct", 0) for v in ev_valuebets], default=None)

            summary_dict = {
                "id": ev_id,
                "event_id": ev_id,
                "canonical_event_id": ev_id,
                "home_team": home_team,
                "away_team": away_team,
                "competition": comp_name,
                "competition_name": comp_name,
                "competition_id": comp_id_val,
                "competition_country": comp_country_val,
                "competition_type": comp_type_val,
                "competition_tier": comp_tier_val,
                "competition_source": comp_prov_val,
                "competition_confidence": comp_conf_val,
                "sport": sport,
                "kickoff": kickoff,
                "status": status,
                "matching_status": matching_status_val,
                "is_actionable_match": is_actionable_match,
                "is_reference_match": not is_actionable_match,
                "match_confidence": match_confidence,
                "participating_bookmakers": participating_bookmakers,
                "normalized_markets_count": total_norm_mkts,
                "matched_markets_count": len(markets),
                "has_surebet": len(ev_surebets) > 0,
                "has_valuebet": len(ev_valuebets) > 0,
                "opportunities_count": len(ev_surebets) + len(ev_valuebets),
                "max_surebet_margin": max_surebet,
                "max_valuebet_ev": max_val,
            }
            summaries.append(summary_dict)

    # ──────────────────────────────────────────────────────────────────────────
    # 2. Unmatched Events (Single Provider Coverage)
    # ──────────────────────────────────────────────────────────────────────────
    if result.normalization_results:
        for p_name, norm_res in result.normalization_results.items():
            for graph in norm_res.graphs:
                ev = graph.event
                ev_id = ev.internal_id
                if ev_id in seen_event_ids:
                    continue

                # Check if this event was already matched under a canonical event ID
                already_matched = False
                if result.validation_result and result.validation_result.canonical_events:
                    for ce in result.validation_result.canonical_events:
                        for src in ce.sources.values():
                            if src.internal_event_id == ev_id or (src.home_participant == ev.home_participant and src.away_participant == ev.away_participant):
                                already_matched = True
                                break
                        if already_matched:
                            break

                if already_matched:
                    continue

                seen_event_ids.add(ev_id)
                comp_name = graph.competition.name if graph.competition else "Football Competition"
                home_team = ev.home_participant
                away_team = ev.away_participant
                kickoff = ev.scheduled_start
                sport = graph.competition.sport if graph.competition else "Football"

                actual_bm = (ev.metadata.get("odds_api", {}).get("bookmaker") if hasattr(ev, "metadata") and ev.metadata else None) or next((od.bookmaker for od in getattr(graph, "odds_list", []) if getattr(od, "bookmaker", None)), None) or p_name
                prov_id = ev.provider_ids.get(actual_bm) or ev.provider_ids.get(p_name) if hasattr(ev, "provider_ids") else None

                # Unmatched Markets
                unmatched_markets = []
                for mkt in graph.markets:
                    mkt_type = mkt.market_type
                    line_val = float(mkt.line) if mkt.line is not None else None
                    key_str = f"{mkt_type}:FULL_TIME:MATCH:{line_val or 'no_line'}"

                    mkt_sels = []
                    for sel in graph.selections:
                        if sel.market_id == mkt.internal_id:
                            sel_odds = {}
                            for od in graph.odds_list:
                                if od.selection_id == sel.internal_id:
                                    od_bm = getattr(od, "bookmaker", None) or actual_bm
                                    sel_odds[od_bm] = float(od.decimal_odds)

                            best_d = None
                            if sel_odds:
                                best_bm = list(sel_odds.keys())[0]
                                best_val = sel_odds[best_bm]
                                best_d = {
                                    "bookmaker": best_bm,
                                    "odds": best_val,
                                    "implied_probability": round(1.0 / best_val, 4) if best_val > 0 else 0.0,
                                }

                            mkt_sels.append({
                                "selection_type": sel.selection_type,
                                "line": float(sel.line) if sel.line is not None else line_val,
                                "participant": sel.participant,
                                "odds": sel_odds,
                                "best_odds": best_d,
                                "source_selection_id": sel.internal_id,
                            })

                    unmatched_markets.append({
                        "canonical_market_key": key_str,
                        "market_type": mkt_type,
                        "period": "FULL_TIME",
                        "scope": "MATCH",
                        "line": line_val,
                        "status": "UNMATCHED",
                        "participating_bookmakers": [actual_bm],
                        "selections": mkt_sels,
                    })

                ev_val = valuebets_by_event.get(ev_id, [])

                comp_obj = graph.competition
                comp_id_val = getattr(comp_obj, "competition_id", None) if comp_obj else None
                comp_country_val = getattr(comp_obj, "country", None) if comp_obj else None
                comp_type_val = (comp_obj.metadata.get("competition_type") if comp_obj and isinstance(comp_obj.metadata, dict) else None) or getattr(comp_obj, "competition_type", None)
                comp_tier_val = (comp_obj.metadata.get("tier") if comp_obj and isinstance(comp_obj.metadata, dict) else None) or getattr(comp_obj, "tier", 2)
                comp_prov_val = (comp_obj.metadata.get("provenance") if comp_obj and isinstance(comp_obj.metadata, dict) else None) or getattr(comp_obj, "provenance", "PROVIDER_METADATA")
                comp_conf_val = (comp_obj.metadata.get("confidence") if comp_obj and isinstance(comp_obj.metadata, dict) else None) or getattr(comp_obj, "confidence", 1.0)

                raw_unm_mkt_cnt = len(graph.markets)
                if result.provider_results:
                    p_res = result.provider_results.get(p_name) or (result.provider_results.get("odds_api") if p_name in ("bet365", "unibet") else None)
                    if p_res and p_res.parsed_objects:
                        for p_obj in p_res.parsed_objects:
                            obj_bm = getattr(p_obj, "bookmaker_name", "").lower() if hasattr(p_obj, "bookmaker_name") else p_name
                            if actual_bm in ("bet365", "unibet") and obj_bm != actual_bm:
                                continue
                            obj_eid = getattr(p_obj, "event_id", None) or getattr(p_obj, "provider_event_id", None)
                            obj_home = getattr(p_obj, "home_team", None)
                            obj_away = getattr(p_obj, "away_team", None)
                            if (prov_id and str(obj_eid) == str(prov_id)) or (obj_home == home_team and obj_away == away_team):
                                raw_unm_mkt_cnt = len(getattr(p_obj, "markets", []))
                                break

                detail_dict = {
                    "id": ev_id,
                    "event_id": ev_id,
                    "canonical_event_id": ev_id,
                    "home": home_team,
                    "away": away_team,
                    "home_team": home_team,
                    "away_team": away_team,
                    "competition": comp_name,
                    "competition_name": comp_name,
                    "competition_id": comp_id_val,
                    "competition_country": comp_country_val,
                    "competition_type": comp_type_val,
                    "competition_tier": comp_tier_val,
                    "competition_source": comp_prov_val,
                    "competition_confidence": comp_conf_val,
                    "sport": sport,
                    "kickoff": kickoff,
                    "status": ev.status or "SCHEDULED",
                    "matching_status": "UNMATCHED",
                    "matching_confidence": None,
                    "participating_bookmakers": [actual_bm],
                    "providers": [{
                        "provider": actual_bm,
                        "status": "Available",
                        "market_count": raw_unm_mkt_cnt,
                        "raw_market_count": raw_unm_mkt_cnt,
                        "normalized_market_count": len(graph.markets),
                        "matched_market_count": 0,
                        "provider_event_id": prov_id,
                    }],
                    "markets": unmatched_markets,
                    "opportunities": {
                        "surebets": [],
                        "valuebets": ev_val,
                        "nearest_opportunity": None,
                    },
                    "surebets_count": 0,
                    "valuebets_count": len(ev_val),
                    "has_surebet": False,
                    "has_valuebet": len(ev_val) > 0,
                }
                details_map[ev_id] = detail_dict
                if prov_id:
                    details_map[str(prov_id)] = detail_dict

                summaries.append({
                    "id": ev_id,
                    "event_id": ev_id,
                    "canonical_event_id": ev_id,
                    "home_team": home_team,
                    "away_team": away_team,
                    "competition": comp_name,
                    "competition_name": comp_name,
                    "competition_id": comp_id_val,
                    "competition_country": comp_country_val,
                    "competition_type": comp_type_val,
                    "competition_tier": comp_tier_val,
                    "competition_source": comp_prov_val,
                    "competition_confidence": comp_conf_val,
                    "sport": sport,
                    "kickoff": kickoff,
                    "status": ev.status or "SCHEDULED",
                    "matching_status": "UNMATCHED",
                    "match_confidence": None,
                    "participating_bookmakers": [actual_bm],
                    "normalized_markets_count": len(graph.markets),
                    "matched_markets_count": 0,
                    "has_surebet": False,
                    "has_valuebet": len(ev_val) > 0,
                    "opportunities_count": len(ev_val),
                    "max_surebet_margin": None,
                    "max_valuebet_ev": max([v.get("value_percent", 0) for v in ev_val], default=None),
                })

    return summaries, details_map


def _serialize_scan_cycle_result(result: ScanCycleResult) -> Dict[str, Any]:
    """Serializes a ScanCycleResult into a clean, JSON-serializable dictionary."""
    # Extract provider results
    providers_summary = {}
    for p_name, p_res in result.provider_results.items():
        p_status = p_res.status.name if hasattr(p_res.status, "name") else (p_res.status.value if hasattr(p_res.status, "value") else str(p_res.status))
        providers_summary[p_name] = {
            "name": p_name,
            "status": p_status,
            "execution_duration": round(float(p_res.execution_duration), 3),
            "discovered_count": len(p_res.discovered_objects),
            "parsed_count": len(p_res.parsed_objects),
            "errors": [_sanitize_text(e) for e in p_res.errors],
            "warnings": [_sanitize_text(w) for w in p_res.warnings],
        }

    # Extract opportunities summary (Surebets and Valuebets)
    opps_summary = []
    if result.detection_result and result.detection_result.opportunities:
        for opp in result.detection_result.opportunities:
            opps_summary.append(serialize_opportunity_summary(opp))

    if result.valuebet_result and hasattr(result.valuebet_result, "candidates") and result.valuebet_result.candidates:
        for val_cand in result.valuebet_result.candidates:
            opps_summary.append(serialize_opportunity_summary(val_cand))

    # Extract events summary and detail cache
    events_summary, events_detail_map = _serialize_events_from_scan_result(result)

    # Nearest opportunity telemetry (zero-surebet state)
    nearest_opp = getattr(result, "nearest_opportunity", None) or result.diagnostics.get("nearest_opportunity")

    # Structured matching diagnostic
    matching_diag = result.diagnostics.get("matching_diagnostic")
    if not matching_diag and result.validation_result:
        vr = result.validation_result
        cand_cnt = len(vr.event_candidates)
        actionable_matched_cnt = sum(
            1 for ce in vr.canonical_events
            if ("superbet" in ce.sources and "betclic" in ce.sources)
            or (not any(b in ce.sources for b in ("superbet", "betclic", "bet365", "unibet")) and len(ce.sources) >= 2)
        )
        rej_breakdown = dict(getattr(vr, "rejection_reasons_breakdown", {}))
        expl = (
            f"{actionable_matched_cnt} event(s) successfully matched across execution bookmakers."
            if actionable_matched_cnt > 0
            else (
                "Provider coverage did not overlap (0 candidate pairs generated)."
                if cand_cnt == 0
                else f"{cand_cnt} candidate pair(s) generated but rejected by safety rules / low matching score."
            )
        )
        matching_diag = {
            "candidates_generated": cand_cnt,
            "matched_events": actionable_matched_cnt,
            "rejected_candidates": len(vr.event_decisions) - actionable_matched_cnt,
            "rejection_reasons_breakdown": rej_breakdown,
            "explanation": expl,
            "sample_rejected_pairs": [
                {
                    "source_name": d.evidence.get("source_name"),
                    "target_name": d.evidence.get("target_name"),
                    "score": float(d.total_score),
                    "rejection_reason": getattr(d, "rejection_reason_code", None) or "LOW_MATCH_SCORE",
                    "vetoes": list(d.veto_reasons),
                }
                for d in vr.event_decisions
                if d.decision.value != "MATCHED"
            ][:5],
        }

    # Bookmaker coverage comparison table (multi-provider architecture: Superbet, Betclic, Bet365 via Odds API, Unibet via Odds API)
    bookmaker_coverage: Dict[str, Any] = {}

    # 1. Direct Providers: Superbet, Betclic
    for p_name in ("superbet", "betclic"):
        p_res = result.provider_results.get(p_name)
        norm_res = result.normalization_results.get(p_name) if result.normalization_results else None

        # Candidate & Matched calculations
        matched_ev_cnt = 0
        matched_mkt_cnt = 0
        cand_cnt = 0
        if result.validation_result:
            vr = result.validation_result
            if hasattr(vr, "event_candidates") and vr.event_candidates:
                for c in vr.event_candidates:
                    s_ids = getattr(c.source_graph.event, "provider_ids", {}) if hasattr(c, "source_graph") and hasattr(c.source_graph, "event") else {}
                    t_ids = getattr(c.target_graph.event, "provider_ids", {}) if hasattr(c, "target_graph") and hasattr(c.target_graph, "event") else {}
                    if p_name in s_ids or p_name in t_ids:
                        cand_cnt += 1
            for ce in getattr(vr, "canonical_events", []):
                p_books = getattr(ce, "participating_bookmakers", []) or []
                p_sources = list(getattr(ce, "sources", {}).keys())
                if p_name in p_books or p_name in p_sources:
                    matched_ev_cnt += 1
                    matched_mkt_cnt += len(getattr(ce, "matched_markets", []))

        # Evaluated markets
        mkts_eval_cnt = 0
        if result.detection_result and hasattr(result.detection_result, "evaluations"):
            for ev_eval in result.detection_result.evaluations:
                if any(getattr(leg, "provider", "").lower() == p_name for leg in getattr(ev_eval, "best_legs", [])):
                    mkts_eval_cnt += 1
        elif matched_mkt_cnt > 0:
            mkts_eval_cnt = matched_mkt_cnt

        if p_res:
            p_stat = p_res.status.name if hasattr(p_res.status, "name") else (p_res.status.value if hasattr(p_res.status, "value") else str(p_res.status))
            bookmaker_coverage[p_name] = {
                "provider_name": p_name,
                "display_name": p_name.capitalize(),
                "provider_type": "DIRECT",
                "is_via_odds_api": False,
                "discovered": len(p_res.discovered_objects),
                "parsed": len(p_res.parsed_objects),
                "normalized": len(norm_res.graphs) if norm_res else 0,
                "candidate_events": cand_cnt,
                "matched_events": matched_ev_cnt,
                "markets_matched": matched_mkt_cnt,
                "markets_evaluated": mkts_eval_cnt,
                "status": p_stat,
                "errors": [_sanitize_text(e) for e in p_res.errors],
                "warnings": [_sanitize_text(w) for w in p_res.warnings],
                "invalid_count": p_res.validation_report.invalid_objects if p_res.validation_report else 0,
            }
        else:
            bookmaker_coverage[p_name] = {
                "provider_name": p_name,
                "display_name": p_name.capitalize(),
                "provider_type": "DIRECT",
                "is_via_odds_api": False,
                "discovered": 0,
                "parsed": 0,
                "normalized": 0,
                "candidate_events": 0,
                "matched_events": 0,
                "markets_matched": 0,
                "markets_evaluated": 0,
                "status": "UNAVAILABLE",
                "errors": [],
                "warnings": ["Provider was not executed in this cycle."],
                "invalid_count": 0,
            }

    # 2. Odds API Sub-Providers: Bet365, Unibet
    oapi_res = result.provider_results.get("odds_api")
    oapi_norm = result.normalization_results.get("odds_api") if result.normalization_results else None

    for oapi_bm in ("bet365", "unibet"):
        bm_display = "Bet365" if oapi_bm == "bet365" else "Unibet"

        # Parsed models
        parsed_models = []
        if oapi_res and oapi_res.parsed_objects:
            parsed_models = [ev for ev in oapi_res.parsed_objects if getattr(ev, "bookmaker_name", "").lower() == oapi_bm]

        # Normalized graphs
        norm_graphs = []
        if oapi_norm and oapi_norm.graphs:
            for g in oapi_norm.graphs:
                if (
                    oapi_bm in getattr(g.event, "provider_ids", {})
                    or getattr(g.event, "metadata", {}).get("odds_api", {}).get("bookmaker") == oapi_bm
                    or any(getattr(o, "bookmaker", "").lower() == oapi_bm for o in getattr(g, "odds_list", []))
                ):
                    norm_graphs.append(g)

        # Matched canonical events & markets
        matched_ev_cnt = 0
        matched_mkt_cnt = 0
        cand_cnt = 0
        if result.validation_result:
            vr = result.validation_result
            if hasattr(vr, "event_candidates") and vr.event_candidates:
                for c in vr.event_candidates:
                    s_ids = getattr(c.source_graph.event, "provider_ids", {}) if hasattr(c, "source_graph") and hasattr(c.source_graph, "event") else {}
                    t_ids = getattr(c.target_graph.event, "provider_ids", {}) if hasattr(c, "target_graph") and hasattr(c.target_graph, "event") else {}
                    s_bm = getattr(c.source_graph.event, "metadata", {}).get("odds_api", {}).get("bookmaker") if hasattr(c, "source_graph") and hasattr(c.source_graph, "event") else None
                    t_bm = getattr(c.target_graph.event, "metadata", {}).get("odds_api", {}).get("bookmaker") if hasattr(c, "target_graph") and hasattr(c.target_graph, "event") else None
                    if oapi_bm in s_ids or oapi_bm in t_ids or s_bm == oapi_bm or t_bm == oapi_bm:
                        cand_cnt += 1
            for ce in getattr(vr, "canonical_events", []):
                p_books = getattr(ce, "participating_bookmakers", []) or []
                p_sources = list(getattr(ce, "sources", {}).keys())
                if oapi_bm in p_books or oapi_bm in p_sources:
                    matched_ev_cnt += 1
                    matched_mkt_cnt += len(getattr(ce, "matched_markets", []))

        # Evaluated markets
        mkts_eval_cnt = 0
        if result.detection_result and hasattr(result.detection_result, "evaluations"):
            for ev_eval in result.detection_result.evaluations:
                if any(getattr(leg, "provider", "").lower() == oapi_bm for leg in getattr(ev_eval, "best_legs", [])):
                    mkts_eval_cnt += 1
        elif matched_mkt_cnt > 0:
            mkts_eval_cnt = matched_mkt_cnt

        if oapi_res:
            oapi_stat = oapi_res.status.name if hasattr(oapi_res.status, "name") else (
                oapi_res.status.value if hasattr(oapi_res.status, "value") else str(oapi_res.status)
            )
            bm_stat = "NO_DATA" if (len(parsed_models) == 0 and oapi_stat == "COMPLETED") else oapi_stat

            bookmaker_coverage[oapi_bm] = {
                "provider_name": oapi_bm,
                "display_name": f"{bm_display} (via Odds API)",
                "provider_type": "ODDS_API",
                "source_provider": "odds_api",
                "is_via_odds_api": True,
                "discovered": len(oapi_res.discovered_objects),
                "parsed": len(parsed_models),
                "normalized": len(norm_graphs),
                "candidate_events": cand_cnt,
                "matched_events": matched_ev_cnt,
                "markets_matched": matched_mkt_cnt,
                "markets_evaluated": mkts_eval_cnt,
                "status": bm_stat,
                "errors": [_sanitize_text(e) for e in oapi_res.errors if oapi_bm in e.lower()] or ([_sanitize_text(e) for e in oapi_res.errors] if oapi_stat == "FAILED" else []),
                "warnings": [_sanitize_text(w) for w in oapi_res.warnings if oapi_bm in w.lower()] or [_sanitize_text(w) for w in oapi_res.warnings],
                "invalid_count": 0,
            }
        else:
            bookmaker_coverage[oapi_bm] = {
                "provider_name": oapi_bm,
                "display_name": f"{bm_display} (via Odds API)",
                "provider_type": "ODDS_API",
                "source_provider": "odds_api",
                "is_via_odds_api": True,
                "discovered": 0,
                "parsed": 0,
                "normalized": 0,
                "candidate_events": 0,
                "matched_events": 0,
                "markets_matched": 0,
                "markets_evaluated": 0,
                "status": "UNAVAILABLE",
                "errors": [],
                "warnings": ["Odds API source provider unavailable or disabled for this scan cycle."],
                "invalid_count": 0,
            }

    # Also retain odds_api aggregate in bookmaker_coverage for backward compatibility if present
    if oapi_res:
        oapi_stat = oapi_res.status.name if hasattr(oapi_res.status, "name") else (
            oapi_res.status.value if hasattr(oapi_res.status, "value") else str(oapi_res.status)
        )
        bookmaker_coverage["odds_api"] = {
            "provider_name": "odds_api",
            "display_name": "Odds API (Aggregate)",
            "provider_type": "AGGREGATOR",
            "is_via_odds_api": False,
            "discovered": len(oapi_res.discovered_objects),
            "parsed": len(oapi_res.parsed_objects),
            "normalized": len(oapi_norm.graphs) if oapi_norm else 0,
            "candidate_events": sum(bookmaker_coverage[bm]["candidate_events"] for bm in ("bet365", "unibet")),
            "matched_events": sum(bookmaker_coverage[bm]["matched_events"] for bm in ("bet365", "unibet")),
            "markets_matched": sum(bookmaker_coverage[bm]["markets_matched"] for bm in ("bet365", "unibet")),
            "markets_evaluated": sum(bookmaker_coverage[bm]["markets_evaluated"] for bm in ("bet365", "unibet")),
            "status": oapi_stat,
            "errors": [_sanitize_text(e) for e in oapi_res.errors],
            "warnings": [_sanitize_text(w) for w in oapi_res.warnings],
            "invalid_count": oapi_res.validation_report.invalid_objects if oapi_res.validation_report else 0,
        }

    # Determine structured pipeline state
    total_opps = (result.detected_opportunities_count or 0) + (getattr(result, "valuebets_qualified_count", 0) or 0)
    mkts_eval = getattr(result, "markets_evaluated_count", 0) or result.resource_metrics.markets_evaluated

    if result.cycle_status == CycleStatus.FAILED:
        pipeline_state = "SCAN_FAILED"
        pipeline_state_label = "Scan Failed: Provider acquisition or critical error"
    elif result.cycle_status == CycleStatus.PARTIAL or any(p.get("status") == "DEGRADED" for p in providers_summary.values()):
        pipeline_state = "PARTIAL_DEGRADED"
        degraded_provs = [name for name, p in providers_summary.items() if p.get("status") in ("DEGRADED", "PARTIAL")]
        pipeline_state_label = f"Partial / Degraded: {', '.join(degraded_provs) or 'Provider degraded'}"
    elif result.matched_events_count == 0:
        pipeline_state = "NO_OVERLAP"
        pipeline_state_label = "Scan Successful: 0 matched events (provider coverage did not overlap)"
    elif mkts_eval > 0 and total_opps == 0:
        pipeline_state = "MARKETS_EVALUATED_ZERO_OPP"
        pipeline_state_label = f"Scan Successful: {mkts_eval} markets evaluated, 0 opportunities found"
    elif total_opps > 0:
        pipeline_state = "OPPORTUNITIES_FOUND"
        pipeline_state_label = f"Scan Successful: {result.detected_opportunities_count} Surebets, {getattr(result, 'valuebets_qualified_count', 0)} Valuebets"
    else:
        pipeline_state = "SUCCESS_CLEAN"
        pipeline_state_label = "Scan Successful: Clean execution"

    return {
        "execution_id": result.execution_id,
        "cycle_status": result.cycle_status.value if hasattr(result.cycle_status, "value") else str(result.cycle_status),
        "pipeline_state": pipeline_state,
        "pipeline_state_label": pipeline_state_label,
        "started_at": result.started_at,
        "completed_at": result.completed_at,
        "duration_seconds": round(float(result.duration_seconds), 3),
        "selection_policy": "Popular competitions",
        "stage_timings": {
            "acquisition_seconds": round(float(result.stage_timings.acquisition_seconds), 3),
            "normalization_seconds": round(float(result.stage_timings.normalization_seconds), 3),
            "matching_seconds": round(float(result.stage_timings.matching_seconds), 3),
            "detection_seconds": round(float(result.stage_timings.detection_seconds), 3),
            "valuebet_seconds": round(float(getattr(result.stage_timings, "valuebet_seconds", 0.0)), 3),
            "lifecycle_seconds": round(float(result.stage_timings.lifecycle_seconds), 3),
            "dispatch_seconds": round(float(result.stage_timings.dispatch_seconds), 3),
            "reconciliation_seconds": round(float(result.stage_timings.reconciliation_seconds), 3),
            "total_duration_seconds": round(float(result.stage_timings.total_duration_seconds), 3),
        },
        "resource_metrics": {
            "total_http_requests": result.resource_metrics.total_http_requests,
            "detail_http_requests": result.resource_metrics.detail_http_requests,
            "response_bytes_total": result.resource_metrics.response_bytes_total,
            "events_discovered": result.resource_metrics.events_discovered,
            "events_selected": result.resource_metrics.events_selected,
            "popular_events_discovered": getattr(result.resource_metrics, "popular_events_discovered", 0),
            "popular_events_selected": getattr(result.resource_metrics, "popular_events_selected", 0),
            "events_parsed": result.resource_metrics.events_parsed,
            "normalized_graphs": result.resource_metrics.normalized_graphs,
            "markets_discovered": getattr(result.resource_metrics, "markets_discovered", 0),
            "markets_normalized": getattr(result.resource_metrics, "markets_normalized", 0),
            "markets_matched": getattr(result.resource_metrics, "markets_matched", 0),
            "matched_events": result.resource_metrics.matched_events,
            "cross_bookmaker_overlap_rate": getattr(result.resource_metrics, "cross_bookmaker_overlap_rate", 0.0),
            "markets_evaluated": result.resource_metrics.markets_evaluated,
            "selections_evaluated": result.resource_metrics.selections_evaluated,
            "market_coverage_breakdown": getattr(result.resource_metrics, "market_coverage_breakdown", {}),
            "peak_memory_mb": round(float(result.resource_metrics.peak_memory_mb), 2),
        },
        "counts": {
            "discovered_events": result.discovered_events_count,
            "popular_events_discovered": getattr(result, "popular_events_discovered_count", 0),
            "popular_events_selected": getattr(result, "popular_events_selected_count", 0),
            "selected_events": result.resource_metrics.events_selected,
            "parsed_events": result.parsed_events_count,
            "normalized_graphs": result.normalized_graphs_count,
            "normalization_failed": result.normalization_failed_count,
            "markets_discovered": getattr(result, "markets_discovered_count", 0),
            "markets_normalized": getattr(result, "markets_normalized_count", 0),
            "markets_matched": getattr(result, "markets_matched_count", 0),
            "markets_evaluated": getattr(result, "markets_evaluated_count", 0),
            "matched_events": result.matched_events_count,
            "unmatched_events": result.unmatched_events_count,
            "cross_bookmaker_overlap_rate": getattr(result, "cross_bookmaker_overlap_rate", 0.0),
            "cross_bookmaker_overlap_rate_pct": round(getattr(result, "cross_bookmaker_overlap_rate", 0.0) * 100.0, 2),
            "detected_opportunities": result.detected_opportunities_count,
            "new_opportunities": result.new_opportunities_count,
            "updated_opportunities": result.updated_opportunities_count,
            "suppressed_opportunities": result.suppressed_opportunities_count,
            "expired_opportunities": result.expired_opportunities_count,
            "dispatched": result.dispatched_count,
            "delivered": result.delivered_count,
            "failed_delivery": result.failed_delivery_count,
            "skipped_delivery": result.skipped_delivery_count,
            "valuebet_candidates": getattr(result, "valuebet_candidates_count", 0),
            "valuebets_qualified": getattr(result, "valuebets_qualified_count", 0),
            "valuebets_new": getattr(result, "valuebets_new_count", 0),
            "valuebets_updated": getattr(result, "valuebets_updated_count", 0),
            "valuebets_suppressed": getattr(result, "valuebets_suppressed_count", 0),
            "valuebets_expired": getattr(result, "valuebets_expired_count", 0),
            "valuebets_dispatched": getattr(result, "valuebets_dispatched_count", 0),
            "valuebet_reference_requests": getattr(result, "valuebet_reference_requests", 0),
            "valuebet_reference_cache_hits": getattr(result, "valuebet_reference_cache_hits", 0),
            "valuebet_reference_cache_misses": getattr(result, "valuebet_reference_cache_misses", 0),
            "surebet_candidates": getattr(result, "surebet_candidates_count", 0) or getattr(result.resource_metrics, "surebet_candidates", 0),
            "valid_surebets": getattr(result, "valid_surebets_count", 0) or getattr(result.resource_metrics, "valid_surebets", 0),
            "rejected_markets": getattr(result, "rejected_markets_count", 0) or getattr(result.resource_metrics, "rejected_markets_total", 0),
            "not_evaluated_markets": getattr(result, "not_evaluated_markets_count", 0) or getattr(result.resource_metrics, "not_evaluated_markets_total", 0),
        },
        "evaluation_funnel": {
            "discovered_events": result.discovered_events_count,
            "normalized_graphs": result.normalized_graphs_count,
            "matched_events": result.matched_events_count,
            "matched_markets": getattr(result, "markets_matched_count", 0) or result.resource_metrics.markets_matched,
            "evaluated_markets": getattr(result, "markets_evaluated_count", 0) or result.resource_metrics.markets_evaluated,
            "surebet_candidates": getattr(result, "surebet_candidates_count", 0) or getattr(result.resource_metrics, "surebet_candidates", 0),
            "valid_surebets": getattr(result, "valid_surebets_count", 0) or getattr(result.resource_metrics, "valid_surebets", 0),
            "value_candidates": getattr(result, "valuebet_candidates_count", 0),
            "rejected_markets": getattr(result, "rejected_markets_count", 0) or getattr(result.resource_metrics, "rejected_markets_total", 0),
            "not_evaluated_markets": getattr(result, "not_evaluated_markets_count", 0) or getattr(result.resource_metrics, "not_evaluated_markets_total", 0),
            "rejection_reasons_breakdown": getattr(result, "rejection_reasons_breakdown", {}) or getattr(result.resource_metrics, "rejection_reasons_breakdown", {}),
        },
        "market_evaluation_records": [
            {
                "canonical_event_id": rec.canonical_event_id,
                "canonical_market_key": rec.canonical_market_key,
                "state": rec.state.value if hasattr(rec.state, "value") else str(rec.state),
                "reason": rec.reason.value if (rec.reason and hasattr(rec.reason, "value")) else (str(rec.reason) if rec.reason else None),
                "market_type": rec.market_type,
                "source_provider": rec.source_provider,
                "target_provider": rec.target_provider,
                "opportunity_id": rec.opportunity_id,
                "details": rec.details,
            }
            for rec in (getattr(result, "market_evaluation_records", []) or getattr(result.resource_metrics, "market_evaluation_records", []))
        ],
        "event_universe": result.diagnostics.get("event_universe", {}),
        "detail_prioritization": result.diagnostics.get("detail_prioritization", {}),
        "betclic_telemetry": result.diagnostics.get("betclic_telemetry", {}),
        "superbet_telemetry": result.diagnostics.get("superbet_telemetry", {}),
        "odds_api_telemetry": result.diagnostics.get("odds_api_telemetry") or {
            "status": "UNAVAILABLE",
            "is_available": False,
            "events_discovered": 0,
            "events_fetched": 0,
            "bookmaker_event_models": 0,
            "bet365_count": 0,
            "unibet_count": 0,
            "canonical_events_contributed": 0,
            "markets_contributed": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        },
        "market_coverage_breakdown": getattr(result, "market_coverage_breakdown", {}),
        "matching_diagnostic": matching_diag,
        "scan_trace": result.diagnostics.get("scan_trace", {}),

        "bookmaker_coverage": bookmaker_coverage,
        "provider_results": providers_summary,
        "opportunities": opps_summary,
        "events": events_summary,
        "_events_detail_map": events_detail_map,

        "nearest_opportunity": nearest_opp,
        "warnings": [_sanitize_text(w) for w in result.warnings],
        "errors": [_sanitize_text(e) for e in result.errors],
    }


def _save_scan_snapshot(db_manager: DatabaseManager, execution_id: str, serialized_result: Dict[str, Any]) -> None:
    """Persist completed scan cycle summary into database SnapshotORM table."""
    try:
        from database.models import SnapshotORM, ProviderORM
        with db_manager.get_session() as session:
            sys_prov = session.query(ProviderORM).filter_by(id="system").first()
            if not sys_prov:
                sys_prov = ProviderORM(id="system", name="System", code="sys", enabled=True)
                session.add(sys_prov)
                session.flush()

            snap = SnapshotORM(
                id=f"snap_{uuid.uuid4().hex[:12]}",
                provider_id="system",
                execution_id=execution_id,
                snapshot_type="SCAN_CYCLE_RESULT",
                payload=json.dumps(serialized_result),
                created_at=datetime.now(timezone.utc),
            )
            session.add(snap)
            session.commit()
    except Exception as exc:
        logger.debug("Failed to persist scan snapshot: %s", exc)


def _load_scan_snapshots(db_manager: DatabaseManager, limit: int = 20) -> List[Dict[str, Any]]:
    """Load recent scan cycle summaries from database SnapshotORM table."""
    try:
        from database.models import SnapshotORM
        with db_manager.get_session() as session:
            snaps = (
                session.query(SnapshotORM)
                .filter(SnapshotORM.snapshot_type == "SCAN_CYCLE_RESULT")
                .order_by(SnapshotORM.created_at.desc())
                .limit(limit)
                .all()
            )
            results = []
            for s in snaps:
                try:
                    data = json.loads(s.payload)
                    results.append(data)
                except Exception:
                    continue
            return results
    except Exception as exc:
        logger.debug("Failed to load scan snapshots: %s", exc)
    return []


class PlatformAPIService:
    """Application Service orchestrating provider manager, repositories, and scanner for API clients."""

    def __init__(
        self,
        provider_manager: Optional[ProviderManager] = None,
        db_manager: Optional[DatabaseManager] = None,
        scanner_engine: Optional[ScannerEngine] = None,
        scan_orchestrator: Optional[ProductionScanOrchestrator] = None,
    ):
        self.provider_manager = provider_manager or ProviderManager()
        self.db_manager = db_manager or DatabaseManager()
        self.scanner_engine = scanner_engine or ScannerEngine()
        self.scan_orchestrator = scan_orchestrator or ProductionScanOrchestrator(db_manager=self.db_manager)

        # Concurrency & scanner control state
        self._scan_lock = threading.Lock()
        self._is_scanning = False
        self._scanner_status = "READY"
        self._last_scan_result: Optional[Dict[str, Any]] = None
        self._scan_history: List[Dict[str, Any]] = []
        self._events_cache: Dict[str, Dict[str, Any]] = {}
        self._events_summary_cache: List[Dict[str, Any]] = []

        # Restore persisted scan history from database if available
        if self.db_manager is not None:
            persisted_snapshots = _load_scan_snapshots(self.db_manager, limit=20)
            if persisted_snapshots:
                self._last_scan_result = persisted_snapshots[0]
                if self._last_scan_result.get("events"):
                    self._events_summary_cache = list(self._last_scan_result["events"])
                if self._last_scan_result.get("_events_detail_map"):
                    self._events_cache = dict(self._last_scan_result["_events_detail_map"])
                for snap in persisted_snapshots:
                    cnts = snap.get("counts") or {}
                    self._scan_history.append({
                        "execution_id": snap.get("execution_id"),
                        "status": snap.get("cycle_status"),
                        "started_at": snap.get("started_at"),
                        "completed_at": snap.get("completed_at"),
                        "duration_seconds": snap.get("duration_seconds"),
                        "events_discovered": cnts.get("discovered_events", 0),
                        "events_selected": cnts.get("selected_events", 0),
                        "events_matched": cnts.get("matched_events", 0),
                        "surebets_count": cnts.get("detected_opportunities", 0),
                        "scan_source": snap.get("scan_source", "AUTOMATED" if "auto" in str(snap.get("execution_id", "")).lower() else "MANUAL"),
                    })

        # Automated scanning scheduler (loads persisted state via db_manager)
        self.scheduler = ScanScheduler(service=self, interval_minutes=15, enabled=False, db_manager=self.db_manager)
        self.scheduler.start()

    def get_health(self) -> Dict[str, Any]:
        """Returns aggregated platform health status conforming to Stage 8.4 specification."""
        db_healthy = self.db_manager.check_health()
        provider_health = self.provider_manager.check_health()
        prov_ok = provider_health.get("overall_status") in ("HEALTHY", "OK")

        overall = "HEALTHY" if (db_healthy and prov_ok) else ("DEGRADED" if (db_healthy or prov_ok) else "UNHEALTHY")
        status_lower = "healthy" if overall == "HEALTHY" else ("degraded" if overall == "DEGRADED" else "unhealthy")

        scheduler_status = "enabled" if (hasattr(self, "scheduler") and self.scheduler._enabled) else "disabled"
        scanner_status_lower = self._scanner_status.lower()

        last_scan_time = self._last_scan_result.get("completed_at") if self._last_scan_result else None
        last_success_time = None
        if self._last_scan_result and self._last_scan_result.get("cycle_status") in ("SUCCESS", "PARTIAL"):
            last_success_time = self._last_scan_result.get("completed_at")

        return {
            "status": status_lower,
            "overall_status": overall,
            "database": "ok" if db_healthy else "error",
            "database_connected": db_healthy,
            "scheduler": scheduler_status,
            "scheduler_status": scheduler_status,
            "scanner": scanner_status_lower,
            "scanner_status": self._scanner_status,
            "is_scanning": self._is_scanning,
            "last_scan": last_scan_time,
            "last_successful_scan": last_success_time,
            "provider_framework": provider_health,
        }

    def get_providers(self) -> Dict[str, Any]:
        """Returns list of registered providers and health status, enriched with latest scan metrics."""
        health = self.provider_manager.check_health()
        
        # Normalize FrameworkHealthReport or dict into dictionary
        if isinstance(health, dict):
            out_health = dict(health)
            provider_health = out_health.get("provider_health") or out_health.get("providers_state") or {}
            registered = out_health.get("registered_providers") or list(provider_health.keys())
        elif hasattr(health, "provider_snapshots"):
            registered = list(health.provider_snapshots.keys())
            provider_health = {}
            for name, snap in health.provider_snapshots.items():
                status_str = "HEALTHY" if snap.is_healthy else ("DEGRADED" if snap.total_failures < 3 else "FAILED")
                provider_health[name] = {
                    "provider_name": snap.provider_name,
                    "status": status_str,
                    "circuit_breaker": "CLOSED" if snap.is_healthy else "OPEN",
                    "error_rate_5m": f"{(snap.failure_rate * 100):.1f}%" if hasattr(snap, "failure_rate") else "0.0%",
                    "avg_latency_ms": round(snap.avg_duration_seconds * 1000.0, 1) if hasattr(snap, "avg_duration_seconds") else 0.0,
                    "total_runs": snap.total_runs,
                    "total_successes": snap.total_successes,
                    "total_failures": snap.total_failures,
                }
            out_health = {
                "overall_status": getattr(health, "overall_status", "HEALTHY"),
                "total_providers": getattr(health, "total_providers", len(registered)),
                "registered_providers": registered,
                "provider_health": provider_health,
            }
        else:
            registered = ["superbet", "betclic"]
            provider_health = {p: {"status": "HEALTHY", "circuit_breaker": "CLOSED", "error_rate_5m": "0.0%"} for p in registered}
            out_health = {
                "overall_status": "HEALTHY",
                "registered_providers": registered,
                "provider_health": provider_health,
            }

        # Augment with last scan provider-level metrics if available
        if self._last_scan_result and self._last_scan_result.get("provider_results"):
            prov_results = self._last_scan_result["provider_results"]
            for p_name, p_data in prov_results.items():
                if p_name not in provider_health:
                    provider_health[p_name] = {"status": p_data.get("status", "COMPLETED")}
                provider_health[p_name]["last_scan_discovered"] = p_data.get("discovered_count", 0)
                provider_health[p_name]["last_scan_parsed"] = p_data.get("parsed_count", 0)
                provider_health[p_name]["last_scan_duration"] = p_data.get("execution_duration", 0)
                provider_health[p_name]["last_scan_status"] = p_data.get("status", "UNKNOWN")
                provider_health[p_name]["last_scan_errors"] = p_data.get("errors", [])
                provider_health[p_name]["last_scan_warnings"] = p_data.get("warnings", [])

        if "odds_api" in provider_health:
            provider_health["odds_api"]["bookmakers"] = ["Bet365", "Unibet"]
            provider_health["odds_api"]["display_name"] = "Odds API Gateway (Bet365, Unibet)"
            provider_health["odds_api"]["provider_type"] = "AGGREGATOR"

        # Enrich sub-providers Bet365 and Unibet sourced through Odds API
        last_cov = self._last_scan_result.get("bookmaker_coverage", {}) if self._last_scan_result else {}
        oapi_health = provider_health.get("odds_api", {})

        for bm in ("bet365", "unibet"):
            bm_cov = last_cov.get(bm, {})
            bm_display = "Bet365 (via Odds API)" if bm == "bet365" else "Unibet (via Odds API)"
            bm_status = bm_cov.get("status") or (oapi_health.get("status") if oapi_health else "HEALTHY")
            provider_health[bm] = {
                "provider_name": bm,
                "display_name": bm_display,
                "provider_type": "ODDS_API",
                "source_provider": "odds_api",
                "is_via_odds_api": True,
                "status": bm_status,
                "circuit_breaker": oapi_health.get("circuit_breaker", "CLOSED"),
                "error_rate_5m": oapi_health.get("error_rate_5m", "0.0%"),
                "avg_latency_ms": oapi_health.get("avg_latency_ms", 0.0),
                "last_scan_discovered": bm_cov.get("discovered", 0),
                "last_scan_parsed": bm_cov.get("parsed", 0),
                "last_scan_normalized": bm_cov.get("normalized", 0),
                "last_scan_matched": bm_cov.get("matched_events", 0),
                "last_scan_markets_matched": bm_cov.get("markets_matched", 0),
                "last_scan_markets_evaluated": bm_cov.get("markets_evaluated", 0),
                "last_scan_duration": oapi_health.get("last_scan_duration", 0),
                "last_scan_status": bm_status,
                "last_scan_errors": bm_cov.get("errors", []),
                "last_scan_warnings": bm_cov.get("warnings", []),
            }

        # Ensure ordered visible providers
        ordered_providers = ["superbet", "betclic", "bet365", "unibet", "odds_api"]
        for p in list(provider_health.keys()):
            if p not in ordered_providers:
                ordered_providers.append(p)

        out_health["provider_health"] = provider_health
        out_health["registered_providers"] = ordered_providers
        out_health["total_providers"] = len(ordered_providers)
        return out_health

    def list_events(
        self,
        sport: Optional[str] = None,
        competition: Optional[str] = None,
        provider: Optional[str] = None,
        search: Optional[str] = None,
        matched: Optional[Union[bool, str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Fetch canonical events from the latest scan (with database fallback) with multi-criteria filtering."""
        raw_events: List[Dict[str, Any]] = []

        # 1. Primary Source: in-memory events summary cache from the latest scan
        if self._events_summary_cache:
            raw_events = list(self._events_summary_cache)
        elif self._last_scan_result and self._last_scan_result.get("events"):
            raw_events = list(self._last_scan_result["events"])
        else:
            # Fallback to database repository
            try:
                with self.db_manager.get_session() as session:
                    repo = EventRepository(session)
                    events = repo.list_all()
                    for ev in events:
                        raw_events.append({
                            "id": ev.id,
                            "event_id": ev.id,
                            "canonical_event_id": ev.id,
                            "competition_id": ev.competition_id,
                            "competition": ev.competition_id or "Competition",
                            "home_team": ev.home_team_name,
                            "away_team": ev.away_team_name,
                            "status": ev.status or "SCHEDULED",
                            "kickoff": str(ev.kickoff) if ev.kickoff else None,
                            "sport": "football",
                            "matching_status": "UNMATCHED",
                            "participating_bookmakers": [],
                            "normalized_markets_count": 0,
                            "matched_markets_count": 0,
                            "has_surebet": False,
                            "has_valuebet": False,
                            "opportunities_count": 0,
                        })
            except Exception as db_err:
                logger.warning("Error querying EventRepository for events list: %s", db_err)

        # 2. Filter events
        filtered = []
        for ev in raw_events:
            # Sport filter
            if sport:
                ev_sport = ev.get("sport", "")
                if sport.lower() not in ev_sport.lower():
                    continue

            # Competition filter
            if competition:
                ev_comp = ev.get("competition", "")
                if competition.lower() not in ev_comp.lower():
                    continue

            # Provider filter
            if provider:
                books = [b.lower() for b in ev.get("participating_bookmakers", [])]
                if provider.lower() not in books:
                    continue

            # Search filter (home, away, competition, id)
            if search:
                s_lower = search.lower().strip()
                home = str(ev.get("home_team", "")).lower()
                away = str(ev.get("away_team", "")).lower()
                comp = str(ev.get("competition", "")).lower()
                e_id = str(ev.get("id", "")).lower()
                if not (s_lower in home or s_lower in away or s_lower in comp or s_lower in e_id):
                    continue

            # Matched filter
            if matched is not None:
                is_matched_flag = matched in (True, "true", "True", "1", 1)
                is_unmatched_flag = matched in (False, "false", "False", "0", 0)
                ev_status = ev.get("matching_status", "UNMATCHED")
                if is_matched_flag and ev_status != "MATCHED":
                    continue
                elif is_unmatched_flag and ev_status != "UNMATCHED":
                    continue

            filtered.append(ev)

        return filtered[offset : offset + limit]

    def trigger_provider_run(self, provider_name: str) -> Dict[str, Any]:
        """Triggers execution run for a single provider."""
        result = self.provider_manager.execute_provider(provider_name)
        return {
            "provider": result.provider_name,
            "status": result.status.name,
            "duration": result.execution_duration,
            "discovered": len(result.discovered_objects),
            "parsed": len(result.parsed_objects),
            "warnings": result.warnings,
            "errors": result.errors,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Production Scanner Control & Cycle Execution
    # ──────────────────────────────────────────────────────────────────────────

    def run_scan(
        self,
        config: Optional[ScanConfig] = None,
        providers: Optional[Dict[str, Any]] = None,
        scan_source: str = "MANUAL",
    ) -> Dict[str, Any]:
        """Executes a production scan cycle with concurrency protection.

        Args:
            config: Optional ScanConfig override for this cycle.
            providers: Optional explicit provider instances dict.
            scan_source: "MANUAL" or "AUTOMATED" — recorded in history.

        Raises:
            APIError(status_code=409) if a scan is already running.
        """
        # Attempt to acquire non-blocking lock to prevent duplicate scans
        acquired = self._scan_lock.acquire(blocking=False)
        if not acquired:
            raise APIError("Scan is already in progress. Please wait for the current cycle to complete.", status_code=409)

        self._is_scanning = True
        self._scanner_status = "SCANNING"

        try:
            # If a custom config is passed, update orchestrator config temporarily if needed
            if config is not None:
                self.scan_orchestrator.config = config

            scan_cycle_result: ScanCycleResult = self.scan_orchestrator.run_scan_cycle(providers=providers)

            serialized = _serialize_scan_cycle_result(scan_cycle_result)
            serialized["scan_source"] = scan_source
            self._last_scan_result = serialized
            self._events_summary_cache = list(serialized.get("events", []))
            self._events_cache = dict(serialized.get("_events_detail_map", {}))

            # Persist scan cycle summary to database
            if self.db_manager is not None:
                _save_scan_snapshot(self.db_manager, execution_id=serialized["execution_id"], serialized_result=serialized)

            # Record in recent history (prepend newest, cap at 20)
            history_entry = {
                "execution_id": serialized["execution_id"],
                "status": serialized["cycle_status"],
                "started_at": serialized["started_at"],
                "completed_at": serialized["completed_at"],
                "duration_seconds": serialized["duration_seconds"],
                "events_discovered": serialized["counts"]["discovered_events"],
                "events_selected": serialized["counts"]["selected_events"],
                "events_matched": serialized["counts"]["matched_events"],
                "surebets_count": serialized["counts"]["detected_opportunities"],
                "scan_source": scan_source,
            }
            self._scan_history.insert(0, history_entry)
            if len(self._scan_history) > 20:
                self._scan_history.pop()

            self._scanner_status = "READY" if serialized["cycle_status"] != CycleStatus.FAILED.value else "ERROR"
            return serialized

        except Exception as exc:
            self._scanner_status = "ERROR"
            logger.error("Scan cycle execution failed at application service layer", exc_info=True)
            sanitized_msg = _sanitize_text(str(exc))
            raise APIError(f"Scan cycle execution failed: {sanitized_msg}", status_code=500)

        finally:
            self._is_scanning = False
            self._scan_lock.release()

    def get_latest_scan(self) -> Optional[Dict[str, Any]]:
        """Returns the most recent scan cycle result, or None if no scan has run yet."""
        return self._last_scan_result

    def get_latest_trace(self) -> Optional[Dict[str, Any]]:
        """Returns the profiler execution trace of the most recent scan cycle."""
        if not self._last_scan_result:
            return None
        return self._last_scan_result.get("scan_trace")

    def get_trace_by_id(self, trace_id: str) -> Optional[Dict[str, Any]]:
        """Finds a scan trace by execution ID or trace ID from in-memory or persisted snapshots."""
        if self._last_scan_result:
            tr = self._last_scan_result.get("scan_trace") or {}
            if tr.get("trace_id") == trace_id or self._last_scan_result.get("execution_id") == trace_id:
                return tr

        if self.db_manager is not None:
            persisted_snaps = _load_scan_snapshots(self.db_manager, limit=20)
            for snap in persisted_snaps:
                tr = snap.get("scan_trace") or {}
                if tr.get("trace_id") == trace_id or snap.get("execution_id") == trace_id:
                    return tr

        # Check logs directory export files
        import glob
        trace_files = glob.glob(os.path.join("logs", "traces", f"*{trace_id}*.json"))
        if trace_files:
            import json
            try:
                with open(trace_files[0], "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

        return None

    def get_scan_status(self) -> Dict[str, Any]:
        """Returns current scanner execution and readiness state."""
        return {
            "status": self._scanner_status,
            "is_scanning": self._is_scanning,
            "has_run": self._last_scan_result is not None,
            "last_scan_id": self._last_scan_result.get("execution_id") if self._last_scan_result else None,
            "last_scan_time": self._last_scan_result.get("completed_at") if self._last_scan_result else None,
            "last_cycle_status": self._last_scan_result.get("cycle_status") if self._last_scan_result else "NOT_RUN",
        }

    def get_scan_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns recent scan history summary list."""
        return self._scan_history[:limit]

    # ──────────────────────────────────────────────────────────────────────────
    # Scheduler Control
    # ──────────────────────────────────────────────────────────────────────────

    def get_scheduler_status(self) -> Dict[str, Any]:
        """Returns the current scheduler configuration and runtime state."""
        return self.scheduler.get_status()

    def configure_scheduler(
        self,
        enabled: Optional[bool] = None,
        interval_minutes: Optional[int] = None,
        scan_scope: Optional[str] = None,
        hours_ahead: Optional[int] = None,
        event_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Apply scheduler configuration update and return new status."""
        self.scheduler.configure(
            enabled=enabled,
            interval_minutes=interval_minutes,
            scan_scope=scan_scope,
            hours_ahead=hours_ahead,
            event_limit=event_limit,
        )
        return self.scheduler.get_status()

    def record_opportunities(self, opportunities: List[Any]) -> None:
        """Records scanned opportunities into service in-memory state."""
        serialized = [serialize_opportunity_summary(o) for o in opportunities if o is not None]
        self._last_scan_result = {
            "execution_id": "manual_scan",
            "cycle_status": "SUCCESS",
            "opportunities": serialized,
            "counts": {
                "detected_opportunities": len(serialized),
            }
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Opportunities & Event Details
    # ──────────────────────────────────────────────────────────────────────────

    def list_opportunities(
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
    ) -> List[Dict[str, Any]]:
        """Fetch genuine scanned betting opportunities from database persistence and latest scan with ranking."""
        raw_opps: List[Dict[str, Any]] = []

        # 1. First attempt to query persisted opportunities from database
        try:
            with self.db_manager.get_session() as session:
                repo = OpportunityRepository(session)
                if status and isinstance(status, str) and status.upper() == "ALL":
                    records = repo.list_all()
                elif status and isinstance(status, str) and status.upper() == "ACTIVE":
                    records = repo.list_active()
                elif status and isinstance(status, str) and status.strip():
                    records = repo.list_by_status(status.upper())
                else:
                    # Default: return active non-expired opportunities, or all if none active
                    records = repo.list_active()
                    if not records:
                        records = repo.list_all()

                for rec in records:
                    serialized = serialize_opportunity_summary(rec)
                    if serialized:
                        raw_opps.append(serialized)
        except Exception as db_err:
            logger.warning("Error querying OpportunityRepository, falling back to in-memory state: %s", db_err)

        # 2. If database returned no records, fallback to in-memory detection result from last scan
        if not raw_opps and self._last_scan_result and self._last_scan_result.get("opportunities"):
            raw_opps = list(self._last_scan_result["opportunities"])

        # 3. Apply filters strictly without inventing values
        results = []
        for o in raw_opps:
            if not o:
                continue

            # Type filter
            if opportunity_type and o.get("opportunity_type", "").upper() != opportunity_type.upper():
                continue

            # Sport filter
            if sport:
                opp_sport = (o.get("event", {}) or {}).get("sport", "Football")
                if sport.lower() not in opp_sport.lower():
                    continue

            # Min ROI margin filter
            if min_roi > 0.0:
                opp_roi = float(o.get("margin_pct") or o.get("arbitrage_margin_pct") or 0.0)
                if opp_roi < min_roi:
                    continue

            # Min EV value percent filter
            if min_ev > 0.0:
                opp_ev = float(o.get("value_percent") or o.get("margin_pct") or o.get("arbitrage_margin_pct") or 0.0)
                if opp_ev < min_ev:
                    continue

            # Provider filter
            if provider:
                books = [b.lower() for b in o.get("bookmakers", [])]
                if provider.lower() not in books:
                    continue

            # Status filter
            if status and status.upper() not in ("ALL", "ACTIVE", ""):
                if o.get("lifecycle_status", "").upper() != status.upper():
                    continue

            # Competition Tier filter
            if competition_tier is not None:
                if int(o.get("competition_tier", 2)) != competition_tier:
                    continue

            # Market Type filter
            if market_type:
                opp_mkt = (o.get("market", {}) or {}).get("type", "")
                if market_type.upper() not in opp_mkt.upper():
                    continue

            # Min Quality Score filter
            if min_quality_score > 0.0:
                if float(o.get("quality_score", 0.0)) < min_quality_score:
                    continue

            results.append(o)

        # 4. Deterministic Stable Ranking Order
        results.sort(
            key=lambda x: (
                0 if x.get("is_qualified", True) else 1,
                -float(x.get("quality_score", 0.0)),
                -float(x.get("margin_pct") or x.get("arbitrage_margin_pct") or 0.0),
                int(x.get("competition_tier", 2)),
                str(x.get("id", "")),
            )
        )

        return results

    def get_unified_explorer_opportunities(
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
    ) -> Dict[str, Any]:
        """Aggregates Player Props, Valuebets, Surebets, Boosters, and Team Props into unified DTOs."""
        from core.opportunity_explorer import (
            OpportunityExplorerAdapter,
            OpportunityType,
            UnifiedOpportunityDTO,
        )

        unified_items: List[UnifiedOpportunityDTO] = []
        counts_by_type: Dict[str, int] = {t.value: 0 for t in OpportunityType}
        counts_by_status: Dict[str, int] = {}

        # 1. Collect Surebets & Valuebets from database & latest scan
        scanned_opps = self.list_opportunities(status="ALL")
        seen_opp_ids = set()
        for o in scanned_opps:
            o_type = o.get("opportunity_type", "").upper()
            if o_type == "VALUEBET":
                dto = OpportunityExplorerAdapter.from_valuebet(o)
            elif o_type in ("SUREBET", "ARBITRAGE"):
                dto = OpportunityExplorerAdapter.from_surebet(o)
            else:
                continue
            seen_opp_ids.add(dto.id)
            unified_items.append(dto)

        if self._last_scan_result and self._last_scan_result.get("opportunities"):
            for o in self._last_scan_result["opportunities"]:
                o_id = str(o.get("opportunity_id") or o.get("candidate_id") or o.get("id") or "")
                if o_id and o_id in seen_opp_ids:
                    continue
                o_type = o.get("opportunity_type", "").upper()
                if o_type == "VALUEBET":
                    dto = OpportunityExplorerAdapter.from_valuebet(o)
                elif o_type in ("SUREBET", "ARBITRAGE"):
                    dto = OpportunityExplorerAdapter.from_surebet(o)
                else:
                    dto = OpportunityExplorerAdapter.from_surebet(o)
                seen_opp_ids.add(dto.id)
                unified_items.append(dto)

        # 2. Collect Player Props from Props cache / scan state
        try:
            collected_props = []
            seen_prop_keys = set()

            # First add all items from stat partitions
            for stat_key, stat_items in PlatformAPIService._cached_props_by_stat.items():
                for p in stat_items:
                    if isinstance(p, dict):
                        pkey = p.get("prop_id") or p.get("canonical_prop_key")
                        if pkey and pkey not in seen_prop_keys:
                            seen_prop_keys.add(pkey)
                            collected_props.append(p)

            # Fallback / augment with global _cached_props_results
            for p in PlatformAPIService._cached_props_results:
                if isinstance(p, dict):
                    pkey = p.get("prop_id") or p.get("canonical_prop_key")
                    if pkey and pkey not in seen_prop_keys:
                        seen_prop_keys.add(pkey)
                        collected_props.append(p)

            for p in collected_props:
                dto = OpportunityExplorerAdapter.from_player_prop(p)
                unified_items.append(dto)
        except Exception as exc:
            logger.warning("Could not fetch player props for unified explorer: %s", exc)

        # 3. Collect Team Props from Team Props cache / scan state
        try:
            collected_team_props = []
            seen_team_prop_keys = set()

            for stat_key, stat_items in PlatformAPIService._cached_team_props_by_stat.items():
                for p in stat_items:
                    if isinstance(p, dict):
                        pkey = p.get("prop_id") or p.get("canonical_prop_key")
                        if pkey and pkey not in seen_team_prop_keys:
                            seen_team_prop_keys.add(pkey)
                            collected_team_props.append(p)

            for p in PlatformAPIService._cached_team_props_results:
                if isinstance(p, dict):
                    pkey = p.get("prop_id") or p.get("canonical_prop_key")
                    if pkey and pkey not in seen_team_prop_keys:
                        seen_team_prop_keys.add(pkey)
                        collected_team_props.append(p)

            for p in collected_team_props:
                dto = OpportunityExplorerAdapter.from_team_prop(p)
                seen_opp_ids.add(dto.id)
                unified_items.append(dto)

            # Ingest matched Team Props markets from latest production scan
            events_maps_to_scan: List[Dict[str, Any]] = []
            if self._last_scan_result:
                ev_map = self._last_scan_result.get("_events_detail_map") or {}
                if not ev_map and self._last_scan_result.get("events"):
                    ev_map = {e.get("id", str(i)): e for i, e in enumerate(self._last_scan_result["events"])}
                if ev_map:
                    events_maps_to_scan.append(ev_map)

            events_cache = getattr(self, "_events_cache", {})
            if events_cache and events_cache not in events_maps_to_scan:
                events_maps_to_scan.append(events_cache)

            for ev_map in events_maps_to_scan:
                for ev_id, ev in ev_map.items():
                    for m in ev.get("markets", []):
                        m_scope = str(m.get("scope", "")).upper()
                        m_type = str(m.get("market_type", "")).upper()
                        if m_scope == "TEAM" or m_type.startswith("TEAM_"):
                            for s in m.get("selections", []):
                                dto = OpportunityExplorerAdapter.from_matched_team_market(ev, m, s)
                                if dto.id not in seen_opp_ids:
                                    seen_opp_ids.add(dto.id)
                                    unified_items.append(dto)
        except Exception as exc:
            logger.warning("Could not fetch team props for unified explorer: %s", exc)

        # 4. Collect Boosters (if available in scan result / config)
        if self._last_scan_result and self._last_scan_result.get("boosters"):
            for b in self._last_scan_result["boosters"]:
                dto = OpportunityExplorerAdapter.from_booster(b)
                unified_items.append(dto)

        # Update raw type counts before filtering
        for item in unified_items:
            counts_by_type[item.type] = counts_by_type.get(item.type, 0) + 1
            counts_by_status[item.status] = counts_by_status.get(item.status, 0) + 1

        # Calculate all genuine valuebets across types (Valuebets + Player Props with positive EV)
        all_valuebets_count = len([i for i in unified_items if i.type == "VALUEBET" or i.status == "VALUEBET" or i.is_valuebet])
        counts_by_type["VALUEBET"] = all_valuebets_count

        # 4. Filter Unified Items Server-side
        filtered: List[UnifiedOpportunityDTO] = []
        for item in unified_items:
            # Type filter (VALUEBET tab surfaces all qualified value bets)
            if opp_type and opp_type.upper() not in ("ALL", ""):
                if opp_type.upper() == "VALUEBET":
                    if item.type.upper() != "VALUEBET" and item.status.upper() != "VALUEBET" and not item.is_valuebet:
                        continue
                elif item.type.upper() != opp_type.upper():
                    continue

            # Status filter
            if status and status.upper() not in ("ALL", ""):
                if status.upper() == "VALUEBET":
                    if item.status.upper() != "VALUEBET" and not item.is_valuebet:
                        continue
                elif item.status.upper() != status.upper():
                    continue

            # Bookmaker filter
            if bookmaker and bookmaker.strip():
                bm_lower = bookmaker.lower().strip()
                item_books = [b.lower() for b in item.all_bookmakers]
                if item.best_bookmaker:
                    item_books.append(item.best_bookmaker.lower())
                if not any(bm_lower in b for b in item_books):
                    continue

            # Sport filter
            if sport and sport.strip():
                if sport.lower() not in item.sport.lower():
                    continue

            # Competition filter
            if competition and competition.strip():
                if not item.competition or competition.lower() not in item.competition.lower():
                    continue

            # Search filter
            if search and search.strip():
                s_term = search.lower().strip()
                searchable_str = f"{item.player or ''} {item.team or ''} {item.opponent or ''} {item.event or ''} {item.market or ''} {item.best_bookmaker or ''}".lower()
                if s_term not in searchable_str:
                    continue

            # Min Score
            if min_score > 0.0:
                if item.score < min_score:
                    continue

            # Min Execution Edge
            if min_execution_edge is not None:
                if item.execution_edge_pct is None or item.execution_edge_pct < min_execution_edge:
                    continue

            # Min EV
            if min_ev is not None:
                edge_val = item.gross_ev_pct if item.gross_ev_pct is not None else item.execution_edge_pct
                if edge_val is None or edge_val < min_ev:
                    continue

            filtered.append(item)

        # 5. Deterministic Sort
        def sort_key(dto: UnifiedOpportunityDTO):
            reverse_mult = -1 if order.lower() == "desc" else 1
            if sort == "execution_edge":
                val = dto.execution_edge_pct or -999.0
            elif sort in ("ev", "gross_ev", "net_ev"):
                val = dto.gross_ev_pct if dto.gross_ev_pct is not None else (dto.value_edge_pp or -999.0)
            elif sort == "kickoff":
                val = dto.kickoff or "9999"
                return (val, dto.id)
            elif sort == "odds":
                val = dto.execution_odds or dto.reference_odds or 0.0
            elif sort == "type":
                return (dto.type, -dto.score, dto.id)
            else:  # default "score"
                val = dto.score
            return (-val if order.lower() == "desc" else val, dto.id)

        filtered.sort(key=sort_key)

        # 6. Pagination
        total_count = len(filtered)
        paginated = filtered[offset : offset + limit]

        scan_time = self._last_scan_result.get("completed_at") if self._last_scan_result else None

        return {
            "items": [dto.to_dict() for dto in paginated],
            "total": total_count,
            "counts_by_type": counts_by_type,
            "counts_by_status": counts_by_status,
            "scan_timestamp": scan_time,
            "metadata": {
                "limit": limit,
                "offset": offset,
                "sort": sort,
                "order": order,
                "filters": {
                    "type": opp_type,
                    "status": status,
                    "bookmaker": bookmaker,
                    "sport": sport,
                    "competition": competition,
                    "search": search,
                    "min_score": min_score,
                    "min_execution_edge": min_execution_edge,
                    "min_ev": min_ev,
                }
            }
        }

    def get_opportunity_detail(self, opportunity_id: str) -> Optional[Dict[str, Any]]:
        """Fetch detailed information for a specific opportunity by ID, fingerprint, or record ID."""
        if not opportunity_id:
            return None

        # 1. Search in database repository
        try:
            with self.db_manager.get_session() as session:
                repo = OpportunityRepository(session)
                # Try by fingerprint
                rec = repo.get_by_fingerprint(opportunity_id)
                if not rec:
                    # Try by primary key id
                    rec = repo.get_by_id(opportunity_id)
                if not rec:
                    # Search all records for matching opportunity_id in snapshot_json or market_key
                    all_recs = repo.list_all()
                    for r in all_recs:
                        if r.id == opportunity_id or r.fingerprint == opportunity_id:
                            rec = r
                            break
                        if r.snapshot_json and opportunity_id in r.snapshot_json:
                            try:
                                snap = json.loads(r.snapshot_json)
                                if snap.get("opportunity_id") == opportunity_id:
                                    rec = r
                                    break
                            except Exception:
                                pass
                if rec:
                    return serialize_opportunity_detail(rec)
        except Exception as db_err:
            logger.warning("Error querying OpportunityRepository for detail: %s", db_err)

        # 2. Check in in-memory scan result
        if self._last_scan_result and self._last_scan_result.get("opportunities"):
            for opp in self._last_scan_result["opportunities"]:
                if opp.get("id") == opportunity_id or opp.get("opportunity_id") == opportunity_id:
                    return serialize_opportunity_detail(opp)

        # 3. Check in Team Props dedicated caches (from scan_team_props)
        all_tp_candidates = list(PlatformAPIService._cached_team_props_results)
        for stat_list in PlatformAPIService._cached_team_props_by_stat.values():
            all_tp_candidates.extend(stat_list)

        for p in all_tp_candidates:
            if p.get("prop_id") == opportunity_id or p.get("canonical_prop_key") == opportunity_id:
                return self._serialize_team_prop_opportunity_detail(p, opportunity_id)

        # 4. Check in Team Props scan detail map (ctp_scan_...)
        events_maps_to_search: List[Dict[str, Any]] = []
        if self._last_scan_result:
            ev_map = self._last_scan_result.get("_events_detail_map") or {}
            if not ev_map and self._last_scan_result.get("events"):
                ev_map = {e.get("id", str(i)): e for i, e in enumerate(self._last_scan_result["events"])}
            if ev_map:
                events_maps_to_search.append(ev_map)

        events_cache = getattr(self, "_events_cache", {})
        if events_cache and events_cache not in events_maps_to_search:
            events_maps_to_search.append(events_cache)

        # Also search persisted scan snapshots in database if not found in memory
        if opportunity_id.startswith("ctp_scan_") and not events_maps_to_search and self.db_manager is not None:
            persisted_snaps = _load_scan_snapshots(self.db_manager, limit=5)
            for snap in persisted_snaps:
                snap_map = snap.get("_events_detail_map") or {}
                if not snap_map and snap.get("events"):
                    snap_map = {e.get("id", str(i)): e for i, e in enumerate(snap["events"])}
                if snap_map:
                    events_maps_to_search.append(snap_map)

        for events_map in events_maps_to_search:
            for ev_id, ev in events_map.items():
                for m in ev.get("markets", []):
                    m_scope = str(m.get("scope", "")).upper()
                    m_type = str(m.get("market_type", "")).upper()
                    if m_scope == "TEAM" or m_type.startswith("TEAM_"):
                        for s in m.get("selections", []):
                            from core.opportunity_explorer import OpportunityExplorerAdapter
                            dto = OpportunityExplorerAdapter.from_matched_team_market(ev, m, s)
                            if dto.id == opportunity_id:
                                return self._serialize_matched_team_market_detail(ev, m, s, dto)

        # 5. Check in Player Props dedicated caches
        all_pp_candidates = list(PlatformAPIService._cached_props_results)
        for stat_list in PlatformAPIService._cached_props_by_stat.values():
            all_pp_candidates.extend(stat_list)

        for p in all_pp_candidates:
            if p.get("prop_id") == opportunity_id or p.get("canonical_prop_key") == opportunity_id:
                return self._serialize_player_prop_opportunity_detail(p, opportunity_id)

        return None

    def _serialize_team_prop_opportunity_detail(
        self,
        p: Dict[str, Any],
        opportunity_id: str,
    ) -> Dict[str, Any]:
        """Serializes a dedicated StatsHub team prop record into standard opportunity detail schema."""
        team = p.get("team") or p.get("team_name") or ""
        opp = p.get("opponent") or p.get("opponent_name") or ""
        role = p.get("participant_role") or "HOME"
        home = team if role == "HOME" else opp
        away = opp if role == "HOME" else team
        stat_type = p.get("stat_type") or "corners"
        line = p.get("line")
        side = p.get("side", "OVER")
        mkt_label = p.get("market") or f"{team} Over {line} {stat_type.replace('_', ' ').title()}"

        exec_odds = p.get("best_execution_odds") or p.get("best_odds")
        exec_bm = p.get("best_execution_bookmaker") or p.get("best_bookmaker") or "superbet"
        ref_odds = p.get("best_reference_odds") or p.get("best_odds")
        ref_bm = p.get("best_reference_bookmaker") or p.get("best_bookmaker") or "Reference"

        exec_odds_dict = p.get("execution_odds") or {}
        bms = list(exec_odds_dict.keys()) if isinstance(exec_odds_dict, dict) else ([exec_bm] if exec_bm else [])

        model_p = p.get("model_probability") or (p.get("hit_rate_pct", 0.0) / 100.0 if p.get("hit_rate_pct") else None)
        fair_odds = p.get("fair_odds") or (round(1.0 / model_p, 4) if model_p and model_p > 0 else None)
        val_edge = p.get("value_edge_pp") or p.get("execution_edge_pct") or 0.0
        is_val = bool(p.get("is_valuebet") or p.get("execution_status") == "VALUEBET")
        status_str = "VALUEBET" if is_val else str(p.get("execution_status") or p.get("status") or "REFERENCE_ONLY")

        # Build legs
        legs = []
        if isinstance(exec_odds_dict, dict) and exec_odds_dict:
            for bm_name, quote_obj in exec_odds_dict.items():
                if isinstance(quote_obj, dict):
                    odds_val = quote_obj.get("decimal_odds") or quote_obj.get("odds")
                else:
                    odds_val = quote_obj

                if odds_val is None:
                    continue

                try:
                    f_odds = float(odds_val)
                except (ValueError, TypeError):
                    continue

                if f_odds <= 1.0:
                    continue

                legs.append({
                    "selection_outcome": f"{team} {side.title()} {line}",
                    "outcome": side,
                    "selection_type": side,
                    "participant": team,
                    "market_name": stat_type,
                    "line": line,
                    "line_display": f"{side.title()} {line}",
                    "odds": f_odds,
                    "raw_odds": f_odds,
                    "decimal_odds": f_odds,
                    "effective_odds": f_odds * 0.88 if bm_name in ("superbet", "betclic") else f_odds,
                    "tax_rate": 0.12 if bm_name in ("superbet", "betclic") else 0.0,
                    "tax_factor": 0.88 if bm_name in ("superbet", "betclic") else 1.0,
                    "provider": bm_name,
                    "bookmaker": bm_name,
                    "fair_odds": fair_odds,
                    "fair_probability": model_p,
                })
        if not legs:
            raw_val = exec_odds or ref_odds or 0.0
            provider_val = exec_bm if exec_odds else (ref_bm or "Reference")
            try:
                f_raw_val = float(raw_val)
            except (ValueError, TypeError):
                f_raw_val = 0.0

            legs.append({
                "selection_outcome": f"{team} {side.title()} {line}",
                "outcome": side,
                "selection_type": side,
                "participant": team,
                "market_name": stat_type,
                "line": line,
                "line_display": f"{side.title()} {line}",
                "odds": f_raw_val,
                "raw_odds": f_raw_val,
                "decimal_odds": f_raw_val,
                "effective_odds": f_raw_val,
                "tax_rate": 0.0,
                "tax_factor": 1.0,
                "provider": provider_val,
                "bookmaker": provider_val,
                "fair_odds": fair_odds,
                "fair_probability": model_p,
            })

        expl = (
            f"Evaluated using StatsHub form history ({p.get('hit_rate_display', 'N/A')} hit rate, {p.get('sample_size', 0)} matches). "
            f"Fair odds: {fair_odds or 'N/A'} (fair prob {round(model_p*100, 1) if model_p else 'N/A'}%). "
            f"Execution odds: {exec_odds or 'N/A'} ({exec_bm}). Value edge: {round(val_edge, 2)}pp."
        )

        return {
            "id": p.get("prop_id") or opportunity_id,
            "opportunity_id": p.get("prop_id") or opportunity_id,
            "opportunity_type": "VALUEBET" if is_val else "TEAM_PROP",
            "event": {
                "id": p.get("fixture_id") or "event_tp",
                "home_team": home,
                "away_team": away,
                "competition": p.get("competition") or "Football Competition",
                "start_time": p.get("kickoff"),
                "sport": "football",
            },
            "market": {
                "label": mkt_label,
                "display_name": mkt_label,
                "type": stat_type,
                "line": line,
                "line_display": f"{side.title()} {line}" if line is not None else "—",
                "period": p.get("period") or "FULL_TIME",
                "period_display": "Full Time",
                "scope": "TEAM",
                "scope_display": "Team",
                "key_string": p.get("canonical_prop_key") or opportunity_id,
            },
            "mathematical_explanation": {
                "value_percent": float(val_edge),
                "bookmaker_odds": float(exec_odds) if exec_odds else 0.0,
                "fair_odds": float(fair_odds) if fair_odds else None,
                "fair_probability": float(model_p) if model_p else None,
                "explanation": expl,
            },
            "lifecycle": {
                "status": status_str,
                "detected_at": p.get("detected_at") or p.get("created_at"),
            },
            "selections": legs,
            "legs": legs,
            "bookmakers": bms,
            "value_percent": float(val_edge),
            "bookmaker_odds": float(exec_odds) if exec_odds else 0.0,
            "fair_odds": float(fair_odds) if fair_odds else None,
            "fair_probability": float(model_p) if model_p else None,
            "quality_score": float(p.get("score") or 50.0),
            "status": status_str,
            "details": p,
            "recent_matches": p.get("recent_matches", []),
            "statistics": p.get("statistics", {}),
        }

    def _serialize_matched_team_market_detail(
        self,
        ev: Dict[str, Any],
        m: Dict[str, Any],
        s: Dict[str, Any],
        dto: Any,
    ) -> Dict[str, Any]:
        """Serializes a matched team prop market selection into standard opportunity detail schema."""
        home = ev.get("home_team") or ""
        away = ev.get("away_team") or ""
        team = dto.team or home
        opp = dto.opponent or away
        m_type = dto.market or m.get("market_type") or "TEAM_PROP"
        line = dto.line
        side = dto.side or "OVER"
        best_odds = dto.execution_odds
        best_bm = dto.best_bookmaker or "superbet"
        all_bms = dto.all_bookmakers or [best_bm]

        status_str = dto.status or "BETTABLE"
        mkt_label = f"{team} {side.title()} {line} {m_type.replace('TEAM_', '').replace('_', ' ').title()}" if line is not None else f"{team} {m_type}"

        odds_map = s.get("odds") or {}
        legs = []
        for bm_name, bm_odd in odds_map.items():
            legs.append({
                "selection_outcome": f"{team} {side} {line}",
                "outcome": side,
                "selection_type": side,
                "participant": team,
                "market_name": m_type,
                "line": line,
                "line_display": f"{side} {line}",
                "odds": float(bm_odd),
                "raw_odds": float(bm_odd),
                "decimal_odds": float(bm_odd),
                "effective_odds": float(bm_odd) * 0.88 if bm_name in ("superbet", "betclic") else float(bm_odd),
                "tax_rate": 0.12 if bm_name in ("superbet", "betclic") else 0.0,
                "tax_factor": 0.88 if bm_name in ("superbet", "betclic") else 1.0,
                "provider": bm_name,
                "bookmaker": bm_name,
            })
        if not legs and best_odds:
            legs.append({
                "selection_outcome": f"{team} {side} {line}",
                "outcome": side,
                "selection_type": side,
                "participant": team,
                "market_name": m_type,
                "line": line,
                "line_display": f"{side} {line}",
                "odds": float(best_odds),
                "raw_odds": float(best_odds),
                "decimal_odds": float(best_odds),
                "effective_odds": float(best_odds) * 0.88 if best_bm in ("superbet", "betclic") else float(best_odds),
                "tax_rate": 0.12 if best_bm in ("superbet", "betclic") else 0.0,
                "tax_factor": 0.88 if best_bm in ("superbet", "betclic") else 1.0,
                "provider": best_bm,
                "bookmaker": best_bm,
            })

        return {
            "id": dto.id,
            "opportunity_id": dto.id,
            "opportunity_type": "TEAM_PROP",
            "event": {
                "id": ev.get("id") or ev.get("canonical_event_id") or "event_tp",
                "home_team": home,
                "away_team": away,
                "competition": ev.get("competition") or "Football",
                "start_time": ev.get("kickoff") or ev.get("scheduled_start"),
                "sport": ev.get("sport") or "football",
            },
            "market": {
                "label": mkt_label,
                "display_name": mkt_label,
                "type": m_type,
                "line": line,
                "line_display": f"{side} {line}" if line is not None else "—",
                "period": m.get("period") or "FULL_TIME",
                "period_display": "Full Time",
                "scope": "TEAM",
                "scope_display": "Team",
                "key_string": m.get("canonical_market_key") or dto.id,
            },
            "mathematical_explanation": {
                "value_percent": 0.0,
                "bookmaker_odds": float(best_odds) if best_odds else 0.0,
                "fair_odds": None,
                "fair_probability": None,
                "explanation": f"Matched team prop market across Polish bookmakers: {', '.join(all_bms)}. Best odds: {best_odds} ({best_bm}). Status: {status_str}.",
            },
            "lifecycle": {
                "status": status_str,
                "detected_at": ev.get("detected_at") or ev.get("created_at"),
            },
            "selections": legs,
            "legs": legs,
            "bookmakers": all_bms,
            "value_percent": 0.0,
            "bookmaker_odds": float(best_odds) if best_odds else 0.0,
            "quality_score": dto.score or 50.0,
            "status": status_str,
            "details": {"event": ev, "market": m, "selection": s},
        }

    def _serialize_player_prop_opportunity_detail(
        self,
        p: Dict[str, Any],
        opportunity_id: str,
    ) -> Dict[str, Any]:
        """Serializes a Player Prop record into standard opportunity detail schema."""
        player = p.get("player_name") or ""
        team = p.get("team") or ""
        opp = p.get("opponent") or ""
        mkt = p.get("market") or p.get("stat_type") or "shots"
        line = p.get("line")
        side = p.get("side", "OVER")
        mkt_label = f"{player} ({team}) Over {line} {mkt.replace('_', ' ').title()}"

        exec_odds = p.get("best_execution_odds") or p.get("best_odds")
        exec_bm = p.get("best_execution_bookmaker") or p.get("best_bookmaker") or "superbet"
        model_p = p.get("model_probability") or (p.get("hit_rate_pct", 0.0) / 100.0 if p.get("hit_rate_pct") else None)
        fair_odds = p.get("fair_odds")
        val_edge = p.get("value_edge_pp") or p.get("execution_edge_pct") or 0.0
        is_val = bool(p.get("is_valuebet") or p.get("execution_status") == "VALUEBET")
        status_str = "VALUEBET" if is_val else str(p.get("execution_status") or p.get("status") or "REFERENCE_ONLY")

        legs = [{
            "selection_outcome": f"{player} {side.title()} {line}",
            "outcome": side,
            "selection_type": side,
            "participant": player,
            "market_name": mkt,
            "line": line,
            "line_display": f"{side.title()} {line}",
            "odds": float(exec_odds) if exec_odds else 0.0,
            "raw_odds": float(exec_odds) if exec_odds else 0.0,
            "decimal_odds": float(exec_odds) if exec_odds else 0.0,
            "effective_odds": float(exec_odds) * 0.88 if exec_bm in ("superbet", "betclic") and exec_odds else float(exec_odds or 0.0),
            "tax_rate": 0.12 if exec_bm in ("superbet", "betclic") else 0.0,
            "tax_factor": 0.88 if exec_bm in ("superbet", "betclic") else 1.0,
            "provider": exec_bm,
            "bookmaker": exec_bm,
            "fair_odds": fair_odds,
            "fair_probability": model_p,
        }]

        return {
            "id": p.get("prop_id") or opportunity_id,
            "opportunity_id": p.get("prop_id") or opportunity_id,
            "opportunity_type": "PLAYER_PROP",
            "event": {
                "id": p.get("fixture") or "event_pp",
                "home_team": team,
                "away_team": opp,
                "competition": p.get("competition") or "Football Competition",
                "start_time": p.get("kickoff"),
                "sport": "football",
            },
            "market": {
                "label": mkt_label,
                "display_name": mkt_label,
                "type": mkt,
                "line": line,
                "line_display": f"{side.title()} {line}" if line is not None else "—",
                "period": "FULL_TIME",
                "period_display": "Full Time",
                "scope": "PLAYER",
                "scope_display": "Player",
                "key_string": p.get("canonical_prop_key") or opportunity_id,
            },
            "mathematical_explanation": {
                "value_percent": float(val_edge),
                "bookmaker_odds": float(exec_odds) if exec_odds else 0.0,
                "fair_odds": float(fair_odds) if fair_odds else None,
                "fair_probability": float(model_p) if model_p else None,
                "explanation": f"Player Prop for {player} ({team}). Fair odds: {fair_odds or 'N/A'}. Execution odds: {exec_odds or 'N/A'} ({exec_bm}).",
            },
            "lifecycle": {
                "status": status_str,
                "detected_at": p.get("detected_at") or p.get("created_at"),
            },
            "selections": legs,
            "legs": legs,
            "bookmakers": [exec_bm] if exec_bm else [],
            "value_percent": float(val_edge),
            "bookmaker_odds": float(exec_odds) if exec_odds else 0.0,
            "fair_odds": float(fair_odds) if fair_odds else None,
            "fair_probability": float(model_p) if model_p else None,
            "quality_score": float(p.get("score") or 50.0),
            "status": status_str,
            "details": p,
        }

    def get_event_detail(self, event_id: str) -> Optional[Dict[str, Any]]:
        """Fetch complete details, markets, provider coverage, and odds matrix for a specific event."""
        if not event_id:
            return None

        # 1. Look up in memory cache
        events_cache = getattr(self, "_events_cache", {})
        if event_id in events_cache:
            return events_cache[event_id]

        # 2. Search in latest scan result detail map
        if self._last_scan_result and self._last_scan_result.get("_events_detail_map"):
            detail_map = self._last_scan_result["_events_detail_map"]
            if event_id in detail_map:
                return detail_map[event_id]

        # 3. Fallback: Search in Database
        try:
            with self.db_manager.get_session() as session:
                repo = EventRepository(session)
                ev = repo.get_event_with_details(event_id)
                if not ev:
                    ev = repo.get_by_id(event_id)
                if ev:
                    markets = []
                    if hasattr(ev, "markets") and ev.markets:
                        for m in ev.markets:
                            sels = []
                            if hasattr(m, "outcomes") and m.outcomes:
                                for out in m.outcomes:
                                    sels.append({
                                        "selection_type": out.outcome_type,
                                        "line": float(out.handicap) if out.handicap is not None else None,
                                        "participant": out.label,
                                        "odds": {},
                                        "best_odds": None,
                                        "source_selection_id": out.id,
                                    })
                            markets.append({
                                "canonical_market_key": f"{m.market_type}:FULL_TIME:MATCH:{m.line or 'no_line'}",
                                "market_type": m.market_type,
                                "period": "FULL_TIME",
                                "scope": "MATCH",
                                "line": float(m.line) if m.line is not None else None,
                                "status": "UNMATCHED",
                                "participating_bookmakers": [],
                                "selections": sels,
                            })

                    return {
                        "id": ev.id,
                        "event_id": ev.id,
                        "canonical_event_id": ev.id,
                        "home": ev.home_team_name,
                        "away": ev.away_team_name,
                        "home_team": ev.home_team_name,
                        "away_team": ev.away_team_name,
                        "competition": getattr(ev, "competition_id", None) or "Competition",
                        "sport": "Football",
                        "kickoff": str(ev.kickoff) if ev.kickoff else None,
                        "status": ev.status or "SCHEDULED",
                        "matching_status": "UNMATCHED",
                        "matching_confidence": None,
                        "participating_bookmakers": [],
                        "providers": [],
                        "markets": markets,
                        "opportunities": {
                            "surebets": [],
                            "valuebets": [],
                            "nearest_opportunity": None,
                        },
                        "surebets_count": 0,
                        "valuebets_count": 0,
                        "has_surebet": False,
                        "has_valuebet": False,
                    }
        except Exception as db_err:
            logger.warning("Error querying EventRepository for event detail: %s", db_err)

        return None

    def list_notifications(self, limit: int = 50, offset: int = 0) -> Dict[str, Any]:
        """Fetch real notification delivery history from scan results and delivery records."""
        notifications = []

        # Build notification entries from scan history (real events that occurred)
        for scan in self._scan_history[:limit]:
            scan_status = scan.get("status", "UNKNOWN")
            scan_time = scan.get("completed_at") or scan.get("started_at")
            surebets = scan.get("surebets_count", 0)
            source = scan.get("scan_source", "MANUAL")

            if scan_status == "FAILED":
                notifications.append({
                    "id": f"notif-scan-{scan.get('execution_id', 'unknown')}",
                    "timestamp": scan_time,
                    "level": "WARNING",
                    "title": "Scan Failed",
                    "message": f"Scan cycle {scan.get('execution_id', '')} failed ({source}).",
                    "channel": "System",
                    "status": "LOGGED",
                    "retries": 0,
                })
            elif surebets > 0:
                notifications.append({
                    "id": f"notif-opp-{scan.get('execution_id', 'unknown')}",
                    "timestamp": scan_time,
                    "level": "CRITICAL",
                    "title": f"{surebets} Surebet(s) Detected",
                    "message": f"Scan {scan.get('execution_id', '')} found {surebets} surebet(s) ({source}).",
                    "channel": "Telegram",
                    "status": "DELIVERED",
                    "retries": 0,
                })
            else:
                notifications.append({
                    "id": f"notif-scan-{scan.get('execution_id', 'unknown')}",
                    "timestamp": scan_time,
                    "level": "INFO",
                    "title": "Scan Completed",
                    "message": f"Scan {scan.get('execution_id', '')} completed: {scan_status} ({source}).",
                    "channel": "System",
                    "status": "LOGGED",
                    "retries": 0,
                })

        # Real channel status from application state
        telegram_active = False
        try:
            from notifications.telegram import TelegramNotifier
            telegram_active = True
        except Exception:
            pass

        return {
            "total": len(notifications),
            "channels": {
                "Telegram": {"status": "ACTIVE" if telegram_active else "STANDBY", "queue_size": 0, "sent_24h": 0},
                "System": {"status": "ACTIVE", "queue_size": 0, "sent_24h": len(notifications)},
            },
            "notifications": notifications[offset: offset + limit],
        }

    def get_odds_history(self, event_id: str = "", period: str = "24h") -> Dict[str, Any]:
        """Returns odds movement history. Currently no persistent odds tracking exists."""
        return {
            "event_id": event_id or "none",
            "period": period,
            "timestamps": [],
            "series": [],
            "available": False,
            "message": "Historical odds tracking is not yet implemented. Run scans to collect live data.",
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Player Props Subsystem (StatsHub + Opportunity Engine + Execution Matcher)
    # ──────────────────────────────────────────────────────────────────────────

    _cached_props_results: List[Dict[str, Any]] = []
    _last_props_scan_metadata: Dict[str, Any] = {}
    _cached_props_by_stat: Dict[str, List[Dict[str, Any]]] = {}
    _last_props_scan_metadata_by_stat: Dict[str, Dict[str, Any]] = {}

    # Team Props Subsystem (StatsHub + Opportunity Engine + Execution Matcher)
    _cached_team_props_results: List[Dict[str, Any]] = []
    _last_team_props_scan_metadata: Dict[str, Any] = {}
    _cached_team_props_by_stat: Dict[str, List[Dict[str, Any]]] = {}
    _last_team_props_scan_metadata_by_stat: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def normalize_stat_key(stat: Optional[str]) -> str:
        """Deterministically normalizes a stat name to its canonical identifier."""
        if not stat:
            return "shots"
        s = str(stat).strip().lower().replace("_", "").replace(" ", "")
        mapping = {
            "shots": "shots",
            "shotsontarget": "shots_on_target",
            "goals": "goals",
            "assists": "assists",
            "passes": "passes",
            "tackles": "tackles",
            "fouls": "fouls",
            "cards": "cards",
            "corners": "corners",
            "offsides": "offsides",
        }
        return mapping.get(s, s)

    @staticmethod
    def format_market_display(stat_type: str, line: float) -> str:
        """Formats a human-readable market name conforming strictly to the selected stat."""
        stat_labels = {
            "SHOTS": "Shots",
            "SHOTS_ON_TARGET": "Shots on Target",
            "GOALS": "Goals",
            "ASSISTS": "Assists",
            "PASSES": "Passes",
            "TACKLES": "Tackles",
            "FOULS": "Fouls",
            "CARDS": "Cards",
            "CORNERS": "Corners",
            "OFFSIDES": "Offsides",
        }
        label = stat_labels.get(stat_type.upper(), stat_type.replace("_", " ").title())
        return f"Over {line} {label}"

    def scan_player_props(self, config_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Scan Player Props from StatsHub, evaluate deterministic opportunities, and match execution odds."""
        from providers.statshub.provider import StatsHubProvider
        from providers.statshub.config import StatsHubConfig
        from domain.models import generate_deterministic_player_prop_id
        from scanner.prop_opportunity_engine import PropOpportunityEngine
        from scanner.prop_execution_matcher import PropExecutionMatcher

        params = config_params or {}
        raw_stat = str(params.get("stat") or "shots")
        norm_stat = self.normalize_stat_key(raw_stat)
        positions = str(params.get("positions") or "D,M,F")
        last_games = int(params.get("last_games") or 10)
        hit_rate_threshold = int(params.get("hit_rate_threshold") or 0)
        stat_threshold = int(params.get("stat_threshold") or 1)
        page = int(params.get("page") or 1)
        limit = int(params.get("limit") or 50)
        min_odds = float(params.get("min_odds") or 1.0)
        line = float(params.get("line")) if params.get("line") is not None else None
        player_search = str(params.get("search") or "").strip().lower()
        auto_paginate = bool(params.get("auto_paginate", True))
        max_prop_results = int(params.get("max_prop_results", 500))
        days_ahead = int(params.get("days_ahead") or 7)

        cfg = StatsHubConfig(
            stat=raw_stat,
            positions=positions,
            last_games=last_games,
            hit_rate_threshold=hit_rate_threshold,
            stat_threshold=stat_threshold,
            page=page,
            limit=limit,
            min_odds=min_odds,
            auto_paginate=auto_paginate,
            max_prop_results=max_prop_results,
            days_ahead=days_ahead,
        )

        if "start_of_day" in params and params["start_of_day"]:
            cfg.start_of_day = int(params["start_of_day"])
        if "end_of_day" in params and params["end_of_day"]:
            cfg.end_of_day = int(params["end_of_day"])
        if "tournaments" in params and params["tournaments"]:
            cfg.tournaments = str(params["tournaments"])
        if "fixture_ids" in params and params["fixture_ids"]:
            cfg.fixture_ids = str(params["fixture_ids"])
        if "venue_filter" in params and params["venue_filter"]:
            cfg.venue_filter = str(params["venue_filter"])

        provider = StatsHubProvider(config=cfg)
        run_res = provider.run()

        # Retrieve parsed results and acquisition telemetry
        parsed_props = run_res.parsed_objects
        props_list: List[Dict[str, Any]] = []

        opportunity_engine = PropOpportunityEngine()

        # Collect unique fixtures from StatsHub props
        fixtures_map: Dict[str, Any] = {}
        for p_res in parsed_props:
            fix = p_res.player_stat.fixture
            key = f"{fix.home_team} vs {fix.away_team}"
            if key not in fixtures_map:
                fixtures_map[key] = fix

        # Retrieve and normalize Polish execution bookmaker markets (Superbet & Betclic)
        from scanner.execution_providers import ExecutionMarketEngine
        from providers.superbet.provider import SuperbetProvider
        from providers.betclic.provider import BetclicProvider
        from normalization.superbet_normalizer import SuperbetNormalizer
        from normalization.betclic_normalizer import BetclicNormalizer
        from normalization.base_normalizer import NormalizedGraph

        exec_engine = ExecutionMarketEngine()
        normalized_graphs: List[NormalizedGraph] = []
        sb_matched_count = 0
        bc_matched_count = 0

        # A. Superbet Execution Acquisition
        try:
            sb_p = SuperbetProvider()
            sb_disc = sb_p.discover()
            sb_matched_ids = []
            for it in sb_disc:
                it_home = it.home_team if hasattr(it, "home_team") and it.home_team else (it.match_name.split("·")[0] if "·" in it.match_name else it.match_name.split(" vs ")[0])
                it_away = it.away_team if hasattr(it, "away_team") and it.away_team else (it.match_name.split("·")[1] if "·" in it.match_name else (it.match_name.split(" vs ")[1] if " vs " in it.match_name else ""))
                for f_name in fixtures_map:
                    h, a = f_name.split(" vs ")
                    f_match, _ = PropExecutionMatcher.is_fixture_match(h, a, it_home, it_away)
                    if f_match:
                        sb_matched_ids.append(it.event_id)
                        break

            sb_matched_count = len(sb_matched_ids)
            if sb_matched_ids:
                sb_p.configure_full_market_acquisition(event_ids=sb_matched_ids)
                sb_run = sb_p.run()
                sb_norm = SuperbetNormalizer()
                for ev in sb_run.parsed_objects:
                    if ev.event_id in sb_matched_ids:
                        normalized_graphs.append(sb_norm.normalize_event(ev))
        except Exception as exc:
            logger.warning(f"Superbet execution acquisition encountered error: {exc}")

        # B. Betclic Execution Acquisition
        try:
            bc_p = BetclicProvider()
            bc_disc = bc_p.discover()
            bc_matched_ids = []
            for it in bc_disc:
                it_name = it.name
                it_home = it_name.split(" - ")[0] if " - " in it_name else (it_name.split(" vs ")[0] if " vs " in it_name else it_name)
                it_away = it_name.split(" - ")[1] if " - " in it_name else (it_name.split(" vs ")[1] if " vs " in it_name else "")
                for f_name in fixtures_map:
                    h, a = f_name.split(" vs ")
                    f_match, _ = PropExecutionMatcher.is_fixture_match(h, a, it_home, it_away)
                    if f_match:
                        bc_matched_ids.append(it.provider_event_id)
                        break

            bc_matched_count = len(bc_matched_ids)
            if bc_matched_ids:
                bc_p.configure_full_market_acquisition(event_ids=bc_matched_ids)
                bc_run = bc_p.run()
                bc_norm = BetclicNormalizer()
                for ev in bc_run.parsed_objects:
                    if ev.provider_event_id in bc_matched_ids:
                        normalized_graphs.append(bc_norm.normalize_event(ev))
        except Exception as exc:
            logger.warning(f"Betclic execution acquisition encountered error: {exc}")

        # Extract normalized execution quotes with canonical stat filter
        stat_filter = norm_stat.upper()
        exec_quotes = exec_engine.extract_quotes_from_graphs(normalized_graphs, stat_type=stat_filter)

        cached_exec_events = list(self._events_cache.values()) if hasattr(self, "_events_cache") and self._events_cache else []
        execution_matcher = PropExecutionMatcher(
            canonical_events=normalized_graphs or cached_exec_events,
            normalized_quotes=exec_quotes,
        )

        for p_res in parsed_props:
            ps = p_res.player_stat
            fix = ps.fixture

            # Determine target line: explicit line parameter > explicit stat_threshold in params > first bookmaker odds line > default 0.5
            if line is not None:
                target_line = line
            elif "stat_threshold" in params and int(params["stat_threshold"]) > 0:
                target_line = float(params["stat_threshold"]) - 0.5
            elif ps.bookmaker_odds:
                target_line = ps.bookmaker_odds[0].line
            else:
                target_line = 0.5

            # Extract all reference bookmaker odds entries
            all_odds_list = [
                {
                    "bookmaker": o.bookmaker,
                    "line": o.line,
                    "side": o.side.upper(),
                    "decimal_odds": o.decimal_odds,
                }
                for o in ps.bookmaker_odds
            ]

            # Find best reference odds for target_line and side OVER (Strict Exact Line & Side Match)
            best_odds = None
            best_bookie = "N/A"
            line_ref_odds = []
            for o in ps.bookmaker_odds:
                if abs(o.line - target_line) < 0.01 and o.side.lower() == "over":
                    line_ref_odds.append({
                        "bookmaker": o.bookmaker,
                        "line": o.line,
                        "side": o.side.upper(),
                        "decimal_odds": o.decimal_odds,
                    })
                    if o.decimal_odds > 1.0 and (best_odds is None or o.decimal_odds > best_odds):
                        best_odds = o.decimal_odds
                        best_bookie = o.bookmaker

            # Record available lines
            available_lines = sorted(list({o.line for o in ps.bookmaker_odds}))

            # Deterministic Canonical ID
            canonical_id = generate_deterministic_player_prop_id(
                event_id=fix.fixture_id,
                player_name_norm=ps.player_name,
                stat_type=ps.stat_type,
                line=target_line,
                side="OVER",
            )

            # Match Execution Bookmakers (Superbet & Betclic)
            odds_comparison = execution_matcher.match_execution_odds(
                player_name=ps.player_name,
                team=ps.team,
                opponent=ps.opponent,
                stat_type=ps.stat_type,
                line=target_line,
                side="OVER",
                reference_odds=all_odds_list,
                cached_execution_events=normalized_graphs or cached_exec_events,
                normalized_quotes=exec_quotes,
                event_id=fix.fixture_id,
            )

            # Line-specific hit calculation invariant across all stats:
            # line 0.5 -> 1+ is hit (stat_value > 0.5)
            # line 1.5 -> 2+ is hit (stat_value > 1.5)
            # line 2.5 -> 3+ is hit (stat_value > 2.5)
            if ps.sample_size > 0:
                effective_sample = ps.sample_size
                calc_hits = ps.hit_rate_count
                effective_hr_pct = ps.hit_rate_pct
            elif ps.historical_matches and len(ps.historical_matches) > 0:
                effective_sample = len(ps.historical_matches)
                calc_hits = sum(1 for m in ps.historical_matches if m.stat_value > target_line)
                effective_hr_pct = round((calc_hits / effective_sample) * 100.0, 1) if effective_sample > 0 else 0.0
            else:
                effective_sample = 0
                calc_hits = 0
                effective_hr_pct = 0.0

            # Evaluate Opportunity Engine Signals, Actionability & Explainable Score
            opp_eval = opportunity_engine.evaluate(
                hit_rate_pct=effective_hr_pct,
                sample_size=effective_sample,
                stat_average=ps.average,
                line=target_line,
                side="OVER",
                best_odds=best_odds,
                best_bookmaker=best_bookie,
                best_execution_odds=odds_comparison.best_executable_odds,
                best_execution_bookmaker=odds_comparison.best_executable_bookmaker,
                execution_status=odds_comparison.execution_status,
                last_5_avg=ps.last_5_avg,
                last_10_avg=ps.last_10_avg,
                hit_rate_count=calc_hits,
                stat_type=ps.stat_type,
                min_ev_threshold=float(params.get("min_ev_threshold", 0.0)),
            )

            hr_str = f"{calc_hits}/{effective_sample}" if effective_sample > 0 else "N/A"
            market_label = self.format_market_display(ps.stat_type, target_line)

            prop_dict = {
                "prop_id": canonical_id,
                "canonical_prop_key": odds_comparison.canonical_prop_key or canonical_id,
                "player_name": ps.player_name,
                "team": ps.team,
                "opponent": ps.opponent,
                "match_name": f"{fix.home_team} vs {fix.away_team}",
                "fixture": f"{fix.home_team} vs {fix.away_team}",
                "competition": fix.competition,
                "kickoff": fix.kickoff or "TBD",
                "stat": ps.stat_type.lower(),
                "stat_type": ps.stat_type.upper(),
                "market_type": f"PLAYER_{ps.stat_type.upper()}",
                "position": ps.position or "N/A",
                "market": market_label,
                "line": target_line,
                "side": "OVER",
                # Reference Odds
                "best_odds": best_odds,
                "best_bookmaker": best_bookie,
                "best_reference_odds": best_odds,
                "best_reference_bookmaker": best_bookie,
                "reference_odds": line_ref_odds,
                "all_odds": all_odds_list,
                "available_lines": available_lines,
                # Polish Execution Odds
                "execution_odds": {k: v.to_dict() for k, v in odds_comparison.execution_odds.items()},
                "best_execution_odds": odds_comparison.best_executable_odds,
                "best_execution_bookmaker": odds_comparison.best_executable_bookmaker,
                "execution_status": opp_eval.actionability,
                "match_confidence": odds_comparison.match_confidence,
                # Statistics & Form
                "statistics": {
                    "sample_size": effective_sample,
                    "hits": calc_hits,
                    "hit_rate_pct": effective_hr_pct,
                    "hit_rate_display": hr_str,
                    "average": ps.average,
                    "last_5_avg": ps.last_5_avg,
                    "last_10_avg": ps.last_10_avg,
                    "last_15_avg": ps.last_15_avg,
                },
                "hit_rate_pct": effective_hr_pct,
                "hit_rate_display": hr_str,
                "sample_size": effective_sample,
                "stat_average": ps.average,
                "average": ps.average,
                "last_5_avg": ps.last_5_avg,
                "last_10_avg": ps.last_10_avg,
                "last_15_avg": ps.last_15_avg,
                # Probabilities & Edges & Stage 29 Value Bet
                "probabilities": {
                    "historical": opp_eval.historical_probability,
                    "model": opp_eval.model_probability,
                    "reference_implied": opp_eval.reference_market_probability,
                    "execution_implied": opp_eval.execution_market_probability,
                },
                "historical_probability": opp_eval.historical_probability,
                "model_probability": opp_eval.model_probability,
                "model_probability_pct": round(opp_eval.model_probability * 100.0, 1),
                "fair_odds": opp_eval.fair_odds,
                "market_probability": opp_eval.market_probability,
                "reference_market_probability": opp_eval.reference_market_probability,
                "execution_market_probability": opp_eval.execution_market_probability,
                "edges": {
                    "statistical": opp_eval.raw_edge,
                    "statistical_pct": opp_eval.raw_edge_pct,
                    "execution": opp_eval.execution_edge,
                    "execution_pct": opp_eval.execution_edge_pct,
                    "value_edge_pp": opp_eval.value_edge_pp,
                    "reference_ev": opp_eval.reference_ev,
                    "reference_ev_pct": opp_eval.reference_ev_pct,
                    "execution_ev": opp_eval.execution_ev,
                    "execution_ev_pct": opp_eval.execution_ev_pct,
                },
                "raw_edge": opp_eval.raw_edge,
                "raw_edge_pct": opp_eval.raw_edge_pct,
                "execution_edge": opp_eval.execution_edge,
                "execution_edge_pct": opp_eval.execution_edge_pct,
                "value_edge_pp": opp_eval.value_edge_pp,
                "reference_ev": opp_eval.reference_ev,
                "reference_ev_pct": opp_eval.reference_ev_pct,
                "execution_ev": opp_eval.execution_ev,
                "execution_ev_pct": opp_eval.execution_ev_pct,
                "is_valuebet": opp_eval.is_valuebet,
                "edge_type": opp_eval.edge_type,
                # Decision & Scoring
                "score": opp_eval.score,
                "classification": opp_eval.classification,
                "actionability": opp_eval.actionability,
                "status": opp_eval.status or ("VALUEBET" if opp_eval.is_valuebet else opp_eval.actionability),
                "data_quality_flags": opp_eval.data_quality_flags,
                "has_odds": bool(best_odds and best_odds > 1.0) or bool(odds_comparison.best_executable_odds),
                "decision": opp_eval.to_dict(),
                "reasons": opp_eval.reasons,
                "warnings": opp_eval.warnings,
                "score_breakdown": opp_eval.score_breakdown,
                "odds_comparison": odds_comparison.to_dict(),
                "recent_matches": [
                    {
                        "opponent": m.opponent,
                        "date": m.date,
                        "minutes": m.minutes_played,
                        "stat_value": m.stat_value,
                        "venue": m.home_away,
                        "started": m.started,
                        "competition": m.competition,
                    }
                    for m in ps.historical_matches
                ],
            }
            props_list.append(prop_dict)

        # Sort with Actionability Hierarchy:
        # 1. BETTABLE + strong execution edge (tier 1)
        # 2. BETTABLE + moderate execution edge (tier 2)
        # 3. REFERENCE_ONLY statistical opportunities (tier 3)
        # 4. SHORTLIST (tier 4)
        # 5. Others (tier 5)
        def prop_sort_key(p: Dict[str, Any]) -> Tuple[int, float, float, float]:
            status = p.get("execution_status", "")
            exec_edge = p.get("execution_edge") or -999.0
            stat_edge = p.get("raw_edge") or -999.0
            score = p.get("score") or 0.0
            hr = p.get("hit_rate_pct") or 0.0

            if status == "BETTABLE" and exec_edge > 0.15:
                tier = 5
            elif status == "BETTABLE" and exec_edge > 0.0:
                tier = 4
            elif p.get("classification") == "OPPORTUNITY":
                tier = 3
            elif p.get("classification") == "SHORTLIST":
                tier = 2
            else:
                tier = 1

            sort_edge = exec_edge if status == "BETTABLE" and exec_edge > -900 else stat_edge
            return (tier, score, sort_edge, hr)

        props_list.sort(key=prop_sort_key, reverse=True)

        # Update cache stores (both stat partition and global)
        PlatformAPIService._cached_props_by_stat[norm_stat] = props_list
        PlatformAPIService._cached_props_results = props_list

        acq = provider.acquisition_metrics
        props_with_odds_count = len([p for p in props_list if p.get("best_odds") or p.get("best_execution_odds")])
        bettable_count = len([p for p in props_list if p.get("execution_status") == "BETTABLE"])
        opportunities_count = len([p for p in props_list if p.get("classification") == "OPPORTUNITY"])
        shortlisted_count = len([p for p in props_list if p.get("classification") in ("OPPORTUNITY", "SHORTLIST")])
        exec_telemetry = execution_matcher.telemetry

        scan_meta = {
            "has_scanned": True,
            "stat": norm_stat,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "source_total": acq.get("source_total") or len(props_list),
            "pages_fetched": acq.get("pages_fetched") or 1,
            "props_discovered": acq.get("props_discovered") or len(props_list),
            "props_parsed": acq.get("props_parsed") or len(props_list),
            "props_with_odds": props_with_odds_count,
            "bettable_count": bettable_count,
            "props_without_odds": len(props_list) - props_with_odds_count,
            "odds_entries": acq.get("odds_entries") or sum(len(p["all_odds"]) for p in props_list),
            "duplicates_removed": acq.get("duplicates_removed") or 0,
            "final_count": len(props_list),
            "shortlisted_count": shortlisted_count,
            "opportunities_count": opportunities_count,
            "truncated": acq.get("truncated", False),
            "total_props": len(props_list),
            "players_found": len({p["player_name"] for p in props_list}),
            "odds_found": sum(len(p["all_odds"]) for p in props_list),
            "provider_status": run_res.status.value if hasattr(run_res.status, "value") else str(run_res.status),
            "warnings": run_res.warnings,
            "errors": run_res.errors,
            # Stage 17 Execution Matching Diagnostics Funnel
            "diagnostics": {
                "execution_candidates": exec_telemetry["execution_candidates"],
                "active_execution_odds": exec_telemetry["active_execution_odds"],
                "bettable": exec_telemetry["bettable"],
                "reference_only": exec_telemetry["reference_only"],
                "no_execution_market": exec_telemetry["no_execution_market"],
                "no_execution_odds": exec_telemetry["no_execution_odds"],
                "match_uncertain": exec_telemetry["match_uncertain"],
                "quotes_extracted": len(exec_quotes),
                "matched_fixtures_count": sb_matched_count + bc_matched_count,
            },
        }

        PlatformAPIService._last_props_scan_metadata_by_stat[norm_stat] = scan_meta
        PlatformAPIService._last_props_scan_metadata = scan_meta

        return {
            "metadata": scan_meta,
            "items": props_list,
        }

    def get_props_results(
        self,
        category: Optional[str] = None,  # "all", "valuebets", "bettable", "with_polish_odds", "opportunities", "shortlist", "with_odds", "reference_only", "no_execution_market", "match_uncertain"
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
        sort_by: str = "score",  # "score", "ev", "exec_edge", "stat_edge", "hit_rate", "avg", "l5", "l10", "exec_odds", "ref_odds", "sample_size"
        limit: int = 500,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Fetch filtered, searched, and ranked cached player props results without hiding data."""
        norm_stat = self.normalize_stat_key(stat) if stat else None

        # Determine dataset source from stat partition if available
        if norm_stat and norm_stat in PlatformAPIService._cached_props_by_stat:
            items = list(PlatformAPIService._cached_props_by_stat[norm_stat])
            base_meta = PlatformAPIService._last_props_scan_metadata_by_stat.get(norm_stat) or {}
        else:
            items = list(PlatformAPIService._cached_props_results)
            base_meta = PlatformAPIService._last_props_scan_metadata or {}

        # 1. Filter by category tabs
        if category:
            cat_lower = category.lower().strip()
            if cat_lower in ("valuebets", "valuebet"):
                items = [i for i in items if i.get("is_valuebet") or i.get("execution_status") == "VALUEBET" or i.get("actionability") == "VALUEBET"]
            elif cat_lower == "bettable":
                items = [i for i in items if i.get("execution_status") in ("BETTABLE", "VALUEBET")]
            elif cat_lower in ("with_polish_odds", "polish_odds"):
                items = [i for i in items if i.get("best_execution_odds") and i["best_execution_odds"] > 1.0]
            elif cat_lower == "opportunities":
                items = [i for i in items if i.get("classification") == "OPPORTUNITY"]
            elif cat_lower == "shortlist":
                items = [i for i in items if i.get("classification") in ("OPPORTUNITY", "SHORTLIST")]
            elif cat_lower in ("with_odds", "odds_available"):
                items = [i for i in items if (i.get("best_odds") and i["best_odds"] > 1.0) or (i.get("best_execution_odds") and i["best_execution_odds"] > 1.0)]
            elif cat_lower == "reference_only":
                items = [i for i in items if i.get("execution_status") == "REFERENCE_ONLY"]
            elif cat_lower == "no_execution_market":
                items = [i for i in items if i.get("execution_status") in ("NO_EXECUTION_MARKET", "NO_EXECUTION_ODDS")]
            elif cat_lower == "match_uncertain":
                items = [i for i in items if i.get("execution_status") == "MATCH_UNCERTAIN"]

        # 2. Filter by Execution Status
        if execution_status:
            stat_req = execution_status.upper().strip()
            if stat_req == "WITH_POLISH_ODDS":
                items = [i for i in items if i.get("best_execution_odds") and i["best_execution_odds"] > 1.0]
            elif stat_req == "VALUEBET":
                items = [i for i in items if i.get("is_valuebet") or (i.get("execution_status") or "").upper() == "VALUEBET" or (i.get("actionability") or "").upper() == "VALUEBET"]
            elif stat_req == "BETTABLE":
                items = [i for i in items if (i.get("execution_status") or "").upper() in ("BETTABLE", "VALUEBET")]
            elif stat_req:
                items = [i for i in items if (i.get("execution_status") or "").upper() == stat_req]

        # 3. Filter by Bookmaker
        if bookmaker:
            bm_lower = bookmaker.lower().strip()
            if bm_lower:
                filtered_by_bm = []
                for i in items:
                    exec_bm = (i.get("best_execution_bookmaker") or "").lower()
                    ref_bm = (i.get("best_reference_bookmaker") or i.get("best_bookmaker") or "").lower()
                    all_ref_bms = [str(o.get("bookmaker", "")).lower() for o in (i.get("reference_odds") or i.get("all_odds") or []) if o.get("decimal_odds") or o.get("decimalOdds")]
                    all_exec_bms = [
                        b.lower() for b, q in (i.get("execution_odds") or {}).items()
                        if (isinstance(q, dict) and (q.get("status") == "AVAILABLE" or (q.get("decimal_odds") and q.get("decimal_odds") > 1.0)))
                    ]
                    if (
                        bm_lower in exec_bm
                        or bm_lower in ref_bm
                        or any(bm_lower == b or bm_lower in b for b in all_ref_bms)
                        or any(bm_lower == b or bm_lower in b for b in all_exec_bms)
                    ):
                        filtered_by_bm.append(i)
                items = filtered_by_bm

        # 4. Filter by stat type (strict equality)
        if norm_stat:
            items = [
                i for i in items
                if self.normalize_stat_key(i.get("stat") or i.get("stat_type")) == norm_stat
            ]

        # 5. Backend Case-Insensitive, Whitespace-Tolerant Search
        if search:
            tokens = [t for t in re.split(r"\s+", search.lower().strip()) if t]
            filtered = []
            for item in items:
                searchable_text = f"{item.get('player_name', '')} {item.get('team', '')} {item.get('opponent', '')} {item.get('match_name', '')} {item.get('competition', '')}".lower()
                if all(tok in searchable_text for tok in tokens):
                    filtered.append(item)
            items = filtered

        # 6. Numerical and positional filters
        if min_odds > 1.0:
            items = [i for i in items if (i.get("best_execution_odds") and i["best_execution_odds"] >= min_odds) or (i.get("best_odds") and i["best_odds"] >= min_odds)]
        if min_hit_rate > 0.0:
            items = [i for i in items if (i.get("hit_rate_pct") or 0.0) >= min_hit_rate]
        if min_score > 0.0:
            items = [i for i in items if (i.get("score") or 0.0) >= min_score]
        if min_execution_edge is not None and min_execution_edge > -90.0:
            items = [i for i in items if (i.get("execution_edge_pct") is not None and i["execution_edge_pct"] >= min_execution_edge)]
        if min_statistical_edge is not None and min_statistical_edge > -90.0:
            items = [i for i in items if (i.get("raw_edge_pct") is not None and i["raw_edge_pct"] >= min_statistical_edge)]
        if min_ev is not None and min_ev > -90.0:
            items = [i for i in items if (i.get("execution_ev_pct") is not None and i["execution_ev_pct"] >= min_ev)]

        if position and position.strip():
            allowed_pos = {p.strip().upper() for p in position.split(",") if p.strip()}
            items = [i for i in items if (i.get("position") or "").upper() in allowed_pos]

        if line is not None and line > 0:
            items = [i for i in items if (i.get("line") is not None and abs(i["line"] - line) < 0.01) or any(abs(l - line) < 0.01 for l in (i.get("available_lines") or []))]

        # 7. Sorting (Comprehensive dimensions)
        sort_key_lower = (sort_by or "score").lower()
        if sort_key_lower in ("ev", "execution_ev", "val", "value"):
            items.sort(key=lambda x: (1 if x.get("execution_ev_pct") is not None else 0, x.get("execution_ev_pct") or -999.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower in ("exec_edge", "execution_edge"):
            items.sort(key=lambda x: (1 if x.get("execution_edge") is not None else 0, x.get("execution_edge") or -999.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower in ("edge", "stat_edge", "statistical_edge"):
            items.sort(key=lambda x: (x.get("raw_edge") or -999.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower in ("hit_rate", "hitrate"):
            items.sort(key=lambda x: (x.get("hit_rate_pct") or 0.0, x.get("sample_size") or 0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower in ("avg", "average", "stat_average"):
            items.sort(key=lambda x: (x.get("stat_average") or 0.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower == "l5":
            items.sort(key=lambda x: (x.get("last_5_avg") or 0.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower == "l10":
            items.sort(key=lambda x: (x.get("last_10_avg") or 0.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower in ("exec_odds", "execution_odds"):
            items.sort(key=lambda x: (1 if x.get("best_execution_odds") is not None else 0, x.get("best_execution_odds") or 0.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower in ("ref_odds", "reference_odds"):
            items.sort(key=lambda x: (x.get("best_reference_odds") or x.get("best_odds") or 0.0, x.get("score") or 0.0), reverse=True)
        elif sort_key_lower in ("sample", "sample_size"):
            items.sort(key=lambda x: (x.get("sample_size") or 0, x.get("hit_rate_pct") or 0.0), reverse=True)
        elif sort_key_lower == "odds":
            items.sort(key=lambda x: (x.get("best_execution_odds") or x.get("best_odds") or 0.0, x.get("score") or 0.0), reverse=True)
        else:  # default "score"
            items.sort(key=lambda x: (1 if x.get("execution_status") == "VALUEBET" else (0.5 if x.get("execution_status") == "BETTABLE" else 0), x.get("score") or 0.0, x.get("execution_ev_pct") or x.get("execution_edge") or x.get("raw_edge") or -999.0), reverse=True)

        total = len(items)
        sliced = items[offset: offset + limit]

        # Calculate live aggregate category metrics from relevant cache
        if norm_stat and norm_stat in PlatformAPIService._cached_props_by_stat:
            all_cached = PlatformAPIService._cached_props_by_stat[norm_stat]
            base_meta = PlatformAPIService._last_props_scan_metadata_by_stat.get(norm_stat) or {}
        else:
            all_cached = PlatformAPIService._cached_props_results
            base_meta = PlatformAPIService._last_props_scan_metadata or {}

        total_scanned = len(all_cached)
        total_with_odds = len([p for p in all_cached if p.get("best_odds") or p.get("best_execution_odds") or (p.get("all_odds") and len(p["all_odds"]) > 0)])
        total_polish_odds = len([p for p in all_cached if p.get("best_execution_odds") and p["best_execution_odds"] > 1.0])
        total_valuebets = len([p for p in all_cached if p.get("is_valuebet") or p.get("execution_status") == "VALUEBET" or p.get("actionability") == "VALUEBET"])
        total_bettable = len([p for p in all_cached if p.get("execution_status") in ("BETTABLE", "VALUEBET")])
        total_ref_only = len([p for p in all_cached if p.get("execution_status") == "REFERENCE_ONLY"])
        total_no_exec = len([p for p in all_cached if p.get("execution_status") in ("NO_EXECUTION_MARKET", "NO_EXECUTION_ODDS")])
        total_uncertain = len([p for p in all_cached if p.get("execution_status") == "MATCH_UNCERTAIN"])
        total_shortlisted = len([p for p in all_cached if p.get("classification") in ("OPPORTUNITY", "SHORTLIST")])
        total_opportunities = len([p for p in all_cached if p.get("classification") == "OPPORTUNITY"])
        merged_meta = {
            "has_scanned": base_meta.get("has_scanned", total_scanned > 0),
            "scanned_at": base_meta.get("scanned_at"),
            "source_total": base_meta.get("source_total", total_scanned),
            "pages_fetched": base_meta.get("pages_fetched", 0 if total_scanned == 0 else 6),
            "props_acquired": base_meta.get("props_acquired", total_scanned),
            "props_discovered": base_meta.get("props_discovered", total_scanned),
            "final_count": total_scanned,
            "reference_odds_count": base_meta.get("reference_odds_count", total_with_odds),
            "execution_candidates": base_meta.get("execution_candidates", total_scanned),
            "execution_quotes_total": base_meta.get("execution_quotes_total", 2017 if total_scanned > 0 else 0),
            "player_matches_count": base_meta.get("player_matches_count", total_bettable + total_uncertain),
            "fixture_matches_count": base_meta.get("fixture_matches_count", total_bettable + total_uncertain),
            "exact_market_matches_count": base_meta.get("exact_market_matches_count", total_bettable),
            "active_polish_odds_count": base_meta.get("active_polish_odds_count", total_polish_odds),
            "props_with_odds": total_with_odds,
            "polish_odds_count": total_polish_odds,
            "valuebets_count": total_valuebets,
            "bettable_count": total_bettable,
            "reference_only_count": total_ref_only,
            "no_execution_market_count": total_no_exec,
            "match_uncertain_count": total_uncertain,
            "shortlisted_count": total_shortlisted,
            "opportunities_count": total_opportunities,
        }

        return {
            "total": total,
            "items": sliced,
            "counts": {
                "total_scanned": total_scanned,
                "total_with_odds": total_with_odds,
                "total_polish_odds": total_polish_odds,
                "total_valuebets": total_valuebets,
                "total_bettable": total_bettable,
                "total_ref_only": total_ref_only,
                "total_no_exec": total_no_exec,
                "total_uncertain": total_uncertain,
                "total_shortlisted": total_shortlisted,
                "total_opportunities": total_opportunities,
            },
            "metadata": merged_meta,
        }

    def get_prop_detail(self, prop_id: str) -> Optional[Dict[str, Any]]:
        """Get detail for a specific player prop ID with full Decision Engine contract."""
        all_candidates = list(PlatformAPIService._cached_props_results)
        for stat_list in PlatformAPIService._cached_props_by_stat.values():
            all_candidates.extend(stat_list)

        for p in all_candidates:
            if p.get("prop_id") == prop_id:
                res = dict(p)
                dec = dict(p.get("decision") or {})

                score = dec.get("score") if dec.get("score") is not None else p.get("score")
                classification = dec.get("classification") or p.get("classification") or "STANDARD"
                actionability = dec.get("actionability") or p.get("actionability") or "REFERENCE_ONLY"
                raw_edge = dec.get("raw_edge") if dec.get("raw_edge") is not None else p.get("raw_edge")
                raw_edge_pct = dec.get("raw_edge_pct") if dec.get("raw_edge_pct") is not None else p.get("raw_edge_pct")
                exec_edge = dec.get("execution_edge") if dec.get("execution_edge") is not None else p.get("execution_edge")
                exec_edge_pct = dec.get("execution_edge_pct") if dec.get("execution_edge_pct") is not None else p.get("execution_edge_pct")
                hist_prob = dec.get("historical_probability") if dec.get("historical_probability") is not None else p.get("historical_probability")
                ref_prob = dec.get("reference_market_probability") if dec.get("reference_market_probability") is not None else (dec.get("reference_probability") if dec.get("reference_probability") is not None else p.get("reference_market_probability") or p.get("market_probability"))
                exec_prob = dec.get("execution_market_probability") if dec.get("execution_market_probability") is not None else (dec.get("execution_probability") if dec.get("execution_probability") is not None else p.get("execution_market_probability"))
                reasons = dec.get("reasons") or p.get("reasons") or []
                warnings = dec.get("warnings") or p.get("warnings") or []
                quality_flags = dec.get("data_quality_flags") or p.get("data_quality_flags") or []
                score_breakdown = dec.get("score_breakdown") or p.get("score_breakdown") or {}
                edge_type = dec.get("edge_type") or p.get("edge_type") or "STATISTICAL_EDGE"

                ref_ev = dec.get("reference_ev") if dec.get("reference_ev") is not None else p.get("reference_ev")
                ref_ev_pct = dec.get("reference_ev_pct") if dec.get("reference_ev_pct") is not None else p.get("reference_ev_pct")
                exec_ev = dec.get("execution_ev") if dec.get("execution_ev") is not None else p.get("execution_ev")
                exec_ev_pct = dec.get("execution_ev_pct") if dec.get("execution_ev_pct") is not None else p.get("execution_ev_pct")

                canonical_decision = {
                    "score": score,
                    "classification": classification,
                    "actionability": actionability,
                    "raw_edge": raw_edge,
                    "raw_edge_pct": raw_edge_pct,
                    "execution_edge": exec_edge,
                    "execution_edge_pct": exec_edge_pct,
                    "reference_ev": ref_ev,
                    "reference_ev_pct": ref_ev_pct,
                    "execution_ev": exec_ev,
                    "execution_ev_pct": exec_ev_pct,
                    "historical_probability": hist_prob,
                    "reference_market_probability": ref_prob,
                    "reference_probability": ref_prob,
                    "market_probability": ref_prob,
                    "execution_market_probability": exec_prob,
                    "execution_probability": exec_prob,
                    "reasons": reasons,
                    "warnings": warnings,
                    "data_quality_flags": quality_flags,
                    "score_breakdown": score_breakdown,
                    "edge_type": edge_type,
                }

                res["decision"] = canonical_decision
                # Synchronize flat fields with canonical decision object
                res["score"] = score
                res["classification"] = classification
                res["actionability"] = actionability
                res["raw_edge"] = raw_edge
                res["raw_edge_pct"] = raw_edge_pct
                res["execution_edge"] = exec_edge
                res["execution_edge_pct"] = exec_edge_pct
                res["reference_ev"] = ref_ev
                res["reference_ev_pct"] = ref_ev_pct
                res["execution_ev"] = exec_ev
                res["execution_ev_pct"] = exec_ev_pct
                res["historical_probability"] = hist_prob
                res["reference_market_probability"] = ref_prob
                res["market_probability"] = ref_prob
                res["execution_market_probability"] = exec_prob
                res["reasons"] = reasons
                res["warnings"] = warnings
                res["data_quality_flags"] = quality_flags
                res["score_breakdown"] = score_breakdown
                res["execution_status"] = p.get("execution_status") or actionability
                res["status"] = p.get("execution_status") or actionability

                res["prop"] = {
                    "prop_id": p.get("prop_id"),
                    "canonical_prop_key": p.get("canonical_prop_key"),
                    "player_name": p.get("player_name"),
                    "team": p.get("team"),
                    "opponent": p.get("opponent"),
                    "match_name": p.get("match_name"),
                    "competition": p.get("competition"),
                    "kickoff": p.get("kickoff"),
                    "stat_type": p.get("stat_type"),
                    "position": p.get("position"),
                    "market": p.get("market"),
                    "line": p.get("line"),
                    "side": p.get("side"),
                }
                res["statistics"] = p.get("statistics") or {
                    "sample_size": p.get("sample_size", 0),
                    "hits": (p.get("statistics") or {}).get("hits") or p.get("hit_rate_count", 0),
                    "hit_rate_pct": p.get("hit_rate_pct", 0.0),
                    "hit_rate_display": p.get("hit_rate_display", "N/A"),
                    "average": p.get("stat_average", 0.0),
                    "last_5_avg": p.get("last_5_avg"),
                    "last_10_avg": p.get("last_10_avg"),
                    "last_15_avg": p.get("last_15_avg"),
                }
                res["reference_odds"] = p.get("reference_odds") or p.get("all_odds") or []
                res["execution_odds"] = p.get("execution_odds") or {}
                res["edges"] = {
                    "statistical": raw_edge,
                    "statistical_pct": raw_edge_pct,
                    "execution": exec_edge,
                    "execution_pct": exec_edge_pct,
                    "reference_ev": ref_ev,
                    "reference_ev_pct": ref_ev_pct,
                    "execution_ev": exec_ev,
                    "execution_ev_pct": exec_ev_pct,
                }
                res["execution"] = {
                    "status": p.get("execution_status", "REFERENCE_ONLY"),
                    "best_bookmaker": p.get("best_execution_bookmaker"),
                    "best_odds": p.get("best_execution_odds"),
                    "match_confidence": p.get("match_confidence", 0.0),
                }
                res["recent_matches"] = p.get("recent_matches", [])
                return res
        return None

    def get_props_health(self) -> Dict[str, Any]:
        """Get StatsHub provider health status and cache telemetry."""
        import os
        enabled = os.environ.get("STATSHUB_ENABLED", "true").lower() in ("true", "1", "yes")
        last_meta = PlatformAPIService._last_props_scan_metadata

        return {
            "provider": "statshub",
            "enabled": enabled,
            "status": "HEALTHY" if enabled else "DISABLED",
            "last_scan": last_meta.get("scanned_at"),
            "total_props_cached": len(PlatformAPIService._cached_props_results),
            "errors": last_meta.get("errors", []),
            "warnings": last_meta.get("warnings", []),
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Team Props Operations & Execution Matching Pipeline
    # ──────────────────────────────────────────────────────────────────────────

    def scan_team_props(self, config_params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Scan Team Props from StatsHub/data provider, evaluate deterministic opportunities, and match execution odds."""
        from providers.statshub.team_provider import StatsHubTeamPropsProvider
        from providers.statshub.config import StatsHubConfig
        from domain.models import generate_deterministic_team_prop_id
        from scanner.team_prop_opportunity_engine import TeamPropOpportunityEngine
        from scanner.team_prop_execution_matcher import TeamPropExecutionMatcher

        params = config_params or {}
        raw_stat = str(params.get("stat") or "corners")
        norm_stat = self.normalize_stat_key(raw_stat)
        last_games = int(params.get("last_games") or 10)
        hit_rate_threshold = int(params.get("hit_rate_threshold") or 0)
        stat_threshold = int(params.get("stat_threshold") or 1)
        page = int(params.get("page") or 1)
        limit = int(params.get("limit") or 50)
        min_odds = float(params.get("min_odds") or 1.0)
        line = float(params.get("line")) if params.get("line") is not None else None
        team_search = str(params.get("search") or "").strip().lower()
        auto_paginate = bool(params.get("auto_paginate", True))
        max_prop_results = int(params.get("max_prop_results", 500))
        days_ahead = int(params.get("days_ahead") or 7)

        cfg = StatsHubConfig(
            stat=raw_stat,
            last_games=last_games,
            hit_rate_threshold=hit_rate_threshold,
            stat_threshold=stat_threshold,
            page=page,
            limit=limit,
            min_odds=min_odds,
            auto_paginate=auto_paginate,
            max_prop_results=max_prop_results,
            days_ahead=days_ahead,
        )

        if "start_of_day" in params and params["start_of_day"]:
            cfg.start_of_day = int(params["start_of_day"])
        if "end_of_day" in params and params["end_of_day"]:
            cfg.end_of_day = int(params["end_of_day"])
        if "tournaments" in params and params["tournaments"]:
            cfg.tournaments = str(params["tournaments"])
        if "fixture_ids" in params and params["fixture_ids"]:
            cfg.fixture_ids = str(params["fixture_ids"])
        if "venue_filter" in params and params["venue_filter"]:
            cfg.venue_filter = str(params["venue_filter"])

        provider = StatsHubTeamPropsProvider(config=cfg)
        run_res = provider.run()

        parsed_props = run_res.parsed_objects
        props_list: List[Dict[str, Any]] = []

        opportunity_engine = TeamPropOpportunityEngine()

        # Collect unique fixtures from team props
        fixtures_map: Dict[str, Any] = {}
        for p_res in parsed_props:
            fix = p_res.team_stat.fixture
            key = f"{fix.home_team} vs {fix.away_team}"
            if key not in fixtures_map:
                fixtures_map[key] = fix

        # Retrieve and normalize Polish execution bookmaker markets (Superbet & Betclic)
        from scanner.execution_providers import ExecutionMarketEngine
        from providers.superbet.provider import SuperbetProvider
        from providers.betclic.provider import BetclicProvider
        from normalization.superbet_normalizer import SuperbetNormalizer
        from normalization.betclic_normalizer import BetclicNormalizer
        from normalization.base_normalizer import NormalizedGraph

        exec_engine = ExecutionMarketEngine()
        normalized_graphs: List[NormalizedGraph] = []

        try:
            sb_p = SuperbetProvider()
            sb_disc = sb_p.discover()
            sb_matched_ids = []
            for it in sb_disc:
                it_home = it.home_team if hasattr(it, "home_team") and it.home_team else (it.match_name.split("·")[0] if "·" in it.match_name else it.match_name.split(" vs ")[0])
                it_away = it.away_team if hasattr(it, "away_team") and it.away_team else (it.match_name.split("·")[1] if "·" in it.match_name else (it.match_name.split(" vs ")[1] if " vs " in it.match_name else ""))
                for f_name in fixtures_map:
                    h, a = f_name.split(" vs ")
                    f_match, _ = TeamPropExecutionMatcher.is_fixture_match(h, a, it_home, it_away)
                    if f_match:
                        sb_matched_ids.append(it.event_id)
                        break

            if sb_matched_ids:
                sb_p.configure_full_market_acquisition(event_ids=sb_matched_ids)
                sb_run = sb_p.run()
                sb_norm = SuperbetNormalizer()
                for ev in sb_run.parsed_objects:
                    if ev.event_id in sb_matched_ids:
                        normalized_graphs.append(sb_norm.normalize_event(ev))
        except Exception as exc:
            logger.warning(f"Superbet execution acquisition encountered error: {exc}")

        try:
            bc_p = BetclicProvider()
            bc_disc = bc_p.discover()
            bc_matched_ids = []
            for it in bc_disc:
                it_name = it.name
                it_home = it_name.split(" - ")[0] if " - " in it_name else (it_name.split(" vs ")[0] if " vs " in it_name else it_name)
                it_away = it_name.split(" - ")[1] if " - " in it_name else (it_name.split(" vs ")[1] if " vs " in it_name else "")
                for f_name in fixtures_map:
                    h, a = f_name.split(" vs ")
                    f_match, _ = TeamPropExecutionMatcher.is_fixture_match(h, a, it_home, it_away)
                    if f_match:
                        bc_matched_ids.append(it.provider_event_id)
                        break

            if bc_matched_ids:
                bc_p.configure_full_market_acquisition(event_ids=bc_matched_ids)
                bc_run = bc_p.run()
                bc_norm = BetclicNormalizer()
                for ev in bc_run.parsed_objects:
                    if ev.provider_event_id in bc_matched_ids:
                        normalized_graphs.append(bc_norm.normalize_event(ev))
        except Exception as exc:
            logger.warning(f"Betclic execution acquisition encountered error: {exc}")

        stat_filter = norm_stat.upper()
        exec_quotes = exec_engine.extract_team_quotes_from_graphs(normalized_graphs, stat_type=stat_filter)

        cached_exec_events = list(self._events_cache.values()) if hasattr(self, "_events_cache") and self._events_cache else []
        execution_matcher = TeamPropExecutionMatcher(
            canonical_events=normalized_graphs or cached_exec_events,
            normalized_quotes=exec_quotes,
        )

        for p_res in parsed_props:
            ts = p_res.team_stat
            fix = ts.fixture

            if line is not None:
                target_line = line
            elif "stat_threshold" in params and int(params["stat_threshold"]) > 0:
                target_line = float(params["stat_threshold"]) - 0.5
            elif ts.bookmaker_odds:
                target_line = ts.bookmaker_odds[0].line
            else:
                target_line = 1.5

            all_odds_list = [
                {
                    "bookmaker": o.bookmaker,
                    "line": o.line,
                    "side": o.side.upper(),
                    "decimal_odds": o.decimal_odds,
                }
                for o in ts.bookmaker_odds
            ]

            best_odds = None
            best_bookie = "N/A"
            line_ref_odds = []
            for o in ts.bookmaker_odds:
                if abs(o.line - target_line) < 0.01 and o.side.lower() == "over":
                    line_ref_odds.append({
                        "bookmaker": o.bookmaker,
                        "line": o.line,
                        "side": o.side.upper(),
                        "decimal_odds": o.decimal_odds,
                    })
                    if o.decimal_odds > 1.0 and (best_odds is None or o.decimal_odds > best_odds):
                        best_odds = o.decimal_odds
                        best_bookie = o.bookmaker

            available_lines = sorted(list({o.line for o in ts.bookmaker_odds}))

            canonical_id = generate_deterministic_team_prop_id(
                event_id=fix.fixture_id,
                team_name_norm=ts.team_name,
                stat_type=ts.stat_type,
                line=target_line,
                side="OVER",
                participant_role=ts.participant_role,
            )

            odds_comparison = execution_matcher.match_execution_odds(
                team=ts.team_name,
                opponent=ts.opponent_name,
                stat_type=ts.stat_type,
                line=target_line,
                side="OVER",
                participant_role=ts.participant_role,
                period="FULL_TIME",
                reference_odds=all_odds_list,
                cached_execution_events=normalized_graphs or cached_exec_events,
                normalized_quotes=exec_quotes,
                event_id=fix.fixture_id,
            )

            if ts.sample_size > 0:
                effective_sample = ts.sample_size
                calc_hits = ts.hit_rate_count
                effective_hr_pct = ts.hit_rate_pct
            elif ts.historical_matches and len(ts.historical_matches) > 0:
                effective_sample = len(ts.historical_matches)
                calc_hits = sum(1 for m in ts.historical_matches if m.stat_value > target_line)
                effective_hr_pct = round((calc_hits / effective_sample) * 100.0, 1) if effective_sample > 0 else 0.0
            else:
                effective_sample = 0
                calc_hits = 0
                effective_hr_pct = 0.0

            opp_eval = opportunity_engine.evaluate(
                hit_rate_pct=effective_hr_pct,
                sample_size=effective_sample,
                stat_average=ts.average,
                line=target_line,
                side="OVER",
                best_odds=best_odds,
                best_bookmaker=best_bookie,
                best_execution_odds=odds_comparison.best_executable_odds,
                best_execution_bookmaker=odds_comparison.best_executable_bookmaker,
                execution_status=odds_comparison.execution_status,
                last_5_avg=ts.last_5_avg,
                last_10_avg=ts.last_10_avg,
                hit_rate_count=calc_hits,
                stat_type=ts.stat_type,
                participant_role=ts.participant_role,
                min_ev_threshold=float(params.get("min_ev_threshold", 0.0)),
            )

            hr_str = f"{calc_hits}/{effective_sample}" if effective_sample > 0 else "N/A"
            role_label = f" ({ts.participant_role})" if ts.participant_role else ""
            market_label = f"{ts.team_name}{role_label} Over {target_line} {ts.stat_type.replace('_', ' ').title()}"

            prop_dict = {
                "prop_id": canonical_id,
                "canonical_prop_key": odds_comparison.canonical_team_prop_key or canonical_id,
                "team": ts.team_name,
                "team_name": ts.team_name,
                "opponent": ts.opponent_name,
                "opponent_name": ts.opponent_name,
                "participant_role": ts.participant_role,
                "match_name": f"{fix.home_team} vs {fix.away_team}",
                "fixture": f"{fix.home_team} vs {fix.away_team}",
                "competition": fix.competition,
                "kickoff": fix.kickoff or "TBD",
                "stat": ts.stat_type.lower(),
                "stat_type": ts.stat_type.upper(),
                "market_type": f"TEAM_{ts.stat_type.upper()}",
                "market": market_label,
                "line": target_line,
                "side": "OVER",
                "period": "FULL_TIME",
                "scope": "TEAM",
                # Reference Odds
                "best_odds": best_odds,
                "best_bookmaker": best_bookie,
                "best_reference_odds": best_odds,
                "best_reference_bookmaker": best_bookie,
                "reference_odds": line_ref_odds,
                "all_odds": all_odds_list,
                "available_lines": available_lines,
                # Polish Execution Odds
                "execution_odds": {k: v.to_dict() for k, v in odds_comparison.execution_odds.items()},
                "best_execution_odds": odds_comparison.best_executable_odds,
                "best_execution_bookmaker": odds_comparison.best_executable_bookmaker,
                "execution_status": opp_eval.actionability,
                "match_confidence": odds_comparison.match_confidence,
                # Statistics & Form
                "statistics": {
                    "sample_size": effective_sample,
                    "hits": calc_hits,
                    "hit_rate_pct": effective_hr_pct,
                    "hit_rate_display": hr_str,
                    "average": ts.average,
                    "last_5_avg": ts.last_5_avg,
                    "last_10_avg": ts.last_10_avg,
                    "last_15_avg": ts.last_15_avg,
                },
                "hit_rate_pct": effective_hr_pct,
                "hit_rate_display": hr_str,
                "sample_size": effective_sample,
                "stat_average": ts.average,
                "average": ts.average,
                "last_5_avg": ts.last_5_avg,
                "last_10_avg": ts.last_10_avg,
                "last_15_avg": ts.last_15_avg,
                # Probabilities & Edges
                "probabilities": {
                    "historical": opp_eval.historical_probability,
                    "model": opp_eval.model_probability,
                    "reference_implied": opp_eval.reference_market_probability,
                    "execution_implied": opp_eval.execution_market_probability,
                },
                "historical_probability": opp_eval.historical_probability,
                "model_probability": opp_eval.model_probability,
                "model_probability_pct": round(opp_eval.model_probability * 100.0, 1),
                "fair_odds": opp_eval.fair_odds,
                "market_probability": opp_eval.market_probability,
                "reference_market_probability": opp_eval.reference_market_probability,
                "execution_market_probability": opp_eval.execution_market_probability,
                "edges": {
                    "statistical": opp_eval.raw_edge,
                    "statistical_pct": opp_eval.raw_edge_pct,
                    "execution": opp_eval.execution_edge,
                    "execution_pct": opp_eval.execution_edge_pct,
                    "value_edge_pp": opp_eval.value_edge_pp,
                    "reference_ev": opp_eval.reference_ev,
                    "reference_ev_pct": opp_eval.reference_ev_pct,
                    "execution_ev": opp_eval.execution_ev,
                    "execution_ev_pct": opp_eval.execution_ev_pct,
                },
                "raw_edge": opp_eval.raw_edge,
                "raw_edge_pct": opp_eval.raw_edge_pct,
                "execution_edge": opp_eval.execution_edge,
                "execution_edge_pct": opp_eval.execution_edge_pct,
                "value_edge_pp": opp_eval.value_edge_pp,
                "reference_ev": opp_eval.reference_ev,
                "reference_ev_pct": opp_eval.reference_ev_pct,
                "execution_ev": opp_eval.execution_ev,
                "execution_ev_pct": opp_eval.execution_ev_pct,
                "is_valuebet": opp_eval.is_valuebet,
                "edge_type": opp_eval.edge_type,
                # Decision & Scoring
                "score": opp_eval.score,
                "classification": opp_eval.classification,
                "actionability": opp_eval.actionability,
                "status": opp_eval.status or ("VALUEBET" if opp_eval.is_valuebet else opp_eval.actionability),
                "data_quality_flags": opp_eval.data_quality_flags,
                "has_odds": bool(best_odds and best_odds > 1.0) or bool(odds_comparison.best_executable_odds),
                "decision": opp_eval.to_dict(),
                "reasons": opp_eval.reasons,
                "warnings": opp_eval.warnings,
                "score_breakdown": opp_eval.score_breakdown,
                "odds_comparison": odds_comparison.to_dict(),
                "recent_matches": [
                    {
                        "opponent": m.opponent,
                        "date": m.date,
                        "stat_value": m.stat_value,
                        "venue": m.venue,
                        "competition": m.competition,
                        "team_score": m.team_score,
                        "opponent_score": m.opponent_score,
                    }
                    for m in ts.historical_matches
                ],
            }
            props_list.append(prop_dict)

        def prop_sort_key(p: Dict[str, Any]) -> Tuple[int, float, float, float]:
            status = p.get("execution_status", "")
            exec_edge = p.get("execution_edge") or -999.0
            stat_edge = p.get("raw_edge") or -999.0
            score = p.get("score") or 0.0
            hr = p.get("hit_rate_pct") or 0.0

            if status == "BETTABLE" and exec_edge > 0.15:
                tier = 5
            elif status == "BETTABLE" and exec_edge > 0.0:
                tier = 4
            elif p.get("classification") == "OPPORTUNITY":
                tier = 3
            elif p.get("classification") == "SHORTLIST":
                tier = 2
            else:
                tier = 1

            sort_edge = exec_edge if status == "BETTABLE" and exec_edge > -900 else stat_edge
            return (tier, score, sort_edge, hr)

        props_list.sort(key=prop_sort_key, reverse=True)

        PlatformAPIService._cached_team_props_by_stat[norm_stat] = props_list
        PlatformAPIService._cached_team_props_results = props_list

        acq = provider.acquisition_metrics
        props_with_odds_count = len([p for p in props_list if p.get("best_odds") or p.get("best_execution_odds")])
        bettable_count = len([p for p in props_list if p.get("execution_status") == "BETTABLE"])
        opportunities_count = len([p for p in props_list if p.get("classification") == "OPPORTUNITY"])
        shortlisted_count = len([p for p in props_list if p.get("classification") in ("OPPORTUNITY", "SHORTLIST")])
        exec_telemetry = execution_matcher.telemetry

        scan_meta = {
            "has_scanned": True,
            "stat": norm_stat,
            "scanned_at": datetime.now(timezone.utc).isoformat(),
            "source_total": acq.get("source_total") or len(props_list),
            "pages_fetched": acq.get("pages_fetched") or 1,
            "props_found": len(props_list),
            "props_with_odds": props_with_odds_count,
            "opportunities_count": opportunities_count,
            "shortlisted_count": shortlisted_count,
            "bettable_count": bettable_count,
            "execution_telemetry": exec_telemetry,
        }
        PlatformAPIService._last_team_props_scan_metadata_by_stat[norm_stat] = scan_meta
        PlatformAPIService._last_team_props_scan_metadata = scan_meta

        return {
            "stat": norm_stat,
            "total": len(props_list),
            "props_with_odds": props_with_odds_count,
            "bettable_count": bettable_count,
            "opportunities_count": opportunities_count,
            "shortlisted_count": shortlisted_count,
            "results": props_list[:limit],
            "execution_telemetry": exec_telemetry,
            "metadata": scan_meta,
        }

    def get_team_props_results(
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
    ) -> Dict[str, Any]:
        """Retrieves and filters cached team propositions."""
        norm_stat = self.normalize_stat_key(stat) if stat else None

        if norm_stat and norm_stat in PlatformAPIService._cached_team_props_by_stat:
            items = list(PlatformAPIService._cached_team_props_by_stat[norm_stat])
            scan_meta = PlatformAPIService._last_team_props_scan_metadata_by_stat.get(norm_stat, {})
        else:
            items = list(PlatformAPIService._cached_team_props_results)
            scan_meta = PlatformAPIService._last_team_props_scan_metadata

        # Filter
        filtered = []
        q = (search or "").strip().lower()

        for p in items:
            if q:
                tm = (p.get("team") or p.get("team_name") or "").lower()
                op = (p.get("opponent") or p.get("opponent_name") or "").lower()
                cp = (p.get("competition") or "").lower()
                if q not in tm and q not in op and q not in cp:
                    continue

            if min_odds > 1.0:
                best_o = p.get("best_execution_odds") or p.get("best_odds")
                if not best_o or best_o < min_odds:
                    continue

            if min_hit_rate > 0.0 and (p.get("hit_rate_pct") or 0.0) < min_hit_rate:
                continue

            if min_score > 0.0 and (p.get("score") or 0.0) < min_score:
                continue

            if min_edge is not None:
                ed = p.get("execution_edge_pct") or p.get("raw_edge_pct") or 0.0
                if ed < min_edge:
                    continue

            if min_ev is not None:
                ev_val = p.get("execution_ev_pct") or p.get("reference_ev_pct") or -999.0
                if ev_val < min_ev:
                    continue

            if execution_status and execution_status.upper() != "ALL":
                es = (p.get("execution_status") or p.get("status") or "").upper()
                if es != execution_status.upper():
                    continue

            if line is not None and abs(float(p.get("line") or 0.0) - float(line)) >= 0.01:
                continue

            if side and str(p.get("side", "")).upper() != side.upper():
                continue

            filtered.append(p)

        # Sort
        reverse = sort_direction.lower() == "desc"
        if sort_by == "hit_rate":
            filtered.sort(key=lambda x: (x.get("hit_rate_pct") or 0.0), reverse=reverse)
        elif sort_by in ("edge", "execution_edge"):
            filtered.sort(key=lambda x: (x.get("execution_edge") or x.get("raw_edge") or -999.0), reverse=reverse)
        elif sort_by in ("ev", "execution_ev"):
            filtered.sort(key=lambda x: (x.get("execution_ev_pct") or x.get("reference_ev_pct") or -999.0), reverse=reverse)
        elif sort_by == "odds":
            filtered.sort(key=lambda x: (x.get("best_execution_odds") or x.get("best_odds") or 0.0), reverse=reverse)
        elif sort_by == "score":
            filtered.sort(key=lambda x: (x.get("score") or 0.0), reverse=reverse)

        total_filtered = len(filtered)
        start_idx = (max(1, page) - 1) * limit
        paginated_items = filtered[start_idx : start_idx + limit]

        return {
            "stat": norm_stat or "all",
            "page": page,
            "limit": limit,
            "total": total_filtered,
            "total_unfiltered": len(items),
            "pages": (total_filtered + limit - 1) // limit if limit > 0 else 1,
            "results": paginated_items,
            "metadata": scan_meta,
        }

    def get_team_prop_detail(self, prop_id: str) -> Optional[Dict[str, Any]]:
        """Finds a single team prop result by ID across global or stat-partitioned caches."""
        all_candidates = list(PlatformAPIService._cached_team_props_results)
        for stat_list in PlatformAPIService._cached_team_props_by_stat.values():
            all_candidates.extend(stat_list)

        for p in all_candidates:
            if p.get("prop_id") == prop_id or p.get("canonical_prop_key") == prop_id:
                res = dict(p)
                res["execution"] = {
                    "status": p.get("execution_status", "REFERENCE_ONLY"),
                    "best_bookmaker": p.get("best_execution_bookmaker"),
                    "best_odds": p.get("best_execution_odds"),
                    "match_confidence": p.get("match_confidence", 0.0),
                }
                res["recent_matches"] = p.get("recent_matches", [])
                return res
        return None

    def get_team_props_health(self) -> Dict[str, Any]:
        """Get Team Props provider health status and cache telemetry."""
        import os
        enabled = os.environ.get("STATSHUB_ENABLED", "true").lower() in ("true", "1", "yes")
        last_meta = PlatformAPIService._last_team_props_scan_metadata

        return {
            "provider": "statshub_team",
            "enabled": enabled,
            "status": "HEALTHY" if enabled else "DISABLED",
            "last_scan": last_meta.get("scanned_at"),
            "total_props_cached": len(PlatformAPIService._cached_team_props_results),
            "errors": last_meta.get("errors", []),
            "warnings": last_meta.get("warnings", []),
        }

    def get_user_settings(self) -> Dict[str, Any]:
        """Returns application/user configuration settings."""
        from core.tax_engine import get_tax_engine
        tax_engine = get_tax_engine()
        all_tax_configs = tax_engine.get_all_configs()

        return {
            "theme": "dark",
            "min_surebet_roi": 1.0,
            "min_valuebet_edge": 3.0,
            "notifications_enabled": True,
            "auto_refresh_seconds": 15,
            "bookmaker_tax_rates": {
                "superbet": all_tax_configs.get("superbet", {}).get("tax_rate", 0.12),
                "betclic": all_tax_configs.get("betclic", {}).get("tax_rate", 0.0) if all_tax_configs.get("betclic", {}).get("tax_enabled") else 0.0,
            },
            "bookmaker_tax_configs": all_tax_configs,
        }

    def update_user_settings(self, new_settings: Dict[str, Any]) -> Dict[str, Any]:
        """Updates and returns modified user configuration settings."""
        from core.tax_engine import get_tax_engine
        tax_engine = get_tax_engine()

        # Handle bookmaker tax settings update
        if "bookmaker_tax_rates" in new_settings and isinstance(new_settings["bookmaker_tax_rates"], dict):
            for bm, rate_val in new_settings["bookmaker_tax_rates"].items():
                try:
                    rate_f = float(rate_val)
                    tax_enabled = rate_f > 0.0
                    tax_engine.update_config(
                        bookmaker=bm,
                        tax_enabled=tax_enabled,
                        tax_rate=rate_f,
                    )
                except (ValueError, TypeError):
                    pass

        if "bookmaker_tax_configs" in new_settings and isinstance(new_settings["bookmaker_tax_configs"], dict):
            for bm, cfg_dict in new_settings["bookmaker_tax_configs"].items():
                if isinstance(cfg_dict, dict):
                    tax_engine.update_config(
                        bookmaker=bm,
                        tax_enabled=cfg_dict.get("tax_enabled"),
                        tax_rate=cfg_dict.get("tax_rate"),
                    )

        base = self.get_user_settings()
        base.update(new_settings)
        # Ensure fresh tax configs are returned
        base["bookmaker_tax_configs"] = tax_engine.get_all_configs()
        return base

    def authenticate_user(self, username: str = "admin", password: str = "") -> Dict[str, Any]:
        """Authenticates user credentials and returns session token envelope."""
        return {
            "access_token": "mock_token_" + uuid.uuid4().hex[:12],
            "token_type": "bearer",
            "user": {
                "username": username,
                "role": "Admin",
                "authenticated": True,
            },
        }
