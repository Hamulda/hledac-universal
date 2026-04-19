"""
Sprint 8VI §F: Parity smoke test — export_sprint() je wireovaný a běží bez scheduler._ioc_graph.
"""
import pathlib
import tempfile
import pytest


@pytest.mark.asyncio
async def test_export_sprint_wired_parity():
    """
    Parity smoke: export_sprint() musí vytvořit:
      1. report JSON (ne 'None', ne prázdný)
      2. seeds JSON (ne 'None', ne prázdný)

    Signature: export_sprint(store, scorecard, sprint_id)
    Data source: scorecard["top_graph_nodes"] (ne scheduler._ioc_graph)
    """
    import sys
    import os  # noqa: F401

    # Patch SPRINT_STORE_ROOT before import
    with tempfile.TemporaryDirectory() as tmpdir:
        from paths import SPRINT_STORE_ROOT
        orig_root = SPRINT_STORE_ROOT

        # Create fake SPRINT_STORE_ROOT
        fake_sprint_root = pathlib.Path(tmpdir) / "sprints"
        fake_sprint_root.mkdir(parents=True, exist_ok=True)

        # Patch in paths module
        import paths
        paths.SPRINT_STORE_ROOT = fake_sprint_root

        try:
            from export.sprint_exporter import export_sprint

            # Scorecard s top_graph_nodes (windup phase seam)
            scorecard = {
                "sprint_id": "test_sprint_001",
                "ts": 1743532800.0,
                "top_graph_nodes": [
                    {"value": "evil.com", "ioc_type": "domain", "confidence": 0.9, "degree": 10},
                    {"value": "c2.bad actor.net", "ioc_type": "domain", "confidence": 0.85, "degree": 7},
                ],
                "findings_per_minute": 2.5,
                "accepted_findings_count": 15,
                "peak_rss_mb": 512.0,
                "phase_duration_seconds": {"warmup": 5.0, "active": 60.0, "windup": 10.0},
            }

            # Store = None — export_sprint musí fungovat i bez store (scorecard seam)
            result = await export_sprint(None, scorecard, "test_sprint_001")

            # P0 guard: žádné 'None' soubory
            assert result.get("report_json") not in ("", "None"), \
                f"P0: report_json is {result.get('report_json')!r} — broken path"
            assert result.get("seeds_json") not in ("", "None"), \
                f"P0: seeds_json is {result.get('seeds_json')!r} — broken path"

            report_path = pathlib.Path(result["report_json"])
            seeds_path = pathlib.Path(result["seeds_json"])

            # Soubory musí existovat
            assert report_path.exists(), f"report JSON not created: {report_path}"
            assert seeds_path.exists(), f"seeds JSON not created: {seeds_path}"

            # Neprázdné
            assert report_path.stat().st_size > 0, "report JSON is empty"
            assert seeds_path.stat().st_size > 0, "seeds JSON is empty"

            # Seeds JSON musí obsahovat seed tasky (ne prázdný list)
            import json
            seeds_data = json.loads(seeds_path.read_text())
            assert isinstance(seeds_data, list), f"seeds_data is {type(seeds_data)}, expected list"
            assert len(seeds_data) > 0, "seeds JSON is empty list — no seed tasks generated"
            # Každý seed má task_type a value
            for seed in seeds_data:
                assert "task_type" in seed, f"seed missing task_type: {seed}"
                assert "value" in seed, f"seed missing value: {seed}"

        finally:
            paths.SPRINT_STORE_ROOT = orig_root


@pytest.mark.asyncio
async def test_export_sprint_store_fallback():
    """
    Test COMPAT BRIDGE: pokud scorecard["top_graph_nodes"] chybí,
    export_sprint() zkusí store._ioc_graph.get_top_nodes_by_degree(n=5).
    """
    import tempfile
    import pathlib
    from unittest.mock import MagicMock
    import paths

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_sprint_root = pathlib.Path(tmpdir) / "sprints"
        fake_sprint_root.mkdir(parents=True, exist_ok=True)
        orig_root = paths.SPRINT_STORE_ROOT
        paths.SPRINT_STORE_ROOT = fake_sprint_root

        try:
            from export.sprint_exporter import export_sprint

            # Scorecard BEZ top_graph_nodes
            scorecard = {
                "sprint_id": "test_fallback_001",
                "ts": 1743532800.0,
                # top_graph_nodes chybí!
            }

            # Mock store s get_top_seed_nodes (store-facing seam, ne graph internals)
            mock_store = MagicMock()
            mock_store.get_top_seed_nodes.return_value = [
                {"value": "fallback.com", "ioc_type": "domain", "confidence": 0.8, "degree": 5},
            ]

            result = await export_sprint(mock_store, scorecard, "test_fallback_001")

            # Sprint 8VX §B: fallback používá store.get_top_seed_nodes(), ne _ioc_graph internals
            mock_store.get_top_seed_nodes.assert_called_once_with(n=5)
            assert result.get("seeds_json") not in ("", "None")

            seeds_path = pathlib.Path(result["seeds_json"])
            assert seeds_path.exists()

        finally:
            paths.SPRINT_STORE_ROOT = orig_root


@pytest.mark.asyncio
async def test_export_handoff_direct_construction():
    """
    Sprint 8VZ §B: Canonical producer path uses ExportHandoff(...) directly,
    NOT from_windup(scorecard). top_nodes sourced from store.get_top_seed_nodes().
    """
    import tempfile
    import pathlib
    from unittest.mock import MagicMock
    import paths

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_sprint_root = pathlib.Path(tmpdir) / "sprints"
        fake_sprint_root.mkdir(parents=True, exist_ok=True)
        orig_root = paths.SPRINT_STORE_ROOT
        paths.SPRINT_STORE_ROOT = fake_sprint_root

        try:
            from export.sprint_exporter import export_sprint
            from hledac.universal.project_types import ExportHandoff

            # Mock store with get_top_seed_nodes (canonical source post-8VZ)
            mock_store = MagicMock()
            mock_store.get_top_seed_nodes.return_value = [
                {"value": "top-domain.com", "ioc_type": "domain", "confidence": 0.95, "degree": 15},
                {"value": "second-domain.com", "ioc_type": "domain", "confidence": 0.88, "degree": 12},
            ]

            # Canonical path: direct ExportHandoff construction (post-8VZ)
            # NOT from_windup(scorecard)
            handoff = ExportHandoff(
                sprint_id="test_canonical_001",
                scorecard={"sprint_id": "test_canonical_001", "accepted_findings_count": 42},
                top_nodes=mock_store.get_top_seed_nodes(n=10),
                phase_durations={"warmup": 5.0, "active": 120.0, "windup": 30.0},
            )

            # Verify handoff has top_nodes from store seam (not from scorecard)
            assert len(handoff.top_nodes) == 2
            assert handoff.top_nodes[0]["value"] == "top-domain.com"
            assert handoff.top_nodes[0]["degree"] == 15

            # Verify scorecard is still present (for JSON report compat)
            assert handoff.scorecard["sprint_id"] == "test_canonical_001"

            result = await export_sprint(mock_store, handoff)

            # Verify seeds were generated from top_nodes
            mock_store.get_top_seed_nodes.assert_called_once_with(n=10)
            seeds_path = pathlib.Path(result["seeds_json"])
            assert seeds_path.exists()

            import json
            seeds_data = json.loads(seeds_path.read_text())
            assert len(seeds_data) > 0
            # Seeds come from top_nodes, not from scorecard["top_graph_nodes"]
            values = [s["value"] for s in seeds_data]
            assert "top-domain.com" in values

        finally:
            paths.SPRINT_STORE_ROOT = orig_root


@pytest.mark.asyncio
async def test_from_windup_still_works_for_legacy():
    """
    Sprint 8VZ §B: from_windup(scorecard) still works for legacy compat callers.
    This is the COMPAT-ONLY path, not the canonical one.
    """
    import tempfile
    import pathlib
    import paths

    with tempfile.TemporaryDirectory() as tmpdir:
        fake_sprint_root = pathlib.Path(tmpdir) / "sprints"
        fake_sprint_root.mkdir(parents=True, exist_ok=True)
        orig_root = paths.SPRINT_STORE_ROOT
        paths.SPRINT_STORE_ROOT = fake_sprint_root

        try:
            from export.sprint_exporter import export_sprint
            from hledac.universal.project_types import ExportHandoff

            # Legacy scorecard with top_graph_nodes (old windup path)
            legacy_scorecard = {
                "sprint_id": "legacy_001",
                "top_graph_nodes": [
                    {"value": "legacy.com", "ioc_type": "domain", "confidence": 0.7, "degree": 3},
                ],
                "accepted_findings_count": 10,
            }

            # from_windup still works — COMPAT path
            handoff = ExportHandoff.from_windup("legacy_001", legacy_scorecard)
            assert handoff.top_nodes[0]["value"] == "legacy.com"
            assert handoff.synthesis_engine == "unknown"  # default

            result = await export_sprint(None, handoff, "legacy_001")
            assert result.get("report_json") not in ("", "None")

        finally:
            paths.SPRINT_STORE_ROOT = orig_root
