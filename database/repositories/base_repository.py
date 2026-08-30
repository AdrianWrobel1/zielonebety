"""
Abstract Base Repository Interface
"""

from typing import TypeVar, Generic, List, Optional
from sqlalchemy.orm import Session
from database.models import BaseORM

T = TypeVar("T", bound=BaseORM)


class BaseRepository(Generic[T]):
    """Generic base repository for CRUD persistence operations."""

    def __init__(self, session: Session, model_cls: type[T]):
        self.session = session
        self.model_cls = model_cls

    def add(self, entity: T) -> T:
        """Add an entity to session."""
        self.session.add(entity)
        return entity

    def add_all(self, entities: List[T]) -> List[T]:
        """Add multiple entities to session."""
        self.session.add_all(entities)
        return entities

    def get_by_id(self, entity_id: str) -> Optional[T]:
        """Retrieve entity by primary key."""
        return self.session.query(self.model_cls).filter(self.model_cls.id == entity_id).first()

    def list_all(self) -> List[T]:
        """List all entities."""
        return self.session.query(self.model_cls).all()
