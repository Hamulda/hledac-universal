"""
EvidenceSink Protocol — Dependency Inversion pro evidence persistence
===================================================================

A5-02: FetchCoordinator vytváří evidence packets, EvidenceLog je globální singleton.
Coordinator by neměl vědět o persistence layer — závislost jde opačným směrem.

Řešení: EvidenceSink Protocol — FetchCoordinator dostane injektovanou závislost,
která ví jak zapsat evidence. SprintScheduler.Injector (nebo DI container)
rozhodne, že to je EvidenceLog.

Pravidla:
- FetchCoordinator NENÍ závislý na EvidenceLog (neimportuje ho)
- EvidenceSink Protocol definuje pouze to, co FetchCoordinator potřebuje
- Implementace (EvidenceLog) žije v injection layer
- Fail-safe: pokud evidence_sink=None, evidence se pouze sbírají v paměti
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.evidence_log import EvidenceEvent

__all__ = ["EvidenceSink", "EvidenceSinkProtocol"]


class EvidenceSink(ABC):
    """
    Abstract base pro evidence persistence.

    Drží pouze to rozhraní, které FetchCoordinator potřebuje —
    nic víc, nic míň. Oddělení concerns podle Dependency Inversion Principle.
    """

    @abstractmethod
    async def append_evidence(self, event: EvidenceEvent) -> str | None:
        """
        Přidá evidence event a vrátí event_id.

        Args:
            event: EvidenceEvent k zapsání

        Returns:
            event_id nebo None při chybě
        """
        ...

    @abstractmethod
    async def append_batch(self, events: list[EvidenceEvent]) -> list[str | None]:
        """
        Přidá více evidence events batch operací.

        Args:
            events: Seznam EvidenceEvent

        Returns:
            Seznam event_id (None pro failed)
        """
        ...

    @abstractmethod
    async def get_evidence_ids(self, limit: int = 100) -> list[str]:
        """
        Vrátí seznam posledních event_id.

        Args:
            limit: Max počet

        Returns:
            Seznam event_id
        """
        ...


# Protocol verze — pro structural typing (Protocol + typing.Protocol)
from typing import Protocol as EvidenceSinkProtocol  # noqa: E402, F401
from _core._util import aclose
