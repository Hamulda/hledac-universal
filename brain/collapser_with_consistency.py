"""
META-007: Finding Collapser with Consistency Gate
=================================================


Combines PropositionalConsistencyVerifier with FindingCollapser for
the "confident liar" detection feedback loop.

GATE PLACEMENT:
    findings (raw)
        ↓
    PropositionalConsistencyBridge.check_batch(findings)
        ↓ (before collapser map)
    clean findings → FindingCollapser.collapse_findings()
        ↓
    contradictory findings → flagged with consistency_flag="contradiction"
        ↓
    Synthesis receives both with consistency metadata

This ensures contradictory findings are NOT silently merged by the collapser.
Analyst sees explicit contradiction warnings instead of "domain X → [1.2.3.4, 5.6.7.8]".
"""

from __future__ import annotations

import json as _json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class FindingCollapserWithConsistency:
    """
    FindingCollapser wrapper that integrates consistency verification.

    Architecture:
        1. Call PropositionalConsistencyBridge.check_batch(findings)
        2. Pass clean findings to FindingCollapser.collapse_findings()
        3. Attach consistency metadata to groups
        4. Return structured result with contradiction flags

    M1 8GB: Single-pass processing, no additional memory overhead.
    """

    __slots__ = (
        "_collapser_available",
        "_consistency_bridge",
    )

    def __init__(self) -> None:
        self._collapser_available = False
        self._consistency_bridge = None
        self._init_dependencies()

    def _init_dependencies(self) -> None:
        """Lazy initialization of dependencies."""
        # Try to import finding_collapser
        try:
            from hledac.universal.core.rust_backend import rust
            raw = rust.raw
            if hasattr(raw, "collapse_findings"):
                self._collapser_available = True
        except ImportError:
            pass

        # Get consistency bridge
        try:
            from hledac.universal.brain.consistency_bridge import get_consistency_bridge
            self._consistency_bridge = get_consistency_bridge()
        except ImportError:
            pass

    async def collapse_with_consistency(
        self,
        findings: list[dict[str, Any]],
        *,
        max_groups: int = 12,
        max_chars_per_group: int = 400,
        max_sources_per_group: int = 8,
        emit_alerts: bool = True,
    ) -> dict[str, Any]:
        """
        Collapse findings with consistency verification gate.

        META-007: Async method — calls PropositionalConsistencyBridge.check_batch()
        directly via await (no run_until_complete, no nested event loops).

        Args:
            findings: List of finding dicts
            max_groups: Maximum output groups (default 12)
            max_chars_per_group: Max chars per group (default 400)
            max_sources_per_group: Max sources per group (default 8)
            emit_alerts: Whether to emit PropositionalContradictionAlert

        Returns:
            dict with keys:
                - markdown: Collapsed Markdown output (from FindingCollapser)
                - consistency_result: ConsistencyCheckResult
                - clean_findings: Findings that passed consistency
                - contradictory_findings: Findings with contradictions
                - disputed_findings: Findings from disputed entities
                - suspect_sources: Sources flagged as suspect
                - batch_consistency_score: Batch-level score [0.0-1.0]
        """
        # META-007: Direct async call — no asyncio.run() / run_until_complete()
        consistency_result = await self._consistency_bridge.check_batch(
            findings, emit_alerts=emit_alerts
        ) if self._consistency_bridge else None

        # Prepare findings for collapser
        if consistency_result and consistency_result.contradictory:
            # Remove contradictory findings from collapser input
            contradictory_ids = {f.get("finding_id") for f in consistency_result.contradictory if f.get("finding_id")}
            clean_for_collapse = [
                f for f in findings
                if f.get("finding_id") not in contradictory_ids
            ]
        else:
            clean_for_collapse = findings

        # Run collapser on clean findings
        markdown = ""
        if self._collapser_available and clean_for_collapse:
            try:
                from hledac.universal.core.rust_backend import rust
                findings_json = _json.dumps(clean_for_collapse).encode("utf-8")
                result = rust.raw.collapse_findings(
                    findings_json,
                    max_groups,
                    max_chars_per_group,
                    max_sources_per_group,
                )
                markdown = result.decode("utf-8") if isinstance(result, bytes) else result
                
                # [SWARM]-004: Apply entropy-guided word pruning BEFORE returning
                # This is a fast Rust pre-pass (~5-10μs for 4000 chars) that:
                # - Removes boilerplate words (TF-IDF: words in >=80% of groups)
                # - Drops low-entropy tokens (Shannon entropy < 3.5 bits)
                # - Preserves all IOCs (IPs, domains, hashes, CVEs, APT names)
                # - Target: 30-50% token reduction, ~1.5x Hermes inference speedup
                try:
                    compressed = rust.raw.compress_prompt(markdown)
                    if compressed and len(compressed) < len(markdown):
                        original_len = len(markdown)
                        compressed_len = len(compressed)
                        reduction = (1.0 - compressed_len / original_len) * 100
                        logger.debug(
                            f"[COLLAPSER] [SWARM]-004: compress_prompt "
                            f"{original_len} → {compressed_len} chars ({reduction:.1f}% reduction)"
                        )
                        markdown = compressed
                except Exception as compress_err:
                    logger.debug(f"[COLLAPSER] [SWARM]-004: compress_prompt failed: {compress_err}")
                    
            except Exception as e:
                logger.debug(f"[COLLAPSER] collapse_findings failed: {e}")
                markdown = self._fallback_collapse(clean_for_collapse, max_groups, max_chars_per_group)
        else:
            markdown = self._fallback_collapse(clean_for_collapse, max_groups, max_chars_per_group)

        # Build result
        result = {
            "markdown": markdown,
            "consistency_result": consistency_result,
            "clean_findings": consistency_result.clean if consistency_result else findings,
            "contradictory_findings": consistency_result.contradictory if consistency_result else [],
            "disputed_findings": consistency_result.disputed if consistency_result else [],
            "suspect_sources": consistency_result.suspect_sources if consistency_result else [],
            "batch_consistency_score": consistency_result.consistency_score if consistency_result else 1.0,
        }

        # Add consistency warnings to markdown
        if consistency_result and consistency_result.contradictions:
            warning = self._build_consistency_warning(consistency_result)
            result["markdown"] = warning + "\n\n" + markdown

        return result

    def _fallback_collapse(
        self,
        findings: list[dict[str, Any]],
        max_groups: int,
        max_chars: int,
    ) -> str:
        """Pure-Python fallback for finding collapse."""
        if not findings:
            return "## Pre-Collapsed IOC Tree\n\n*No findings to collapse.*\n"

        # Simple grouping by entity
        groups: dict[str, list[dict]] = {}
        for f in findings:
            entity = f.get("value") or f.get("ioc") or f.get("entity_value", "unknown")
            if entity not in groups:
                groups[entity] = []
            groups[entity].append(f)

        # Sort by source count
        sorted_groups = sorted(
            groups.items(),
            key=lambda x: len(x[1]),
            reverse=True,
        )[:max_groups]

        lines = ["## Pre-Collapsed IOC Tree", f"**{len(findings)} findings → {len(groups)} groups**\n"]

        for i, (entity, group) in enumerate(sorted_groups, 1):
            ioc_type = group[0].get("ioc_type", "unknown")
            lines.append(f"### Group {i} ({len(group)} sources)")
            lines.append(f"**Type:** {ioc_type}")
            lines.append(f"**Value:** `{entity}`")

            confs = [f.get("confidence", 0.5) for f in group]
            lines.append(f"**Confidence:** {min(confs):.2f}–{max(confs):.2f}")

            lines.append("**Sources:**")
            for f in group[:8]:
                source = f.get("source_type", "unknown")
                text = (f.get("text") or f.get("snippet") or "")[:80]
                lines.append(f"  - {source}: {text}")

            lines.append("")

        return "\n".join(lines)

    def _build_consistency_warning(self, consistency_result) -> str:
        """Build consistency warning markdown block."""
        lines = [
            "!!! warning \"Propositional Contradictions Detected\"",
            f"**{len(consistency_result.contradictions)} contradiction(s)** found in this batch.",
            f"**Batch consistency score:** `{consistency_result.consistency_score:.3f}`",
            "",
        ]

        for c in consistency_result.contradictions[:5]:  # Show top 5
            ctype = c.get("contradiction_type", "unknown")
            entity = c.get("entity", "")
            severity = c.get("severity", 0.0)
            lines.append(
                f"- **{ctype}**: `{entity}` (severity: {severity:.2f})"
            )

        if consistency_result.suspect_sources:
            lines.append("")
            lines.append("**Suspect Sources:**")
            for s in consistency_result.suspect_sources[:3]:
                source = s.get("source", "")
                entity = s.get("entity", "")
                lines.append(f"- Source `{source}` flagged for `{entity}`")

        return "\n".join(lines)


# Module-level singleton
_collapser_with_consistency: FindingCollapserWithConsistency | None = None


def get_collapser_with_consistency() -> FindingCollapserWithConsistency:
    """Get the singleton FindingCollapserWithConsistency instance."""
    global _collapser_with_consistency
    if _collapser_with_consistency is None:
        _collapser_with_consistency = FindingCollapserWithConsistency()
    return _collapser_with_consistency
