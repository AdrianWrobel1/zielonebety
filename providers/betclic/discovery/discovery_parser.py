"""
Betclic Discovery Parser Module

Pure, decoupled extraction of matches and event metadata from HTML/JSON discovery payloads.
"""

import json
import re
from typing import List, Dict, Any, Set, Optional, Tuple


class BetclicDiscoveryParser:
    """Decoupled parser for extracting matches from HTML and embedded JSON payloads."""

    @staticmethod
    def extract_match_url_map(html: str) -> Dict[str, str]:
        """Extracts matchId -> relative_url href mapping from Betclic HTML."""
        match_links = re.findall(r'href="(/[^"]+-m(\d+))"', html)
        return {mid: href for href, mid in match_links}

    @classmethod
    def parse_html_page(cls, html: str) -> List[Dict[str, Any]]:
        """Extracts match objects from the SSR JSON state inside HTML."""
        match_url_map = cls.extract_match_url_map(html)
        script_matches = re.findall(r'<script[^>]*type="application/json"[^>]*>(.*?)</script>', html, re.DOTALL)
        if not script_matches:
            return []

        page_matches: List[Dict[str, Any]] = []
        for script_content in script_matches:
            try:
                data = json.loads(script_content)
            except Exception:
                continue

            if not isinstance(data, dict):
                continue

            for k, v in data.items():
                if isinstance(v, dict) and "response" in v:
                    resp_payload = v["response"].get("payload", {})
                    if isinstance(resp_payload, dict) and "matches" in resp_payload:
                        matches = resp_payload.get("matches", [])
                        if isinstance(matches, list):
                            for m in matches:
                                if isinstance(m, dict):
                                    mid = str(m.get("id") or m.get("matchId", "")).strip()
                                    if mid and mid in match_url_map:
                                        m["relative_url"] = match_url_map[mid]
                                    page_matches.append(m)

        return page_matches
