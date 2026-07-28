from __future__ import annotations
"""
Canonical entry point for the reporting pipeline.
Coordinates all renderers and provides streaming write for M1 8GB disk bottleneck.
"""
from dataclasses import dataclass, field
import msgspec
from typing import TYPE_CHECKING, Any
if TYPE_CHECKING:
    from pathlib import Path
__all__ = ['ReportEngine', 'get_report_engine', 'ReportOutput']

class ReportOutput(msgspec.Struct):
    """Result of a render operation."""
    path: Path | None = None
    content: str | bytes | None = None
    format: str = ''
    success: bool = False
    error: str | None = None

class ReportEngine(msgspec.Struct, frozen=True):
    """
    Unified reporting engine — emits {json, md, html, svg, pdf}.

    Usage:
        engine = get_report_engine()
        engine.render(report_data, formats=["json", "md", "html"], output_dir="~/reports")
    """
    json_renderer: Any = field(default=None)
    md_renderer: Any = field(default=None)
    html_renderer: Any = field(default=None)
    svg_renderer: Any = field(default=None)
    pdf_renderer: Any = field(default=None)

    def __post_init__(self) -> None:
        if self.json_renderer is None:
            from hledac.universal.report.renderers.json_renderer import JSONRenderer
            self.json_renderer = JSONRenderer()
        if self.md_renderer is None:
            from hledac.universal.report.renderers.markdown_renderer import MarkdownRenderer
            self.md_renderer = MarkdownRenderer()
        if self.html_renderer is None:
            from hledac.universal.report.renderers.html_renderer import HTMLRenderer
            self.html_renderer = HTMLRenderer()
        if self.svg_renderer is None:
            from hledac.universal.report.renderers.svg_renderer import SVGRenderer
            self.svg_renderer = SVGRenderer()
        if self.pdf_renderer is None:
            from hledac.universal.report.renderers.pdf_renderer import PDFRenderer
            self.pdf_renderer = PDFRenderer()

    def render(self, report_data: dict[str, Any], *, formats: list[str] | None=None, output_dir: Path | str | None=None, sprint_id: str='unknown', template: str='sprint_report') -> dict[str, ReportOutput]:
        """
        Render report to multiple formats.

        Args:
            report_data: Report dict with keys: summary, findings, scorecard, etc.
            formats: List of formats to render. Defaults to ["json", "md"].
            output_dir: Output directory for files. Defaults to ~/hledac/reports/.
            sprint_id: Sprint identifier for filenames.
            template: Template name for markdown renderer.
        """
        if formats is None:
            formats = ['json', 'md']
        if output_dir is None:
            from pathlib import Path
            output_dir = Path.home() / '.hledac' / 'reports'
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, ReportOutput] = {}
        for fmt in formats:
            try:
                if fmt == 'json':
                    results['json'] = self._render_json(report_data, output_dir, sprint_id)
                elif fmt == 'md':
                    results['md'] = self._render_md(report_data, output_dir, sprint_id, template)
                elif fmt == 'html':
                    results['html'] = self._render_html(report_data, output_dir, sprint_id)
                elif fmt == 'svg':
                    results['svg'] = self._render_svg(report_data, output_dir, sprint_id)
                elif fmt == 'pdf':
                    results['pdf'] = self._render_pdf(report_data, output_dir, sprint_id)
                else:
                    results[fmt] = ReportOutput(success=False, error=f'Unknown format: {fmt}')
            except Exception as e:
                results[fmt] = ReportOutput(success=False, error=str(e))
        return results

    def _render_json(self, data: dict[str, Any], output_dir: Path, sprint_id: str) -> ReportOutput:
        """Render to JSON."""
        path = output_dir / f'{sprint_id}.json'
        self.json_renderer.render_to_file(data, path)
        return ReportOutput(path=path, format='json', success=True)

    def _render_md(self, data: dict[str, Any], output_dir: Path, sprint_id: str, template: str) -> ReportOutput:
        """Render to Markdown."""
        path = output_dir / f'{sprint_id}.md'
        import time
        context = {'sprint_id': sprint_id, 'generated': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), 'summary': data.get('summary', '_No summary_'), 'metrics': data.get('metrics', {}), 'findings': data.get('findings', []), 'scorecard': data.get('scorecard', {})}
        content = self.md_renderer.render(template, context)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(content)
        return ReportOutput(path=path, format='md', success=True)

    def _render_html(self, data: dict[str, Any], output_dir: Path, sprint_id: str) -> ReportOutput:
        """Render to HTML."""
        path = output_dir / f'{sprint_id}.html'
        import time
        context = {'sprint_id': sprint_id, 'generated': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), 'summary': data.get('summary', '_No summary_'), 'metrics': data.get('metrics', {}), 'findings': data.get('findings', []), 'scorecard': data.get('scorecard', {})}
        md_content = self.md_renderer.render('sprint_report', context)
        html_content = self.html_renderer.render(md_content)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(html_content)
        return ReportOutput(path=path, format='html', success=True)

    def _render_svg(self, data: dict[str, Any], output_dir: Path, sprint_id: str) -> ReportOutput:
        """Render graph to SVG."""
        path = output_dir / f'{sprint_id}_graph.svg'
        graph_data = data.get('graph', {})
        svg_content = self.svg_renderer.render(graph_data)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(svg_content)
        return ReportOutput(path=path, format='svg', success=True)

    def _render_pdf(self, data: dict[str, Any], output_dir: Path, sprint_id: str) -> ReportOutput:
        """Render to PDF."""
        path = output_dir / f'{sprint_id}.pdf'
        import time
        context = {'sprint_id': sprint_id, 'generated': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), 'summary': data.get('summary', '_No summary_'), 'metrics': data.get('metrics', {}), 'findings': data.get('findings', []), 'scorecard': data.get('scorecard', {})}
        md_content = self.md_renderer.render('sprint_report', context)
        html_content = self.html_renderer.render(md_content)
        self.pdf_renderer.render_to_file(html_content, path)
        return ReportOutput(path=path, format='pdf', success=True)

    def render_streaming(self, report_data: dict[str, Any], *, formats: list[str] | None=None, output_dir: Path | str | None=None, sprint_id: str='unknown') -> dict[str, ReportOutput]:
        """
        Streaming render for very large reports — M1 8GB disk bottleneck safe.
        Writes in chunks to avoid memory spikes.
        """
        if formats is None:
            formats = ['json', 'md', 'html']
        if output_dir is None:
            from pathlib import Path
            output_dir = Path.home() / '.hledac' / 'reports'
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        results: dict[str, ReportOutput] = {}
        for fmt in formats:
            try:
                if fmt == 'json':
                    path = output_dir / f'{sprint_id}.json'
                    self.json_renderer.render_to_file(report_data, path)
                    results['json'] = ReportOutput(path=path, format='json', success=True)
                elif fmt == 'md':
                    path = output_dir / f'{sprint_id}.md'
                    self._stream_markdown(report_data, path, sprint_id)
                    results['md'] = ReportOutput(path=path, format='md', success=True)
                elif fmt == 'html':
                    path = output_dir / f'{sprint_id}.html'
                    self._stream_html(report_data, path, sprint_id)
                    results['html'] = ReportOutput(path=path, format='html', success=True)
            except Exception as e:
                results[fmt] = ReportOutput(success=False, error=str(e))
        return results

    def _stream_markdown(self, data: dict[str, Any], path: Path, sprint_id: str) -> None:
        """Stream markdown to file in chunks."""
        import time
        context = {'sprint_id': sprint_id, 'generated': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), 'summary': data.get('summary', '_No summary_'), 'metrics': data.get('metrics', {}), 'findings': data.get('findings', []), 'scorecard': data.get('scorecard', {})}
        content = self.md_renderer.render('sprint_report', context)
        chunk_size = 64 * 1024
        with open(path, 'w', encoding='utf-8') as fh:
            for i in range(0, len(content), chunk_size):
                fh.write(content[i:i + chunk_size])

    def _stream_html(self, data: dict[str, Any], path: Path, sprint_id: str) -> None:
        """Stream HTML to file in chunks."""
        import time
        context = {'sprint_id': sprint_id, 'generated': time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime()), 'summary': data.get('summary', '_No summary_'), 'metrics': data.get('metrics', {}), 'findings': data.get('findings', []), 'scorecard': data.get('scorecard', {})}
        md_content = self.md_renderer.render('sprint_report', context)
        html_content = self.html_renderer.render(md_content)
        chunk_size = 64 * 1024
        with open(path, 'w', encoding='utf-8') as fh:
            for i in range(0, len(html_content), chunk_size):
                fh.write(html_content[i:i + chunk_size])
_engine: ReportEngine | None = None

def get_report_engine() -> ReportEngine:
    """Get singleton ReportEngine instance."""
    global _engine
    if _engine is None:
        _engine = ReportEngine()
    return _engine