<key_points>
- Naming/Error-handling conventions: EXISTS in CLAUDE.md - snake_case, no bare except, asyncio.gather return_exceptions=True
- Integrations: NO dedicated docs/integrations/ directory - scattered across DuckDB/LMDB/LanceDB, MLX/Hermes3, curl_cffi/httpx/aioquic, Tor/I2P/SOCKS
- probe_tests: EMPTY directory at .brv/context-tree/testing/probe_tests/ - tests/probe_p_e2_feed_pipeline/ not documented
- Exit codes: ALREADY HAS CONTENT - tests/test_exit_codes.py (6 tests) + smoke_runner.py
</key_points>
<structure>
KB audit gap tracker with 4 items. Narrative sections cover Structure, Dependencies, Highlights. Requires periodic review to consolidate integrations documentation.
</structure>
<entities>
CLAUDE.md, tests/test_exit_codes.py (6 tests), smoke_runner.py, .brv/context-tree/testing/probe_tests/
</entities>
<patterns>
asyncio.gather with return_exceptions=True, no time.sleep in async, DuckDB writes via async_ingest_findings_batch, LMDB bulk via cursor.putmulti
</patterns>
<decisions>
Requires periodic review to consolidate integrations documentation into dedicated directory
</decisions>