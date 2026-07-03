# report/renderers/html_renderer.py
# Issue 12.1: HTML renderer — Markdown to HTML conversion
"""
Converts Markdown to HTML.
Uses markdown-it-py (pure Python, no external binary).
Can use mistune as faster alternative.
Falls back to basic regex-based conversion.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["HTMLRenderer"]

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

    def __init__(self, *, full_document: bool = True, css: str | None = None) -> None:
        self._full_document = full_document
        self._css = css or self._default_css()
        self._parser: Any = None
        self._parser_name: str = ""

        if _MISTUNE_AVAILABLE:
            self._parser = mistune.create_markdown()
            self._parser_name = "mistune"
        elif _MARKDOWN_IT_AVAILABLE:
            self._parser = markdown_it.MarkdownIt()
            self._parser_name = "markdown-it"
        else:
            self._parser_name = "regex"

    @staticmethod
    def _default_css() -> str:
        """Minimal CSS for sprint report HTML output."""
        return """
        :root {
            --bg: #0d1117;
            --bg-secondary: #161b22;
            --border: #30363d;
            --text: #e6edf3;
            --text-muted: #8b949e;
            --accent: #58a6ff;
            --accent-secondary: #1f6feb;
            --code-bg: #161b22;
            --success: #3fb950;
            --warning: #d29922;
            --danger: #f85149;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif;
            background: var(--bg);
            color: var(--text);
            line-height: 1.6;
            max-width: 900px;
            margin: 0 auto;
            padding: 2rem 1rem;
        }
        h1, h2, h3 { color: var(--text); border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
        h1 { font-size: 1.8em; }
        h2 { font-size: 1.4em; }
        h3 { font-size: 1.2em; }
        a { color: var(--accent); }
        code { background: var(--code-bg); padding: 0.15em 0.4em; border-radius: 4px; font-size: 0.9em; }
        pre { background: var(--code-bg); border: 1px solid var(--border); border-radius: 6px; padding: 1em; overflow-x: auto; }
        pre code { background: none; padding: 0; }
        table { border-collapse: collapse; width: 100%; margin: 1em 0; }
        th, td { border: 1px solid var(--border); padding: 0.5em 0.75em; text-align: left; }
        th { background: var(--bg-secondary); }
        tr:nth-child(even) { background: var(--bg-secondary); }
        blockquote { border-left: 3px solid var(--accent-secondary); margin: 1em 0; padding-left: 1em; color: var(--text-muted); }
        hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
        .meta { color: var(--text-muted); font-size: 0.85em; }
        """

    def render(self, markdown: str) -> str:
        """Convert markdown string to HTML."""
        if not markdown:
            return ""

        html_content = self._convert_markdown(markdown)

        if not self._full_document:
            return html_content

        return self._wrap_document(html_content)

    def _convert_markdown(self, markdown: str) -> str:
        """Convert markdown to HTML using available parser."""
        if self._parser_name == "mistune":
            return self._parser(markdown)
        elif self._parser_name == "markdown-it":
            return self._parser.render(markdown)
        else:
            return self._basic_convert(markdown)

    @staticmethod
    def _basic_convert(markdown: str) -> str:
        """Basic regex-based markdown to HTML conversion."""
        import re

        html = markdown
        # Code blocks
        html = re.sub(r"```(\w*)\n(.*?)\n```", r"<pre><code>\2</code></pre>", html, flags=re.DOTALL)
        # Inline code
        html = re.sub(r"`([^`]+)`", r"<code>\1</code>", html)
        # Headers
        for i in range(3, 0, -1):
            html = re.sub(rf"^{'#' * i} (.+)$", rf"<h{i}>\1</h{i}>", html, flags=re.MULTILINE)
        # Bold
        html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
        # Italic
        html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)
        # Links
        html = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', html)
        # Lists
        html = re.sub(r"^- (.+)$", r"<li>\1</li>", html, flags=re.MULTILINE)
        html = re.sub(r"(<li>.*</li>\n?)+", r"<ul>\g<0></ul>", html)
        # Paragraphs
        html = re.sub(r"\n\n+", "\n\n", html)
        lines = html.split("\n\n")
        para_lines: list[str] = []
        block_tags = {"h1", "h2", "h3", "h4", "ul", "ol", "pre", "blockquote"}
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("<"):
                para_lines.append(stripped)
            else:
                para_lines.append(f"<p>{stripped}</p>")
        return "\n".join(para_lines)

    def _wrap_document(self, html_content: str) -> str:
        """Wrap content in full HTML document."""
        return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sprint Report</title>
<style>{self._css}</style>
</head>
<body>
{html_content}
</body>
</html>"""

    def render_to_file(
        self, markdown: str, path: Path | str, *, full_document: bool | None = None
    ) -> Path:
        """Render markdown to HTML file."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)

        use_full = full_document if full_document is not None else self._full_document
        html = self.render(markdown) if use_full else self._convert_markdown(markdown)

        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)

        return path
