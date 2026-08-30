"""
Scan Orchestration Exceptions
"""


class ScanOrchestrationError(Exception):
    """Base exception for all scan orchestration errors."""
    pass


class CriticalPipelineFailure(ScanOrchestrationError):
    """Raised when an unrecoverable critical error aborts the scan cycle."""
    pass


class ScanConfigurationError(ScanOrchestrationError):
    """Raised when scan configuration is invalid or missing required components."""
    pass
