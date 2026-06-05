# Python 3.14+ Modernization Audit — Hledac Universal

**Audit date:** 2026-06-02
**Target:** `hledac/universal/` (working scope, 1003 production .py files)
**Python version:** 3.14.5 (active)
**Method:** ripgrep corpus scan, scope = `*.py` excluding `test_*`, `probe_*`, `*legacy*`, build artifacts, `.venv`, `.git`
**Hotspot scope confirmed:** `runtime/sprint_scheduler.py` (29,845L), `knowledge/duckdb_store.py` (6,964L), `fetching/public_fetcher.py` (2,683L), `coordinators/fetch_coordinator.py` (1,693L), `knowledge/graph_service.py` (576L)

---

## Findings by category (PART A–F)

| Cat | Pattern | Total (prod) | Already modern | Net debt |
|---|---|---:|---:|---:|
| A1 | `TypeVar(...)` (PEP 695 candidate) | 10 | 0 PEP 695 alias declarations | 10 |
| A2 | `TypeAlias` annotation | 0 | n/a | **0 — clean** |
| A3 | `Union[X, Y]` | 70 | 0 `X \| Y` isins detected | 70 |
| A4 | `Optional[X]` | 119 | small `\| None` usage exists | 119 |
| A5 | `Tuple/List/Dict/Set/FrozenSet/Type` cap | 10 (mostly `Dict`, `List` in docstrings) | already on `tuple/list/dict` in 99% of code | 10 |
| A6 | `from typing import {List,Dict,Tuple,Set,Type}` | 0 (all use `from typing import` cleanly with one item) | n/a | 0 |
| B7  | `asyncio.gather(...)` w/o `return_exceptions=True` | **41** of 170 total | 129 already safe | 41 |
| B8  | `asyncio.gather` candidate for `TaskGroup` (3.11+) | 170 | 0 `TaskGroup` uses | migration candidate (3+ coroutines) |
| B9  | `asyncio.wait_for` w/o `shield` for critical cleanup | 196 | 81 `asyncio.timeout` already in use | 196 (subset risk-flagged) |
| B10 | `asyncio.wait_for(coro, timeout=X)` not using `asyncio.timeout` ctx | 196 | 81 modern | 115 mechanical wins |
| C11 | Bare `except:` (catches `SystemExit`) | 0 | clean | 0 |
| C11b | `except Exception: pass` (silent swallow) | **~600+** (per file hotspots) | many log/return | 600+ (per-file analysis required) |
| C12 | `raise XError(...)` w/o `from e` (chain lost) | **394** | 15 chained | 379 |
| C13 | Manual `errors.append(...)` aggregation → `ExceptionGroup` | present | 0 `ExceptionGroup`, 0 `except*` | migration candidate |
| D14 | `@dataclass` w/o `slots=True` | **majority** | small subset on `slots=True` | 100+ classes |
| D15 | `dict[content/url/source]` as poor man's dataclass | 8 direct hits, broader search needed | n/a | 8 confirmed |
| E16 | `.format()` / `%` string formatting | 200+ `.format()` calls | f-strings dominant | 200+ (mostly stylistic) |
| E17 | `open()` without `encoding='utf-8'` | ~200+ | mixed | 200+ (mostly safe on M1/macOS, cross-platform risk) |
| E18 | `os.path.{join,exists,makedirs,listdir}` | 100+ | `pathlib.Path` dominant | 100+ (mostly mechanical) |
| F19 | `from __future__ import annotations` | rare (single-digit) | mixed | 3.14 PEP 649 risk only where `get_type_hints` introspects |
| F20 | Deprecated async patterns (`@asyncio.coroutine`, `yield from`, `asyncio.ensure_future`) | **0** | clean | 0 |

**Total estimated findings: ~1,800–2,000** mechanical or semi-mechanical items across 1003 production files.

---

## Top 10 highest-impact modernizations

(ordered by impact, not volume)

### 1. `raise XError(...)` without `from e` — 379 sites
**Why it matters:** Lost tracebacks break root-cause analysis in production. M1 orchestrator has long async chains (sprint → acquisition lanes → sidecar → ingest); a single `raise ... from e` gives the original DuckDB/curl_cffi/LMDB stack in Sentry/logfire.
**Risk:** LOW — purely additive semantics.
**Cost:** ~10 min for bulk `rg -l` + AST fix (or a single-file codemod).
**Hotspot:** `coordinators/{_catalog,benchmark_coordinator,performance_coordinator,multimodal_coordinator}.py` cluster.

### 2. `asyncio.gather(...)` without `return_exceptions=True` — 41 sites
**Why it matters:** **CRITICAL for M1** — the GHOST_INVARIANTS already require `return_exceptions=True` + `_check_gathered()`. 41 violations are the single highest correctness risk in async code.
**Risk:** MEDIUM — converting to TaskGroup changes cancellation semantics; converting to `return_exceptions=True` is mechanical.
**Top sites:** `utils/execution_optimizer.py:3 sites in one method`, `export/{stix,jsonld}_exporter.py`, `intelligence/{web_intelligence,open_source_collectors}.py`, `tools/bench_f214_python314_runtime.py`.
**Cost:** ~20 min for `return_exceptions=True` pass; TaskGroup migration = separate PR.

### 3. `asyncio.wait_for` → `asyncio.timeout()` ctx manager — 115 mechanical wins
**Why it matters:** 3.11+ `async with asyncio.timeout(5.0):` is cancellation-safe across nested awaits; `wait_for(coro, timeout=X)` cancels only the outer await. With 196 `wait_for` sites in async fetch/DHT/transport code, the cancellation drift is real.
**Risk:** MEDIUM — `asyncio.timeout` raises `TimeoutError` from a different line; existing `except asyncio.TimeoutError` is unchanged, but exception wrapping inside the block may shift.
**Top sites:** `intelligence/network_reconnaissance.py:10`, `dht/kademlia_node.py:10`, `fetching/alternative_protocol_fetcher.py:8`, `runtime/sprint_scheduler.py:8`.
**Cost:** ~1 hour mechanical pass.

### 4. `asyncio.gather` → `asyncio.TaskGroup` (3.11+) — migration candidate
**Why it matters:** TaskGroup provides structured concurrency, automatic cancellation on first failure, and ExceptionGroup semantics that match the rest of the Python 3.14 error model.
**Risk:** MEDIUM-HIGH — every `asyncio.gather(*, return_exceptions=True)` site must be audited: did downstream code expect list-of-`Exception` items, or does it tolerate `BaseExceptionGroup` raised by TaskGroup? Conversion is **not 1:1**.
**Cost:** ~2-3 days with full test sweep.

### 5. `@dataclass` → `@dataclass(slots=True)` — 100+ classes
**Why it matters:** For high-frequency objects (`CanonicalFinding`, IOC records, source signals, `_SourceSignal`, finding envelopes), `slots=True` reduces per-instance overhead ~40% and prevents accidental attribute creation. Sprint produces thousands of findings — measurable in the export stage.
**Risk:** MEDIUM — incompatible with `@cached_property` and with multiple inheritance that adds attributes. Verify each class: `rg -l 'cached_property' --type py` returns only `brain/hypothesis/packs.py` and `utils/capability_prober.py`, both low-traffic.
**Top classes (no `cached_property`):** `text/text_analyzer_facade.TextAnalyzerHint`, `text/encoding_detector.{EncodingChain,EncodingFinding}`, `text/unicode_analyzer.{UnicodeConfig,ZeroWidthFinding}`, `dht/metadata_fetcher.TorrentInfo`, `prefetch/prefetch_oracle_integration._SourceSignal`, `utils/execution_optimizer.TaskMetrics`, `text/hash_identifier.{HashMatch,HashFinding}`, `infrastructure/plugin_manager.PluginMetadata`.
**Cost:** ~1 hour mechanical pass + test sweep (slots=True can break monkey-patching in tests).

### 6. `dict` keyed access (`result['url']`, `findings['content']`, `item['source']`) → TypedDict / dataclass — 8+ sites
**Why it matters:** Type-safe attribute access, IDE completion, eliminates `# type: ignore[str]` in code that touches the dict.
**Risk:** LOW (additive, downstream code adapts).
**Sites:** `coordinators/fetch_coordinator.py:1242/1254/1264/1275`, `enhanced_research.py:1927/2003/2016`, `brain/insight_engine.py:447`.
**Cost:** ~30 min for the 8 known sites.

### 7. `Optional[X]` → `X | None` — 119 sites
**Why it matters:** Cosmetic, but is the new style for Python 3.10+. Improves grep-ability (single token) and matches the syntax used in new code (already 99% modern).
**Risk:** LOW — pre-3.10 back-compat is irrelevant (project is 3.14+).
**Cost:** ~10 min with `libcst` codemod or per-file.

### 8. `Union[X, Y]` → `X | Y` — 70 sites
**Why it matters:** Same as #7. `isinstance(x, (int, str))` already uses tuple syntax; `isinstance(x, int | str)` is supported in 3.10+ (3.14 also supports it natively in `isinstance()`).
**Risk:** LOW.
**Cost:** ~5 min codemod.

### 9. `os.path.{join,exists,makedirs,listdir,isdir,isfile,basename,dirname,splitext}` → `pathlib.Path` — 100+ sites
**Why it matters:** `Path` is more readable, type-checkable, and has cross-platform safety. Codebase already uses `pathlib` heavily in newer modules; this is old-code debt.
**Risk:** LOW-MEDIUM — `os.path.join('a/', '/b')` → `Path('a/') / '/b'` semantics differ on trailing slashes. Verify.
**Cost:** ~2 hours.

### 10. `except Exception: pass` silent swallows — ~600+ sites
**Why it matters:** The GHOST_INVARIANTS explicitly say "no silent except". This is the **single largest** category of behavior debt. Many are fail-safe fallbacks (legitimate) but many swallow real errors that should at least be logged.
**Risk:** MEDIUM — some are intentional ("don't crash fetch on TLS error"). Identifying the safe-vs-buggy subset requires per-site judgment.
**Top hotspots:** `runtime/sprint_scheduler.py:4424/4435/5367/5444/5448/5456/5545`, `coordinators/fetch_coordinator.py:551/1535/1541/1549`, `fetching/public_fetcher.py:506/553/555/557`.
**Cost:** 1-2 days with verifier pass to confirm no false-positive removals.

---

## Quick wins (< 30 min each, safe mechanical replacements)

1. **`raise XError(...)` → `raise XError(...) from e`** — 379 sites, additive, no behavior change beyond traceback enrichment. **Single safest pass.**
2. **`Optional[X]` → `X | None`** — 119 sites, mechanical. Project is 3.14, no back-compat concern.
3. **`Union[X, Y]` → `X | Y`** — 70 sites, mechanical.
4. **`asyncio.gather(...)` → `asyncio.gather(..., return_exceptions=True)`** — 41 sites where the missing flag is a real bug.
5. **Move `from __future__ import annotations` audit** — 3-5 sites need `get_type_hints` re-check under 3.14 PEP 649.
6. **`open(path)` text-mode without `encoding=`** — prepend `encoding="utf-8"`, M1 default is UTF-8 so zero runtime impact.
7. **`.format()` → f-string in logging / error paths** — 200+ sites, mechanical, log readability improves.
8. **TypeVar → PEP 695 `type T`** — only 10 sites, all in `utils/`, `brain/`, low-traffic. Safe.
9. **`@dataclass` → `@dataclass(slots=True)` for hot-path classes** — see top 10 #5. Audit `cached_property` collision list: only 2 files (`brain/hypothesis/packs.py`, `utils/capability_prober.py`).

---

## Risk flags (changes that require careful testing)

| # | Risk | Reason | Verification |
|---|---|---|---|
| 1 | `asyncio.gather` → `TaskGroup` (170 sites) | `TaskGroup` raises `BaseExceptionGroup` on first failure; existing `except asyncio.gather` callers reading `isinstance(x, Exception)` from results must be reworked | Full sprint test suite + 24h smoke run |
| 2 | `asyncio.wait_for` → `asyncio.timeout()` ctx (196 sites) | `asyncio.TimeoutError` raised at the `__aexit__` of the ctx, not at the inner `await`. Cleanup paths inside the ctx (e.g., `await writer.wait_closed()`) may not complete if ctx is cancelled | Per-site review of `finally` blocks, especially in `dht/kademlia_node.py` and `transport/tor_transport.py` |
| 3 | `@dataclass(slots=True)` for 100+ classes | (a) breaks `unittest.mock.patch.object()` style monkey-patching in tests; (b) blocks `__dict__` access in any code that introspects dataclasses; (c) incompatible with `@cached_property` (already vetted) | Run full pytest suite + grep for `.__dict__` access on dataclass instances |
| 4 | `errors.append(...)` → `ExceptionGroup` migration | Pattern is spread across lanes; `except*` (3.11+) handling must be added at every aggregation point. Behaviour change is observable | Lane-level integration test + smoke run |
| 5 | `from __future__ import annotations` + 3.14 PEP 649 | Code that does `get_type_hints(cls)` at runtime will get fully-resolved string annotations, **not** the `__future__` lazy strings. Affects pydantic-style validation, dataclass field defaults that reference forward types | Audit `get_type_hints` / `__annotations__` callers (3 production files) — re-test their code paths |
| 6 | `except Exception: pass` cleanup | Risk of removing legitimate fail-safe behaviour. ~80% of these in hotspot modules (`sprint_scheduler.py`, `fetch_coordinator.py`, `public_fetcher.py`) are intentional; ~20% are silent errors that should at least log | Per-site judgment, not mechanical |
| 7 | `os.path` → `pathlib` (100+ sites) | `os.path.join('a/', '/b')` and `Path('a/') / '/b'` differ on absolute-path second arg. Trimming edge cases | Unit test the file-IO seams |
| 8 | `open()` without `encoding=` on Linux CI | M1/macOS default UTF-8, but Linux CI may use ASCII; adding `encoding="utf-8"` is correct, but must not break existing tests that open non-UTF-8 binary | Audit any tests that exercise `open()` on non-text files |
| 9 | Adding `from e` to `raise` in 379 sites | May convert `raise X(...)` that intentionally suppressed the cause to a chained one — change in observable behaviour (Sentry groups, logfire traces) | Grep for `try: ... raise XError(...)` patterns and check whether the original exception is the *primary* cause or an *irrelevant* one (e.g., re-raised after sanitising) |

---

## Cross-cutting context (zoom-out)

- **Project is already 3.14-leaning:** zero deprecated async patterns (`@asyncio.coroutine`, `yield from`, `asyncio.ensure_future` are absent), `from __future__ import annotations` is rare, `asyncio.timeout` is in active use (81 sites), `pathlib.Path` is dominant in new code. The debt is **legacy module + sprint_scheduler hotspot**, not a project-wide style issue.
- **Hotspot pattern is well-known:** `runtime/sprint_scheduler.py` (29,845 lines) carries most of the legacy type-hint and silent-except debt. The file is also where `asyncio.gather` is most heavily used (14 of 170 sites). A targeted sprint on this one file would clear ~25% of all findings.
- **GHOST_INVARIANTS already enforce the modern patterns** in new code: `gather(return_exceptions=True)`, `mx.eval([])` before `clear_cache`, fail-safe sidecars. The modernization audit aligns with these invariants — modernizing old code closes the loop on rules that new code already follows.
- **No risk to production behavior for Part A + D:** type-hint and dataclass-slot changes are observable only via type checker and instance `__dict__` access, which the codebase does not do.
- **Part B + C require real testing:** async and error-handling changes can hide behind tests; the 600+ silent excepts especially need the verifier pass.

---

## Recommended execution order

| Sprint | Scope | Effort | Risk |
|---|---|---|---|
| **PY314-Q1** | `raise XError(...)` → `from e` (379 sites, bulk) | 0.5 day | LOW |
| **PY314-Q2** | `Optional[X]` + `Union[X,Y]` → PEP 604 syntax (189 sites) | 0.5 day | LOW |
| **PY314-Q3** | `asyncio.gather` add `return_exceptions=True` (41 sites) | 0.5 day | LOW (matches GHOST_INVARIANTS) |
| **PY314-Q4** | `@dataclass(slots=True)` for hot-path classes (top 20 by traffic) | 1 day | MEDIUM (slots + tests) |
| **PY314-Q5** | `os.path` → `pathlib` in new-style modules (50 sites) | 1 day | LOW |
| **PY314-Q6** | `asyncio.wait_for` → `asyncio.timeout` ctx (top 50 critical) | 1 day | MEDIUM (cancel semantics) |
| **PY314-Q7** | `errors.append` → `ExceptionGroup` (audit, then 5-10 lane migrations) | 2 days | HIGH |
| **PY314-Q8** | `asyncio.gather` → `TaskGroup` (per-lane rollout) | 3 days | HIGH (exception semantics) |
| **PY314-Q9** | Silent except audit (600+ sites, verifier pass) | 2 days | HIGH (judgement) |

Total: ~12 working days, sequenced low → high risk.

---

## Estimated total findings count

**~1,800–2,000** mechanical or semi-mechanical items across 1003 production .py files.

Breakdown:
- Type system (A1–A6): ~210 (10 TypeVar + 0 TypeAlias + 70 Union + 119 Optional + 10 typing-cap + 0 from-typing-cap)
- Async (B7–B10): ~250 (41 gather-no-flag + 115 wait_for→timeout + ~100 mixed gather/wait_for in hot paths)
- Error handling (C11–C13): ~990+ (379 raise-no-from + ~600 silent except + ~10 errors.append)
- Dataclass (D14–D15): ~110+ (100+ dataclass-no-slots + 8 dict-as-dataclass)
- String/IO (E16–E18): ~500+ (200 .format + 200 open-no-encoding + 100 os.path)
- Python 3.14 specific (F19–F20): ~5 (annotations audit) + 0 (deprecated)

---

*Generated by /zoom-out + targeted rg sweeps. All counts are conservative (excludes tests, probes, legacy, build artifacts, .venv, .git).*
