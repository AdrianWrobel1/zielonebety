"""
Append-Only Odds Repository Implementation
"""

from typing import List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database.repositories.base_repository import BaseRepository
from database.models import OddsORM, ProviderORM
from domain.models import Odds as CanonicalOdds
from database.exceptions import RepositoryError


class OddsRepository(BaseRepository[OddsORM]):
    """Repository for appending and querying immutable historical odds snapshots."""

    def __init__(self, session: Session):
        super().__init__(session, OddsORM)

    def save_canonical_odds(self, canonical_odds_list: List[CanonicalOdds]) -> List[OddsORM]:
        """Appends new canonical odds snapshots to database."""
        added_records: List[OddsORM] = []

        for co in canonical_odds_list:
            # Ensure provider exists
            provider = self.session.query(ProviderORM).filter_by(id=co.bookmaker).first()
            if not provider:
                provider = ProviderORM(
                    id=co.bookmaker,
                    name=co.bookmaker.title(),
                    code=co.bookmaker[:4].lower(),
                    enabled=True,
                )
                self.session.add(provider)
                self.session.flush()

            dt_collected = datetime.now(timezone.utc)
            if co.timestamp:
                try:
                    dt_collected = datetime.fromisoformat(co.timestamp)
                except ValueError:
                    pass

            odds_orm = OddsORM(
                id=co.internal_id,
                provider_id=provider.id,
                outcome_id=co.selection_id,
                decimal_odds=co.decimal_odds,
                collected_at=dt_collected,
            )
            self.session.add(odds_orm)
            added_records.append(odds_orm)

        self.session.flush()
        return added_records

    def get_latest_odds_for_outcome(self, outcome_id: str) -> List[OddsORM]:
        """Fetch historical odds snapshot sequence for an outcome ordered by timestamp."""
        return (
            self.session.query(OddsORM)
            .filter(OddsORM.outcome_id == outcome_id)
            .order_by(OddsORM.collected_at.desc())
            .all()
        )
