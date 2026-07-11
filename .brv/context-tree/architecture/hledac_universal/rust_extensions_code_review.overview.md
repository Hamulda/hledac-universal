**Key Points:**
- Fixed OnceLock compile error by storing module name as OnceLock<&str> and re-importing via py.import() on each call
- Patched SQL injection risk by replacing manual string escaping with DuckDB native parameterized queries (stmt.query with &dyn ToSql)
- Identified pool_run.rs GIL safety warning as false positive — #[pyfunction] macro already ensures GIL is held
- Documented 87 pre-existing compile errors as unrelated PyClass Frozen=False mismatches
- Verified fixes with cargo check showing only deprecation warnings on edited files

**Structure:**
- Reason: Documents purpose of the code review
- Raw Concept: Task, changes, files, flow, timestamp
- Narrative: Three-issue breakdown with structure, highlights, and rules
- Facts: Key facts with project-scoped tags

**Notable Entities & Patterns:**
- Files: adaptive_scheduler.rs, async_query.rs, pool_run.rs, bloom.rs, dedup_bloom.rs, graph_cache.rs, lancedb_bridge.rs
- Key constraint: OnceLock<T> requires T: Sync + Send, but Bound<PyModule> contains *mut Python (not Sync/Send)
- Pattern: Python module import is idempotent — safe to re-import
- Rule: Manual string escaping for SQL is never sufficient — always use parameterized queries
- Macro guarantee: #[pyfunction] ensures GIL is held on function entry