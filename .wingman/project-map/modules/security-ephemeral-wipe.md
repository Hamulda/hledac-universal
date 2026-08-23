# security-ephemeral-wipe

**Type:** Security Layer  
**Path:** `security/ephemeral_wipe.py`  
**Status:** current

## Purpose

Secure deletion of ephemeral data. Overwrites memory before release.

## Key Functions

| Function | Purpose |
|----------|---------|
| `secure_delete(buffer)` | Overwrite buffer with zeros |
| `wipe_file(path)` | Secure file deletion |
| `memory_pressure_wipe()` | Wipe on memory pressure |

## Invariants

- [SEW-1] Overwrite pattern: zeros (M1 optimized)
- [SEW-2] File deletion: overwrite then unlink
- [SEW-3] Called on session cleanup

## M1 Memory Notes

Uses `madvise(MADV_FREE_REUSABLE, 7)` for memory reclamation.
