"""
Database Connection Engine & Session Management
"""

from typing import Generator, Optional
from sqlalchemy import create_engine, text, event
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

            if self.config.db_url.startswith("sqlite"):
                @event.listens_for(self.engine, "connect")
                def _set_sqlite_pragma(dbapi_connection, connection_record):
                    cursor = dbapi_connection.cursor()
                    try:
                        if ":memory:" not in self.config.db_url:
                            cursor.execute("PRAGMA journal_mode=WAL;")
                        cursor.execute("PRAGMA busy_timeout=15000;")
                        cursor.execute("PRAGMA synchronous=NORMAL;")
                        cursor.execute("PRAGMA cache_size = -64000;")
                        cursor.execute("PRAGMA temp_store = MEMORY;")
                        cursor.execute("PRAGMA mmap_size = 268435456;")
                    except Exception:
                        pass
                    finally:
                        cursor.close()

            self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)
        except Exception as e:
            raise ConnectionError(f"Failed to create database engine: {e}") from e

    def create_tables(self) -> None:
        """Initialize database schema tables and ensure performance indexes."""
        try:
            BaseORM.metadata.create_all(self.engine)
            if self.config.db_url.startswith("sqlite"):
                with self.engine.begin() as conn:
                    indexes = [
                        "CREATE INDEX IF NOT EXISTS ix_opportunity_records_status ON opportunity_records(status);",
                        "CREATE INDEX IF NOT EXISTS ix_snapshots_execution_id ON snapshots(execution_id);",
                        "CREATE INDEX IF NOT EXISTS ix_snapshots_type_created ON snapshots(snapshot_type, created_at);",
                        "CREATE INDEX IF NOT EXISTS ix_odds_outcome_collected ON odds(outcome_id, collected_at);",
                        "CREATE INDEX IF NOT EXISTS ix_player_prop_snapshots_status_kickoff ON player_prop_snapshots(outcome_status, kickoff_at);",
                        "CREATE INDEX IF NOT EXISTS ix_player_prop_snapshots_kickoff_at ON player_prop_snapshots(kickoff_at);",
                        "CREATE INDEX IF NOT EXISTS ix_delivery_records_state_retry_created ON delivery_records(state, next_retry_at, created_at);",
                        "CREATE INDEX IF NOT EXISTS ix_markets_event_id ON markets(event_id);",
                        "CREATE INDEX IF NOT EXISTS ix_outcomes_market_id ON outcomes(market_id);",
                        "CREATE INDEX IF NOT EXISTS ix_events_competition_id ON events(competition_id);",
                    ]
                    for idx_sql in indexes:
                        try:
                            conn.execute(text(idx_sql))
                        except Exception:
                            pass
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
