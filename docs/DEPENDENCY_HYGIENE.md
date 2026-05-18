# Dependency Hygiene Guide

**Scope:** `hledac/universal/` — uv-managed Python environment on MacBook Air M1 8GB
**Authority:** `pyproject.toml` — single source of truth for dependencies
**Lockfile:** `uv.lock` — must match pyproject.toml intent

---

## Core Rules

1. **uv lock is truth.** All dependencies must be declared in `pyproject.toml` and resolved via `uv lock`.
2. **No pip install.** Never run `pip install`, `pip install --user`, or `python -m pip install`. Use `uv add` or `uv sync`.
3. **No global site-packages.** Do not install packages into the system Python or user site-packages.
4. **Profile discipline.** Use named profiles (`m1-local`, `dev`) rather than ad-hoc installs.

---

## Profile Quick Reference

| Profile | Command | Contents |
|---------|---------|----------|
| `default` | `uv sync` | Core async, duckdb, lmdb, msgspec, xxhash |
| `m1-local` | `uv sync --extra m1-local --extra dev` | MLX, selectolax, duckdb, pyarrow, rapidfuzz, dev |
| `graph-storage` | `uv sync --extra graph-storage` | duckdb, lancedb, pyarrow, polars |
| `osint-html` | `uv sync --extra osint-html` | selectolax, curl_cffi, h2 |
| `dev` | `uv sync --extra dev` | pytest, ruff, mypy, pytest-asyncio |

### Recommended setup for M1 MacBook

```bash
# Fresh clone / after .venv removal
cd hledac/universal
uv sync --extra m1-local --extra dev
```

### Verify profiles

```bash
# Check default profile
uv run python tools/check_dependency_profiles.py --profile default

# Check m1-local profile
uv run python tools/check_dependency_profiles.py --profile m1-local

# Check all profiles
uv run python tools/check_dependency_profiles.py
```

---

## Drift Detection

Over time, `site-packages` may contain packages that are not tracked by `uv pip list`. This is drift.

### Check for drift

```bash
# Report drift (warn only, does not fail)
uv run python tools/check_dependency_profiles.py --drift

# Report drift and fail if untracked packages found
uv run python tools/check_dependency_profiles.py --strict
```

### Clean drift

If drift is detected and you want a clean state:

```bash
# 1. Remove the entire venv
rm -rf .venv

# 2. Re-sync from lockfile (recreates .venv and installs all tracked packages)
uv sync --extra m1-local --extra dev

# 3. Verify no drift remains
uv run python tools/check_dependency_profiles.py --drift
```

**Why this works:** Removing `.venv` forces a full reinstall from `uv.lock`. Any packages that were `pip install`ed directly (bypassing uv) are gone.

---

## Adding a Dependency

### Production dependency

```bash
uv add package-name
```

This updates `pyproject.toml` and regenerates `uv.lock`.

### Development dependency

```bash
uv add --dev package-name
```

### Optional extra dependency

```bash
uv add --extra extra-name package-name
```

Or edit `pyproject.toml` directly, then run:

```bash
uv lock --no-sync
uv sync
```

---

## Removing a Dependency

```bash
uv remove package-name
```

Or edit `pyproject.toml`, then:

```bash
uv lock --no-sync
uv sync
```

---

## Why No Global pip?

### Problems with `pip install`

- **No lockfile.** pip does not update `uv.lock`, creating a permanent gap between declared and actual dependencies.
- **No audit trail.** Direct installs are invisible to `uv pip list` and break `check_dependency_profiles.py --drift`.
- **No profile isolation.** A package installed with `pip install` appears in all profiles equally.
- **M1 wheel incompatibility.** pip may install x86_64 wheels that do not work on Apple Silicon.

### What happens if you pip install

```
uv pip list          # does NOT show the package
.venv/site-packages/ # package IS present
```

This creates drift. The `check_dependency_profiles.py --drift` check detects this and reports it.

---

## Lockfile Integrity

### Verify lockfile matches pyproject.toml

```bash
uv lock --check
```

If this fails, `pyproject.toml` and `uv.lock` are out of sync. Fix with:

```bash
uv lock
```

### Update dependencies safely

```bash
# Preview what would change
uv lock --check

# Apply updates
uv lock
uv sync
```

---

## Verification Commands

Run these after any dependency change:

```bash
# 1. Lockfile sanity
uv lock --check

# 2. Profile smoke checks (all profiles)
uv run python tools/check_dependency_profiles.py

# 3. Drift check
uv run python tools/check_dependency_profiles.py --drift

# 4. Full test suite
pytest hledac/universal/ -q
```

---

## Troubleshooting

### "package not found" after uv sync

1. Check the package is in `pyproject.toml` (correctly spelled, correct version spec)
2. Run `uv lock` to regenerate lockfile
3. Run `uv sync` again

### Import errors after uv sync

1. Verify the package is in the correct `[project.optional-dependencies]` section
2. Check you synced the correct profile (e.g., `--extra osint-html` for `selectolax`)
3. Try a clean reinstall: `rm -rf .venv && uv sync --extra m1-local --extra dev`

### Drift detected but can't remove .venv

If you cannot remove `.venv` (e.g., it contains large cached model files):

```bash
# Find untracked packages
uv run python tools/check_dependency_profiles.py --drift

# Manually identify which are safe to remove
# (never remove: mlx*, transformers, torch — these may be large cached models)

# For each untracked package that is NOT a cached model:
uv pip uninstall package-name
```