# Sprint F214Z: Export Formatter Class Hierarchy
# Sprint F350M-R: JSONFormatter moved to sprint_exporter.py to break circular import
# Sprint F232C: JSONFormatter extracted to export/_formatters.py
# Formatters module is now a re-export surface + utility functions.
"""
Export formatter class hierarchy.

Architecture:
  ExportFormatter (ABC)
    ├── JSONFormatter   # re-exported from export/_formatters.py
    └── (future: STIXFormatter, MarkdownFormatter)

Each formatter encapsulates its format's logic. The sprint_exporter module
acts as a thin dispatcher.

**CONSTRAINT: No circular imports.**
`sprint_exporter.py` must NEVER import from `formatters.py`.
`sprint_exporter.py` is the stable foundation; `formatters.py` imports from it.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from hledac.universal.project_types import ExportHandoff

import itertools
import logging

logger = logging.getLogger(__name__)

__all__ = [
    "ExportFormatter",
    "JSONFormatter",
    "render_investigation_packet_markdown",
]

# Sprint F232C: JSONFormatter extracted to export/_formatters.py
# Keep this module as a stable re-export surface for backward compatibility
from hledac.universal.export._formatters import JSONFormatter  # noqa: E402, F401
from _core import aclose


class ExportFormatter(ABC):
    """
    Abstract base class for export formatters.
    """

    @abstractmethod
    async def format(
        self,
        store: Any,
        handoff: ExportHandoff,
        sprint_id: str | None = None,
        enable_security_enrichment: bool = False,
        export_mode: str = "slim",
    ) -> dict:
        """
        Format and write sprint export artifact.

        Returns:
            dict with artifact paths and metadata (same as original export_sprint return)
        """
        ...  # pragma: no cover


def render_investigation_packet_markdown(packet: dict | None) -> str:
    """
    Sprint F232A: Render compact Investigation Packet markdown section.

    Deterministic. No LLM. No new report file type.
    Sections: Seed Context, Source Family Coverage, Corroboration, Gaps,
              Recommended Next Actions.

    Applied to existing export/formatters.py as Phase 3 integration point.
    No new markdown formatter file created.
    """
    if not packet:
        return ""

    lines: list[str] = []
    lines.append("## Investigation Packet")

    # ── Seed Context ───────────────────────────────────────────────────────
    sc = packet.get("seed_context") or {}
    lines.append("### Seed Context")
    available = sc.get("available", False)
    source = sc.get("source", "") or "unknown"
    lines.append(f"- **Available**: {available}")
    lines.append(f"- **Source**: {source}")
    domains = sc.get("domains", [])
    ips = sc.get("ips", [])
    urls = sc.get("urls", [])
    hashes = sc.get("hashes", [])
    cves = sc.get("cves", [])
    if domains:
        lines.append(f"- **Domains** ({len(domains)}): {', '.join(str(d) for d in domains[:10])}")
    if ips:
        lines.append(f"- **IPs** ({len(ips)}): {', '.join(str(ip) for ip in ips[:10])}")
    if urls:
        lines.append(f"- **URLs** ({len(urls)}): {', '.join(str(u) for u in urls[:5])}")
    if hashes:
        lines.append(f"- **Hashes** ({len(hashes)}): {', '.join(str(h) for h in hashes[:5])}")
    if cves:
        lines.append(f"- **CVEs** ({len(cves)}): {', '.join(str(c) for c in cves[:5])}")
    if not (domains or ips or urls or hashes or cves):
        lines.append("- _No seed context available_")
    lines.append("")

    # ── Source Family Coverage ─────────────────────────────────────────────
    sfs = packet.get("source_family_summary") or []
    lines.append("### Source Family Coverage")
    if sfs:
        for sf in sfs[:20]:
            fam = sf.get("family", "?")
            accepted = sf.get("accepted", 0)
            ts = sf.get("terminal_state", "") or "no_attempt"
            has_f = sf.get("has_findings", False)
            term_only = sf.get("terminal_only", False)
            status = "FINDINGS" if has_f else ("TERMINAL_ONLY" if term_only else "no_result")
            lines.append(f"- **{fam}**: {accepted} accepted, {ts} [{status}]")
    else:
        lines.append("- _No source family data_")
    lines.append("")

    # ── Corroboration ─────────────────────────────────────────────────────
    corr = packet.get("corroboration") or {}
    lines.append("### Corroboration")
    if corr:
        for ioc, score in itertools.islice(corr.items(), 20):
            lines.append(f"- {ioc}: {round(float(score), 4) if score is not None else 0.0}")
    else:
        lines.append("- _No corroboration scores_")
    lines.append("")

    # ── Gaps ──────────────────────────────────────────────────────────────
    gaps = packet.get("gaps") or []
    lines.append("### Gaps")
    if gaps:
        for gap in gaps[:20]:
            lines.append(f"- {gap}")
    else:
        lines.append("- _No significant gaps identified_")
    lines.append("")

    # ── Recommended Next Actions ─────────────────────────────────────────
    actions = packet.get("planner_actions") or []
    lines.append("### Recommended Next Actions")
    if actions:
        for act in actions[:10]:
            act_type = act.get("action", "?")
            target = act.get("target", "") or ""
            priority = act.get("priority", 0.0)
            lane = act.get("lane", "")
            reason = act.get("reason", "") or ""
            target_str = f" → {target}" if target else ""
            lines.append(f"- **{act_type}**{target_str} (p={round(priority, 3)}, lane={lane}) — {reason}")
    else:
        lines.append("- _No actions generated_")

    return "\n".join(lines)
