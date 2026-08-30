"""
Database Connection Engine & Session Management
"""

from typing import Generator, Optional
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from database.config import DatabaseConfig
from database.models import BaseORM
from database.exceptions import ConnectionError


class DatabaseManager:
    """Manages SQLAlchemy Engine, Session creation, and schema initialization."""

    def __init__(self, config: Optional[DatabaseConfig] = None):
        self.config = config or DatabaseConfig.from_env()
        
        try:
            connect_args = {}
            if self.config.db_url.startswith("sqlite"):
                connect_args = {"check_same_thread": False, "timeout": 30}

            self.engine = create_engine(
                self.config.db_url,
                echo=self.config.echo,
                connect_args=connect_args,
            )
            self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        except Exception as e:
            raise ConnectionError(f"Failed to create database engine: {e}") from e

    def create_tables(self) -> None:
        """Initialize database schema tables."""
        try:
            BaseORM.metadata.create_all(self.engine)
        except Exception as e:
            raise ConnectionError(f"Failed to create database tables: {e}") from e

    def get_session(self) -> Session:
        """Create a new database session."""
        return self.session_factory()

    def check_health(self) -> bool:
        """Verify database connectivity."""
        try:
            with self.get_session() as session:
                session.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    def dispose(self) -> None:
        """Close engine and release all pooled connection resources."""
        try:
            if hasattr(self, "engine") and self.engine is not None:
                self.engine.dispose()
        except Exception:
            pass

    def close(self) -> None:
        """Alias for dispose()."""
        self.dispose()
