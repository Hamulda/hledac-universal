# Testing Guide

## Running Tests

### Use the project venv pytest

**NEVER use the system/homebrew pytest.** The system pytest may lack required dependencies (`msgspec`, `lmdb`).

```bash
# WRONG — uses homebrew Python, may lack msgspec
uv run pytest tests/

# CORRECT — uses project venv
cd /Users/vojtechhamada/PycharmProjects/Hledac
.venv/bin/pytest tests/

# Or via uv with explicit venv
cd /Users/vojtechhamada/PycharmProjects/Hledac
uv run --no-project python -m pytest tests/
```

### Recommended M1 Setup

```bash
cd /Users/vojtechhamada/PycharmProjects/Hledac
uv sync --extra m1-local --extra dev
```

This installs: `apple-accel`, `osint-html`, `graph-storage`, `acceleration`, `transport`, `dev` extras — everything needed for local testing on M1 without heavy deps (torch, browser binaries).

### Collection Smoke

```bash
.venv/bin/pytest --collect-only -q
```

Should show `collected N` with zero errors. Collection errors (as opposed to skips) indicate missing dependencies or import errors.

## Optional Dependency Skips

Tests with optional dependencies use `pytest.importorskip()` at module level to prevent collection errors:

| Dependency | When Skipped | Files |
|------------|-------------|-------|
| `mlx` | Non-M1 / mlx not installed | `test_sprint62a.py` |
| `lmdb` | Not installed | `test_sprint43.py` |
| `aiohttp_socks` | Not installed | `test_sprint46.py`, `test_sprint61.py` |
| `gliner` | Not installed | `test_sprint51_52.py` |

If a test hard-fails at collection (not skip), add `pytest.importorskip("<module>")` after the `import pytest` line:

```python
import pytest
pytest.importorskip("mlx")
import mlx.core as mx
```

## What Not to Install

- **torch / torchvision** — too heavy for M1 8GB
- **camoufox / chromium** — browser binary heavy; use `osint-html` (curl_cffi) instead
- **pytesseract / ocr** — use `ocr` extra only if needed
- **nodriver / selenium / playwright** — use camoufox (bundled binary)

## Legacy Import Path

Some tests still import from `hledac.universal.autonomous_orchestrator` (deprecated). The canonical path is `runtime.sprint_scheduler`. These are **warnings**, not errors — tests still run, but imports should be updated as technical debt.