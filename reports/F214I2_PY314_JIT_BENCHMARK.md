# F214I-2 — Python 3.14 Experimental JIT Benchmark Report

**Date:** 2026-05-05
**Platform:** macOS Darwin 25.4.0 (Apple Silicon M1)
**Interpreter:** Python 3.14.4 via uv (cpython-3.14.4-macos-aarch64-none)
**Location:** `hledac/universal/tools/bench_py314_jit.py`

---

## Verdict

**KEEP_DISABLED — NO_PATCH**

Python 3.14.4 from uv was built **without `--with-jit`**. The `sys.jit`
attribute is `NOT_FOUND`. `PYTHON_JIT=1` has no effect on this interpreter.

---

## JIT Availability Check

| Interpreter | Version | sys.jit attr | sys.flags.jit | PYTHON_JIT=1 effect |
|---|---|---|---|---|
| `.venv/bin/python` (uv) | 3.14.4 | **False** (not present) | N/A | **none** |
| `/opt/homebrew/bin/python3.14` | 3.14.2 | **False** (not present) | N/A | **none** |
| `.venv-py3135/bin/python` | 3.13.5 | **False** (not present) | N/A | N/A |

**All tested Python 3.14 interpreters lack JIT support.**

The Python 3.14 experimental JIT is a **build-time option**. Distributions
(e.g., Homebrew, uv-managed CPython) must be compiled with `--with-jit` for
`sys.jit` to appear. None of the available 3.14 builds include it.

---

## Benchmark Tool

`tools/bench_py314_jit.py` — self-contained, report-only.

- **Does NOT** modify `.venv` or any runtime configuration
- **Does NOT** add `PYTHON_JIT=1` to any env file
- **Does NOT** patch any source
- Runs smoke probes: `import_smoke`, `boot_smoke`, `execution_optimizer`, `content_miner`
- Exit codes: `0` = complete, `64` = NO_PATCH, `65` = error

### Validation

```bash
source .venv/bin/activate
PYTHONPATH=$(pwd) python tools/bench_py314_jit.py
```

Expected output (NO_PATCH):
```
F214I-2 Python 3.14 JIT Benchmark
==================================================
Python: /Users/vojtechhamada/PycharmProjects/Hledac/.venv/bin/python
Project: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal

JIT available: False
Reason: sys.jit attribute NOT_FOUND. Python 3.14.4 was built WITHOUT --with-jit. PYTHON_JIT=1 has no effect on this interpreter.

NO_PATCH: Python 3.14.4 from uv was built WITHOUT --with-jit.
  sys.jit attribute NOT_FOUND. Python 3.14.4 was built WITHOUT --with-jit. PYTHON_JIT=1 has no effect on this interpreter.
Verdict: KEEP_DISABLED — No JIT support in this interpreter build.
```

---

## Analysis

### Why no JIT in available Python 3.14 builds?

Python 3.14's experimental JIT (PEP 749) is **optional and experimental**.
It must be explicitly enabled at compile time via `--with-jit`. Most package
managers (Homebrew, uv, conda) do **not** enable it by default because:

1. The JIT is still experimental and subject to change
2. It adds compilation overhead and complexity to the build
3. It's not yet recommended for production workloads

### What would a JIT-enabled build show?

If a Python 3.14 binary were built with `--with-jit`:
- `sys.jit` would be `True` (the attribute exists)
- `sys.flags.jit` would be `1` when `PYTHON_JIT=1` is set
- Potential speedups on hot paths (function dispatch, tight loops)

For Hledac specifically, the expected benefit areas would be:
- **MLX model inference**: hot tensor operations in `mlx_lm`
- **Async event loop**: faster task scheduling in `asyncio`
- **Large module imports**: cached bytecode compilation

### Is JIT ever worth it for Hledac?

Possibly — if:
1. A JIT-enabled Python 3.14 build becomes available in the ecosystem
2. The `mlx` LLM path is the primary bottleneck
3. Microbenchmarks show >5% improvement in end-to-end sprint time

Currently **not applicable** — no binary available.

---

## Recommendation

| Action | Rationale |
|---|---|
| **Keep `PYTHON_JIT=1` disabled** | No JIT-enabled interpreter in environment |
| **Re-evaluate after Python 3.15** | JIT API may stabilize and become more widely available |
| **Monitor upstream** | Homebrew/uv may add JIT-enabled builds when 3.14 exits experimental |
| **No production config change** | Current setup is correct; JIT is not a drop-in improvement |

---

## Files

- `tools/bench_py314_jit.py` — benchmark implementation (report-only helper)
- `reports/F214I2_PY314_JIT_BENCHMARK.md` — this report
