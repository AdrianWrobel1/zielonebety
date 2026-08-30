"""
Offline Reference Odds & Bookmaker Fixtures for Deterministic Valuebet Testing
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from domain.models import (
    Competition,
    Event,
    Market,
    Odds,
    Selection,
)
from normalization.base_normalizer import NormalizedGraph
from normalization.market_identity import CanonicalMarketType
from normalization.selection_identity import CanonicalSelectionType
from reference_odds.models import (
    ReferenceEvent,
    ReferenceMarket,
    ReferenceSelection,
    ReferenceSource,
)


def create_reference_event(
    home_team: str = "Arsenal",
    away_team: str = "Chelsea",
    sport: str = "football",
    scheduled_start: Optional[str] = None,
    markets: Optional[List[ReferenceMarket]] = None,
    timestamp: Optional[str] = None,
    source: str = ReferenceSource.PINNACLE.value,
) -> ReferenceEvent:
    """Helper to build a ReferenceEvent fixture."""
    now_str = timestamp or datetime.now(timezone.utc).isoformat()
    return ReferenceEvent(
        source=source,
        source_event_id=f"ref_{home_team}_{away_team}".lower().replace(" ", "_"),
        home_team=home_team,
        away_team=away_team,
        scheduled_start=scheduled_start or (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
        sport=sport,
        markets=markets or [],
        timestamp=now_str,
    )


def create_bookmaker_graph(
    home_team: str = "Arsenal",
    away_team: str = "Chelsea",
    bookmaker: str = "superbet",
    market_type: str = "1X2",
    line: Optional[float] = None,
    selections_odds: Optional[Dict[str, float]] = None,
    scheduled_start: Optional[str] = None,
) -> NormalizedGraph:
    """Helper to build a bookmaker NormalizedGraph fixture."""
    comp = Competition(name="Premier League", sport="Football")
    ev = Event(
        competition_id=comp.internal_id,
        home_participant=home_team,
        away_participant=away_team,
        scheduled_start=scheduled_start or (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(),
    )
    mkt = Market(
        event_id=ev.internal_id,
        market_type=market_type,
        line=line,
    )

    selections: List[Selection] = []
    odds_list: List[Odds] = []

    sel_odds = selections_odds or {"HOME": 2.20, "DRAW": 3.40, "AWAY": 3.60}
    for sel_type, price in sel_odds.items():
        sel = Selection(
            market_id=mkt.internal_id,
            selection_type=sel_type,
            line=line,
        )
        selections.append(sel)
        odds_list.append(
            Odds(
                selection_id=sel.internal_id,
                bookmaker=bookmaker,
                decimal_odds=price,
            )
        )

    return NormalizedGraph(
        competition=comp,
        event=ev,
        markets=[mkt],
        selections=selections,
        odds_list=odds_list,
    )


def fixture_valid_1x2_reference_market() -> ReferenceMarket:
    """Returns valid 1X2 reference market with 3.57% margin.
    Odds: Home 2.00, Draw 3.50, Away 4.00
    Raw probabilities: 0.5000, 0.2857, 0.2500 -> Sum = 1.0357
    Fair probabilities: 0.4828, 0.2759, 0.2414
    Fair odds: 2.0714, 3.6250, 4.1429
    """
    return ReferenceMarket(
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections={
            CanonicalSelectionType.HOME.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.HOME.value,
                odds=Decimal("2.00"),
                participant_role="HOME",
            ),
            CanonicalSelectionType.DRAW.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.DRAW.value,
                odds=Decimal("3.50"),
            ),
            CanonicalSelectionType.AWAY.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.AWAY.value,
                odds=Decimal("4.00"),
                participant_role="AWAY",
            ),
        },
        timestamp=datetime.now(timezone.utc).isoformat(),
        bookmaker_name="pinnacle",
    )


def fixture_valid_btts_reference_market() -> ReferenceMarket:
    """Returns valid BTTS reference market.
    Odds: Yes 1.80, No 2.10
    """
    return ReferenceMarket(
        market_type=CanonicalMarketType.BTTS.value,
        selections={
            CanonicalSelectionType.YES.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.YES.value,
                odds=Decimal("1.80"),
            ),
            CanonicalSelectionType.NO.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.NO.value,
                odds=Decimal("2.10"),
            ),
        },
        timestamp=datetime.now(timezone.utc).isoformat(),
        bookmaker_name="pinnacle",
    )


def fixture_valid_totals_reference_market(line: Decimal = Decimal("2.5")) -> ReferenceMarket:
    """Returns valid Totals 2.5 reference market.
    Odds: Over 1.95, Under 1.95
    """
    return ReferenceMarket(
        market_type=CanonicalMarketType.TOTALS.value,
        line=line,
        selections={
            CanonicalSelectionType.OVER.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.OVER.value,
                odds=Decimal("1.95"),
                line=line,
            ),
            CanonicalSelectionType.UNDER.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.UNDER.value,
                odds=Decimal("1.95"),
                line=line,
            ),
        },
        timestamp=datetime.now(timezone.utc).isoformat(),
        bookmaker_name="pinnacle",
    )


def fixture_incomplete_1x2_reference_market() -> ReferenceMarket:
    """Missing Away selection."""
    return ReferenceMarket(
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections={
            CanonicalSelectionType.HOME.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.HOME.value,
                odds=Decimal("2.00"),
            ),
            CanonicalSelectionType.DRAW.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.DRAW.value,
                odds=Decimal("3.50"),
            ),
        },
        timestamp=datetime.now(timezone.utc).isoformat(),
        bookmaker_name="pinnacle",
    )


def fixture_stale_reference_market() -> ReferenceMarket:
    """Timestamp 2 hours ago."""
    stale_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    mkt = fixture_valid_1x2_reference_market()
    mkt.timestamp = stale_ts
    return mkt


def fixture_invalid_odds_reference_market() -> ReferenceMarket:
    """Odds <= 1.0."""
    return ReferenceMarket(
        market_type=CanonicalMarketType.ONE_X_TWO.value,
        selections={
            CanonicalSelectionType.HOME.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.HOME.value,
                odds=Decimal("0.95"),
            ),
            CanonicalSelectionType.DRAW.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.DRAW.value,
                odds=Decimal("3.50"),
            ),
            CanonicalSelectionType.AWAY.value: ReferenceSelection(
                selection_type=CanonicalSelectionType.AWAY.value,
                odds=Decimal("4.00"),
            ),
        },
        timestamp=datetime.now(timezone.utc).isoformat(),
        bookmaker_name="pinnacle",
    )
