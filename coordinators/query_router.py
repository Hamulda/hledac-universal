"""
coordinators/query_router.py — Sprint F214Q: Unified DHT+Discovery query router.

Minimal bridge between dht/ and discovery/ acquisition lanes.
M1-safe: no model loading, HLEDAC_ENABLE_DHT gate, bounded concurrency.
"""
from __future__ import annotations


import asyncio
import os
from typing import Any

from hledac.universal.dht.kademlia_node import DHT_REAL_UDP, crawl_dht_for_keyword
from hledac.universal.discovery.discovery_planner import get_discovery_planner
from hledac.universal.utils.async_helpers import safe_gather_dropin

# -----------------------------------------------------------------------------
# Source mask literals
# -----------------------------------------------------------------------------
DHT = "dht"
DDG = "ddg"
WAYBACK = "wayback"
CRT = "crt"
ALL_SOURCES = {DHT, DDG, WAYBACK, CRT}

SourceMask = set[str]


# -----------------------------------------------------------------------------
# QueryRouter
# -----------------------------------------------------------------------------
class QueryRouter:
    """
    Unified query dispatcher bridging dht/ and discovery/.

    Parameters
    ----------
    source_mask:
        Which sources to query. Defaults to all available.
    max_results_per_source:
        Hard cap per source (default 5, DHT 5, discovery 20).
    timeout_s:
        Per-source timeout (default 30s).
    """

    def __init__(
        self,
        source_mask: SourceMask | None = None,
        max_results_per_source: int = 5,
        timeout_s: float = 30.0,
    ) -> None:
        self._mask = source_mask or ALL_SOURCES
        self._max = max_results_per_source
        self._timeout = timeout_s
        self._planner = get_discovery_planner()

    # -------------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------------
    async def query(self, query: str, remaining_budget_s: float = 30.0) -> list[dict]:
        """
        Dispatch query across selected sources concurrently.

        Returns
        -------
        list[dict] — canonical dicts ready for CanonicalFinding construction.
        Each dict has at minimum: {"source_type", "confidence", "payload_text", "provenance"}.
        """
        tasks: list[asyncio.Task] = []

        if DHT in self._mask:
            tasks.append(asyncio.create_task(self._dht_search(query), name="qr:dht"))

        if DDG in self._mask or WAYBACK in self._mask or CRT in self._mask:
            tasks.append(
                asyncio.create_task(
                    self._discovery_search(query, remaining_budget_s),
                    name="qr:discovery",
                )
            )

        if not tasks:
            return []

        gathered = await asyncio.wait_for(
            safe_gather_dropin(*tasks, label="query_router"),
            timeout=self._timeout * 2,
        )

        merged: list[dict] = []
        for batch in gathered:
            if isinstance(batch, BaseException):
                continue
            merged.extend(batch)
        return merged

    # -------------------------------------------------------------------------
    # DHT branch
    # -------------------------------------------------------------------------
    async def _dht_search(self, query: str) -> list[dict]:
        """DHT keyword crawl → list[dict] in canonical dict form."""
        if not DHT_REAL_UDP:
            return []

        dht_flag = os.getenv("HLEDAC_ENABLE_DHT", "").lower()
        if dht_flag not in ("1", "true", "yes", "on"):
            return []

        try:
            raw: list[dict] = await crawl_dht_for_keyword(
                query,
                max_results=self._max,
                duration_s=self._timeout,
            )
            return [self._dht_dict_to_canonical(r, query) for r in raw if r.get("info_hash")]

        except Exception:
            return []

    @staticmethod
    def _dht_dict_to_canonical(r: dict, query: str) -> dict:
        """Map raw DHT dict → canonical dict shape (no DHTFinding dependency)."""
        info_hash = r.get("info_hash", "")
        name = r.get("name", "") or "dht_torrent"
        magnet = f"magnet:?xt=urn:btih:{info_hash}"
        if name and name != "dht_torrent":
            magnet += f"&dn={name}"
        body = f"info_hash={info_hash} peers={r.get('peers', 0)} size={r.get('size_bytes', 0)}"
        files = r.get("files", [])
        if files:
            file_names = ", ".join(f.get("name", "") for f in files[:10])
            body += f"\nfiles: {file_names}"
        payload = f"{name}\n{magnet}\n{body[:3000]}"
        peers = r.get("peers", 0)
        confidence = min(0.9, 0.3 + (peers / 100))
        import hashlib
        import time as time_mod
        finding_id = f"dht_{hashlib.md5(info_hash.encode()).hexdigest()[:16]}"
        return {
            "finding_id": finding_id,
            "query": query[:128],
            "source_type": "dht_discovery",
            "confidence": max(0.0, min(1.0, confidence)),
            "ts": time_mod.time(),
            "provenance": (f"info_hash:{info_hash}",),
            "payload_text": payload[:3000],
        }

    # -------------------------------------------------------------------------
    # Discovery branch (DuckDuckGo + Wayback + CRTSH via DiscoveryPlanner)
    # -------------------------------------------------------------------------
    async def _discovery_search(self, query: str, remaining_budget_s: float) -> list[dict]:
        """
        Run DiscoveryPlanner → extract hits as canonical dicts.
        Note: DiscoveryPlanner.execute() writes to registry; we extract hits only.
        """
        try:
            plan = self._planner.plan(query, remaining_budget_s, target_results=self._max * 4)
            batch_results = await self._planner.execute(query, plan)
            canonicals: list[dict] = []
            for br in batch_results:
                for hit in getattr(br, "hits", []):
                    canonicals.append(self._discovery_hit_to_canonical(hit, br.provider))
            return canonicals
        except Exception:
            return []

    @staticmethod
    def _discovery_hit_to_canonical(hit: Any, provider: str) -> dict:
        """Map a discovery hit (url, name, description) → canonical dict shape."""
        import hashlib
        import time as time_mod
        url = getattr(hit, "url", "") or ""
        name = getattr(hit, "name", "") or ""
        desc = getattr(hit, "description", "") or ""
        payload = f"{name}\n{url}\n{desc[:2000]}"
        fid = f"disc_{hashlib.md5(url.encode()).hexdigest()[:16]}"
        return {
            "finding_id": fid,
            "query": "",
            "source_type": f"discovery_{provider}",
            "confidence": 0.5,
            "ts": time_mod.time(),
            "provenance": (url,),
            "payload_text": payload,
        }
