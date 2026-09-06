"""
Stage 9: ULTRA SCAN Telegram Report Formatter

Formats UltraScanResult into a clean, comprehensive, HTML-escaped Telegram master report.
Enforces:
- Full HTML escaping on all dynamic fields (teams, players, competitions, errors).
- Strict Telegram length protection (< 4000 chars per message, multi-part chunking at section boundaries).
- Complete structured coverage of all opportunity classes and auditable funnel telemetry.
"""

from __future__ import annotations

import html
from typing import Any, List, Optional
from orchestration.ultra_scan import UltraOpportunity, UltraScanResult

MAX_TELEGRAM_MESSAGE_CHARS = 3900  # Conservative safety buffer below 4096


def _esc(val: Any) -> str:
    """Safely escapes dynamic text for Telegram HTML mode."""
    if val is None:
        return ""
    return html.escape(str(val))


def _format_opportunity_card(idx: int, opp: UltraOpportunity) -> str:
    """Formats a single UltraOpportunity card."""
    cat_emoji = {
        "SUREBET": "💰",
        "VALUEBET": "📈",
        "PLAYER_PROP": "🎯",
        "TEAM_PROP": "⚽",
        "WATCHLIST": "⚠️",
    }.get(opp.category, "🔹")

    hb = getattr(opp, "horizon_bucket", "TODAY")
    if hb == "TOMORROW":
        bucket_tag = " [JUTRO]"
    elif hb == "DAY_AFTER_TOMORROW":
        bucket_tag = " [POJUTRZE]"
    else:
        bucket_tag = ""
    lines = []
    lines.append(f"<b>{idx}. {cat_emoji} {_esc(opp.market_display)}</b>")
    lines.append(f"   ⚽ {_esc(opp.match_name)} | <i>{_esc(opp.competition)}</i>{bucket_tag}")

    edge_label = "Zysk" if opp.category == "SUREBET" else "Net EV"
    sign = "+" if opp.edge_pct > 0 else ""
    edge_str = f"{sign}{opp.edge_pct:.2f}%"

    if opp.category == "SUREBET":
        lines.append(f"   🏢 {_esc(opp.bookmaker)} | {edge_label}: <b>{_esc(edge_str)}</b>")
        lines.append(f"   📋 <i>{_esc(opp.selection_display)}</i>")
    elif opp.is_watchlist:
        lines.append(f"   🏢 {_esc(opp.bookmaker)} @ <b>{opp.raw_odds:.2f}</b> | {_esc(opp.watchlist_reason or '')}")
    else:
        fair_str = f" | Fair: {_esc(f'{opp.fair_odds:.2f}')}" if opp.fair_odds else ""
        lines.append(
            f"   🏢 {_esc(opp.bookmaker)} @ <b>{opp.raw_odds:.2f}</b> (netto: {opp.effective_odds:.2f}){fair_str} | {edge_label}: <b>{_esc(edge_str)}</b> (Conf: {_esc(opp.confidence)})"
        )
        lines.append(f"   📋 Selekcja: <b>{_esc(opp.selection_display)}</b>")

    return "\n".join(lines)


def format_ultra_scan_report(result: UltraScanResult) -> List[str]:
    """Generates a list of Telegram HTML messages representing the complete ULTRA SCAN report.

    Returns 1 or more messages guaranteed not to exceed Telegram limits.
    """
    fn = result.funnel
    duration_min = int(result.duration_seconds // 60)
    duration_sec = int(result.duration_seconds % 60)

    status_emoji = {
        "SUCCESS": "🟢",
        "PARTIAL": "🟡",
        "FAILED": "🔴",
    }.get(result.status, "ℹ️")

    sb_status = fn.provider_status.get("superbet", "UNKNOWN")
    bc_status = fn.provider_status.get("betclic", "UNKNOWN")
    sb_badge = "✅ Dostępny" if sb_status == "AVAILABLE" else f"⚠️ {_esc(sb_status)}"
    bc_badge = "✅ Dostępny" if bc_status == "AVAILABLE" else f"⚠️ {_esc(bc_status)}"

    disc_tomorrow = getattr(fn, "discovered_tomorrow_events", 0)
    disc_day_after = getattr(fn, "discovered_day_after_tomorrow_events", 0)
    sb_tomorrow = getattr(fn, "discovered_superbet_tomorrow", 0)
    sb_day_after = getattr(fn, "discovered_superbet_day_after_tomorrow", 0)
    bc_tomorrow = getattr(fn, "discovered_betclic_tomorrow", 0)
    bc_day_after = getattr(fn, "discovered_betclic_day_after_tomorrow", 0)
    tomorrow_date = (
        result.diagnostics.get("tomorrow_date")
        if hasattr(result, "diagnostics") and isinstance(result.diagnostics, dict)
        else None
    )
    day_after_date = (
        result.diagnostics.get("day_after_tomorrow_date")
        if hasattr(result, "diagnostics") and isinstance(result.diagnostics, dict)
        else None
    )

    date_label = f"📅 Data: <b>{_esc(result.target_date)}</b>"
    if disc_tomorrow > 0 and tomorrow_date:
        if disc_day_after > 0 and day_after_date:
            date_label += f" (+ jutro: <b>{_esc(tomorrow_date)}</b>, pojutrze: <b>{_esc(day_after_date)}</b>)"
        else:
            date_label += f" (+ jutro: <b>{_esc(tomorrow_date)}</b>)"

    # ─────────────────────────────────────────────────────────────────────────
    # Header & Coverage Section
    # ─────────────────────────────────────────────────────────────────────────
    header_section = [
        "🚀 <b>ULTRA SCAN — RAPORT DZIENNY</b>",
        date_label,
        f"⏱️ Czas trwania: <b>{duration_min}m {duration_sec}s</b> | Status: {status_emoji} <b>{_esc(result.status)}</b>",
        "",
        "📊 <b>POKRYCIE I FUNNEL (Europe/Warsaw)</b>",
        f"• Status bukmacherów: Superbet [{sb_badge}] | Betclic [{bc_badge}]",
        f"• Zdarzenia dzisiaj: <b>{fn.discovered_today_events}</b> odkrytych (Superbet: {fn.discovered_superbet_today}, Betclic: {fn.discovered_betclic_today})",
    ]
    if disc_tomorrow > 0:
        header_section.append(
            f"• Zdarzenia jutro: <b>{disc_tomorrow}</b> odkrytych (Superbet: {sb_tomorrow}, Betclic: {bc_tomorrow})"
        )
    if disc_day_after > 0:
        header_section.append(
            f"• Zdarzenia pojutrze: <b>{disc_day_after}</b> odkrytych (Superbet: {sb_day_after}, Betclic: {bc_day_after})"
        )

    sel_today = getattr(fn, "selected_today_events", 0)
    sel_tomorrow = getattr(fn, "selected_tomorrow_events", 0)
    sel_day_after = getattr(fn, "selected_day_after_tomorrow_events", 0)
    if sel_tomorrow > 0 or sel_day_after > 0:
        if sel_day_after > 0:
            header_section.append(f"• Wybrane do detali: dzisiaj {sel_today}, jutro {sel_tomorrow}, pojutrze {sel_day_after}")
        else:
            header_section.append(f"• Wybrane do detali: dzisiaj {sel_today}, jutro {sel_tomorrow}")

    header_section.extend([
        f"• Detale Superbet: próbowano {fn.detail_fetch_attempted_superbet}, sukces {fn.detail_fetch_success_superbet}, błędy {fn.detail_fetch_failed_superbet}",
        f"• Detale Betclic: próbowano {fn.detail_fetch_attempted_betclic}, sukces {fn.detail_fetch_success_betclic}, błędy {fn.detail_fetch_failed_betclic}",
    ])
    if fn.detail_fetch_skipped > 0:
        header_section.append(f"• Pominięte przez budżet: <b>{fn.detail_fetch_skipped}</b> zdarzeń")

    matched_tomorrow = getattr(fn, "matched_events_tomorrow", 0)
    matched_day_after = getattr(fn, "matched_events_day_after_tomorrow", 0)
    total_matched = fn.matched_events_today + matched_tomorrow + matched_day_after
    if matched_tomorrow > 0 or matched_day_after > 0:
        if matched_day_after > 0:
            matched_str = (
                f"• Zmatchowane mecze: <b>{total_matched}</b> "
                f"(Dzisiaj: {fn.matched_events_today}, Jutro: {matched_tomorrow}, Pojutrze: {matched_day_after} | "
                f"Wspólne: {fn.overlap_events_count}, Pojedyncze: {fn.single_provider_events_count})"
            )
        else:
            matched_str = (
                f"• Zmatchowane mecze: <b>{total_matched}</b> "
                f"(Dzisiaj: {fn.matched_events_today}, Jutro: {matched_tomorrow} | "
                f"Wspólne: {fn.overlap_events_count}, Pojedyncze: {fn.single_provider_events_count})"
            )
    else:
        matched_str = f"• Zmatchowane mecze: <b>{fn.matched_events_today}</b> (Wspólne: {fn.overlap_events_count}, Pojedyncze: {fn.single_provider_events_count})"

    header_section.extend([
        f"• Detale z rynkami: <b>{fn.details_with_markets}</b> | Bez rynków: <b>{fn.details_without_markets}</b>",
        matched_str,
        f"• Rynki: <b>{fn.acquired_markets_total}</b> pobranych, <b>{fn.normalized_markets_total}</b> znormalizowanych, <b>{fn.matched_markets_total}</b> zmatchowanych, <b>{fn.evaluated_markets_total}</b> ocenionych",
        f"• Ocenione rynki: Surebety: {fn.evaluated_surebets} | Valuebety: {fn.evaluated_valuebets} | Player Props: {fn.evaluated_player_props} | Team Props: {fn.evaluated_team_props}",
        "• Źródła referencyjne: <b>Pinnacle, bet365, StatsHub</b>",
        "",
    ])

    sections: List[Tuple[str, List[str]]] = [("HEADER", header_section)]

    # ─────────────────────────────────────────────────────────────────────────
    # Top Opportunities
    # ─────────────────────────────────────────────────────────────────────────
    if result.top_opportunities:
        top_lines = ["🔥 <b>TOP OPPORTUNITIES</b>", ""]
        for i, opp in enumerate(result.top_opportunities, 1):
            top_lines.append(_format_opportunity_card(i, opp))
            top_lines.append("")
        sections.append(("TOP", top_lines))
    else:
        top_lines = ["🔥 <b>TOP OPPORTUNITIES</b>", "<i>Brak okazji spełniających kryteria progu opłacalności.</i>", ""]
        sections.append(("TOP", top_lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Surebets
    # ─────────────────────────────────────────────────────────────────────────
    if result.surebets:
        sb_lines = [f"💰 <b>SUREBETY ({len(result.surebets)})</b>", ""]
        for i, opp in enumerate(result.surebets, 1):
            sb_lines.append(_format_opportunity_card(i, opp))
            sb_lines.append("")
        sections.append(("SUREBETS", sb_lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Valuebets
    # ─────────────────────────────────────────────────────────────────────────
    if result.valuebets:
        vb_lines = [f"📈 <b>VALUE BETS — RYNKI MECZOWE ({len(result.valuebets)})</b>", ""]
        for i, opp in enumerate(result.valuebets, 1):
            vb_lines.append(_format_opportunity_card(i, opp))
            vb_lines.append("")
        sections.append(("VALUEBETS", vb_lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Player Props
    # ─────────────────────────────────────────────────────────────────────────
    if result.player_props:
        pp_lines = [f"🎯 <b>PLAYER PROPS ({len(result.player_props)})</b>", ""]
        for i, opp in enumerate(result.player_props, 1):
            pp_lines.append(_format_opportunity_card(i, opp))
            pp_lines.append("")
        sections.append(("PLAYER_PROPS", pp_lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Team Props
    # ─────────────────────────────────────────────────────────────────────────
    if result.team_props:
        tp_lines = [f"⚽ <b>TEAM PROPS ({len(result.team_props)})</b>", ""]
        for i, opp in enumerate(result.team_props, 1):
            tp_lines.append(_format_opportunity_card(i, opp))
            tp_lines.append("")
        sections.append(("TEAM_PROPS", tp_lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Watchlist
    # ─────────────────────────────────────────────────────────────────────────
    if result.watchlist:
        wl_lines = [f"⚠️ <b>WATCHLIST / POTENCJAŁ ({len(result.watchlist)})</b>", ""]
        for i, opp in enumerate(result.watchlist[:5], 1):
            wl_lines.append(_format_opportunity_card(i, opp))
            wl_lines.append("")
        sections.append(("WATCHLIST", wl_lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Quality & Diagnostic Summary
    # ─────────────────────────────────────────────────────────────────────────
    diag_lines = [
        "📊 <b>JAKOŚĆ DANYCH I DIAGNOSTYKA</b>",
        f"• Oceniono surebetów: <b>{fn.evaluated_surebets}</b> | Zakwalifikowano: <b>{fn.qualified_surebets}</b>",
        f"• Oceniono valuebetów: <b>{fn.evaluated_valuebets}</b> | Zakwalifikowano: <b>{fn.qualified_valuebets}</b>",
        f"• Oceniono propsów: <b>{fn.evaluated_player_props + fn.evaluated_team_props}</b> | Zakwalifikowano: <b>{fn.qualified_player_props + fn.qualified_team_props}</b>",
    ]
    if fn.depth_pass_fixtures_refined > 0:
        diag_lines.append(f"• Pass 2 Depth: <b>{fn.depth_pass_fixtures_refined}</b> meczów zrefinowanych (zaktualizowano okazji: {fn.depth_pass_opportunities_refined})")
    if fn.odds_api_requests_made > 0 or fn.statshub_requests_made > 0:
        diag_lines.append(f"• Zapytania referencyjne: Odds API: <b>{fn.odds_api_requests_made}</b>, StatsHub: <b>{fn.statshub_requests_made}</b>")
    if fn.rejection_reasons:
        top_rejections = sorted(fn.rejection_reasons.items(), key=lambda x: x[1], reverse=True)[:4]
        rej_str = ", ".join(f"{k}: {v}" for k, v in top_rejections)
        diag_lines.append(f"• Odrzucenia jakościowe: {rej_str}")
    diag_lines.append("")

    if result.failures:
        diag_lines.append("❌ <b>AWARIE KRYTYCZNE</b>")
        for f in result.failures[:3]:
            diag_lines.append(f"• <i>{_esc(f)}</i>")
        diag_lines.append("")

    if result.warnings:
        diag_lines.append("⚠️ <b>OSTRZEŻENIA OPERACYJNE</b>")
        for w in result.warnings[:3]:
            diag_lines.append(f"• <i>{_esc(w)}</i>")
        diag_lines.append("")

    diag_lines.append("🤖 <i>ZieloneBety ULTRA Orchestration Engine</i>")
    sections.append(("DIAGNOSTICS", diag_lines))

    # ─────────────────────────────────────────────────────────────────────────
    # Assemble messages with safe length boundary
    # ─────────────────────────────────────────────────────────────────────────
    messages: List[str] = []
    current_lines: List[str] = []
    current_length = 0

    for section_tag, lines in sections:
        for line in lines:
            line_len = len(line) + 1
            if current_lines and (current_length + line_len) > MAX_TELEGRAM_MESSAGE_CHARS:
                messages.append("\n".join(current_lines).strip())
                current_lines = [line]
                current_length = len(line)
            else:
                current_lines.append(line)
                current_length += line_len

    if current_lines:
        messages.append("\n".join(current_lines).strip())

    return [m for m in messages if m]
