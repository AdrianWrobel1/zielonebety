"""
API Layer Exceptions
"""

from core.exceptions import BaseApplicationError


class APIError(BaseApplicationError):
    """Base exception for API layer errors."""
    
    def __init__(self, message: str, status_code: int = 400, details: dict = None):
        super().__init__(message, details=details)
        self.status_code = status_code


class ResourceNotFoundError(APIError):
    """Raised when requested API resource is not found."""
    
    def __init__(self, message: str = "Resource not found"):
        super().__init__(message, status_code=404)


class UnauthorizedError(APIError):
    """Raised when authentication fails."""
    
    def __init__(self, message: str = "Unauthorized"):
        super().__init__(message, status_code=401)
