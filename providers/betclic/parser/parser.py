"""
Betclic Payload Parser Module (Overview & Tier 2 Full Market Detail)
"""

from typing import List, Dict, Any, Optional
from providers.base.models import (
    DETAIL_FETCH_ERROR_KEY,
    DETAIL_FETCH_ERROR_TYPE_KEY,
    DETAIL_FETCH_FAILED_KEY,
    OVERVIEW_NOT_ACQUIRED_KEY,
)
from providers.betclic.models import (
    BetclicEvent,
    BetclicMarket,
    BetclicSelection,
    BetclicOdds,
)
from providers.betclic.exceptions import BetclicParsingError


class BetclicParser:
    """Parses raw provider JSON payloads into structured Betclic domain models."""

    def parse_payloads(self, raw_responses: List[Any], include_markets: bool = True) -> List[BetclicEvent]:
        """Parses a list of raw response dicts into BetclicEvent objects."""
        parsed_events: List[BetclicEvent] = []

        flattened: List[Dict[str, Any]] = []
        for item in raw_responses:
            if isinstance(item, list):
                for sub in item:
                    if isinstance(sub, dict):
                        flattened.append(sub)
            elif isinstance(item, dict):
                flattened.append(item)

        for payload in flattened:
            try:
                event_id = str(payload.get("id") or payload.get("matchId") or "").strip()
                name = str(payload.get("name", "")).strip()

                comp_raw = payload.get("competition")
                if isinstance(comp_raw, dict):
                    comp = str(comp_raw.get("name", "Unknown Competition")).strip()
                else:
                    comp = str(comp_raw or "Unknown Competition").strip()

                if not event_id or not name:
                    raise BetclicParsingError(f"Missing mandatory event fields in payload: {payload}")

                contestants = payload.get("contestants", [])
                home_team = payload.get("home_team")
                away_team = payload.get("away_team")

                if not home_team and isinstance(contestants, list) and len(contestants) > 0:
                    home_team = contestants[0].get("name")
                if not away_team and isinstance(contestants, list) and len(contestants) > 1:
                    away_team = contestants[1].get("name")

                if not home_team or not away_team:
                    for sep in (" vs ", " VS ", " - ", " – ", " — "):
                        if sep in name:
                            parts = name.split(sep, 1)
                            if len(parts) == 2:
                                if not home_team:
                                    home_team = parts[0].strip()
                                if not away_team:
                                    away_team = parts[1].strip()
                                break

                start_date = payload.get("start_date") or payload.get("matchDateUtc")

                markets: List[BetclicMarket] = []
                if include_markets:
                    # Collect all raw markets from subCategories, markets array, and root market
                    raw_markets_pool: List[Dict[str, Any]] = []
                    seen_mkt_ids = set()

                    # 1. From subCategories (detail pages)
                    for sc in payload.get("subCategories", []):
                        if isinstance(sc, dict):
                            for m in sc.get("markets", []):
                                if isinstance(m, dict):
                                    if m.get("groupMarkets") and not m.get("mainSelections"):
                                        for gm in m["groupMarkets"]:
                                            if isinstance(gm, dict):
                                                gm_id = str(gm.get("id", ""))
                                                if gm_id and gm_id in seen_mkt_ids:
                                                    continue
                                                if gm_id:
                                                    seen_mkt_ids.add(gm_id)
                                                raw_markets_pool.append(gm)
                                    else:
                                        m_id = str(m.get("id", ""))
                                        if m_id and m_id in seen_mkt_ids:
                                            continue
                                        if m_id:
                                            seen_mkt_ids.add(m_id)
                                        raw_markets_pool.append(m)

                    # 2. From markets list
                    for m in payload.get("markets", []):
                        if isinstance(m, dict):
                            m_id = str(m.get("id", ""))
                            if m_id and m_id in seen_mkt_ids:
                                continue
                            if m_id:
                                seen_mkt_ids.add(m_id)
                            raw_markets_pool.append(m)

                    # 3. From single SSR market (overview discovery)
                    ssr_market = payload.get("market")
                    if isinstance(ssr_market, dict):
                        m_id = str(ssr_market.get("id", ""))
                        if not m_id or m_id not in seen_mkt_ids:
                            if m_id:
                                seen_mkt_ids.add(m_id)
                            raw_markets_pool.append(ssr_market)

                    for m_idx, m in enumerate(raw_markets_pool):
                        m_id = str(m.get("id", f"{event_id}_m_{m_idx}"))
                        m_name = str(m.get("name", "Unknown Market")).strip()
                        m_code = str(m.get("code", m_name)).strip()
                        is_open = bool(m.get("is_open", True) and m.get("status", 1) == 1)

                        selections: List[BetclicSelection] = []

                        def _build_betclic_selection(
                            raw_s: Dict[str, Any],
                            default_id: str,
                            name_override: Optional[str] = None,
                            handicap_val: Any = None,
                        ) -> Optional[BetclicSelection]:
                            s_id = str(raw_s.get("id", default_id)).strip()
                            s_name = name_override or str(raw_s.get("name", "")).strip()
                            s_code = str(raw_s.get("code", s_name)).strip()
                            
                            raw_odds = raw_s.get("odds")
                            status = raw_s.get("status", 1)

                            decimal_odds: Optional[float] = None
                            if raw_odds is not None:
                                try:
                                    decimal_odds = float(raw_odds)
                                except (ValueError, TypeError):
                                    decimal_odds = None

                            if decimal_odds is not None and decimal_odds > 1.0 and status == 1:
                                is_active = True
                                odds_obj = BetclicOdds(
                                    provider_odds_id=f"odds_{s_id}",
                                    decimal_odds=decimal_odds,
                                    is_active=True,
                                    timestamp=payload.get("timestamp"),
                                )
                            else:
                                is_active = False
                                odds_obj = BetclicOdds(
                                    provider_odds_id=f"odds_{s_id}",
                                    decimal_odds=decimal_odds if decimal_odds is not None else 0.0,
                                    is_active=False,
                                    timestamp=payload.get("timestamp"),
                                ) if decimal_odds is not None else None

                            return BetclicSelection(
                                provider_selection_id=s_id,
                                name=s_name,
                                type_code=s_code,
                                odds=odds_obj,
                                handicap=handicap_val if handicap_val is not None else raw_s.get("handicap"),
                            )

                        # A. mainSelections
                        for s_idx, s in enumerate(m.get("mainSelections", [])):
                            if not isinstance(s, dict):
                                continue
                            sel = _build_betclic_selection(s, f"{m_id}_s_ms_{s_idx}")
                            if sel:
                                selections.append(sel)

                        # B. selectionMatrix (2D rows with selectionOneof)
                        for r_idx, row in enumerate(m.get("selectionMatrix", [])):
                            if not isinstance(row, dict):
                                continue
                            row_sels = row.get("selections", [])
                            for item_idx, item in enumerate(row_sels):
                                if not isinstance(item, dict):
                                    continue
                                s = None
                                if "selectionOneof" in item and isinstance(item["selectionOneof"], dict):
                                    s = item["selectionOneof"].get("selection")
                                elif "selection" in item and isinstance(item["selection"], dict):
                                    s = item["selection"]
                                elif "id" in item and "odds" in item:
                                    s = item

                                if not s or not isinstance(s, dict):
                                    continue

                                sel = _build_betclic_selection(s, f"{m_id}_s_sm_{r_idx}_{item_idx}")
                                if sel:
                                    selections.append(sel)

                        # C. groupMarkets
                        for gm_idx, gm in enumerate(m.get("groupMarkets", [])):
                            if not isinstance(gm, dict):
                                continue
                            gm_name = str(gm.get("name", "")).strip()
                            gm_sels = gm.get("mainSelections") or gm.get("selections") or []
                            for s_idx, s in enumerate(gm_sels):
                                if not isinstance(s, dict):
                                    continue
                                s_name = f"{gm_name} {s.get('name', '')}".strip() if gm_name else str(s.get('name', '')).strip()
                                sel = _build_betclic_selection(
                                    s,
                                    f"{m_id}_s_gm_{gm_idx}_{s_idx}",
                                    name_override=s_name if gm_name else None,
                                    handicap_val=s.get("handicap") or gm_name,
                                )
                                if sel:
                                    selections.append(sel)

                        # D. Legacy flat selections
                        for s_idx, s in enumerate(m.get("selections", [])):
                            if not isinstance(s, dict):
                                continue
                            sel = _build_betclic_selection(s, f"{m_id}_s_{s_idx}")
                            if sel:
                                selections.append(sel)

                        if selections:
                            markets.append(
                                BetclicMarket(
                                    provider_market_id=m_id,
                                    name=m_name,
                                    market_type_code=m_code,
                                    is_open=is_open,
                                    selections=selections,
                                )
                            )

                parsed_events.append(
                    BetclicEvent(
                        provider_event_id=event_id,
                        name=name,
                        competition_name=comp,
                        start_time=start_date,
                        home_team=home_team,
                        away_team=away_team,
                        markets=markets,
                        raw_payload=payload,
                        # P1-003: propagate explicit detail-acquisition failure
                        # state so a failed request is never equivalent to a
                        # legitimate empty response.
                        fetch_failed=payload.get(DETAIL_FETCH_FAILED_KEY) is True,
                        fetch_error=str(payload.get(DETAIL_FETCH_ERROR_KEY))
                        if payload.get(DETAIL_FETCH_FAILED_KEY) is True
                        and payload.get(DETAIL_FETCH_ERROR_KEY)
                        else None,
                        fetch_error_type=str(payload.get(DETAIL_FETCH_ERROR_TYPE_KEY))
                        if payload.get(DETAIL_FETCH_FAILED_KEY) is True
                        and payload.get(DETAIL_FETCH_ERROR_TYPE_KEY)
                        else None,
                        # P1-NEW-010: propagate Tier-1 overview NOT_ACQUIRED
                        # state (markets never acquired) distinctly.
                        overview_only=payload.get(OVERVIEW_NOT_ACQUIRED_KEY) is True,
                    )
                )

            except Exception as e:
                raise BetclicParsingError(f"Error parsing Betclic payload: {e}") from e

        return parsed_events
