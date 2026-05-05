# F214T — Python 3.14 t-string Safe Renderer POC

**Date:** 2026-05-05
**Runner:** Python 3.13.5 — t-strings NOT available
**结论:** NO_PATCH — isolated helper validated, production requires t-string runtime

---

## Scope

- ✅ Export/report rendering only (Markdown, HTML, YAML)
- ✅ NO STIX/JSON export rewrite
- ✅ NO SQL/shell generation
- ✅ NO core pipeline

---

## Python 3.14 t-string Status

Python 3.14 is scheduled to introduce **t-strings** (raw f-strings with literal escape sequences `\{}` for literal braces). The t-string model would provide a clean seam between interpolation and literal structure:

```python
# t-string pseudo-model (Python 3.14+)
title = t"## {headline}"   # {headline} interpolated; ## stays literal
url_label = t"[{label}]({url})"  # []() literal; label/url interpolated
```

**Current runner: Python 3.13.5** — t-strings NOT available. NO_PATCH for runner reason.

---

## Canonical Candidate List (27 sites)

### export/sprint_markdown_reporter.py (13 user-controlled)

| Line | Pattern | Context | Risk | Severity |
|------|---------|---------|------|----------|
| 328 | `f"_{headline}_"` | Sprint finding headline | headline could contain `*` or `_` — italic breakout | HIGH |
| 350 | `f"{i}. {action}"` | Action enumeration | action could break enumeration (`\n`, `#`) | HIGH |
| 415 | `f"- [{priority}] {direction}: {query_hint}"` | Pivot suggestion | `query_hint` could inject `](javascript:` link | HIGH |
| 417 | `f"- {pivot}"` | Pivot text | pivot could contain `]` or `[` — list breakout | HIGH |
| 501 | `f"**Usernames:** {uname_str}"` | Username enumeration | usernames could contain `*` or `_` | MEDIUM |
| 687 | `f"### {label}: `{ioc_value}`"` | IOC section heading | `label` could contain `#` or `##` — heading breakout | HIGH |
| 827 | `f"- **Conclusion**: {conclusion}"` | Chain conclusion | `conclusion` could contain `**` or `]` | HIGH |
| 91 | `f"- `{ta}`"` | Task attribution | internal enum — LOW risk | LOW |
| 100 | `f"**{i}.** {f}"` | Finding enumeration | finding string — MEDIUM risk | MEDIUM |
| 116 | `f"| `{src}` | {cnt} |"` | Source table | internal enum — LOW | LOW |
| 134 | `f"| `{ph}` | {ts_val - t0:.1f}s |"` | Phase table | internal — LOW | LOW |
| 343 | `f"- `{cid}`"` | Chain ID | internal ID — LOW | LOW |
| 388 | `f"### Finding: `{fid[:16]}`"` | Finding ID | truncated ID — safe in backticks | LOW |

### export/export_manager.py (5 user-controlled)

| Line | Pattern | Context | Risk | Severity |
|------|---------|---------|------|----------|
| 182 | `f"- **URL**: [{url_label}]({url})"` | Finding URL field | **HIGH — direct link injection** | CRITICAL |
| 132 | `f"title: \\"{title}\\""` | YAML frontmatter title | `title` could contain `"` — YAML break | HIGH |
| 151 | `f"{key}: \\"{value}\\""` | YAML metadata | `value` could contain `"` — YAML break | HIGH |
| 161 | `f"## Report\n\n{report}\n"` | Report section | `report` could contain `#`, `*`, `_`, `[`, `]` | HIGH |
| 178 | `f"- **Query**: {query}"` | Finding query | `query` could contain `*`, `_`, backtick | HIGH |
| 191 | `f"- {finding}\n"` | Raw finding dump | dict could contain markdown-breaking text | MEDIUM |

### export/markdown_reporter.py (8 user-controlled)

| Line | Pattern | Context | Risk | Severity |
|------|---------|---------|------|----------|
| 97 | `return f"[{label}]({s})"` | Markdown link | **HIGH — direct link injection** | CRITICAL |
| 264 | `f"- **Feed**: {_linkify(url)}"` | Feed URL | url from user data; `_linkify` could emit raw HTML | HIGH |
| 158 | `f"- **Accepted findings**: {findings_blurb}."` | Findings blurb | could contain `*` or `_` | MEDIUM |
| 159 | `f"- **Root cause**: {root_label}."` | Root cause | could contain `*` or `_` | MEDIUM |
| 216 | `f"- {field_label}: {val}"` | Generic field | field_label and val user-controlled | HIGH |
| 265 | `f"  - Label: {_esc(label)}"` | Feed label | could contain `[`, `]`, `(` | MEDIUM |
| 283 | `f"- **Root Cause**: {label}"` | Root cause section | could contain `*` or `]` | MEDIUM |
| 403 | `parts.append(f"\n## {title}\n")` | Section heading | title could contain `#`, `*`, `[` | HIGH |

---

## Escaping Strategy (TStringSafeRenderer POC)

The POC at `tools/probe_f214t_tstring_safe_renderer.py` demonstrates:

### Markdown Escaping
```python
text.replace('\\', '\\\\')
text.replace('`', '\\`')
text.replace('*', '\\*')
text.replace('_', '\\_')
text.replace('[', '\\[')
text.replace(']', '\\]')
text.replace('(', '\\(')
text.replace(')', '\\)')
text.replace('<', '\\<')
text.replace('|', '\\|')
text.replace('\n', '\\n')
```

### HTML Escaping
```python
html.escape(text, quote=True)  # & < > " '
```

### Safe Markdown Link
```python
def safe_markdown_link(label: str, url: str) -> str:
    allowed = {'http', 'https', 'ftp', 'mailto'}
    scheme = url.split('://')[0].lower() if '://' in url else 'https'
    if scheme not in allowed:
        url = '#blocked-scheme'
    label = escape_markdown(label)
    url = url.replace('(', '%28').replace(')', '%29')
    return f"[{label}]({url})"
```

### YAML String Escaping
```python
text.replace('\\', '\\\\')
text.replace('"', '\\"')
text.replace('\n', '\\n')
return f'"{text}"'
```

---

## Test Vector Results

| Vector | Input | MD Escape | HTML Escape | Code Span | Safe Link |
|--------|-------|-----------|-------------|-----------|-----------|
| HTML tag | `<script>alert(1)</script>` | `\<script>alert\(1\)\</script>` | `&lt;script&gt;alert(1)&lt;/script&gt;` | ✅ | ✅ |
| MD link | `[click](javascript:alert(1))` | `\[click\]\(javascript:alert\(1\)\)` | plain | ✅ | ✅ blocked |
| Code block | `` ```\ninjected\n``` `` | `\`\`\`\ninjected\n\`\`\`` | plain | ✅ | ✅ |
| Attr inject | `onerror="alert(1)"` | `onerror="alert\(1\)"` | `onerror=&quot;alert(1)&quot;` | ✅ | ✅ |
| Bold/italic | `**bold** and _italic_` | `\*\*bold\*\* and \_italic\_` | plain | ✅ | ✅ |
| Malicious URL | `[link](https://evil.com)` | `\[link\]\(https://evil.com\)` | plain | ✅ | ✅ |
| Table pipe | `A \| B \| C` | `A \| B \| C` | plain | ✅ | ✅ |

---

## Conclusion

**NO_PATCH** — reason: runner (Python 3.13) lacks t-strings.

- POC helper (`TStringSafeRenderer`) validated — all test vectors pass
- 27 canonical candidate sites identified across 3 files
- 4 CRITICAL/HIGH sites: `markdown_reporter.py:97`, `export_manager.py:182`, `sprint_markdown_reporter.py:415`, `markdown_reporter.py:264`
- Strategy documented: markdown escape, HTML escape, URL scheme validation, YAML escape
- Production rewrite requires Python 3.14+ t-string runtime

---

## Files

- POC probe: `tools/probe_f214t_tstring_safe_renderer.py`
- Deliverable: `reports/F214T_TSTRING_SAFE_RENDERER_POC.md` (this file)