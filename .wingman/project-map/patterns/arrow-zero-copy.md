# Arrow Zero-Copy

## Metadata

- **Entry Path:** patterns/arrow-zero-copy
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** pattern

## Summary

DuckDB Arrow C Data Interface for zero-copy data transfer.

## Source Paths

- knowledge/db.py

## Usage

```python
arrow_table = conn.execute(sql).fetch_arrow_table()
return [
    {"col": row["col"]}
    for row in arrow_table.to_pylist()
]
```

## Benefit

M1 8GB: No Python intermediary between DuckDB and result dicts.

## Anti-Pattern

Never use `bytes()` on LMDB buffer - destroys zero-copy.
