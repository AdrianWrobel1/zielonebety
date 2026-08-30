"""
Scanner Engine Exceptions
"""

from core.exceptions import BaseApplicationError


class ScannerError(BaseApplicationError):
    """Base exception for scanner engine failures."""
    pass


class InvalidOddsDataError(ScannerError):
    """Raised when scanner receives malformed or empty odds data."""
    pass
