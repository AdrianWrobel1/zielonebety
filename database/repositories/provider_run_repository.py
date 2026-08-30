"""
Provider Run Execution History Repository
"""

import uuid
from typing import Optional, List
from datetime import datetime, timezone
from sqlalchemy.orm import Session
from database.repositories.base_repository import BaseRepository
from database.models import ProviderRunORM, ProviderORM
from providers.base.provider_result import ProviderResult


class ProviderRunRepository(BaseRepository[ProviderRunORM]):
    """Repository for persisting provider execution run metadata and statistics."""

    def __init__(self, session: Session):
        super().__init__(session, ProviderRunORM)

    def record_run(self, result: ProviderResult) -> ProviderRunORM:
        """Persists a provider execution result summary into DB."""
        provider = self.session.query(ProviderORM).filter_by(id=result.provider_name).first()
        if not provider:
            provider = ProviderORM(
                id=result.provider_name,
                name=result.provider_name.title(),
                code=result.provider_name[:4].lower(),
                enabled=True,
            )
            self.session.add(provider)
            self.session.flush()

        run_orm = ProviderRunORM(
            id=f"run_{uuid.uuid4()}",
            provider_id=provider.id,
            started_at=datetime.now(timezone.utc),
            finished_at=datetime.now(timezone.utc),
            duration_seconds=result.execution_duration,
            status=result.status.name,
            events_count=len(result.parsed_objects),
            errors="; ".join(result.errors) if result.errors else None,
        )
        self.session.add(run_orm)
        self.session.flush()
        return run_orm

    def get_latest_run(self, provider_id: str) -> Optional[ProviderRunORM]:
        """Fetch latest run record for a provider."""
        return (
            self.session.query(ProviderRunORM)
            .filter(ProviderRunORM.provider_id == provider_id)
            .order_by(ProviderRunORM.started_at.desc())
            .first()
        )
