

# renderers/ — Output format renderers for report/ pipeline
from hledac.universal.report.renderers.json_renderer import JSONRenderer
from hledac.universal.report.renderers.markdown_renderer import MarkdownRenderer
from hledac.universal.report.renderers.html_renderer import HTMLRenderer
from hledac.universal.report.renderers.svg_renderer import SVGRenderer
from hledac.universal.report.renderers.pdf_renderer import PDFRenderer
from _core import aclose

__all__ = [
    "JSONRenderer",
    "MarkdownRenderer",
    "HTMLRenderer",
    "SVGRenderer",
    "PDFRenderer",
]
