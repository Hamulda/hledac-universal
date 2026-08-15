"""
Converts Markdown to HTML.
Uses markdown-it-py (pure Python, no external binary).
Can use mistune as faster alternative.

Falls back to basic regex-based conversion.
"""
from typing import TYPE_CHECKING, Any
from core import aclose
if TYPE_CHECKING:
    from pathlib import Path
__all__ = ['HTMLRenderer']
try:
    import markdown_it
    _MARKDOWN_IT_AVAILABLE = True
except ImportError:
    _MARKDOWN_IT_AVAILABLE = False
try:
    import mistune
    _MISTUNE_AVAILABLE = True
except ImportError:
    _MISTUNE_AVAILABLE = False

class HTMLRenderer:
    """
    Renders Markdown content to HTML.

    Parser priority:
    1. mistune (faster, CommonMark compliant)
    2. markdown-it-py (full-featured)
    3. Basic regex fallback (no dependencies)

    Supports wrapping in full HTML document with optional CSS.
    """
    __slots__ = tuple(('_css', '_full_document', '_parser', '_parser_name'))

    def __init__(self, *, full_document: bool=True, css: str | None=None) -> None:
        self._full_document = full_document
        self._css = css or self._default_css()
        self._parser: Any = None
        self._parser_name: str = ''
        if _MISTUNE_AVAILABLE:
            self._parser = mistune.create_markdown()
            self._parser_name = 'mistune'
        elif _MARKDOWN_IT_AVAILABLE:
            self._parser = markdown_it.MarkdownIt()
            self._parser_name = 'markdown-it'
        else:
            self._parser_name = 'regex'

    @staticmethod
    def _default_css() -> str:
        """Minimal CSS for sprint report HTML output."""
        return "\n        :root {\n            --bg: #0d1117;\n            --bg-secondary: #161b22;\n            --border: #30363d;\n            --text: #e6edf3;\n            --text-muted: #8b949e;\n            --accent: #58a6ff;\n            --accent-secondary: #1f6feb;\n            --code-bg: #161b22;\n            --success: #3fb950;\n            --warning: #d29922;\n            --danger: #f85149;\n        }\n        body {\n            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;\n            background: var(--bg);\n            color: var(--text);\n            line-height: 1.6;\n            max-width: 900px;\n            margin: 0 auto;\n            padding: 2rem 1rem;\n        }\n        h1, h2, h3 { color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }\n        h1 { font-size: 1.8em; }\n        h2 { font-size: 1.4em; }\n        h3 { font-size: 1.2em; }\n        a { color: var(--accent); }\n        code { background: var(--code-bg); padding: 0.15em 0.4em; border-radius: 4px; font-size: 0.9em; }\n        pre { background: var(--code-bg); border: 1px solid var(--border); border-radius: 6px; padding: 1em; overflow-x: auto; }\n        pre code { background: none; padding: 0; }\n        table { border-collapse: collapse; width: 100%; margin: 1em 0; }\n        th, td { border: 1px solid var(--border); padding: 0.5em 0.75em; text-align: left; }\n        th { background: var(--bg-secondary); }\n        tr:nth-child(even) { background: var(--bg-secondary); }\n        blockquote { border-left: 3px solid var(--accent-secondary); margin: 1em 0; padding-left: 1em; color: var(--text-muted); }\n        hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }\n        .meta { color: var(--text-muted); font-size: 0.85em; }\n        "

    def render(self, markdown: str) -> str:
        """Convert markdown string to HTML."""
        if not markdown:
            return ''
        html_content = self._convert_markdown(markdown)
        if not self._full_document:
            return html_content
        return self._wrap_document(html_content)

    def _convert_markdown(self, markdown: str) -> str:
        """Convert markdown to HTML using available parser."""
        if self._parser_name == 'mistune':
            return self._parser(markdown)
        elif self._parser_name == 'markdown-it':
            return self._parser.render(markdown)
        else:
            return self._basic_convert(markdown)

    @staticmethod
    def _basic_convert(markdown: str) -> str:
        """Basic regex-based markdown to HTML conversion."""
        import re
        html = markdown
        html = re.sub('```(\\w*)\\n(.*?)\\n```', '<pre><code>\\2</code></pre>', html, flags=re.DOTALL)
        html = re.sub('`([^`]+)`', '<code>\\1</code>', html)
        for i in range(3, 0, -1):
            html = re.sub(f"^{'#' * i} (.+)$", f'<h{i}>\\1</h{i}>', html, flags=re.MULTILINE)
        html = re.sub('\\*\\*(.+?)\\*\\*', '<strong>\\1</strong>', html)
        html = re.sub('\\*(.+?)\\*', '<em>\\1</em>', html)
        html = re.sub('\\[([^\\]]+)\\]\\(([^)]+)\\)', '<a href="\\2">\\1</a>', html)
        html = re.sub('^- (.+)$', '<li>\\1</li>', html, flags=re.MULTILINE)
        html = re.sub('(<li>.*</li>\\n?)+', '<ul>\\g<0></ul>', html)
        html = re.sub('\\n\\n+', '\n\n', html)
        lines = html.split('\n\n')
        para_lines: list[str] = []
        block_tags = {'h1', 'h2', 'h3', 'h4', 'ul', 'ol', 'pre', 'blockquote'}
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith('<'):
                para_lines.append(stripped)
            else:
                para_lines.append(f'<p>{stripped}</p>')
        return '\n'.join(para_lines)

    def _wrap_document(self, html_content: str) -> str:
        """Wrap content in full HTML document."""
        return f'<!DOCTYPE html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n<meta name="viewport" content="width=device-width, initial-scale=1">\n<title>Sprint Report</title>\n<style>{self._css}</style>\n</head>\n<body>\n{html_content}\n</body>\n</html>'

    def render_to_file(self, markdown: str, path: Path | str, *, full_document: bool | None=None) -> Path:
        """Render markdown to HTML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        use_full = full_document if full_document is not None else self._full_document
        html = self.render(markdown) if use_full else self._convert_markdown(markdown)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(html)
        return path