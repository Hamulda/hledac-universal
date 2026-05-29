"""
SemanticStoreBuffer — extracted semantic buffering seam from DuckDBShadowStore.

Sprint F222: Isolates semantic buffering logic so DuckDBShadowStore no longer
contains the buffering implementation directly. The buffer delegates to an
injected SemanticStore instance (or silently no-ops when no store is present).

No behavior change — fail-open semantics preserved.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.knowledge.semantic_store import SemanticStore


class SemanticStoreBuffer:
    """
    Bounded semantic buffering for CanonicalFinding list.

    Inject via ``inject(store)``, then call ``buffer_findings(findings)``.
    Fail-open: missing store or any exception is silently skipped — buffering
    failure never blocks canonical storage.
    """

    __slots__ = ("_store",)

    def __init__(self) -> None:
        self._store: SemanticStore | None = None

    # ------------------------------------------------------------------
    # Public seam
    # ------------------------------------------------------------------

    def inject(self, store: Any) -> None:
        """
        Sprint 8SB: Inject SemanticStore instance for semantic buffering of findings.

        The store is used to buffer findings for FastEmbed embedding + LanceDB
        indexing during WINDUP flush.
        """
        self._store = store

    def buffer_findings(self, findings: list[Any]) -> None:
        """
        Sprint 8SB: Buffer findings into SemanticStore for batch embedding.

        Fail-open: any exception is caught and logged — semantic buffering
        failure never blocks storage. Missing store is a silent no-op.
        """
        if self._store is None:
            return
        try:
            for f in findings:
                text = getattr(f, "payload_text", None) or ""
                if not text:
                    continue
                # Collect IOC types from pattern_matches (tuple/dict handling)
                ioc_types: list[str] = []
                pm = getattr(f, "pattern_matches", None)
                if pm:
                    for item in pm:
                        if isinstance(item, tuple) and len(item) >= 2:
                            ioc_types.append(str(item[1]))
                        elif isinstance(item, dict):
                            lbl = item.get("label") or ""
                            if lbl:
                                ioc_types.append(str(lbl))
                ioc_types = list(set(ioc_types)) if ioc_types else []
                self._store.buffer_finding(
                    text=text,
                    source_type=getattr(f, "source_type", "unknown"),
                    finding_id=getattr(f, "finding_id", ""),
                    ts=getattr(f, "ts", 0.0),
                    ioc_types=ioc_types,
                )
        except Exception as exc:
            logging.getLogger(__name__).debug("Semantic buffering skipped: %s", exc)
