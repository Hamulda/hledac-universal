# LMDB Write Contract

## Metadata

- **Entry Path:** contracts/lmdb-write-contract
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** contract

## Summary

Canonical LMDB write patterns for zero-copy and bulk operations.

## Contracts

### Zero-Copy Read

```python
# Read directly from buffer - NO bytes() conversion
with env.begin() as txn:
    val = txn.get(key)
    # Use val directly - bytes conversion destroys zero-copy
```

### Bulk Write

```python
# Use putmany() - NEVER per-item in loop
with env.begin(write=True) as txn:
    txn.putmany([(k1, v1), (k2, v2), ...])
```

### Context Manager

```python
from paths import open_lmdb

with open_lmdb() as env:
    # Use env here
```

## Anti-Patterns

- `bytes(lmdb_buffer)` - destroys zero-copy
- Per-item `env.begin(write=True)` in loop
