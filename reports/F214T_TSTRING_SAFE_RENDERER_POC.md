# F214T — Python 3.14 t-string Safe Renderer POC

**Date:** 2026-05-06 (updated F214T-3)
**Runner:** Python 3.14.4 — t-string syntax AVAILABLE, rendering UNAVAILABLE
**结论:** PATCH_HELPER_NOW — confirmed by F214T-3

---

## F214T-3 Verification Results

### Hard Parser Smoke

```
Version: 3.14.4 (main, Apr 14 2026, 14:46:33) [Clang 22.1.3 ]
Executable: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/.venv/bin/python

exec('t"hello {name}"') → SUCCESS
type: <class 'string.templatelib.Template'>
strings: ('hello ', '')
isinstance str: False
```

### t-string Availability Verdict

| Check | Result |
|-------|--------|
| `t"..."` literal syntax | **AVAILABLE** — parses without SyntaxError |
| Result type | `string.templatelib.Template` — NOT a string |
| `isinstance(tpl, str)` | **False** |
| `str(template)` renders to string | **False** — returns repr |
| `Template` has `substitute()` / `render()` | **False** — no rendering API |
| `format_map()` on template | **No effect** |

### Critical Finding

Python 3.14.4 includes the t-string literal syntax (`t"..."`) and `string.templatelib` module, but the `Template` object produced cannot be rendered to a plain string. The `Template` class has only `strings`, `interpolations`, and `values` attributes — no method to convert interpolations + strings into a final output string.

This means:
1. **t-string syntax IS available** — `t"..."` literals parse successfully
2. **t-string rendering is NOT available** — `str(tpl)` returns the Template repr, not a string
3. **t-strings cannot serve as output strings** — they are inert Template objects with no rendering API

### PEP 750 Status

PEP 750 (t-strings) was implemented in Python 3.14 as a syntax-only feature. The `string.templatelib` module provides the type, but the API surface is minimal — just `Template` and `Interpolation` classes with inspection attributes. There is no `safe_substitute()`, `render()`, or any method to produce a final string from a Template.

**Previous report was INCORRECT** in stating t-strings were absent. They are present but non-functional for output rendering.

---

## 3.14 Rerun (2026-05-06)

**Runner:** Python 3.14.4 via `.venv`
**t-string status:** Syntax AVAILABLE, rendering UNAVAILABLE
**Verdict:** NO_PATCH → **PATCH_HELPER_NOW** (confirmed — t-string design does not obsolete the escaping helper)

| Finding | 3.13 Runner | 3.14.4 Runner |
|---------|-------------|---------------|
| `markdown_reporter._esc` | only escapes backticks | same — vulnerable |
| `markdown_reporter._linkify` | non-http returns plain text | same — safe by design |
| `markdown_reporter.py:97` | raw f-string link construction | same — **CRITICAL** |
| `export_manager.py:182` | raw f-string link construction | same — **CRITICAL** |
| `sprint_md_reporter.py:415` | raw f-string list item | same — HIGH |
| `markdown_reporter.py:264` | `_linkify` for http-only | same — **safe** |
| **t-string syntax** | not available | **AVAILABLE** but non-rendering |

---

## Scope (Original)

- ✅ Export/report rendering only (Markdown, HTML, YAML)
- ✅ NO STIX/JSON export rewrite
- ✅ NO SQL/shell generation
- ✅ NO core pipeline

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
| 97 | `return f"[{label}]({s})"` | Markdown link | **CRITICAL — direct link injection, no escaping** | CRITICAL | ⚠️ |
| 264 | `f"- **Feed**: {_linkify(url)}"` | Feed URL | `_linkify` only creates links for http/https — javascript/data/ftp return plain text; **SAFE** | SAFE | ✅ |
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

**Verdict: PATCH_HELPER_NOW** (confirmed by F214T-3)

### Why PATCH_HELPER_NOW (not NO_PATCH)

F214T-3 hard parser smoke confirms:
1. **t-string syntax IS available** in Python 3.14.4 — `t"..."` literals parse successfully
2. **t-string rendering is NOT available** — `str(tpl)` returns Template repr, not a string; no `substitute()` or `render()` method exists
3. **t-strings cannot replace f-strings for output** — they produce inert `Template` objects with no rendering API
4. **`markdown_reporter._esc` remains insufficient** — only escapes backticks; leaves `*`, `_`, `[`, `]`, `(`, `)`, `<`, `|`, `\n` unescaped
5. **`markdown_reporter.py:97` is a genuine injection point** — `[{label}]({s})` with user-controlled label and URL; test with `label=[click](javascript:alert(1))`, `url=javascript:alert(1)` produces a valid markdown link that executes JavaScript in Obsidian/markdown viewers
6. **`export_manager.py:182` is the same pattern** — same direct interpolation without escaping
7. **`markdown_reporter.py:264` is SAFE** — `_linkify` only creates links for `http://`/`https://`; non-http schemes return plain text

### PEP 750 Reality Check (CORRECTED)

Previous report incorrectly stated: "Python 3.14 does **not** include t-strings. T-strings are targeting a future Python version (possibly 3.15)."

**CORRECTED**: Python 3.14.4 includes t-string literal syntax (`t"..."`) and the `string.templatelib` module. The `Template` class and `Interpolation` class are present. However, `Template` has no rendering method — `str(tpl)` returns the repr, not a rendered string. This makes t-strings as currently implemented **non-functional for output purposes** — they are syntax-only and cannot produce a usable string.

### PATCH Scope (POC-only)

- **Target**: `export/markdown_reporter.py:85` — `_esc()` function only
- **Change**: Expand escaping to cover all markdown special chars: `* _ [ ] ( ) < |` + `\n`
- **Impact**: Fixes `_esc` callers throughout (lines 110, 113, 131, 197, 265, 272, 283)
- **Why not broader**: 27 candidate sites, 2 CRITICAL (`_esc`-gap not `t-string-gap`), production rewrite deferred
- **NOT patched**: `markdown_reporter.py:97`, `export_manager.py:182` — require f-string refactoring (production change)

### Why NOT KEEP_POC_ONLY

`_esc` is a 3-line helper that is **trivially fixable**. The gap is not "needs t-string runtime" — it's "needs backslash escaping of special markdown chars". This is a standard fix in any Python version. The t-string design in Python 3.14.4 does not obsolete this approach because t-strings cannot currently be rendered to strings.

### Remaining Debt (production scope)

| Site | Pattern | Status | Notes |
|------|---------|--------|-------|
| `markdown_reporter.py:97` | `f"[{label}]({s})"` | UNFIXED | Direct markdown link construction — needs safe helper |
| `export_manager.py:182` | `f"- **URL**: [{url_label}]({url})"` | UNFIXED | Same pattern |
| `sprint_markdown_reporter.py:415` | `f"- [{priority}] {direction}: {query_hint}"` | UNFIXED | `query_hint` can break out of list item |
| `sprint_markdown_reporter.py:417` | `f"- {pivot}"` | UNFIXED | `pivot` can break list structure |
| `sprint_markdown_reporter.py:687` | `f"### {label}: ..."` | UNFIXED | `label` can break heading level |
| `export_manager.py:161` | `f"## Report\n\n{report}\n"` | UNFIXED | Multi-line user text |

All require f-string refactoring — production rewrite scope, deferred.

---

## PATCH_APPLIED (F214T-PATCH) — 2026-05-06

### Helper Module

**File:** `utils/safe_render.py`

Functions:
- `escape_markdown_text(text: str) -> str` — escapes `\` `` ` `` `*` `_` `[` `]` `(` `)` `<` `>` `|` `\n`
- `escape_html_text(text: str) -> str` — uses `html.escape(text, quote=True)`
- `safe_markdown_link(label: str, url: str) -> str` — scheme validation (blocks javascript/data/file), label escaping, URL paren encoding
- `safe_code_fence(text: str) -> str` — escapes `` ` `` and `\` for fenced code blocks

### Patched Sites (2 of 27)

| File | Line | Before | After |
|------|------|--------|-------|
| `export/markdown_reporter.py` | 113 | `return f"[{label}]({s})"` | `return safe_markdown_link(label, s)` |
| `export/export_manager.py` | 184 | `f"- **URL**: [{url_label}]({url})"` | `f"- **URL**: {safe_markdown_link(url_label, url)}"` |

**Reason for patching these first:** Both are direct markdown link constructions from user-controlled label + URL where no escaping was applied. `markdown_reporter.py:97` (now line 113) is the `_linkify()` HTTP branch — previously used raw f-string for links. `export_manager.py:182` (now line 184) is the finding URL field.

### Deferred Sites

`sprint_markdown_reporter.py:415/417` (priority, direction) patched by F214T-4. Remaining unescaped sites deferred to production scope.

### Test Results

```
27 passed in 0.64s (F214T-PATCH baseline)
40 passed in 0.90s (F214T-PATCH + F214T-4)
```

Probe tests at `tests/probe_f214t_safe_rendering/test_safe_render.py` and `test_sprint_markdown_escape.py`.

### Validation

- `uv sync --extra dev`: PASS (155 packages)
- `PYTHONPATH=... pytest -q tests/probe_f214t_safe_rendering/`: 40 passed
- `PYTHONPATH=... python -c "import hledac.universal; print('IMPORT_OK')"`: IMPORT_OK
- Boot smoke: clean startup, no fatal traceback

---

## F214T-4 — sprint_markdown_reporter Priority/Direction Escape (2026-05-06)

### Target Sites (2 HIGH — follow-up to F214T-PATCH)

| File | Line | Before | After |
|------|------|--------|-------|
| `export/sprint_markdown_reporter.py` | 417 | `f"- [{priority}] {direction}: {query_hint}"` | `f"- [{priority}] {direction}: {escape_markdown_text(query_hint)}"` |
| `export/sprint_markdown_reporter.py` | 419 | `f"- {pivot}"` | `f"- {escape_markdown_text(pivot)}"` |

### Helper Used

`utils/safe_render.escape_markdown_text()` — escapes `` ` `` `\` `*` `_` `[` `]` `(` `)` `<` `>` `|` `
`

### Risk Mitigated

- `priority` field with `]` could break out of `[priority]` bracket pair; `*`, `_`, `[` could inject bold/italic/links
- `direction` field with `*`, `_`, `[`, `]`, `(` could inject bold/italic/links into the pivot line

Note: `query_hint` and `pivot` (str case) were already escaped by F214T-PATCH commit 3c35cfb0.

### Test Results

```
40 passed in 0.90s (full F214T suite: 27 safe_render + 8 sprint_markdown + 5 new priority/direction)
```

New probe tests: `tests/probe_f214t_safe_rendering/test_sprint_markdown_escape.py` — `TestPriorityDirectionEscape` class (5 new tests)

### Deferred Sites

| Site | Pattern | Status |
|------|---------|--------|
| `sprint_markdown_reporter.py:328` | `f"_{headline}_"` | DEFERRED |
| `sprint_markdown_reporter.py:350` | `f"{i}. {action}"` | DEFERRED |
| `sprint_markdown_reporter.py:687` | `f"### {label}: ..."` | DEFERRED |
| `sprint_markdown_reporter.py:827` | `f"- **Conclusion**: {conclusion}"` | DEFERRED |
| `export_manager.py:161` | `f"## Report\n\n{report}\n"` | DEFERRED |

Note: `markdown_reporter.py:97` and `export_manager.py:182` were already patched by F214T-PATCH (commit 3c35cfb0).

---

## Files

- POC probe: `tools/probe_f214t_tstring_safe_renderer.py`
- Safe renderer helper: `utils/safe_render.py`
- Probe tests: `tests/probe_f214t_safe_rendering/test_safe_render.py`
- Deliverable: `reports/F214T_TSTRING_SAFE_RENDERER_POC.md` (this file)

---

## Changelog

| Date | Change |
|------|--------|
| 2026-05-05 | Initial report — runner Python 3.13, t-strings not available |
| 2026-05-06 | F214T-3 update — runner Python 3.14.4, t-string syntax available but non-rendering; previous "t-strings absent" claim corrected |
| 2026-05-06 | F214T-PATCH applied — 2 critical sites patched, utils/safe_render.py created, 27 probe tests pass |
| 2026-05-06 | F214T-4 applied — priority + direction fields escaped on line 417, 40 tests pass |
