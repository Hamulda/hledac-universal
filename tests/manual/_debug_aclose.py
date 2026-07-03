#!/usr/bin/env python3
import asyncio
import tempfile
from pathlib import Path

from hledac.universal.knowledge.duckdb_store import DuckDBShadowStore

td = Path(tempfile.mkdtemp(prefix="htest_"))
s = DuckDBShadowStore.for_testing(name="x", temp_dir=str(td))
loop = asyncio.new_event_loop()
loop.run_until_complete(s.async_initialize())
loop.close()

print("Initial pool:", len(s._reader_pool), "in_use:", len(s._readers_in_use))

for i in range(3):
    c = s._acquire_reader()
    print(f"acq {i}: pool={len(s._reader_pool)} in_use={len(s._readers_in_use)} exhaust={s._pool_exhaustion_count} id={id(c)}")

print("Final: pool=%d in_use=%d exhaust=%d" % (len(s._reader_pool), len(s._readers_in_use), s._pool_exhaustion_count))
