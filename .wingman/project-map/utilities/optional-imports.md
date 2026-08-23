# Optional Imports

## Metadata

- **Entry Path:** utilities/optional-imports
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** utility

## Summary

Conditional import system with fallback and lazy evaluation.

## Source Paths

- utils/optional_imports.py

## Usage

```python
from utils.optional_imports import optional

# Simple optional
httpx = optional("httpx")

# With fallback
_instrumented = optional("otel:instrumented",
    default=optional("hledac.universal.otel._instrumentation:instrumented"))

# Conditional check
CAP.mlx_embed = optional("mlx_limnos")
```

## Pattern

Zero-cost until first use - module not imported until called.
