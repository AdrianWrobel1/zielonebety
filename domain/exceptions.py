"""
Canonical Domain Model Exceptions
"""

from core.exceptions import BaseApplicationError


class DomainError(BaseApplicationError):
    """Base exception for domain model errors."""
    pass


class DomainValidationError(DomainError):
    """Raised when canonical domain model validation fails."""
    pass


class CanonicalIDError(DomainError):
    """Raised when canonical ID mapping/generation fails."""
    pass
