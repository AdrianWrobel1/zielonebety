"""
Stage B.2: Props Telegram Notification & Digest Formatter

Provides:
- format_telegram_prop_message: Deterministic, HTML-escaped single prop alert or update.
- format_telegram_props_digest: Compact pre-match evening digest of TOP qualified opportunities.
"""

from __future__ import annotations

import html
from typing import Any, List, Optional, Sequence
from scanner.global_props_scanner import GlobalScanOpportunity


def _esc(val: Any) -> str:
    """Safely escapes text for Telegram HTML mode."""
    if val is None:
        return ""
    return html.escape(str(val))


def _format_stat_type_display(stat_type: str) -> str:
    """Converts internal stat_type code to Polish readable label."""
    mapping = {
        "shots": "Strzały",
        "shots_on_target": "Celne strzały",
        "fouls": "Faule",
        "cards": "Kartki",
        "corners": "Rzuty rożne",
        "assists": "Asysty",
        "goals": "Gole",
        "offsides": "Spalone",
        "tackles": "Wślizgi",
    }
    return mapping.get(stat_type.lower(), stat_type.replace("_", " ").title())


def format_telegram_prop_message(
    opp: GlobalScanOpportunity,
    is_update: bool = False,
    update_reason: Optional[str] = None,
) -> str:
    """Formats a GlobalScanOpportunity into an auditable, high-clarity Telegram HTML alert.

    Guarantees:
    - Complete HTML escaping on all dynamic fields (player, teams, competition, error text).
    - Preserves all core contract fields: player/team, fixture, market, line/direction,
      bookmaker, execution odds, fair probability, fair odds, Net EV, reference bookmaker count,
      kickoff, and confidence.
    """
    is_player = (opp.prop_type or "PLAYER").upper() == "PLAYER"
    target_name = opp.player_name if is_player and opp.player_name else opp.team
    stat_display = _format_stat_type_display(opp.stat_type)
    side_display = "Powyżej" if (opp.side or "OVER").upper() == "OVER" else "Poniżej"
    market_line_str = f"{stat_display} ({side_display} {opp.line})"

    # Format kickoff
    kickoff_str = opp.kickoff or "TBD"
    if "T" in kickoff_str:
        # e.g. 2026-09-05T16:30:00Z -> 2026-09-05 16:30 UTC
        kickoff_clean = kickoff_str.replace("T", " ")
        if kickoff_clean.endswith("Z"):
            kickoff_clean = kickoff_clean[:-1] + " UTC"
        elif "+00:00" in kickoff_clean:
            kickoff_clean = kickoff_clean.replace("+00:00", " UTC")
    else:
        kickoff_clean = kickoff_str

    # Header
    if is_update:
        header = f"🔄 <b>PROP VALUEBET UPDATE ({_esc(f'+{opp.net_ev_pct:.2f}%' if opp.net_ev_pct is not None else '')})</b>"
    elif is_player:
        header = f"🎯 <b>PLAYER PROP VALUEBET ({_esc(f'+{opp.net_ev_pct:.2f}%' if opp.net_ev_pct is not None else '')})</b>"
    else:
        header = f"👥 <b>TEAM PROP VALUEBET ({_esc(f'+{opp.net_ev_pct:.2f}%' if opp.net_ev_pct is not None else '')})</b>"

    lines: List[str] = [header, ""]

    # Subject & Fixture
    if is_player:
        lines.append(f"👤 <b>{_esc(target_name)}</b> ({_esc(opp.team)})")
    else:
        lines.append(f"🛡️ <b>{_esc(target_name)}</b>")

    lines.append(f"⚽ <b>{_esc(opp.match_name)}</b>")
    if opp.competition:
        lines.append(f"🏆 <i>{_esc(opp.competition)}</i>")
    lines.append(f"⏰ Kickoff: {_esc(kickoff_clean)}")
    lines.append("")

    # Market & Line
    lines.append(f"📊 Rynek: <b>{_esc(market_line_str)}</b>")
    lines.append("")

    # Polish Execution Odds
    bm_name = (opp.best_bookmaker or "Superbet").upper()
    raw_odds_str = f"{opp.best_raw_odds:.2f}" if opp.best_raw_odds is not None else "N/A"
    eff_odds_str = f"{opp.best_effective_odds:.2f}" if opp.best_effective_odds is not None else raw_odds_str
    lines.append(f"🏢 Bukmacher: <b>{_esc(bm_name)}</b> @ <b>{_esc(raw_odds_str)}</b> (netto: {_esc(eff_odds_str)})")

    # Reference Benchmarks
    fair_odds_str = f"{opp.reference_fair_odds:.2f}" if opp.reference_fair_odds is not None else "N/A"
    fair_prob_val = opp.reference_fair_probability * 100.0 if opp.reference_fair_probability is not None else None
    fair_prob_str = f"{fair_prob_val:.1f}%" if fair_prob_val is not None else "N/A"
    sources_cnt = opp.reference_sources_count or len(opp.reference_odds or [])
    consensus_str = f"{opp.reference_consensus_odds:.2f}" if opp.reference_consensus_odds is not None else "N/A"

    lines.append(f"⚖️ Kurs sprawiedliwy: <b>{_esc(fair_odds_str)}</b> | Prawdopodobieństwo: <b>{_esc(fair_prob_str)}</b>")
    lines.append(f"🔎 Źródła referencyjne: <b>{_esc(sources_cnt)}</b> bukmacherów (konsensus: {_esc(consensus_str)})")
    lines.append("")

    # Value & Quality
    ev_str = f"+{opp.net_ev_pct:.2f}%" if opp.net_ev_pct is not None else "N/A"
    conf_str = opp.confidence or "MEDIUM"
    lines.append(f"💰 Net EV: <b>{_esc(ev_str)}</b>")
    lines.append(f"⭐ Pewność (Confidence): <b>{_esc(conf_str)}</b>")

    if is_update and update_reason:
        lines.append("")
        lines.append(f"ℹ️ <i>Powód aktualizacji: {_esc(update_reason)}</i>")

    lines.append("")
    lines.append("🤖 <i>ZieloneBety Props Engine</i>")

    return "\n".join(lines)


def format_telegram_props_digest(
    opportunities: Sequence[GlobalScanOpportunity],
    max_items: int = 5,
) -> Optional[str]:
    """Formats a concise pre-match evening digest of TOP qualified opportunities.

    Returns None if opportunities sequence is empty.
    """
    if not opportunities:
        return None

    top_opps = list(opportunities)[:max_items]

    lines: List[str] = [
        "📋 <b>WIECZORNY PRE-MATCH DIGEST — TOP PROPS NA JUTRO</b>",
        f"<i>Znaleziono {len(opportunities)} kwalifikowanych okazji. Oto TOP {len(top_opps)}:</i>",
        "",
    ]

    for idx, opp in enumerate(top_opps, start=1):
        is_player = (opp.prop_type or "PLAYER").upper() == "PLAYER"
        target_name = opp.player_name if is_player and opp.player_name else opp.team
        stat_display = _format_stat_type_display(opp.stat_type)
        side_sym = "+" if (opp.side or "OVER").upper() == "OVER" else "-"
        line_short = f"{stat_display} {side_sym}{opp.line}"
        bm = (opp.best_bookmaker or "Superbet").upper()
        odds_s = f"{opp.best_raw_odds:.2f}" if opp.best_raw_odds is not None else ""
        ev_s = f"+{opp.net_ev_pct:.2f}%" if opp.net_ev_pct is not None else ""
        conf = opp.confidence or "MED"

        item_str = (
            f"<b>{idx}. {_esc(target_name)}</b> — {_esc(line_short)}\n"
            f"   ⚽ {_esc(opp.match_name)}\n"
            f"   🏢 {_esc(bm)} @ <b>{_esc(odds_s)}</b> | Net EV: <b>{_esc(ev_s)}</b> | Conf: {_esc(conf)}"
        )
        lines.append(item_str)
        lines.append("")

    lines.append("🤖 <i>ZieloneBety Adaptive Scanner</i>")
    return "\n".join(lines).strip()
