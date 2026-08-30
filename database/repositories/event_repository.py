"""
Event Repository Implementation
"""

from typing import List, Optional
from sqlalchemy.orm import Session, joinedload
from database.repositories.base_repository import BaseRepository
from database.models import EventORM, CompetitionORM, SportORM, MarketORM, OutcomeORM
from normalization.base_normalizer import NormalizedGraph


class EventRepository(BaseRepository[EventORM]):
    """Repository for persisting and querying Events and normalized entity graphs."""

    def __init__(self, session: Session):
        super().__init__(session, EventORM)

    def save_normalized_graph(self, graph: NormalizedGraph) -> EventORM:
        """Persists a full canonical NormalizedGraph (Sport, Competition, Event, Markets, Outcomes) transactionally."""
        # 1. Ensure Sport exists
        sport = self.session.query(SportORM).filter_by(name=graph.competition.sport).first()
        if not sport:
            sport = SportORM(
                id=f"sport_{graph.competition.sport.lower()}",
                name=graph.competition.sport,
                slug=graph.competition.sport.lower(),
            )
            self.session.add(sport)
            self.session.flush()

        # 2. Ensure Competition exists
        comp = self.session.query(CompetitionORM).filter_by(id=graph.competition.internal_id).first()
        if not comp:
            comp = CompetitionORM(
                id=graph.competition.internal_id,
                sport_id=sport.id,
                name=graph.competition.name,
                country=graph.competition.country,
                season=graph.competition.season,
            )
            self.session.add(comp)
            self.session.flush()

        # 3. Create/Update Event
        event_orm = self.session.query(EventORM).filter_by(id=graph.event.internal_id).first()
        if not event_orm:
            event_orm = EventORM(
                id=graph.event.internal_id,
                competition_id=comp.id,
                home_team_name=graph.event.home_participant,
                away_team_name=graph.event.away_participant,
                kickoff=graph.event.scheduled_start,
                status=graph.event.status,
            )
            self.session.add(event_orm)
            self.session.flush()

        # 4. Save Markets and Outcomes
        mkt_map = {m.internal_id: m for m in graph.markets}
        for mkt in graph.markets:
            mkt_orm = self.session.query(MarketORM).filter_by(id=mkt.internal_id).first()
            if not mkt_orm:
                mkt_orm = MarketORM(
                    id=mkt.internal_id,
                    event_id=event_orm.id,
                    market_type=mkt.market_type,
                    line=mkt.line,
                    status=mkt.status,
                )
                self.session.add(mkt_orm)

        self.session.flush()

        for sel in graph.selections:
            out_orm = self.session.query(OutcomeORM).filter_by(id=sel.internal_id).first()
            if not out_orm:
                out_orm = OutcomeORM(
                    id=sel.internal_id,
                    market_id=sel.market_id,
                    outcome_type=sel.selection_type,
                    label=sel.participant,
                    handicap=sel.line,
                )
                self.session.add(out_orm)

        self.session.flush()
        return event_orm

    def get_event_with_details(self, event_id: str) -> Optional[EventORM]:
        """Fetch event with joined competition and market tree."""
        return (
            self.session.query(EventORM)
            .options(
                joinedload(EventORM.competition),
                joinedload(EventORM.markets).joinedload(MarketORM.outcomes)
            )
            .filter(EventORM.id == event_id)
            .first()
        )
