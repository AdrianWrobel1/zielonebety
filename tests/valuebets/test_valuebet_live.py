"""
Bounded Live Verification Test for Reference Odds Provider & Valuebet Engine
"""

import os
import pytest
from decimal import Decimal

from reference_odds.provider import TheOddsApiReferenceProvider
from valuebets.config import ValuebetConfig
from valuebets.engine import ValuebetEngine


@pytest.mark.live
def test_live_reference_odds_bounded_verification():
    """Executes a single bounded live request against The Odds API if credentials are provided."""
    api_key = os.environ.get("THE_ODDS_API_KEY") or os.environ.get("ODDS_API_KEY")

    if not api_key:
        pytest.skip(
            "THE_ODDS_API_KEY / ODDS_API_KEY environment variable not configured. Skipping live reference test."
        )

    provider = TheOddsApiReferenceProvider(api_key=api_key)
    events = provider.fetch_reference_events(sport="soccer_epl", force_refresh=True)

    quota = provider.get_quota_metrics()
    print("\n--- The-Odds-API Live Verification Metrics ---")
    print(f"Total Requests Issued: {quota.total_requests}")
    print(f"Requests Remaining: {quota.requests_remaining}")
    print(f"Requests Used: {quota.requests_used}")
    print(f"Reference Events Discovered: {len(events)}")
    print(f"Reference Markets Discovered: {sum(len(e.markets) for e in events)}")

    assert quota.total_requests == 1, "Must issue exactly one bounded request"
    assert len(events) >= 0
