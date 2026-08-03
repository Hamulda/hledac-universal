"""
runtime/sidecars/forensics/_auto_re.py — AutoRE Sidecar Adapter
============================================================

ADVERSARY-004: Hermes3 Auto-RE Flow

Sidecar lane running concurrently with IPFS / BGP / TI-Feed sidecars
during the advisory runner phase (winddown). Low priority (10), fail-soft.

Trigger condition: DocumentIntelligenceEngine.analyze() returned
metadata.file_type == UNKNOWN AND 1KB <= len(content) <= 1MB.

Pipeline (5 stages):
  Stage A — Magic-byte router: first 16B → FormatFamily | None
  Stage B — Hermes3 prompt: 512B + entropy + ASCII ratio → parser Python
  Stage C — Sandboxed execution: ast.parse + restricted subprocess
  Stage D — IOC validation gate: Rust SIMD extractor confirms IOCs
  Stage E — Audit trail: ~24h disk cache (NOT re-executed)

Rate limit: max 3 attempts per sprint (enforced here).
Opt-in gate: HLEDAC_ENABLE_AUTO_RE=1 (default OFF).
M1 8GB safe: Hermes3 ~4s MLX, sandbox ~1s subprocess.
"""

from __future__ import annotations

import os
import logging
from typing import TYPE_CHECKING, Any

from hledac.universal.runtime.sidecars._base import BaseSidecarAdapter
from hledac.universal.runtime.sidecar_protocol import SidecarRegistry

if TYPE_CHECKING:
    from hledac.universal.brain.auto_re.parser_forge import AutoREResult
    from hledac.universal.runtime.scheduler_v2.protocol import SidecarContext

logger = logging.getLogger(__name__)

# ADVERSARY-004 sidecar priority (low — advisory, not primary acquisition)
_AUTO_RE_SIDECAR_PRIORITY = 10

# Per-sprint attempt limit — one Hermes3 inference per attempt
_MAX_AUTO_RE_ATTEMPTS_PER_SPRINT = 3


@SidecarRegistry.register("auto_re")
class AutoRESidecarAdapter(BaseSidecarAdapter):
    """
    Hermes3 Auto-RE sidecar — converts unknown binary formats into IOC extractions.

    Implements the BaseSidecarAdapter protocol so it can be registered in
    SidecarRegistry and dispatched by SidecarOrchestrator.

    Triggered by: DocumentIntelligenceEngine returning UNKNOWN file type
    (wired in recon/document_intelligence.py via a post-processing hook or
    by the sidecar orchestrator reading the UNKNOWN queue from the result sink).

    Rate-limited: max 3 attempts per sprint.
    Opt-in: HLEDAC_ENABLE_AUTO_RE=1.

    Fail-soft: any exception returns [] and logs a warning.
    """

    sidecar_id: str = "auto_re"
    ram_budget_mb: int = 200  # Hermes3 + sandbox
    priority: int = _AUTO_RE_SIDECAR_PRIORITY

    # Per-instance attempt counter (reset at sprint start)
    __slots__ = tuple((
        "_attempt_count",
        "_engine",
        "_sprint_reset_at",
    ))

    def __init__(self) -> None:
        super().__init__()
        self._attempt_count: int = 0
        self._engine: Any = None  # lazy
        self._sprint_reset_at: float = 0.0  # monotic time of last sprint reset

    def is_available(self) -> bool:
        """Check env gate directly — bypasses LaneRegistry (no auto_re lane exists)."""
        return os.environ.get("HLEDAC_ENABLE_AUTO_RE", "0").strip().lower() in (
            "1", "true", "yes"
        )

    def reset_sprint(self) -> None:
        """Reset per-sprint attempt counter. Called by SidecarOrchestrator on sprint start."""
        import time as _time
        self._attempt_count = 0
        self._sprint_reset_at = _time.monotonic()

    # ── BaseSidecarAdapter ──────────────────────────────────────────────────────

    async def run_async(self, ctx: "SidecarContext") -> list[Any]:
        """
        Run the AutoRE sidecar for all queued unknown binaries.

        Returns:
            List of CanonicalFinding objects derived from parsed IOCs.
        """
        # Reset if we've been idle for >1h (sprint boundary approximation)
        import time as _time
        now = _time.monotonic()
        if now - self._sprint_reset_at > 3600:
            self._attempt_count = 0
            self._sprint_reset_at = now

        # Rate limit check — ONE Hermes3 inference counts as ONE attempt
        if self._attempt_count >= _MAX_AUTO_RE_ATTEMPTS_PER_SPRINT:
            logger.debug(
                "[AUTO-RE] Rate limit: %d/%d attempts used",
                self._attempt_count,
                _MAX_AUTO_RE_ATTEMPTS_PER_SPRINT,
            )
            return []

        # Lazy engine init
        if self._engine is None:
            from hledac.universal.brain.auto_re.parser_forge import get_auto_re_engine
            self._engine = get_auto_re_engine()

        if self._engine is None or not self._engine.enabled:
            logger.debug("[AUTO-RE] Sidecar disabled (HLEDAC_ENABLE_AUTO_RE != 1)")
            return []

        # Collect unknown binaries from the sidecar context
        unknown_candidates = self._collect_unknown_candidates(ctx)

        if not unknown_candidates:
            logger.debug("[AUTO-RE] No unknown binary candidates")
            return []

        logger.info(
            "[AUTO-RE] %d candidates queued (attempt %d/%d)",
            len(unknown_candidates),
            self._attempt_count + 1,
            _MAX_AUTO_RE_ATTEMPTS_PER_SPRINT,
        )

        # Process each candidate (up to rate limit)
        findings: list[Any] = []
        for file_path, content in unknown_candidates:
            if self._attempt_count >= _MAX_AUTO_RE_ATTEMPTS_PER_SPRINT:
                logger.info("[AUTO-RE] Rate limit reached, stopping")
                break

            result = await self._process_one(file_path, content, ctx)
            if result:
                findings.extend(result)
                self._attempt_count += 1

        return findings

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _collect_unknown_candidates(
        self,
        ctx: "SidecarContext",
    ) -> list[tuple[str, bytes]]:
        """
        Pull unknown-binary candidates from the sidecar context.

        Sources (in priority order):
        1. ctx.unknown_binaries: injected by DocumentIntelligenceEngine
           post-processing hook (wired in recon/document_intelligence.py).
        2. Scan ctx.findings for any with metadata.file_type == UNKNOWN
           (only possible when findings are canonical findings with metadata attached).

        Returns:
            List of (file_path, content) tuples, bounded to 10 for M1 8GB safety.
        """
        candidates: list[tuple[str, bytes]] = []
        seen_paths: set[str] = set()

        # Source 1: ctx.unknown_binaries (primary path)
        if hasattr(ctx, "unknown_binaries"):
            ub: list[tuple[str, bytes]] = getattr(ctx, "unknown_binaries", []) or []
            for item in ub:
                if (
                    isinstance(item, (list, tuple))
                    and len(item) >= 2
                    and isinstance(item[0], str)
                    and isinstance(item[1], bytes)
                    and item[0] not in seen_paths
                ):
                    candidates.append((item[0], item[1]))
                    seen_paths.add(item[0])

        # Source 2: scan ctx.findings for UNKNOWN file_type metadata
        # Only works when findings are enriched CanonicalFinding objects with
        # metadata attached (not the default raw dict form).
        findings: list[Any] = getattr(ctx, "findings", []) or []
        for finding in findings:
            file_path = None
            content = None

            # CanonicalFinding: finding.metadata.file_path + finding.content
            if hasattr(finding, "metadata"):
                md = finding.metadata
                file_path = getattr(md, "file_path", None) or getattr(md, "path", None)
                content = getattr(md, "content", None)
                file_type = getattr(md, "file_type", None)
                # Only include UNKNOWN types (if file_type is available)
                if file_type is not None and not (
                    hasattr(file_type, "value")
                    and file_type.value == "UNKNOWN"
                ):
                    continue

            # Dict-style finding
            elif isinstance(finding, dict):
                raw_meta = finding.get("metadata", {}) or {}
                file_type = raw_meta.get("file_type", "")
                # Skip non-UNKNOWN dict findings
                if file_type and file_type != "UNKNOWN":
                    continue
                file_path = (
                    raw_meta.get("file_path")
                    or raw_meta.get("path")
                    or finding.get("file_path")
                    or finding.get("path")
                )
                content = raw_meta.get("content") or finding.get("content")

            if file_path and content and isinstance(content, bytes) and file_path not in seen_paths:
                if 1024 <= len(content) <= 1_048_576:
                    candidates.append((str(file_path), content))
                    seen_paths.add(file_path)

        return candidates[:10]  # M1 8GB safety cap

    async def _process_one(
        self,
        file_path: str,
        content: bytes,
        ctx: "SidecarContext",
    ) -> list[Any]:
        """
        Process a single unknown binary through all 5 AutoRE stages.

        Returns:
            List of CanonicalFinding objects from parsed IOCs.
        """
        result: "AutoREResult | None" = None
        try:
            result = await self._engine.process_unknown_binary(file_path, content)
        except Exception as e:
            logger.warning("[AUTO-RE] process_unknown_binary failed: %s", e)
            return []

        if not result or not result.success or not result.iocs:
            logger.debug(
                "[AUTO-RE] No IOCs from %s (stage=%s error=%s)",
                file_path,
                getattr(result, "stage", "?"),
                getattr(result, "error", ""),
            )
            return []

        # Convert ParsedIOC → finding dict
        return self._iocs_to_findings(result, file_path, ctx)

    def _iocs_to_findings(
        self,
        result: "AutoREResult",
        file_path: str,
        ctx: "SidecarContext",
    ) -> list[Any]:
        """
        Convert AutoRE Stage-D IOCs into finding dicts.

        Each ParsedIOC becomes a finding dict with:
          - ioc_type, ioc_value, confidence from ParsedIOC
          - source = "auto_re:<format_family>"
          - metadata: parser hash, audit path, Hermes3/sandbox timing
        """
        findings: list[Any] = []

        audit_path: str = ""
        if result.parser_source:
            try:
                from pathlib import Path
                audit_dir = Path.home() / ".cache" / "hledac" / "auto_re"
                audit_path = str(audit_dir / f"{result.file_hash}.json")
            except Exception:
                pass

        for ioc in result.iocs:
            finding = {
                "ioc_type": ioc.ioc_type,
                "ioc_value": ioc.ioc_value,
                "confidence": ioc.confidence,
                "source": f"auto_re:{result.format_family}",
                "file_path": file_path,
                "file_hash": result.file_hash,
                "context": ioc.context,
                "metadata": {
                    "auto_re_format_family": result.format_family,
                    "auto_re_format_hypothesis": result.format_hypothesis,
                    "auto_re_hermes3_ms": result.hermes3_ms,
                    "auto_re_sandbox_ms": result.sandbox_ms,
                    "auto_re_audit_path": audit_path,
                    "auto_re_stage": result.stage,
                },
            }
            findings.append(finding)

        # Async graph upsert (fire-and-forget)
        self._upsert_graph_async(ctx, findings)

        return findings

    def _upsert_graph_async(
        self,
        ctx: "SidecarContext",
        findings: list[dict[str, Any]],
    ) -> None:
        """Fire-and-forget graph upsert. Non-blocking."""
        try:
            import asyncio
            loop = asyncio.get_running_loop()
            loop.create_task(self._upsert_graph_loop(findings))
        except Exception:
            pass  # no event loop or upsert failed — non-critical

    async def _upsert_graph_loop(self, findings: list[dict[str, Any]]) -> None:
        """Async graph upsert — runs in the background."""
        try:
            from hledac.universal.knowledge.graph_service import DuckPGQGraph
            graph = DuckPGQGraph.get_instance()
            for finding in findings:
                await graph.upsert_ioc(
                    ioc_type=finding["ioc_type"],
                    ioc_value=finding["ioc_value"],
                    source=finding["source"],
                    metadata=finding.get("metadata", {}),
                )
        except Exception as e:
            logger.debug("[AUTO-RE] graph upsert failed: %s", e)
