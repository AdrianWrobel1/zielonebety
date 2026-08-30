"""
Database Configuration System
"""

from dataclasses import dataclass


@dataclass
class DatabaseConfig:
    db_url: str = "sqlite:///:memory:"
    pool_size: int = 5
    max_overflow: int = 10
    echo: bool = False

    @classmethod
    def default_sqlite_in_memory(cls) -> "DatabaseConfig":
        return cls(db_url="sqlite:///:memory:", echo=False)

    @classmethod
    def from_env(cls) -> "DatabaseConfig":
        """Construct DatabaseConfig from environment variables or default to persistent SQLite."""
        import os

        # 1. Direct DATABASE_URL
        db_url = os.environ.get("DATABASE_URL")
        if db_url:
            return cls(db_url=db_url)

        # 2. PostgreSQL parameters
        pg_host = os.environ.get("POSTGRES_HOST")
        if pg_host:
            pg_port = os.environ.get("POSTGRES_PORT", "5432")
            pg_user = os.environ.get("POSTGRES_USER", "zielonebety_user")
            pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
            pg_db = os.environ.get("POSTGRES_DB", "zielonebety")
            return cls(db_url=f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}")

        # 3. Persistent SQLite file
        sqlite_path = os.environ.get("SQLITE_PATH", "zielonebety.db")
        return cls(db_url=f"sqlite:///{sqlite_path}")
