"""
ResearchContext — CANONICAL CONTEXT CARRIER
===========================================

ROLE: Canonical context carrier for orchestrator <-> agent communication.
This module defines the primary data structure for communication between
HermesCommander and agents during autonomous research.

CONTEXT vs LEDGER vs FACTS:
---------------------------
TIER 1 — EVIDENCE LEDGER (EvidenceLog):
    append-only events recording what happened

TIER 2 — SPRINT FACTS (DuckDBShadowStore):
    sprint_delta, sprint_scorecard, source_hit_log

TIER 3 — GRAPH (injected):
    IOCGraph (Kuzu), SemanticStore (LanceDB)

ResearchContext is the CARRIER between tiers:
    ResearchContext (carrier) --handoff metadata--> EvidenceLog (ledger)
    EvidenceLog.append() --analytics_hook--> DuckDBShadowStore (facts)

AUTHORITY BOUNDARY:
- ResearchContext carries state but does NOT sample, govern, or budget resources.
- For UMA sampling use: utils/uma_budget.py (sampler)
- For UMA governance use: core/resource_governor.py (governor)
- For request budgeting use: resource_allocator.py (allocator)
- For ledgers use: evidence_log.py, tool_exec_log.py, metrics_registry.py

This module is the CORRECT context carrier for orchestrator <-> agent communication.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set

from pydantic import BaseModel, Field, field_validator, ConfigDict, field_serializer
from dataclasses import dataclass as pydantic_dataclass


# =============================================================================
# CONTEXT HANDOFF METADATA — Sprint F11C: Bounded Handoff Surface
# =============================================================================
# Typed descriptor for context/evidence handoff between ResearchContext (carrier)
# and EvidenceLog (ledger). Replaces implicit Dict[str, Any] with explicit schema.
#
# RULES:
#   [1] ResearchContext.context_metadata carries this descriptor
#   [2] EvidenceLog.create_event(correlation=) receives the handoff
#   [3] No new writer/orchestrator authority — only metadata transport
#   [4] Frozen + bounded — no side effects, no eager init
# =============================================================================


@pydantic_dataclass(frozen=True)
class ContextHandoffMetadata:
    """
    Bounded metadata for context → evidence ledger handoff.

    This descriptor:
    - Documents the explicit handoff contract between carrier and ledger
    - Provides type safety for context_metadata field
    - Remains backward-compatible with existing Dict[str, Any] usage
    - Carries NO independent authority

    Fields (all optional for backward compat):
        phase: Current research phase (planning/execution/synthesis)
        branch_id: Parallel branch identifier (for correlation)
        parent_run_id: Parent run if this is a sub-run
        iteration_snapshot: Iteration number at handoff time
        source_component: Which component created the handoff
        target_components: Which components should receive this
        ttl_seconds: Time-to-live for this handoff (0 = no expiry)

    NOTE: This is a typed descriptor, NOT a new authority surface.
          It does NOT govern, sample, or budget resources.
    """
    phase: Optional[str] = None
    branch_id: Optional[str] = None
    parent_run_id: Optional[str] = None
    iteration_snapshot: Optional[int] = None
    source_component: Optional[str] = None
    target_components: Optional[List[str]] = None
    ttl_seconds: Optional[int] = None  # None = no expiry, 0 = expired

    def to_correlation_dict(self) -> Dict[str, Optional[str]]:
        """
        Convert to RunCorrelation-compatible dict for EvidenceLog injection.

        STABLE CORRELATION GRAMMAR (always 4 keys, values may be None):
          run_id     = parent_run_id  (run context propagation)
          branch_id  = branch_id      (parallel branch correlation)
          provider_id = None          (set by provider layer, not carrier)
          action_id  = None          (set by action layer, not carrier)

        NOTE: This is a one-way conversion from handoff metadata to correlation.
              The carrier owns the handoff metadata; the ledger owns correlation.
        """
        return {
            "run_id": self.parent_run_id,
            "branch_id": self.branch_id,
            "provider_id": None,
            "action_id": None,
        }

    @classmethod
    def from_dict_compat(cls, data: Dict[str, Any]) -> "ContextHandoffMetadata":
        """
        Reconstruct typed ContextHandoffMetadata from raw dict compat shape.

        BACKWARD-COMPAT SEAM: Allows existing raw dict handoffs stored in
        context_metadata["handoff"] to be truthfully elevated to typed form.

        Supported dict shapes:
          - {"parent_run_id": "...", "branch_id": "...", ...}  (legacy)
          - {"phase": "...", "ttl_seconds": 300, ...}         (structured)

        Args:
            data: Raw dict from context_metadata["handoff"]

        Returns:
            ContextHandoffMetadata with fields extracted from dict

        NOTE: This is a COMPATIBILITY coercion, not a general validator.
              Fields not matching the dataclass signature are silently dropped.
        """
        return cls(
            phase=data.get("phase"),
            branch_id=data.get("branch_id"),
            parent_run_id=data.get("parent_run_id"),
            iteration_snapshot=data.get("iteration_snapshot"),
            source_component=data.get("source_component"),
            target_components=data.get("target_components"),
            ttl_seconds=data.get("ttl_seconds"),
        )


class EntityType(str, Enum):
    """Typy entit objevených během výzkumu"""
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    CONCEPT = "concept"
    TECHNOLOGY = "technology"
    EVENT = "event"
    SOURCE = "source"
    UNKNOWN = "unknown"


class HypothesisStatus(str, Enum):
    """Stav hypotézy ve výzkumném procesu"""
    PENDING = "pending"           # Čeká na ověření
    TESTING = "testing"           # Právě testována
    CONFIRMED = "confirmed"       # Potvrzena
    REJECTED = "rejected"         # Vyvrácena
    UNCERTAIN = "uncertain"       # Nejasná, potřeba více dat


class ErrorSeverity(str, Enum):
    """Závažnost chyby"""
    LOW = "low"                   # Nezávažná, lze pokračovat
    MEDIUM = "medium"             # Střední, vyžaduje pozornost
    HIGH = "high"                 # Vysoká, omezuje funkčnost
    CRITICAL = "critical"         # Kritická, zastavuje výzkum


class BudgetState(BaseModel):
    """Stav rozpočtu pro výzkum"""
    max_iterations: int = Field(default=20, ge=1, le=100)
    max_time_minutes: float = Field(default=30.0, ge=1.0, le=240.0)
    max_tokens: int = Field(default=100000, ge=1000)
    max_api_calls: int = Field(default=100, ge=1)

    # Aktuální spotřeba
    iterations_used: int = Field(default=0, ge=0)
    time_elapsed_minutes: float = Field(default=0.0, ge=0.0)
    tokens_used: int = Field(default=0, ge=0)
    api_calls_made: int = Field(default=0, ge=0)

    @property
    def iterations_remaining(self) -> int:
        """Zbývající iterace"""
        return max(0, self.max_iterations - self.iterations_used)

    @property
    def time_remaining_minutes(self) -> float:
        """Zbývající čas"""
        return max(0.0, self.max_time_minutes - self.time_elapsed_minutes)

    @property
    def tokens_remaining(self) -> int:
        """Zbývající tokeny"""
        return max(0, self.max_tokens - self.tokens_used)

    @property
    def api_calls_remaining(self) -> int:
        """Zbývající API volání"""
        return max(0, self.max_api_calls - self.api_calls_made)

    @property
    def is_exhausted(self) -> bool:
        """Zda je nějaký rozpočet vyčerpán"""
        return (
            self.iterations_remaining == 0 or
            self.time_remaining_minutes <= 0 or
            self.tokens_remaining == 0 or
            self.api_calls_remaining == 0
        )

    def to_prompt_section(self) -> str:
        """Formátuje rozpočet pro prompt"""
        return (
            f"Budget Status:\n"
            f"  Iterations: {self.iterations_used}/{self.max_iterations} "
            f"({self.iterations_remaining} remaining)\n"
            f"  Time: {self.time_elapsed_minutes:.1f}/{self.max_time_minutes:.1f}min "
            f"({self.time_remaining_minutes:.1f} remaining)\n"
            f"  Tokens: {self.tokens_used}/{self.max_tokens} "
            f"({self.tokens_remaining} remaining)\n"
            f"  API Calls: {self.api_calls_made}/{self.max_api_calls} "
            f"({self.api_calls_remaining} remaining)\n"
            f"  Exhausted: {'YES' if self.is_exhausted else 'NO'}"
        )


class Entity(BaseModel):
    """Entita objevená během výzkumu"""
    entity_id: str = Field(..., description="Unikátní ID entity")
    name: str = Field(..., description="Název entity")
    entity_type: EntityType = Field(default=EntityType.UNKNOWN)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    first_seen_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    source_urls: List[str] = Field(default_factory=list)

    @field_validator('source_urls', mode='before')
    @classmethod
    def ensure_list(cls, v):
        if v is None:
            return []
        return v


class Hypothesis(BaseModel):
    """Hypotéza formulovaná během výzkumu"""
    hypothesis_id: str = Field(..., description="Unikátní ID hypotézy")
    statement: str = Field(..., description="Text hypotézy")
    status: HypothesisStatus = Field(default=HypothesisStatus.PENDING)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    supporting_evidence: List[str] = Field(default_factory=list)
    contradicting_evidence: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    tested_at: Optional[datetime] = None

    @property
    def evidence_balance(self) -> int:
        """Bilance důkazů (kladné - záporné)"""
        return len(self.supporting_evidence) - len(self.contradicting_evidence)


class ErrorRecord(BaseModel):
    """Záznam o chybě během výzkumu"""
    error_id: str = Field(..., description="Unikátní ID chyby")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    severity: ErrorSeverity = Field(default=ErrorSeverity.MEDIUM)
    component: str = Field(..., description="Komponenta, kde chyba nastala")
    message: str = Field(..., description="Chybová zpráva")
    traceback: Optional[str] = None
    context: Dict[str, Any] = Field(default_factory=dict)
    recovered: bool = Field(default=False)


class ResearchContext(BaseModel):
    """
    Hlavní kontextový model pro autonomní výzkum.

    Tento model uchovává veškerý stav během výzkumného procesu
    a slouží jako primární datová struktura pro HermesCommander.
    """

    # Základní identifikace
    query: str = Field(..., description="Původní výzkumný dotaz")
    research_id: str = Field(..., description="Unikátní ID výzkumu")
    iteration: int = Field(default=0, ge=0, description="Aktuální iterace")

    # Rozpočet a limity
    budgets: BudgetState = Field(default_factory=BudgetState)

    # Aktivní entity a hypotézy
    active_entities: List[Entity] = Field(default_factory=list)
    hypotheses: List[Hypothesis] = Field(default_factory=list)

    # Navštívené zdroje
    visited_urls: Set[str] = Field(default_factory=set)
    visited_domains: Set[str] = Field(default_factory=set)

    # Chyby a problémy
    errors: List[ErrorRecord] = Field(default_factory=list)

    # Kontextová data
    key_findings: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    context_metadata: Dict[str, Any] = Field(default_factory=dict)

    # =============================================================================
    # CONTEXT HANDOFF HELPERS — Sprint F1100B: Typed Handoff Surface
    # =============================================================================
    # Convenience helpers for typed handoff metadata.
    # These are thin wrappers over context_metadata dict — no new authority.
    # =============================================================================

    def get_handoff_metadata(self) -> Optional[ContextHandoffMetadata]:
        """
        Get typed handoff metadata from context_metadata dict.

        BACKWARD-COMPAT: If context_metadata["handoff"] is a raw dict (legacy
        storage format), attempt to coerce it to ContextHandoffMetadata via
        from_dict_compat(). If coercion fails or value is not a dict, return None.

        Returns:
            ContextHandoffMetadata if stored in context_metadata["handoff"], else None.
            Typed object or None — never a raw dict.
        """
        handoff = self.context_metadata.get("handoff")
        if isinstance(handoff, ContextHandoffMetadata):
            return handoff
        if isinstance(handoff, dict):
            # BACKWARD-COMPAT: coerce raw dict to typed form
            return ContextHandoffMetadata.from_dict_compat(handoff)
        return None

    def set_handoff_metadata(self, metadata: ContextHandoffMetadata) -> None:
        """
        Store typed handoff metadata in context_metadata dict.

        This is a convenience setter — carrier does NOT validate handoff.
        The metadata is stored at context_metadata["handoff"] key.
        """
        self.context_metadata["handoff"] = metadata
        self.updated_at = datetime.now(timezone.utc)

    def get_correlation_for_handoff(self) -> Dict[str, Optional[str]]:
        """
        Get RunCorrelation-compatible dict from stored handoff metadata.

        STABLE CORRELATION GRAMMAR: Always returns exactly 4 keys:
          run_id, branch_id, provider_id, action_id

        Values may be None if no handoff metadata is stored.

        Returns:
            Correlation dict suitable for EvidenceLog.create_event(correlation=...)
            Stable grammar dict (all 4 keys present) even when handoff is absent.
        """
        handoff = self.get_handoff_metadata()
        if handoff is None:
            return {
                "run_id": None,
                "branch_id": None,
                "provider_id": None,
                "action_id": None,
            }
        return handoff.to_correlation_dict()

    # Časové značky
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = ConfigDict(populate_by_name=True)

    @field_serializer('created_at')
    def serialize_datetime(self, dt: datetime) -> str:
        return dt.isoformat()

    @field_serializer('updated_at')
    def serialize_datetime2(self, dt: datetime) -> str:
        return dt.isoformat()

    @field_serializer('visited_urls')
    def serialize_set_to_list(self, s: set) -> list:
        return list(s)

    @field_serializer('visited_domains')
    def serialize_set_to_list2(self, s: set) -> list:
        return list(s)

    def add_entity(self, entity: Entity) -> None:
        """Přidá entitu pokud ještě neexistuje"""
        existing = [e for e in self.active_entities if e.entity_id == entity.entity_id]
        if not existing:
            self.active_entities.append(entity)
            self.updated_at = datetime.now(timezone.utc)

    def add_hypothesis(self, hypothesis: Hypothesis) -> None:
        """Přidá hypotézu pokud ještě neexistuje"""
        existing = [h for h in self.hypotheses if h.hypothesis_id == hypothesis.hypothesis_id]
        if not existing:
            self.hypotheses.append(hypothesis)
            self.updated_at = datetime.now(timezone.utc)

    def add_error(self, error: ErrorRecord) -> None:
        """Přidá chybový záznam"""
        self.errors.append(error)
        self.updated_at = datetime.now(timezone.utc)

    def add_visited_url(self, url: str) -> None:
        """Zaznamená navštívenou URL"""
        self.visited_urls.add(url)
        # Extrahuj doménu
        try:
            from urllib.parse import urlparse
            domain = urlparse(url).netloc
            if domain:
                self.visited_domains.add(domain)
        except Exception:
            pass
        self.updated_at = datetime.now(timezone.utc)

    def increment_iteration(self) -> None:
        """Zvýší číslo iterace"""
        self.iteration += 1
        self.budgets.iterations_used = self.iteration
        self.updated_at = datetime.now(timezone.utc)

    def get_entities_by_type(self, entity_type: EntityType) -> List[Entity]:
        """Vrátí entity daného typu"""
        return [e for e in self.active_entities if e.entity_type == entity_type]

    def get_pending_hypotheses(self) -> List[Hypothesis]:
        """Vrátí hypotézy čekající na ověření"""
        return [h for h in self.hypotheses if h.status == HypothesisStatus.PENDING]

    def get_confirmed_hypotheses(self) -> List[Hypothesis]:
        """Vrátí potvrzené hypotézy"""
        return [h for h in self.hypotheses if h.status == HypothesisStatus.CONFIRMED]

    def get_errors_by_severity(self, severity: ErrorSeverity) -> List[ErrorRecord]:
        """Vrátí chyby dané závažnosti"""
        return [e for e in self.errors if e.severity == severity]

    def has_critical_errors(self) -> bool:
        """Kontroluje zda jsou nějaké kritické chyby"""
        return any(e.severity == ErrorSeverity.CRITICAL for e in self.errors)

    @property
    def total_entities(self) -> int:
        """Celkový počet entit"""
        return len(self.active_entities)

    @property
    def total_hypotheses(self) -> int:
        """Celkový počet hypotéz"""
        return len(self.hypotheses)

    @property
    def confirmed_count(self) -> int:
        """Počet potvrzených hypotéz"""
        return len(self.get_confirmed_hypotheses())

    @property
    def total_errors(self) -> int:
        """Celkový počet chyb"""
        return len(self.errors)

    def to_hermes_prompt(self) -> str:
        """
        Převede kontext na prompt pro HermesCommander.

        Vytvoří stručné shrnutí vhodné pro LLM prompt - ne celý raw log.
        """
        lines = [
            "=" * 60,
            "RESEARCH CONTEXT",
            "=" * 60,
            f"",
            f"Query: {self.query}",
            f"Research ID: {self.research_id}",
            f"Iteration: {self.iteration}",
            f"",
            "-" * 40,
            self.budgets.to_prompt_section(),
            "-" * 40,
            f"",
            f"Entities Found: {self.total_entities}",
        ]

        # Přidej rozdělení entit podle typu
        if self.active_entities:
            lines.append("  By Type:")
            for et in EntityType:
                count = len(self.get_entities_by_type(et))
                if count > 0:
                    lines.append(f"    - {et.value}: {count}")

        lines.extend([
            f"",
            f"Hypotheses: {self.total_hypotheses} total, {self.confirmed_count} confirmed",
        ])

        # Přidej pending hypotézy
        pending = self.get_pending_hypotheses()
        if pending:
            lines.append("  Pending Hypotheses:")
            for h in pending[:3]:  # Max 3
                lines.append(f"    - [{h.hypothesis_id}] {h.statement[:60]}...")

        lines.extend([
            f"",
            f"Visited URLs: {len(self.visited_urls)} unique",
            f"Visited Domains: {len(self.visited_domains)} unique",
        ])

        # Přidej key findings
        if self.key_findings:
            lines.extend([
                f"",
                "Key Findings:",
            ])
            for i, finding in enumerate(self.key_findings[-5:], 1):  # Posledních 5
                lines.append(f"  {i}. {finding[:100]}{'...' if len(finding) > 100 else ''}")

        # Přidej open questions
        if self.open_questions:
            lines.extend([
                f"",
                "Open Questions:",
            ])
            for i, question in enumerate(self.open_questions[:3], 1):  # Max 3
                lines.append(f"  {i}. {question[:80]}{'...' if len(question) > 80 else ''}")

        # Přidej chyby pokud existují
        if self.errors:
            critical = self.get_errors_by_severity(ErrorSeverity.CRITICAL)
            high = self.get_errors_by_severity(ErrorSeverity.HIGH)
            if critical or high:
                lines.extend([
                    f"",
                    "⚠️  ERRORS:",
                ])
                for e in critical[:2]:
                    lines.append(f"  [CRITICAL] {e.component}: {e.message}")
                for e in high[:2]:
                    lines.append(f"  [HIGH] {e.component}: {e.message}")

        lines.extend([
            f"",
            "=" * 60,
        ])

        return "\n".join(lines)

    def to_summary_dict(self) -> Dict[str, Any]:
        """Vrátí shrnující dictionary pro export"""
        return {
            "research_id": self.research_id,
            "query": self.query,
            "iteration": self.iteration,
            "budgets": {
                "max_iterations": self.budgets.max_iterations,
                "iterations_used": self.budgets.iterations_used,
                "iterations_remaining": self.budgets.iterations_remaining,
                "time_remaining_minutes": self.budgets.time_remaining_minutes,
                "tokens_remaining": self.budgets.tokens_remaining,
                "is_exhausted": self.budgets.is_exhausted,
            },
            "entities": {
                "total": self.total_entities,
                "by_type": {
                    et.value: len(self.get_entities_by_type(et))
                    for et in EntityType
                },
            },
            "hypotheses": {
                "total": self.total_hypotheses,
                "confirmed": self.confirmed_count,
                "pending": len(self.get_pending_hypotheses()),
            },
            "visited": {
                "urls": len(self.visited_urls),
                "domains": len(self.visited_domains),
            },
            "errors": {
                "total": self.total_errors,
                "has_critical": self.has_critical_errors(),
            },
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }
