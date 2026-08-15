"""
WeasyPrint converts HTML to PDF — cross-platform, no extra browser binary.
Falls back to macos_webkit_renderer if weasyprint unavailable.
M1 8GB: weasyprint uses cairo/pango which are system libraries — acceptable RAM.

"""
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any
from _core import aclose
if TYPE_CHECKING:
    pass
__all__ = ['PDFRenderer', 'WEASYPRINT_AVAILABLE']
try:
    import weasyprint
    _WEASYPRINT_AVAILABLE = True
except ImportError:
    _WEASYPRINT_AVAILABLE = False
WEASYPRINT_AVAILABLE = _WEASYPRINT_AVAILABLE

class PDFRenderer:
    """
    Renders HTML content to PDF.

    Primary: WeasyPrint (pure Python, cross-platform)
    Fallback: uses macos_webkit_renderer if on Darwin
    """
    __slots__ = tuple(('_weasyprint_available',))

    def __init__(self) -> None:
        self._weasyprint_available = WEASYPRINT_AVAILABLE

    def render(self, html_content: str) -> bytes:
        """Render HTML string to PDF bytes."""
        if self._weasyprint_available:
            return self._render_weasyprint(html_content)
        return self._render_webkit_fallback(html_content)

    def render_to_file(self, html_content: str, path: Path | str) -> Path:
        """Render HTML to PDF file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if self._weasyprint_available:
            doc = weasyprint.HTML(string=html_content)
            doc.write_pdf(path)
            return path
        pdf_bytes = self._render_webkit_fallback(html_content)
        with open(path, 'wb') as fh:
            fh.write(pdf_bytes)
        return path

    def render_markdown_to_file(self, markdown: str, path: Path | str, *, html_renderer: Any=None) -> Path:
        """Convert markdown to HTML then to PDF."""
        if html_renderer is None:
            from hledac.universal.report.renderers.html_renderer import HTMLRenderer
            html_renderer = HTMLRenderer()
        html = html_renderer.render(markdown)
        return self.render_to_file(html, path)

    def _render_weasyprint(self, html_content: str) -> bytes:
        """Render using WeasyPrint."""
        try:
            doc = weasyprint.HTML(string=html_content)
            return doc.write_pdf()
        except Exception as e:
            raise RuntimeError(f'WeasyPrint PDF render failed: {e}') from e

    @staticmethod
    def _render_webkit_fallback(html_content: str) -> bytes:
        """Fallback via macos_webkit_renderer if on Darwin."""
        import sys
        if sys.platform != 'darwin':
            raise RuntimeError('PDF rendering unavailable: no backend (weasyprint not installed, webkit only available on macOS)')
        try:
            from hledac.universal.rendering.macos_webkit_renderer import fetch_with_macos_webkit
            import asyncio
            with tempfile.NamedTemporaryFile(suffix='.html', delete=False, mode='w', encoding='utf-8') as f:
                f.write(html_content)
                temp_path = f.name
            try:
                # P1-1: asyncio.run() replaced with run_sync_async() — M1 Metal safe.
                from hledac.universal.utils.sync_bridge import run_sync_async
                result = run_sync_async(fetch_with_macos_webkit(f'file://{temp_path}', timeout_s=30.0))
                if result and result.content:
                    return result.content
            finally:
                Path(temp_path).unlink(missing_ok=True)
            raise RuntimeError('macOS WebKit PDF fallback failed')
        except ImportError:
            raise RuntimeError('PDF rendering unavailable: weasyprint not installed and macos_webkit_renderer not available')