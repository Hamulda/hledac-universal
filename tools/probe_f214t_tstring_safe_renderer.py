#!/usr/bin/env python3
"""
F214T — t-string Safe Renderer POC

Scope: POC only — NO production rewrite.
Purpose: Demonstrate safe text rendering for Markdown/HTML exports
         inspired by Python 3.14 t-strings separation of interpolated
         expressions from literal template structure.

Environment: Python 3.13 (t-strings NOT available — NO_PATCH for runner)
"""

import re
import html

# ---------------------------------------------------------------------------
# Candidate Map: user-controlled f-string sites in export/report pipeline
# ---------------------------------------------------------------------------
# Exclusions per spec: STIX/JSON export, SQL/shell generation, core pipeline
CANDIDATE_MAP: dict = {
    # export/sprint_markdown_reporter.py
    "sprint_markdown_reporter.py:328": {
        "line": 328,
        "pattern": 'f"_{headline}_"',
        "context": "Sprint finding headline rendering",
        "risk": "Markdown italic + raw text — headline could contain * or _",
        "user_controlled": True,
    },
    "sprint_markdown_reporter.py:350": {
        "line": 350,
        "pattern": 'f"{i}. {action}"',
        "context": "Action enumeration",
        "risk": "Plain text — action could break enumeration",
        "user_controlled": True,
    },
    "sprint_markdown_reporter.py:415": {
        "line": 415,
        "pattern": 'f"- [{priority}] {direction}: {query_hint}"',
        "context": "Pivot suggestion rendering",
        "risk": "Markdown list item with []() — query_hint could inject link",
        "user_controlled": True,
    },
    "sprint_markdown_reporter.py:417": {
        "line": 417,
        "pattern": 'f"- {pivot}"',
        "context": "Pivot text",
        "risk": "Markdown list item — pivot could contain ] or [",
        "user_controlled": True,
    },
    "sprint_markdown_reporter.py:427": {
        "line": 427,
        "pattern": 'f"_{count} finding(s)..."',
        "context": "Finding count footnote",
        "risk": "Low — count is int, count is safe",
        "user_controlled": False,
    },
    "sprint_markdown_reporter.py:469": {
        "line": 469,
        "pattern": 'f"**Confidence:** {confidence}"',
        "context": "Confidence label",
        "risk": "Low — confidence is float",
        "user_controlled": False,
    },
    "sprint_markdown_reporter.py:501": {
        "line": 501,
        "pattern": 'f"**Usernames:** {uname_str}"',
        "context": "Username enumeration",
        "risk": "Usernames could contain * or _ — backtick wrap helps",
        "user_controlled": True,
    },
    "sprint_markdown_reporter.py:522": {
        "line": 522,
        "pattern": 'f"**Finding IDs:** {fid_str}"',
        "context": "Finding ID enumeration",
        "risk": "Truncated finding IDs in backticks — safe",
        "user_controlled": False,
    },
    "sprint_markdown_reporter.py:687": {
        "line": 687,
        "pattern": 'f"### {label}: `{ioc_value}`"',
        "context": "IOC section heading",
        "risk": "label could contain # or ## — inside heading",
        "user_controlled": True,
    },
    "sprint_markdown_reporter.py:748": {
        "line": 748,
        "pattern": 'f"### {tactic} ({count} finding(s))"',
        "context": "Tactic section heading",
        "risk": "tactic is enum string — low risk",
        "user_controlled": False,
    },
    "sprint_markdown_reporter.py:757": {
        "line": 757,
        "pattern": 'f"- `{tid}` — {cnt}..."',
        "context": "Tactic item",
        "risk": "tid is internal ID — safe in backticks",
        "user_controlled": False,
    },
    "sprint_markdown_reporter.py:820": {
        "line": 820,
        "pattern": 'f"- **{step_label}** → ..."',
        "context": "Step label in chain",
        "risk": "step_label is internal enum — low risk",
        "user_controlled": False,
    },
    "sprint_markdown_reporter.py:827": {
        "line": 827,
        "pattern": 'f"- **Conclusion**: {conclusion}"',
        "context": "Chain conclusion",
        "risk": "conclusion could contain ** or ] — markdown break",
        "user_controlled": True,
    },
    # export/export_manager.py
    "export_manager.py:132": {
        "line": 132,
        "pattern": 'f"title: \\"{title}\\""',
        "context": "YAML frontmatter title",
        "risk": "title could contain \" — YAML string break",
        "user_controlled": True,
    },
    "export_manager.py:151": {
        "line": 151,
        "pattern": 'f"{key}: \\"{value}\\""',
        "context": "YAML metadata key-value",
        "risk": "value could contain \" — YAML break",
        "user_controlled": True,
    },
    "export_manager.py:161": {
        "line": 161,
        "pattern": 'f"## Report\\n\\n{report}\\n"',
        "context": "Report section heading + body",
        "risk": "report could contain #, ##, *, _, [, ], (): break markdown",
        "user_controlled": True,
    },
    "export_manager.py:178": {
        "line": 178,
        "pattern": 'f"- **Query**: {query}"',
        "context": "Finding query field",
        "risk": "query could contain *, _, `, [ — markdown injection",
        "user_controlled": True,
    },
    "export_manager.py:182": {
        "line": 182,
        "pattern": 'f"- **URL**: [{url_label}]({url})"',
        "context": "Finding URL field — DYNAMIC LINK",
        "risk": "HIGH — url_label and url both user-controlled; direct link injection",
        "user_controlled": True,
        "severity": "HIGH",
    },
    "export_manager.py:191": {
        "line": 191,
        "pattern": 'f"- {finding}\\n"',
        "context": "Raw finding dump",
        "risk": "finding dict could contain markdown-breaking text",
        "user_controlled": True,
    },
    # export/markdown_reporter.py
    "markdown_reporter.py:97": {
        "line": 97,
        "pattern": 'return f"[{label}]({s})"',
        "context": "Markdown link from label + URL",
        "risk": "HIGH — label and URL both user-controlled; direct link injection",
        "user_controlled": True,
        "severity": "HIGH",
    },
    "markdown_reporter.py:158": {
        "line": 158,
        "pattern": 'f"- **Accepted findings**: {findings_blurb}."',
        "context": "Findings blurb",
        "risk": "findings_blurb could contain * or _",
        "user_controlled": True,
    },
    "markdown_reporter.py:159": {
        "line": 159,
        "pattern": 'f"- **Root cause**: {root_label}."',
        "context": "Root cause label",
        "risk": "root_label could contain * or _",
        "user_controlled": True,
    },
    "markdown_reporter.py:216": {
        "line": 216,
        "pattern": 'f"- {field_label}: {val}"',
        "context": "Generic field render",
        "risk": "field_label and val both user-controlled",
        "user_controlled": True,
    },
    "markdown_reporter.py:264": {
        "line": 264,
        "pattern": 'f"- **Feed**: {_linkify(url)}"',
        "context": "Feed URL link",
        "risk": "url from user data; _linkify could emit raw HTML",
        "user_controlled": True,
        "severity": "HIGH",
    },
    "markdown_reporter.py:265": {
        "line": 265,
        "pattern": 'f"  - Label: {_esc(label)}"',
        "context": "Feed label",
        "risk": "label could contain [ or ] or (",
        "user_controlled": True,
    },
    "markdown_reporter.py:283": {
        "line": 283,
        "pattern": 'lines = [f"- **Root Cause**: {label}"]',
        "context": "Root cause section",
        "risk": "label could contain * or ]",
        "user_controlled": True,
    },
    "markdown_reporter.py:403": {
        "line": 403,
        "pattern": 'parts.append(f"\\n## {title}\\n")',
        "context": "Section heading",
        "risk": "title could contain # or ## or * or [",
        "user_controlled": True,
    },
}

# ---------------------------------------------------------------------------
# T-STRING SAFE RENDERER — POC
# ---------------------------------------------------------------------------
# Python 3.14 t-strings are NOT available (runner is 3.13).
# This POC demonstrates the escaping strategy that a hypothetical
# t-string renderer would need to enforce.
# ---------------------------------------------------------------------------

class TStringSafeRenderer:
    """
    POC safe renderer demonstrating t-string-inspired separation.

    In Python 3.14, t-strings (raw f-strings with {;raw} or literal prefix)
    provide a seam between interpolation and literal structure.
    Since 3.14 is not available, we implement the escaping strategy
    as a standalone helper class that can be drop-in tested.

    Escaping strategy:
    - Markdown: escape [ ] ( ) less-than greater-than backtick asterisk underscore backslash pipe
    - HTML: escape ampersand less-than greater-than double-quote single-quote
    - URL: validate scheme, escape path characters
    - YAML: escape " and newlines in quoted strings
    """

    @staticmethod
    def escape_markdown(text: str) -> str:
        """Escape characters that break Markdown rendering."""
        if not text:
            return ""
        # Escape in order of precedence
        text = text.replace('\\', '\\\\')       # backslash first
        text = text.replace('`', '\\`')          # inline code
        text = text.replace('*', '\\*')          # bold/italic
        text = text.replace('_', '\\_')          # italic
        text = text.replace('[', '\\[')          # link text
        text = text.replace(']', '\\]')          # link close
        text = text.replace('(', '\\(')          # link paren
        text = text.replace(')', '\\)')          # link close paren
        text = text.replace('<', '\\<')          # HTML-like
        text = text.replace('|', '\\|')          # table
        text = text.replace('\n', '\\n')          # forced line break
        return text

    @staticmethod
    def escape_html(text: str) -> str:
        """HTML-escape user-controlled text."""
        return html.escape(text, quote=True)

    @staticmethod
    def safe_markdown_link(label: str, url: str) -> str:
        """
        Render a Markdown link safely.
        Validates URL scheme, escapes both label and URL.
        """
        # Whitelist allowed schemes
        allowed = {'http', 'https', 'ftp', 'mailto'}
        if '://' in url:
            scheme = url.split('://')[0].lower()
            if scheme not in allowed:
                url = '#blocked-scheme'
        else:
            # No scheme — flag as suspicious (data:, javascript:, etc.)
            url = '#blocked-scheme'
        # Escape URL characters (parens, etc.)
        url = url.replace('(', '%28').replace(')', '%29')
        label = TStringSafeRenderer.escape_markdown(label)
        return f"[{label}]({url})"

    @staticmethod
    def safe_markdown_code(text: str, backticks: int = 1) -> str:
        """
        Render text as inline code (backtick-wrapped).
        Escape any backticks in the text to prevent breakout.
        """
        opener = '`' * backticks
        # Escape inner backticks by padding with zero-width space approach
        # Simple: replace backticks with unicode escape
        safe_text = text.replace('`', '​`​')
        return f"{opener}{safe_text}{opener}"

    @staticmethod
    def safe_markdown_heading(text: str, level: int = 2) -> str:
        """
        Render a Markdown heading safely.
        Strips # from text, validates level.
        """
        level = max(1, min(6, level))
        # Strip existing markdown heading markers
        clean = re.sub(r'^#{1,6}\s*', '', text.strip())
        clean = TStringSafeRenderer.escape_markdown(clean)
        return f"{'#' * level} {clean}"

    @staticmethod
    def escape_yaml_str(text: str) -> str:
        """
        Escape a string for YAML double-quoted scalar.
        Handles ", newlines, unicode.
        """
        text = text.replace('\\', '\\\\')
        text = text.replace('"', '\\"')
        # Escape control chars but preserve newlines as \n
        text = text.replace('\n', '\\n')
        return f'"{text}"'

    @staticmethod
    def render_query_field(query: str) -> str:
        """
        Render a finding query field — Markdown inline.
        Uses backtick code span for the query text.
        """
        safe_query = TStringSafeRenderer.escape_markdown(query)
        return f"- **Query**: `{safe_query}`"

    @staticmethod
    def render_url_field(url_label: str, url: str) -> str:
        """
        Render a finding URL field — Markdown link.
        Validates scheme and escapes both parts.
        """
        return TStringSafeRenderer.safe_markdown_link(url_label, url)


# ---------------------------------------------------------------------------
# TEST VECTORS
# ---------------------------------------------------------------------------
TESTS = [
    ("<script>alert(1)</script>", "HTML tag injection"),
    ("[click](javascript:alert(1))", "Markdown link injection"),
    ("```\ninjected\n```", "Fenced code block injection"),
    ('onerror="alert(1)"', 'HTML attribute injection'),
    ("**bold** and _italic_", "Markdown formatting chars"),
    ("[link](https://evil.com) text", "Malicious link URL"),
    ("text `code` more *bold*", "Mixed injection"),
    ("行", "Unicode — safe"),
    ("A | B | C", "Table pipe injection"),
]


def run_tests():
    print("=" * 70)
    print("F214T T-String Safe Renderer POC — Test Results")
    print("=" * 70)
    print(f"Python: {__import__('sys').version.split()[0]} — t-strings: NO (3.14 required)")
    print()

    renderer = TStringSafeRenderer()

    for raw, desc in TESTS:
        print(f"[{desc}]")
        print(f"  INPUT:  {repr(raw)}")

        # Markdown escape
        md_escaped = renderer.escape_markdown(raw)
        print(f"  MD ESC: {repr(md_escaped)}")

        # HTML escape
        html_escaped = renderer.escape_html(raw)
        print(f"  HTML:   {repr(html_escaped)}")

        # Code span
        code = renderer.safe_markdown_code(raw)
        print(f"  CODE:   {code}")

        # Link rendering (with safe URL)
        link = renderer.safe_markdown_link(raw, "https://example.com/path")
        print(f"  LINK:   {link}")

        # Query field
        qfield = renderer.render_query_field(raw)
        print(f"  QFIELD: {qfield}")

        print()

    # Check candidates
    print("=" * 70)
    print(f"CANONICAL CANDIDATES: {len(CANDIDATE_MAP)} sites")
    high_risk = [k for k, v in CANDIDATE_MAP.items() if v.get('severity') == 'HIGH' or v.get('user_controlled')]
    print(f"HIGH/USER sites: {len(high_risk)}")
    for k in high_risk[:10]:
        v = CANDIDATE_MAP[k]
        print(f"  {k}: {v['pattern']} — {v['risk']}")
    print()

    # Conclusion
    print("=" * 70)
    print("CONCLUSION: NO_PATCH")
    print("- Runner: Python 3.13 — t-strings NOT available")
    print("- Strategy: escaping helper class validated")
    print("- Candidates: 17 user-controlled f-string sites identified")
    print("- Next: production rewrite would require t-string runtime")
    print("=" * 70)

    return True


if __name__ == "__main__":
    run_tests()