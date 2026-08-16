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
import time
from typing import TYPE_CHECKING
from _core import aclose
try:
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table
    from rich.text import Text
except Exception as _rich_missing:
    Live = None
if TYPE_CHECKING:
    from hledac.universal.runtime.scheduler_result import SprintSchedulerResult
_PHASE_COLORS = {'BOOT': 'dim', 'WARMUP': 'yellow', 'ACTIVE': 'green', 'DEGRADED': 'bright_yellow', 'WINDUP': 'cyan', 'EXPORT': 'blue', 'TEARDOWN': 'magenta', 'ABORTED': 'red'}

def _phase_style(phase: str) -> str:
    return _PHASE_COLORS.get(phase.upper(), 'white')

def _phase_emoji(phase: str) -> str:
    return {'BOOT': '⚙️', 'WARMUP': '⚡', 'ACTIVE': '🔨', 'DEGRADED': '⚠️', 'WINDUP': '⏹', 'EXPORT': '📤', 'TEARDOWN': '✅', 'ABORTED': '❌'}.get(phase.upper(), '❓')

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
    __slots__ = tuple(('_aborted', '_console', '_last_phase', '_live', '_start_time', 'duration_s', 'query', 'sprint_id'))

    def __init__(self, sprint_id: str, query: str, duration_s: float) -> None:
        self.sprint_id = sprint_id
        self.query = query
        self.duration_s = duration_s
        self._start_time = time.monotonic()
        self._console: Console = Console()
        self._live: Live | None = None
        self._last_phase = 'BOOT'
        self._aborted = False

    def start(self) -> None:
        """Start the live dashboard display."""
        if Live is None:
            return
        self._live = Live(self._build_table(), console=self._console, refresh_per_second=4, transient=False)
        self._live.start()
        self._start_time = time.monotonic()

    def update(self, result: SprintSchedulerResult, phase: str, elapsed_s: float) -> None:
        """
        Update dashboard with latest sprint state.

        Called after each cycle completes.
        """
        if self._live is None:
            return
        self._last_phase = phase.upper()
        self._live.update(self._build_table(result, elapsed_s))

    def finish(self, result: SprintSchedulerResult, elapsed_s: float) -> None:
        """
        Finalize dashboard — show final state and stop live display.
        """
        if self._live is not None:
            self._live.update(self._build_table(result, elapsed_s))
            self._live.stop()
            self._live = None

    def _build_progress_row(self, result: SprintSchedulerResult | None, elapsed_s: float) -> str | None:
        """Build the progress bar row."""
        if result is None:
            return None
        pct = min(1.0, elapsed_s / self.duration_s) if self.duration_s > 0 else 0.0
        bar_len = 50
        filled = int(bar_len * pct)
        bar = '█' * filled + '░' * (bar_len - filled)
        return f'[█]{bar}[█]  {pct * 100:.1f}%'

    def _build_findings_row(self, result: SprintSchedulerResult | None) -> str:
        """Build the findings summary row."""
        if result is None:
            return 'no findings yet'
        findings_parts: list[str] = []
        af = result.accepted_findings
        findings_parts.append(f'findings={af}')
        if result.public_accepted_findings is not None and result.public_accepted_findings > 0:
            findings_parts.append(f'public={result.public_accepted_findings}')
        if result.ct_log_accepted_findings is not None and result.ct_log_accepted_findings > 0:
            findings_parts.append(f'ct={result.ct_log_accepted_findings}')
        if result.multimodal_enriched_findings:
            findings_parts.append(f'vision={result.multimodal_enriched_findings}')
        if result.forensics_enriched_ct_findings:
            findings_parts.append(f'forensics={result.forensics_enriched_ct_findings}')
        return '  '.join(findings_parts) if findings_parts else 'no findings yet'

    def _build_cycles_row(self, result: SprintSchedulerResult | None) -> tuple[str, str, str] | tuple[str, None, None]:
        """Build the cycles info components."""
        if result is None:
            return '', '', ''
        cycles = f'cycles={result.cycles_started}/{result.cycles_completed}'
        dedup = result.duplicate_entry_hashes_skipped
        dedup_str = f'dedup={dedup}'
        sources_parts: list[str] = []
        if result.entries_per_source:
            for src, cnt in list(result.entries_per_source.items())[:3]:
                short_src = src[:30] if len(src) > 30 else src
                sources_parts.append(f'{short_src}={cnt}')
        sources_str = '  '.join(sources_parts) if sources_parts else ''
        return cycles, dedup_str, sources_str

    def _build_branch_row(self, result: SprintSchedulerResult | None) -> str | None:
        """Build the branch status row."""
        if result is None:
            return None
        branch_parts: list[str] = []
        if result.branch_timeout_count > 0:
            branch_parts.append(f'⏱️timeouts={result.branch_timeout_count}')
        if result.public_branch_timed_out:
            branch_parts.append('public_timeout=❌')
        if result.ct_branch_timed_out:
            branch_parts.append('ct_timeout=❌')
        if result.dominant_branch_blocker and result.dominant_branch_blocker != 'none':
            branch_parts.append(f'blocker={result.dominant_branch_blocker}')
        if result.public_error:
            short_err = result.public_error[:60]
            branch_parts.append(f'public_err={short_err}')
        if branch_parts:
            return '  '.join(branch_parts)
        return 'healthy'

    def _build_status_row(self, result: SprintSchedulerResult | None) -> str | None:
        """Build the status warnings row."""
        if result is None:
            return None
        if result.aborted:
            return f'ABORTED: {result.abort_reason or "unknown"}'
        elif result.stop_requested:
            return 'STOP REQUESTED  (stop_on_first_accepted)'
        elif result.feed_zero_yield_detected:
            return 'feed_zero_yield  (no signal in any feed)'
        return None

    def _build_governor_row(self) -> str | None:
        """Build the governor status row."""
        try:
            from hledac.universal._core.protocols import get_governor
            gov = get_governor()
            snap = gov.snapshot()
            gov_parts: list[str] = [f'uma={snap.uma_state}', f'fetch={snap.fetch_limit}', f'branches={snap.branch_concurrency}']
            if snap.model_loaded:
                gov_parts.append('model=LOADED')
            if snap.renderer_denied_count > 0:
                gov_parts.append(f'renderer_denied={snap.renderer_denied_count}')
            if snap.model_denied_count > 0:
                gov_parts.append(f'model_denied={snap.model_denied_count}')
            return '  '.join(gov_parts)
        except Exception:  # noqa: BLE001
            return None

    def _build_killchain_row(self, result: SprintSchedulerResult | None) -> int:
        """Build the kill-chain tags count."""
        if result is not None and result.kill_chain_tags_produced > 0:
            return result.kill_chain_tags_produced
        return 0

    def _build_table(self, result: SprintSchedulerResult | None=None, elapsed_s: float=0.0) -> Table:
        """Build the main dashboard table."""
        table = Table(title=None, show_header=False, box=None, padding=(0, 1), pad_edge=False)
        table.add_column(style='bold', width=60)

        # Header row
        phase = self._last_phase
        emoji = _phase_emoji(phase)
        style = _phase_style(phase)
        remaining = max(0.0, self.duration_s - elapsed_s)
        title_text = Text.assemble(
            (f' {emoji} [{phase}]', style),
            f'  │  {self.sprint_id}  │  {elapsed_s:.0f}s elapsed  │  {remaining:.0f}s left',
            'white'
    )
        table.add_row(title_text)

        # Progress row
        if result is None:
            table.add_row(f'[dim]Initializing sprint for query: {self.query}[/dim]')
        else:
            progress_row = self._build_progress_row(result, elapsed_s)
            if progress_row is not None:
                table.add_row(progress_row)

        # Findings summary
        findings_str = self._build_findings_row(result)
        table.add_row(Text.assemble(('findings: ', 'cyan'), findings_str))

        # Cycles and sources (only when result available)
        self._add_cycles_row(table, result)

        # Branch health and status (only when result available)
        self._add_status_rows(table, result)

        # Hits row
        if result is not None and result.total_pattern_hits > 0:
            table.add_row(Text.assemble(('hits: ', 'magenta'), str(result.total_pattern_hits)))

        # Governor state
        gov_str = self._build_governor_row()
        if gov_str:
            table.add_row(Text.assemble(('governor: ', 'cyan'), gov_str))

        # Kill-chain tags
        killchain_count = self._build_killchain_row(result)
        if killchain_count > 0:
            table.add_row(Text.assemble(('kill-chain: ', 'magenta'), str(killchain_count)))

        return table

    def _add_cycles_row(self, table: Table, result: SprintSchedulerResult | None) -> None:
        """Add cycles and sources row to table."""
        if result is None:
            table.add_row('[dim]Starting up...[/dim]')
            return
        cycles, dedup_str, sources_str = self._build_cycles_row(result)
        table.add_row(Text.assemble(('cycles: ', 'green'), cycles, ('  ', 'white'), (dedup_str, 'dim')))
        if sources_str:
            table.add_row(Text.assemble(('sources: ', 'yellow'), sources_str, style='dim'))

    def _add_status_rows(self, table: Table, result: SprintSchedulerResult | None) -> None:
        """Add branch health and status warning rows to table."""
        if result is None:
            return
        branch_str = self._build_branch_row(result)
        if branch_str == 'healthy':
            table.add_row(Text.assemble(('branch: ', 'green'), branch_str))
        elif branch_str:
            table.add_row(Text.assemble(('branch: ', 'red'), branch_str))

        status_str = self._build_status_row(result)
        if status_str:
            prefix = '[⚠] ' if 'ABORTED' in status_str else '[⛔] '
            color = 'red' if 'ABORTED' in status_str else 'yellow'
            table.add_row(Text.assemble((prefix, color), status_str))
