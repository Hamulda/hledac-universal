# Fetch Pipeline

## Metadata

| Field | Value |
| --- | --- |
| Kind | flow |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `flows/fetch-pipeline.md` |

## Summary

URL fetch flow from FetchCoordinator through curl_cffi to evidence creation.

## Flow

```
FetchCoordinator
  └─→ curl_cffi_fetch.py (canonical transport)
        ├─→ JA3 profile rotation (6 profiles)
        ├─→ Per-host LRU session cache
        └─→ HTTP/3 Alt-Svc prewarm
  └─→ Evidence creation
  └─→ DuckDB storage
```

## Evidence

- FetchCoordinator implements start/step/shutdown
- curl_cffi → FAIL FAST (never httpx)
- Evidence creation and storage after successful fetch

## Use When

- Understanding how URLs are fetched and stored
- Debugging fetch transport issues

## Do Not Use When

- Understanding stealth/CAPTCHA handling (see stealth-fetch feature)
