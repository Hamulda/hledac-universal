"""
TestSprintF193ALegacyBoundary — Boundary Quarantine for Legacy Imports
==========================================================================

Tests that canonical sprint path modules do not eagerly drag in the legacy
orchestrator at import time, while the compatibility seam remains accessible
for explicit backward-compatible consumers.

Sprint F193A: boundary quarantine

Invariant tested:
    Canonical sprint imports (knowledge, orchestrator submodules) must not
    require importing the legacy orchestrator just to load.

Allowed compatibility seam:
    Explicit backward-compatible consumers may still access legacy types via
    knowledge.__getattr__ lazy loading (triggered only by explicit attribute
    access, not by module-level imports).
"""

import sys
import warnings


class TestLegacyBoundary:
    """Tests for legacy import boundary quarantine."""

    def test_knowledge_module_imports_without_legacy(self):
        """Canonical knowledge imports succeed without loading legacy world."""
        # Simulate a fresh import by removing from cache
        modules_to_clear = [
            k for k in sys.modules.keys()
            if k.startswith("hledac.universal.knowledge")
            or k.startswith("hledac.universal.legacy")
        ]
        for mod in modules_to_clear:
            sys.modules.pop(mod, None)

        # This must not trigger legacy import
        from hledac.universal.knowledge import (
            KnowledgeGraphLayer,
            GraphRAGOrchestrator,
            KnowledgeGraphBuilder,
            ContextGraph,
            RAGEngine,
        )

        # Verify none of these imported legacy at module level
        assert "hledac.universal.legacy" not in sys.modules, (
            "knowledge package dragged in legacy at import time"
        )

    def test_graph_builder_imports_without_legacy(self):
        """knowledge.graph_builder loads without legacy persistent_layer."""
        modules_to_clear = [
            k for k in sys.modules.keys()
            if k.startswith("hledac.universal.knowledge")
            or k.startswith("hledac.universal.legacy")
        ]
        for mod in modules_to_clear:
            sys.modules.pop(mod, None)

        from hledac.universal.knowledge.graph_builder import KnowledgeGraphBuilder

        # Verify the builder's process_and_store method has lazy legacy access
        builder = KnowledgeGraphBuilder()
        # The legacy types should NOT be loaded yet
        assert "hledac.universal.legacy.persistent_layer" not in sys.modules

    def test_graph_rag_imports_without_legacy_type_check(self):
        """knowledge.graph_rag uses TYPE_CHECKING guard for KnowledgeNode."""
        modules_to_clear = [
            k for k in sys.modules.keys()
            if k.startswith("hledac.universal.knowledge")
            or k.startswith("hledac.universal.legacy")
        ]
        for mod in modules_to_clear:
            sys.modules.pop(mod, None)

        from hledac.universal.knowledge.graph_rag import GraphRAGOrchestrator

        # persistent_layer must not be loaded at import time
        assert "hledac.universal.legacy.persistent_layer" not in sys.modules

    def test_legacy_compat_still_accessible(self):
        """Explicit legacy compat access via knowledge module still works."""
        modules_to_clear = [
            k for k in sys.modules.keys()
            if k.startswith("hledac.universal.knowledge")
            or k.startswith("hledac.universal.legacy")
        ]
        for mod in modules_to_clear:
            sys.modules.pop(mod, None)

        # Import the module (should NOT load legacy yet)
        import hledac.universal.knowledge as knowledge

        # Access a legacy compat name — this SHOULD trigger lazy load
        # and emit a DeprecationWarning
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            # Trigger lazy loading via __getattr__
            PersistentKnowledgeLayer = knowledge.PersistentKnowledgeLayer

            # Should have gotten a deprecation warning
            deprecation_warnings = [
                x for x in w
                if issubclass(x.category, DeprecationWarning)
                and "persistent_layer" in str(x.message)
            ]
            assert len(deprecation_warnings) >= 1, (
                "Expected DeprecationWarning for legacy persistent_layer access"
            )

        # Now legacy should be loaded
        assert "hledac.universal.legacy.persistent_layer" in sys.modules

    def test_orchestrator_init_is_acknowledged_compat_seam(self):
        """orchestrator.__init__ IS the compatibility seam — legacy loads intentionally.

        The orchestrator facade chain (orchestrator/__init__ → autonomous_orchestrator.py
        → legacy/autonomous_orchestrator.py) is an UNAVOIDABLE compatibility seam.
        It EXISTS to provide backward compatibility. This test verifies the seam is
        correctly bounded: canonical sprint path modules (knowledge submodules) do NOT
        drag in legacy unless explicitly accessing the seam.

        This test documents that importing from orchestrator DOES load legacy — which
        is correct and expected behavior for the facade.
        """
        modules_to_clear = [
            k for k in sys.modules.keys()
            if k.startswith("hledac.universal.orchestrator")
            or k.startswith("hledac.universal.legacy")
        ]
        for mod in modules_to_clear:
            sys.modules.pop(mod, None)

        from hledac.universal.orchestrator import (
            FullyAutonomousOrchestrator,
            _ResearchManager,
            _SecurityManager,
        )

        # The orchestrator facade intentionally loads legacy — it IS the seam.
        # This is expected and documented. The key boundary test is that
        # canonical sprint path modules (knowledge submodules) do NOT load legacy
        # unless explicitly accessed via __getattr__.
        # See: test_knowledge_module_imports_without_legacy
        assert "hledac.universal.legacy" in sys.modules, (
            "orchestrator facade should load legacy (it IS the compat seam)"
        )
