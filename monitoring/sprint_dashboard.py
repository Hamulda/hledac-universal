"""
SprintDashboard — rich terminal dashboard for live sprint monitoring.

Usage:
    dashboard = SprintDashboard(sprint_id, query, duration_s)
    dashboard.start()
    # after each cycle:
    dashboard.update(result, phase, elapsed_s)
    # on completion:
    dashboard.finish(result, elapsed_s)

The dashboard survives branch timeout and early windup — it renders the
final SprintSchedulerResult regardless of how the sprint exited.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Optional

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, TimeRemainingColumn
    from rich.table import Table
    from rich.text import Text
except Exception as _rich_missing:  # pragma: no cover
    Live = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from hledac.universal.runtime.sprint_scheduler import SprintSchedulerResult


# ── Phase styling ─────────────────────────────────────────────────────────────

_PHASE_COLORS = {
    "BOOT": "dim",
    "WARMUP": "yellow",
    "ACTIVE": "green",
    "WINDUP": "cyan",
    "EXPORT": "blue",
    "TEARDOWN": "magenta",
    "ABORTED": "red",
}


def _phase_style(phase: str) -> str:
    return _PHASE_COLORS.get(phase.upper(), "white")


def _phase_emoji(phase: str) -> str:
    return {
        "BOOT": "⚙️",      # gear
        "WARMUP": "⚡",           # lightning
        "ACTIVE": "🔨",      # hammer
        "WINDUP": "⏹",           # stop
        "EXPORT": "📤",       # outbox
        "TEARDOWN": "✅",          # check
        "ABORTED": "❌",           # cross
    }.get(phase.upper(), "❓")      # question


# ── SprintDashboard ───────────────────────────────────────────────────────────

class SprintDashboard:
    """
    Rich terminal dashboard for live sprint monitoring.

    Shows:
        - Phase indicator with elapsed / remaining time
        - Findings counter (accepted, public, CT log)
        - Cycle progress bar
        - Per-source entry / hit counts
        - Branch status (timeouts, blockers)
        - Abort / windup reason if applicable
    """

    def __init__(
        self,
        sprint_id: str,
        query: str,
        duration_s: float,
    ) -> None:
        self.sprint_id = sprint_id
        self.query = query
        self.duration_s = duration_s
        self._start_time = time.monotonic()
        self._console: "Console" = Console()
        self._live: Optional["Live"] = None
        self._last_phase = "BOOT"
        self._aborted = False

    # ── Public API ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the live dashboard display."""
        if Live is None:
            return
        self._live = Live(
            self._build_table(),
            console=self._console,
            refresh_per_second=4,
            transient=False,
        )
        self._live.start()
        self._start_time = time.monotonic()

    def update(
        self,
        result: "SprintSchedulerResult",
        phase: str,
        elapsed_s: float,
    ) -> None:
        """
        Update dashboard with latest sprint state.

        Called after each cycle completes.
        """
        if self._live is None:
            return
        self._last_phase = phase.upper()
        self._live.update(self._build_table(result, elapsed_s))

    def finish(
        self,
        result: "SprintSchedulerResult",
        elapsed_s: float,
    ) -> None:
        """
        Finalize dashboard — show final state and stop live display.
        """
        if self._live is not None:
            self._live.update(self._build_table(result, elapsed_s))
            self._live.stop()
            self._live = None

    # ── Table builder ───────────────────────────────────────────────────────

    def _build_table(
        self,
        result: Optional["SprintSchedulerResult"] = None,
        elapsed_s: float = 0.0,
    ) -> "Table":
        """Build the main dashboard table."""
        table = Table(
            title=None,
            show_header=False,
            box=None,
            padding=(0, 1),
            pad_edge=False,
        )
        table.add_column(style="bold", width=60)

        # ── Row 1: Title bar ──────────────────────────────────────────────
        phase = self._last_phase
        emoji = _phase_emoji(phase)
        style = _phase_style(phase)
        remaining = max(0.0, self.duration_s - elapsed_s)
        title_text = Text.assemble(
            (f" {emoji} [{phase}]", style),
            f"  │  {self.sprint_id}  │  {elapsed_s:.0f}s elapsed  │  {remaining:.0f}s left", "white",
        )
        table.add_row(title_text)

        # ── Row 2: Time progress bar ────────────────────────────────────────
        if result is None:
            table.add_row(f"[dim]Initializing sprint for query: {self.query}[/dim]")
        else:
            pct = min(1.0, elapsed_s / self.duration_s) if self.duration_s > 0 else 0.0
            bar_len = 50
            filled = int(bar_len * pct)
            bar = "█" * filled + "░" * (bar_len - filled)
            table.add_row(f"[█]{bar}[█]  {pct*100:.1f}%")

        # ── Row 3: Findings summary ─────────────────────────────────────────
        findings_parts: list[str] = []
        if result is not None:
            af = result.accepted_findings
            findings_parts.append(f"findings={af}")
            if result.public_accepted_findings is not None and result.public_accepted_findings > 0:
                findings_parts.append(f"public={result.public_accepted_findings}")
            if result.ct_log_accepted_findings is not None and result.ct_log_accepted_findings > 0:
                findings_parts.append(f"ct={result.ct_log_accepted_findings}")
            if result.multimodal_enriched_findings:
                findings_parts.append(f"vision={result.multimodal_enriched_findings}")
            if result.forensics_enriched_ct_findings:
                findings_parts.append(f"forensics={result.forensics_enriched_ct_findings}")
        findings_str = "  ".join(findings_parts) if findings_parts else "no findings yet"
        table.add_row(Text.assemble(("findings: ", "cyan"), findings_str))

        # ── Row 4: Cycle + source telemetry ────────────────────────────────
        if result is not None:
            cycles = f"cycles={result.cycles_started}/{result.cycles_completed}"
            dedup = result.duplicate_entry_hashes_skipped
            dedup_str = f"dedup={dedup}"
            sources_parts: list[str] = []
            if result.entries_per_source:
                for src, cnt in list(result.entries_per_source.items())[:3]:
                    short_src = src[:30] if len(src) > 30 else src
                    sources_parts.append(f"{short_src}={cnt}")
            sources_str = "  ".join(sources_parts) if sources_parts else ""
            table.add_row(Text.assemble(("cycles: ", "green"), cycles, ("  ", "white"), (dedup_str, "dim")))
            if sources_str:
                table.add_row(Text.assemble(("sources: ", "yellow"), sources_str, style="dim"))
        else:
            table.add_row("[dim]Starting up...[/dim]")

        # ── Row 5: Branch / blocker status ─────────────────────────────────
        if result is not None:
            branch_parts: list[str] = []
            if result.branch_timeout_count > 0:
                branch_parts.append(f"⏱️timeouts={result.branch_timeout_count}")
            if result.public_branch_timed_out:
                branch_parts.append("public_timeout=❌")
            if result.ct_branch_timed_out:
                branch_parts.append("ct_timeout=❌")
            if result.dominant_branch_blocker and result.dominant_branch_blocker != "none":
                branch_parts.append(f"blocker={result.dominant_branch_blocker}")
            if result.public_error:
                short_err = result.public_error[:60]
                branch_parts.append(f"public_err={short_err}")
            if branch_parts:
                table.add_row(Text.assemble(("branch: ", "red"), "  ".join(branch_parts)))
            else:
                table.add_row(Text.assemble(("branch: ", "green"), "healthy"))

        # ── Row 6: Abort / windup reason ────────────────────────────────────
        if result is not None:
            if result.aborted:
                table.add_row(Text.assemble(("[⚠] ABORTED: ", "red"), result.abort_reason or "unknown"))
            elif result.stop_requested:
                table.add_row(Text.assemble(("[⛔] STOP REQUESTED", "yellow"), "  (stop_on_first_accepted)"))
            elif result.feed_zero_yield_detected:
                table.add_row(Text.assemble(("[⚠] feed_zero_yield", "yellow"), "  (no signal in any feed)"))

        # ── Row 7: Pattern hits ─────────────────────────────────────────────
        if result is not None and result.total_pattern_hits > 0:
            table.add_row(Text.assemble(("hits: ", "magenta"), str(result.total_pattern_hits)))

        # ── Row 7: Governor state (F202J) ─────────────────────────────────────
        try:
            from hledac.universal.runtime.resource_governor import get_governor
            gov = get_governor()
            snap = gov.snapshot()
            gov_parts: list[str] = [
                f"uma={snap.uma_state}",
                f"fetch={snap.fetch_limit}",
                f"branches={snap.branch_concurrency}",
            ]
            if snap.model_loaded:
                gov_parts.append("model=LOADED")
            if snap.renderer_denied_count > 0:
                gov_parts.append(f"renderer_denied={snap.renderer_denied_count}")
            if snap.model_denied_count > 0:
                gov_parts.append(f"model_denied={snap.model_denied_count}")
            table.add_row(Text.assemble(("governor: ", "cyan"), "  ".join(gov_parts)))
        except Exception:
            pass  # Governor state is optional dashboard info

        return table
