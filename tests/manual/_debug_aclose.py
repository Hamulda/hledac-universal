#!/usr/bin/env python3
"""Debug script for DuckDBShadowStore aclose() — uses public API only."""
import asyncio
import tempfile
from pathlib import Path

from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

td = Path(tempfile.mkdtemp(prefix="htest_"))
s = DuckDBShadowStore.for_testing(name="x", temp_dir=str(td))
loop = asyncio.new_event_loop()
try:
    loop.run_until_complete(s.async_initialize())
finally:
    loop.close()

print("Store initialized:", s.is_initialized())
print("DB path:", s.db_path())

# Verify public API works
loop2 = asyncio.new_event_loop()
try:
    loop2.run_until_complete(s.aclose())
finally:
    loop2.close()

print("Store closed:", s.is_closed())
