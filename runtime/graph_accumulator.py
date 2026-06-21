"""
SprintGraphAccumulator — Graph IOC accumulation adapter.
=======================================================

Extracts the graph accumulation logic from SprintScheduler into a
standalone, testable adapter.

Responsibilities:
  - Build IOC rows from findings (finding_id, source_type, confidence, sprint_id)
  - Delegate to graph_service.upsert_ioc_batch()
  - Fail-soft: graph errors never propagate; return 0 on failure
  - Return count of rows successfully submitted to graph_service

IMPORTANT:
  - This adapter does NOT reset session state (that's handled by the scheduler).
  - _get_graph_signal, _pivot_ioc_graph, enqueue_pivot stay in the scheduler.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass  # CanonicalFinding used via getattr, no direct reference needed

logger = logging.getLogger(__name__)


class SprintGraphAccumulator:
    """
    Accumulates accepted findings into the cross-sprint DuckPGQ graph.

    Each finding is represented as an IOC node:
      - ioc_value = finding_id  (stable cross-sprint identifier)
      - ioc_type  = source_type (e.g. "ct_log", "public", "feed")
      - confidence = finding.confidence
      - source     = sprint_id

    Fail-soft: graph errors must NOT prevent sprint continuation.
    """

    def __init__(self, graph_service_module=None) -> None:
        """
        Args:
            graph_service_module: Optional pre-injected graph_service module.
                                  If None, imported lazily on first accumulate().
        """
        self._gs_mod = graph_service_module

    def _get_graph_service(self):
        if self._gs_mod is None:
            # Lazy import — avoids circular dep at construction time
            from hledac.universal.knowledge import graph_service as gs

            self._gs_mod = gs
        return self._gs_mod

    def accumulate_findings(self, findings: list, sprint_id: str = "") -> int:
        """
        Accumulate findings into the graph.

        Args:
            findings: List of CanonicalFinding (or finding-like objects).
            sprint_id: Sprint identifier; used as the 'source' field.

        Returns:
            Number of rows submitted to graph_service.upsert_ioc_batch().
            Returns 0 if findings is empty or if graph_service raises.
            Graph exceptions are swallowed — this method never raises.
        """
        if not findings:
            return 0

        rows: list[tuple[str, str, float, str]] = []
        for finding in findings:
            fid = getattr(finding, "finding_id", None)
            if not fid:
                continue
            src_type = getattr(finding, "source_type", "unknown") or "unknown"
            raw_confidence = getattr(finding, "confidence", 0.5) or 0.5
            confidence = max(0.0, min(1.0, float(raw_confidence)))
            rows.append((fid, src_type, confidence, sprint_id or ""))

        if not rows:
            return 0

        try:
            gs = self._get_graph_service()
            gs.upsert_ioc_batch(rows)

            # F265B-FIX: Edge creation between findings from same source_type+sprint.
            # Without edges the graph is N isolated nodes — OODA PageRank gets 0,
            # decided_seeds stays empty, "acted on 0 nodes" every cycle.
            # OSINT semantics: co-source findings are thematically related (same query,
            # same lane, same campaign). Connect them with co_source edge.
            self._create_co_source_edges(findings, gs, sprint_id or "")

            return len(rows)
        except Exception:
            # Fail-soft: graph must never block sprint
            logger.warning("[GraphAccumulator] upsert_ioc_batch failed, returning 0")
            return 0

    def _create_co_source_edges(
        self, findings: list, gs, sprint_id: str
    ) -> None:
        """
        F265B-FIX: Create edges between findings that share source_type.

        Groups findings by source_type and creates co_source edges between
        all pairs within each group. This gives the DuckPGQ graph the edge
        density needed for OODA PageRank to rank nodes.

        Bounded: MAX_EDGES_PER_SPRINT=200 to avoid O(n²) blowup on large batches.
        """
        from collections import defaultdict

        MAX_EDGES_PER_SPRINT = 200

        try:
            # Group finding_ids by source_type
            by_source: dict[str, list[str]] = defaultdict(list)
            for f in findings:
                fid = getattr(f, "finding_id", None)
                if not fid:
                    continue
                src = getattr(f, "source_type", "unknown") or "unknown"
                by_source[src].append(fid)

            edges_created = 0
            for src, fids in by_source.items():
                if len(fids) < 2:
                    continue
                # Connect all pairs in group (fully connected subgraph per source)
                for i in range(len(fids)):
                    for j in range(i + 1, len(fids)):
                        if edges_created >= MAX_EDGES_PER_SPRINT:
                            return
                        try:
                            gs.upsert_relation(
                                fids[i],
                                fids[j],
                                "co_source",
                                weight=0.5,
                                evidence=f"sprint:{sprint_id}",
                            )
                            edges_created += 1
                        except Exception:
                            pass
        except Exception:
            pass  # noqa: BARE-EXCEPT  # fail-soft

    def buffer_pivot_relation(
        self, ioc_value: str, ioc_type: str, confidence: float
    ) -> None:
        """
        Buffer a pivot relation into the DuckPGQ graph.

        Args:
            ioc_value: IOC value (URL or raw IOC string).
            ioc_type:  IOC type (e.g. "domain", "ip", "url").
            confidence: Confidence score [0..1].

        Behavior:
          - Lazy-init DuckPGQGraph on first call.
          - URL ioc_value → target domain via urlparse().netloc.
          - Non-URL → target = ioc_value.
          - Calls graph.add_relation(ioc_value, target, rel_type="pivot", evidence="pivot").
          - Fail-soft: exceptions from graph construction and add_relation are swallowed.
          - This method does NOT interact with pivot queues or _pivot_ioc_graph.
        """
        try:
            from urllib.parse import urlparse

            # Determine target: URL → domain, otherwise raw ioc_value
            target = ioc_value
            try:
                parsed = urlparse(ioc_value)
                if parsed.netloc:
                    target = parsed.netloc
            except Exception:
                pass

            # Sprint F265C: Use graph_service upsert_relation (shared singleton)
            # instead of creating a private DuckPGQGraph instance.
            # The old _ioc_graph = DuckPGQGraph() here created an isolated graph
            # that OODA could not see — pivot nodes were invisible to PageRank.
            gs = self._get_graph_service()
            gs.upsert_relation(
                ioc_value,
                target,
                rel_type="pivot",
                weight=1.0,
                evidence="pivot",
            )
        except Exception:
            # Fail-soft: graph errors must never block pivot processing
            logger.warning("[GraphAccumulator] buffer_pivot_relation failed, swallowing")
            pass
