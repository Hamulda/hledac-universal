# Dependency Profiles

UV dependency profiles as defined in `pyproject.toml`. Each profile is a
curated combination of extras targeting a specific use case.

## Smoke Check Script

```bash
# Run all profiles
uv run python tools/check_dependency_profiles.py

# Run specific profile
uv run python tools/check_dependency_profiles.py --profile default --profile m1-local -v
```

The smoke check script:
- Does NOT install packages (reads current uv environment)
- Does NOT make network requests
- Does NOT launch browsers
- Does NOT load MLX models
- Does NOT import torch in default profile

## Profiles

### default
Core deps only — no torch, no browser binary, no heavy extras.

```bash
uv sync
uv run python -c "import aiohttp, duckdb, lmdb, msgspec, xxhash, ahocorasick; print('default OK')"
```

Key invariants:
- `torch` must NOT be importable
- DuckDB, LMDB, msgspec must be present

### m1-local
Ergonomic default for MacBook Air M1 8GB.

```bash
uv sync --extra m1-local --extra dev
uv run python -c "import mlx, selectolax, duckdb, pyarrow, rapidfuzz; print('m1-local OK')"
```

Includes: `apple-accel`, `osint-html`, `graph-storage`, `acceleration`, `transport`.

### browser
Full JS rendering stack (import smoke only, no browser launch).

```bash
uv sync --extra browser
# import smoke only
```

### graph-storage
Columnar analytics stack.

```bash
uv sync --extra graph-storage
uv run python -c "import duckdb, lancedb, pyarrow, polars; print('graph-storage OK')"
```

### no-torch-in-default
Guard: default environment must not have torch importable.

```bash
uv run python -c "import importlib.util; spec = importlib.util.find_spec('torch'); sys.exit(0 if spec is None else 1)"
```

## Adding a New Profile

1. Add the extra to `[project.optional-dependencies]` in `pyproject.toml`
2. Add a `ProfileCheck` entry in `tools/check_dependency_profiles.py`
3. Document the profile in this file
4. Run smoke checks: `uv run python tools/check_dependency_profiles.py --profile <name>`