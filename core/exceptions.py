"""
Core Application Exceptions

Defines the root exception hierarchy for the entire Zielone Bety platform.
All application-level exceptions inherit from BaseApplicationError.
"""


class BaseApplicationError(Exception):
    """Base exception for all Zielone Bety application errors."""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} | Details: {self.details}"
        return self.message

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(message={self.message!r}, details={self.details!r})"


class ConfigurationError(BaseApplicationError):
    """Raised when there is a configuration error."""
    pass


class ValidationError(BaseApplicationError):
    """Raised when input/output validation fails."""
    pass


class InfrastructureError(BaseApplicationError):
    """Raised when an infrastructure dependency fails."""
    pass


class TimeoutError(BaseApplicationError):
    """Raised when an operation exceeds its configured time limit."""
    pass


class ResourceExhaustedError(BaseApplicationError):
    """Raised when a resource pool or limit is exhausted."""
    pass
