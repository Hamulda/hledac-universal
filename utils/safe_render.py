"""Safe text rendering for Markdown/HTML exports.

PEP 750 t-strings inspired seam between interpolated expressions and literal
template structure. Since t-strings in Python 3.14.4 produce inert Template
objects without a rendering API, this module provides explicit escaping helpers
that work in any Python version.

Scope: export/report rendering only.
Exclusions: STIX/JSON export, SQL/shell generation, core pipeline.
"""

from __future__ import annotations

__all__ = [
    "escape_markdown_text",
    "escape_html_text",
    "safe_markdown_link",
    "safe_code_fence",
]


# ---------------------------------------------------------------------------
# Markdown escaping
# ---------------------------------------------------------------------------

MARKDOWN_SPECIAL_CHARS = (
    ("\\", "\\\\"),   # backslash first
    ("`", "\\`"),     # inline code
    ("*", "\\*"),     # bold/italic
    ("_", "\\_"),     # italic
    ("[", "\\["),     # link text
    ("]", "\\]"),     # link close
    ("(", "\\("),     # link paren
    (")", "\\)"),     # link close paren
    ("<", "\\<"),     # html-like
    (">", "\\>"),     # html-like
    ("|", "\\|"),     # table
    ("\n", "\\n"),    # newlines
)


def escape_markdown_text(text: str) -> str:
    """Escape characters that break Markdown rendering.

    Escapes: \\ ` * _ [ ] ( ) < | \\n
    Preserves literal structure so user content cannot breakout of
    markdown elements (links, code spans, headings, tables).
    """
    if not text:
        return ""
    for char, escaped in MARKDOWN_SPECIAL_CHARS:
        text = text.replace(char, escaped)
    return text


# ---------------------------------------------------------------------------
# HTML escaping
# ---------------------------------------------------------------------------

def escape_html_text(text: str) -> str:
    """Escape characters that break HTML rendering.

    Uses html.escape with quote=True to cover all HTML special chars.
    """
    import html

    return html.escape(text, quote=True)


# ---------------------------------------------------------------------------
# Safe Markdown links
# ---------------------------------------------------------------------------

# Allowed URL schemes in markdown links
_ALLOWED_SCHEMES: frozenset[str] = frozenset({"http", "https", "ftp", "mailto"})
_BLOCKED_SCHEME_REPLACEMENT = "#blocked-scheme"


def safe_markdown_link(label: str, url: str) -> str:
    """Render a markdown link with scheme validation and label escaping.

    - javascript:, data:, file: and other schemes → #blocked-scheme
    - http, https, ftp, mailto → allowed
    - label is always escaped for markdown
    - url path parens are percent-encoded to prevent link-text injection

    Does NOT guard against malicious domains — only scheme-level.
    """
    # Validate scheme
    scheme = url.split("://", 1)[0].lower() if "://" in url else url.split(":", 1)[0].lower()
    if scheme not in _ALLOWED_SCHEMES:
        url = _BLOCKED_SCHEME_REPLACEMENT

    # Escape label for markdown
    escaped_label = escape_markdown_text(label)

    # Percent-encode parens in URL to prevent ] injection into link text
    # e.g. [label](http://example.com/path(含) → [label](http://example.com/path%28%E5%90%AB%29)
    safe_url = url.replace("(", "%28").replace(")", "%29")

    return f"[{escaped_label}]({safe_url})"


# ---------------------------------------------------------------------------
# Safe code fences
# ---------------------------------------------------------------------------

def safe_code_fence(text: str) -> str:
    """Escape text intended for a fenced code block.

    Escapes backslash and backtick only — sufficient to prevent
    fence breakout inside a triple-backtick code block.
    """
    if not text:
        return ""
    text = text.replace("\\", "\\\\")
    text = text.replace("`", "\\`")
    return text
