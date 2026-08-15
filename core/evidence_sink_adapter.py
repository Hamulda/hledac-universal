"""
EvidenceSink Adapter — EvidenceLog implementuje EvidenceSink Protocol
===================================================================


A5-02: Adapter který obalí EvidenceLog a implementuje EvidenceSink Protocol.

Tento adapter žije v injection layer — spojuje Protocol s implementací.
FetchCoordinator nikdy neimportuje EvidenceLog přímo.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from core._util import aclose

if TYPE_CHECKING:
    from hledac.universal.evidence_log import EvidenceLog

__all__ = ["EvidenceSinkAdapter"]


class EvidenceSinkAdapter:
    """
    Adapter který obalí EvidenceLog a implementuje EvidenceSink Protocol.

    FetchCoordinator dostane tento adapter místo přímo EvidenceLog.
    Žádná změna v chování — pouze structural typing compliance.

    Usage:
        evidence_log = EvidenceLog(run_id="sprint-001")
        sink = EvidenceSinkAdapter(evidence_log)
        coordinator = FetchCoordinator(..., evidence_sink=sink)
    """

    __slots__ = ("_log",)

    def __init__(self, log: EvidenceLog) -> None:
        """
        Initialize adapter with EvidenceLog instance.

        Args:
            log: EvidenceLog instance to wrap
        """
        self._log = log

    async def append_evidence(self, event: Any) -> str | None:
        """
        Přidá evidence event přes EvidenceLog.

        Args:
            event: EvidenceEvent k zapsání

        Returns:
            event_id nebo None při chybě
        """
        try:
            self._log.append(event)
            return getattr(event, "event_id", None)
        except Exception:
            return None

    async def append_batch(self, events: list[Any]) -> list[str | None]:
        """
        Přidá více evidence events batch operací.

        Args:
            events: Seznam EvidenceEvent

        Returns:
            Seznam event_id (None pro failed)
        """
        results: list[str | None] = []
        for event in events:
            try:
                self._log.append(event)
                results.append(getattr(event, "event_id", None))
            except Exception:
                results.append(None)
        return results

    async def get_evidence_ids(self, limit: int = 100) -> list[str]:
        """
        Vrátí seznam posledních event_id z EvidenceLog.

        Args:
            limit: Max počet

        Returns:
            Seznam event_id
        """
        try:
            # EvidenceLog má _log deque s posledními events
            log = getattr(self._log, "_log", None)
            if log is None:
                return []
            ids: list[str] = []
            for event in list(log)[-limit:]:
                eid = getattr(event, "event_id", None)
                if eid:
                    ids.append(eid)
            return ids
        except Exception:
            return []
