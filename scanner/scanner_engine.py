"""
Scanner Engine Orchestrator
"""

from typing import List, Dict, Optional, Set
from normalization.base_normalizer import NormalizedGraph
from scanner.models import Opportunity
from scanner.surebet_detector import SurebetDetector
from scanner.valuebet_detector import ValuebetDetector


class ScannerEngine:
    """Orchestrates deterministic opportunity scanning over canonical NormalizedGraph inputs."""

    def __init__(
        self,
        surebet_detector: Optional[SurebetDetector] = None,
        valuebet_detector: Optional[ValuebetDetector] = None
    ):
        self.surebet_detector = surebet_detector or SurebetDetector()
        self.valuebet_detector = valuebet_detector or ValuebetDetector()

    def scan_graph(
        self,
        graph: NormalizedGraph,
        fair_probabilities: Optional[Dict[str, float]] = None
    ) -> List[Opportunity]:
        """Scans a single NormalizedGraph for surebets and valuebets."""
        opportunities: List[Opportunity] = []
        seen_fingerprints: Set[str] = set()

        # 1. Detect Surebets
        surebets = self.surebet_detector.detect_surebets(
            event=graph.event,
            markets=graph.markets,
            selections=graph.selections,
            odds_list=graph.odds_list,
        )

        for sb in surebets:
            fp = sb.fingerprint
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                opportunities.append(sb)

        # 2. Detect Valuebets
        valuebets = self.valuebet_detector.detect_valuebets(
            event=graph.event,
            markets=graph.markets,
            selections=graph.selections,
            odds_list=graph.odds_list,
            fair_probabilities=fair_probabilities,
        )

        for vb in valuebets:
            fp = vb.fingerprint
            if fp not in seen_fingerprints:
                seen_fingerprints.add(fp)
                opportunities.append(vb)

        return opportunities

    def scan_graphs(
        self,
        graphs: List[NormalizedGraph],
        fair_probabilities_map: Optional[Dict[str, Dict[str, float]]] = None
    ) -> List[Opportunity]:
        """Scans multiple canonical normalized graphs deterministically."""
        all_opportunities: List[Opportunity] = []
        fp_map = fair_probabilities_map or {}

        for graph in graphs:
            event_fair_p = fp_map.get(graph.event.internal_id)
            opps = self.scan_graph(graph, fair_probabilities=event_fair_p)
            all_opportunities.extend(opps)

        return all_opportunities
