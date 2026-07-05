import sys
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

from core.concurrency_registry import ConcurrencyCategory
from knowledge.duckdb_store import DuckDBShadowStore
import inspect

# Check semaphore usage in duckdb_store.py
source = inspect.getsource(DuckDBShadowStore.__init__)
if 'DUCKDB_STORE' in source:
    print("OK: duckdb_store.py uses DUCKDB_STORE semaphore")
else:
    print("WARN: duckdb_store.py still uses GRAPH_RAG or other semaphore")

# Check DUCKDB_STORE enum value
print(f"DUCKDB_STORE value: {ConcurrencyCategory.DUCKDB_STORE.value}")
