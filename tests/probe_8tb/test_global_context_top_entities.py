"""
Sprint 8TB probe tests — _load_global_context top entities.
Sprint: 8TB
Area: Ghost Global Context
"""

import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hledac.universal.brain.synthesis_runner import SynthesisRunner


class TestGlobalContextTopEntities:
    """Real duckdb with rows → _load_global_context returns formatted string."""

    @pytest.mark.asyncio
    async def test_returns_formatted_entity_string(self):
        """DuckDB returns 3 rows → output string contains entity_value from first row."""
        from unittest.mock import MagicMock
        import duckdb

        mock_lifecycle = MagicMock()
        runner = SynthesisRunner(mock_lifecycle)

        # Create temp dir + ghost_global.duckdb inside db/ subdir
        tmpdir = tempfile.mkdtemp()
        db_dir = os.path.join(tmpdir, "db")
        os.makedirs(db_dir)
        db_path = os.path.join(db_dir, "ghost_global.duckdb")

        try:
            conn = duckdb.connect(db_path)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS global_entities (
                    entity_value TEXT,
                    entity_type TEXT,
                    sprint_count INTEGER,
                    confidence_cumulative FLOAT
                )
            """)
            conn.execute("""
                INSERT INTO global_entities VALUES
                ('evil.com', 'domain', 5, 0.95),
                ('1.2.3.4', 'ipv4', 3, 0.88),
                ('CVE-2024-1', 'cve', 2, 0.75)
            """)
            conn.close()

            # Patch at the import source: hledac.universal.paths
            with patch("hledac.universal.paths.RAMDISK_ROOT", Path(tmpdir)):
                result = await runner._load_global_context()

            assert "evil.com" in result, f"Expected 'evil.com' in result, got: {result!r}"
            assert "domain" in result
            assert "5x" in result
            assert "0.95" in result
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
