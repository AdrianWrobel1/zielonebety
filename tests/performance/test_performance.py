"""
Performance Benchmark & High Load Simulation Test Suite
"""

import time
import unittest
from scanner.scanner_engine import ScannerEngine
from normalization.base_normalizer import NormalizedGraph
from domain.models import Event, Market, Selection, Odds
from api.routes import APIRouter
from api.services import PlatformAPIService


class TestPerformanceAndLoad(unittest.TestCase):
    """Performance benchmarks testing scanner latency, throughput, and REST API response times."""

    def test_scanner_throughput_and_latency(self):
        scanner = ScannerEngine()

        ev = Event(competition_id="c1", home_participant="H", away_participant="A", scheduled_start="2026-08-07T20:00:00Z", internal_id="perf-ev")
        mkt = Market(event_id="perf-ev", market_type="1X2", internal_id="perf-m")
        s1 = Selection(market_id="perf-m", selection_type="HOME", internal_id="sel-1")
        s2 = Selection(market_id="perf-m", selection_type="DRAW", internal_id="sel-2")
        s3 = Selection(market_id="perf-m", selection_type="AWAY", internal_id="sel-3")

        odds_list = [
            Odds(selection_id=f"sel-{(i % 3) + 1}", bookmaker=f"Bookie_{i % 10}", decimal_odds=2.0 + (i % 5) * 0.2, internal_id=f"o-{i}")
            for i in range(300)
        ]

        from domain.models import Competition
        comp = Competition(name="Perf Competition")
        graph = NormalizedGraph(competition=comp, event=ev, markets=[mkt], selections=[s1, s2, s3], odds_list=odds_list)

        start = time.perf_counter()
        for _ in range(100):
            scanner.scan_graph(graph)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        avg_latency = elapsed_ms / 100.0
        # Assert average scan latency per graph is under 15 milliseconds
        self.assertLess(avg_latency, 15.0)

    def test_api_router_response_performance(self):
        service = PlatformAPIService()
        router = APIRouter(service=service)

        start = time.perf_counter()
        for _ in range(50):
            res = router.handle_get_opportunities()
            self.assertEqual(res.status_code, 200)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        avg_api_latency = elapsed_ms / 50.0
        # Assert average REST API endpoint response time is under 10 milliseconds
        self.assertLess(avg_api_latency, 10.0)


if __name__ == "__main__":
    unittest.main()
