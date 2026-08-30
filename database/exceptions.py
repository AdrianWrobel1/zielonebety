"""
Database Layer Exceptions
"""

from core.exceptions import BaseApplicationError


class DatabaseError(BaseApplicationError):
    """Base exception for database failures."""
    pass


class ConnectionError(DatabaseError):
    """Raised when database connection fails."""
    pass


class RepositoryError(DatabaseError):
    """Raised when repository operation fails."""
    pass


class EntityNotFoundError(RepositoryError):
    """Raised when requested entity is not found in database."""
    pass
