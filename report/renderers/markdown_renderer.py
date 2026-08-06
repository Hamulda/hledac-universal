"""
Jinja2 for Markdown replaces string-concatenation templates.
Templates are loaded from report/templates/ directory.
Supports streaming write for large reports.

"""
from typing import TYPE_CHECKING, Any
from pathlib import Path
if TYPE_CHECKING:
    pass
try:
    import jinja2
    _JINJA2_AVAILABLE = True
except ImportError:
    _JINJA2_AVAILABLE = False
__all__ = ['MarkdownRenderer', 'JINJA2_AVAILABLE']
JINJA2_AVAILABLE = _JINJA2_AVAILABLE

class MarkdownRenderer:
    """
    Renders reports to Markdown using Jinja2 templates.

    Template search order:
    1. Explicit template_path parameter
    2. report/templates/{template_name}.j2
    3. Built-in fallback templates (string-based)

    Falls back to string formatting if jinja2 unavailable.
    """
    __slots__ = tuple(('_env', '_template_dir'))

    def __init__(self, template_dir: Path | str | None=None) -> None:
        self._template_dir = Path(template_dir) if template_dir else self._default_template_dir()
        self._env: jinja2.Environment | None = None
        if _JINJA2_AVAILABLE and self._template_dir.exists():
            self._env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(self._template_dir)), autoescape=jinja2.select_autoescape(['html', 'xml']), keep_trailing_newline=True)

    @staticmethod
    def _default_template_dir() -> Path:
        """Get default template directory."""
        from hledac.universal.report import __file__ as report_init
        return Path(report_init).parent / 'templates'

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        """Render template with context dict."""
        if not _JINJA2_AVAILABLE:
            return self._render_fallback(template_name, context)
        if self._env is None:
            return self._render_fallback(template_name, context)
        try:
            template = self._env.get_template(f'{template_name}.j2')
            return template.render(**context)
        except jinja2.TemplateNotFound:
            return self._render_fallback(template_name, context)

    def render_to_file(self, template_name: str, context: dict[str, Any], path: Path | str) -> Path:
        """Render to file with streaming write."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        content = self.render(template_name, context)
        with open(path, 'w', encoding='utf-8') as fh:
            chunk_size = 64 * 1024
            for i in range(0, len(content), chunk_size):
                fh.write(content[i:i + chunk_size])
        return path

    def _render_fallback(self, template_name: str, context: dict[str, Any]) -> str:
        """Fallback string-based rendering without jinja2."""
        lines: list[str] = []
        sprint_id = context.get('sprint_id', 'unknown')
        generated = context.get('generated', '')
        summary = context.get('summary', '_No summary_')
        metrics = context.get('metrics', {})
        findings = context.get('findings', [])
        scorecard = context.get('scorecard', {})
        lines.append(f'# Sprint Report — {sprint_id}')
        lines.append(f'Generated: {generated}')
        lines.append('')
        lines.append('---')
        lines.append('')
        lines.append('## Executive Summary')
        lines.append(summary)
        lines.append('')
        if metrics:
            lines.append('## Research Metrics')
            lines.append('')
            fpm = metrics.get('findings_per_minute', 0.0)
            ioc_d = metrics.get('ioc_density', 0.0)
            novel = metrics.get('semantic_novelty', 0.0)
            lines.append(f'- **Findings/min:** {fpm:.2f}')
            lines.append(f'- **IOC density:** {ioc_d:.3f}')
            lines.append(f'- **Semantic novelty:** {novel:.1%}')
            lines.append('')
        if findings:
            lines.append('## Top Findings')
            lines.append('')
            for i, f in enumerate(findings[:10], 1):
                lines.append(f'{i}. {f}')
            lines.append('')
        src_yield = scorecard.get('source_yield_json', {})
        if isinstance(src_yield, str):
            import orjson
            try:
                src_yield = orjson.loads(src_yield)
            except Exception:
                src_yield = {}
        if src_yield:
            lines.append('## Source Leaderboard')
            lines.append('')
            for src, cnt in sorted(src_yield.items(), key=lambda x: x[1], reverse=True)[:10]:
                lines.append(f'- `{src}`: {cnt}')
            lines.append('')
        return '\n'.join(lines)