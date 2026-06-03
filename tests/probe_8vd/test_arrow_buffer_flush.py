"""Test Arrow buffer flush to Parquet."""
import asyncio
import pathlib
import tempfile
import sys

import pytest

_universal = pathlib.Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(_universal))

from runtime.sprint_scheduler import SprintScheduler
from unittest.mock import patch

# M1-8GB-friendly: pyarrow is optional. Skip if not installed.
pytest.importorskip("pyarrow")


def test_arrow_batch_flush():
    """Flushing 1001 items creates a parquet file."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = pathlib.Path(tmp)
        store_path = tmp_path / "test_sprint"
        store_path.mkdir(parents=True, exist_ok=True)

        sched = SprintScheduler.__new__(SprintScheduler)
        sched._arrow_batch = []
        sched._arrow_last_flush = 0.0
        sched._duckdb_read_con = None
        sched.sprint_id = "test_sprint"
        sched._ARROW_FLUSH_N = 1000
        sched._ARROW_FLUSH_S = 60.0
        sched._fetch_latency_ema = {}
        sched._config = None
        sched._seen_hashes = {}
        sched._entries_per_source = {}
        sched._hits_per_source = {}
        sched._result = None
        sched._stop_requested = False
        sched._lifecycle = None
        sched._lc_adapter = None
        sched._dedup_env = None
        sched._dedup_seen = set()
        sched._dedup_dirty = False
        sched._source_weights = {}
        sched._novelty_bonuses = {}
        sched._pivot_queue = None
        sched._pivot_stats = {}
        sched._pivot_ioc_graph = None
        sched._bg_tasks = set()
        sched._speculative_results = {}
        sched._last_speculative = 0.0
        sched._ooda_interval = 60.0
        sched._last_ooda = 0.0

        for i in range(1001):
            sched._arrow_batch.append({
                "url": f"http://x{i}.com",
                "title": f"T{i}",
                "snippet": "",
                "source": "test",
                "ioc": None,
                "ioc_type": None,
                "confidence": 0.5,
                "timestamp": None,
                "sprint_id": "test_sprint",
            })

        # Patch at the source module — import is inside the method
        with patch("hledac.universal.paths.get_sprint_parquet_dir",
                   return_value=store_path):
            asyncio.run(sched._maybe_flush_to_parquet())

        parquets = list(store_path.glob("batch_*.parquet"))
        assert len(parquets) >= 1, f"Expected at least 1 parquet, got {len(parquets)}"


if __name__ == "__main__":
    test_arrow_batch_flush()
    print("test_arrow_buffer_flush: PASSED")
