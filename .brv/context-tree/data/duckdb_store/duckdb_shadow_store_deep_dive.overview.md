**Key Points:**
- DuckDB Shadow Store with 3-tier facts hierarchy (sprint facts, shadow findings, cross-sprint temporal events)
- IOC extraction uses 3-tier fallback chain: batch_ioc_extract_unified_python (zero-copy) → batch_ioc_extract_unified (rayon) → pure Python
- RemoteParquetSource supports S3, HTTPS, Azure, GCS, and Postgres backends via DuckDB ATTACH
- DuckDB chunk config updated to chunk_size=1024, pipeline_maxsize=4 (from 500/2)
- M1 8GB safe settings: threads=2, read memory_limit=1GB, write memory_limit=2GB, hard_memory_limit=1GB
- DuckPGQGraph extension replaces ioc_graph table for IOC storage (F272)

**Structure:**
- Reason: Purpose statement for documentation
- Raw Concept: Task description, IOC extraction flow, timestamp, regex patterns for DuckDB/IOC configuration
- Narrative: Structure overview, dependencies (DuckDB >=1.5, DuckPGQGraph, Rust batch functions), highlights, M1 rules, RemoteParquetSource ATTACH example
- Facts: Key-value pairs with project tags for duckdb_chunk_config, duckdb_pressure_states, ioc_chunk_size, m1_8gb_duckdb settings

**Notable Entities & Decisions:**
- DuckDB >= 1.5 required for ATTACH remote Parquet support
- Pressure states: WARN=768/3, CRITICAL=512/2, EMERGENCY=256/2
- IOC buffering chunk size: _IOC_CHUNK = 128
- M1 settings: gc=False, preserve_insertion_order=false, async duckdb.connect() via asyncio.to_thread()
- Pattern: `PRAGMA threads = [0-9]+`, `SET memory_limit = .*GB`, `_IOC_CHUNK = [0-9]+`