"""
Scanner Package
"""

from scanner.models import Opportunity, OpportunityType, OpportunityLeg
from scanner.surebet_detector import SurebetDetector
from scanner.valuebet_detector import ValuebetDetector
from scanner.scanner_engine import ScannerEngine
from scanner.exceptions import ScannerError, InvalidOddsDataError

__all__ = [
    "Opportunity",
    "OpportunityType",
    "OpportunityLeg",
    "SurebetDetector",
    "ValuebetDetector",
    "ScannerEngine",
    "ScannerError",
    "InvalidOddsDataError",
]
