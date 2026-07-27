---
consolidated_at: '2026-07-27T13:43:23.753Z'
---
10 critical invariants for M1 8GB stability: asyncio.gather with return_exceptions, mx.eval([]) before clear_cache, no time.sleep in async, DuckDB via async_ingest_findings_batch, LMDB via cursor.putmulti, RotatingBloomFilter for dedup, M1 Metal cache dynamic formula (1GiB ceiling on 8GB), fail-safe sidecar return [], no bare except.