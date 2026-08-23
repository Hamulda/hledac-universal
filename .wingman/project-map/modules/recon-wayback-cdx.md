# recon-wayback-cdx

**Type:** Recon Lane  
**Path:** `recon/wayback_cdx.py`  
**Status:** current

## Purpose

Wayback Machine CDX API client for historical URL discovery.

## Key Functions

| Function | Purpose |
|----------|---------|
| `WaybackCDX` | Main class |
| `get_snapshots(url)` | Get all snapshots |
| `get_diff(old, new)` | Diff between snapshots |
| `crawl_snapshot(timestamp, url)` | Fetch historical version |

## CDX Fields

| Field | Description |
|-------|-------------|
| original | Original URL |
| timestamp | YYYYMMDDHHMMSS |
| statuscode | HTTP status |
| digest | Content hash |
| length | Content length |

## Invariants

- [RWB-1] No CDX caching: always fresh query
- [RWB-2] Max results: 100K per query
- [RWB-3] Filter: statuscode:200 for success

## Dependencies

- `waybackpy` or direct CDX API
