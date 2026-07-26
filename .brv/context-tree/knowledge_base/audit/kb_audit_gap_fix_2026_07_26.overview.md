<key_points>
- Naming/Error-handling conventions: RESOLVED - documented in CLAUDE.md with coding invariants (snake_case, no bare except, asyncio.gather return_exceptions=True)
- Integrations: OPEN - scattered across DuckDB/LMDB/LanceDB, MLX/Hermes3, curl_cffi/httpx/aioquic, Tor/I2P/SOCKS
- probe_tests: OPEN - .brv/context-tree/testing/probe_tests/ is EMPTY, recommend delete or populate
- Exit codes: RESOLVED - tests/test_exit_codes.py with 242 lines + smoke_runner.py exist
</key_points>
<structure>
KB audit gap fix addressing 4 open items from previous audits. Sections: Raw Concept (Task, Changes, Files, Flow, Timestamp), Narrative (Structure, Dependencies, Highlights). Depends on CLAUDE.md for naming conventions.
</structure>
<entities>
CLAUDE.md, tests/test_exit_codes.py (242 lines), smoke_runner.py, tests/probe_p_e2_feed_pipeline/
</entities>
<patterns>
asyncio.gather with return_exceptions=True, mx.eval before clear_cache, RotatingBloomFilter for URL dedup, sidecars return [] on errors
</patterns>
<decisions>
Delete or populate empty probe_tests directory; consolidate integrations documentation into dedicated docs/integrations/ directory
</decisions>