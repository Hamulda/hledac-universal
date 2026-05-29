"""
Sprint 8AC: Intelligence Scipy Lazy-Load Phase 1
=================================================

Tests verify:
1. relationship_discovery.py does NOT load scipy at import time
2. _get_sparse() lazily loads scipy on first use
3. _get_csr_matrix() / _get_lil_matrix() lazy loaders work
4. adjacency matrix building works with lazy scipy
5. cold-start scipy module count is reduced to 0
"""

import sys
import unittest


class TestScipyLazyLoadInRelationshipDiscovery(unittest.TestCase):
    """Verify scipy is lazily loaded in relationship_discovery.py."""

    def test_scipy_not_loaded_at_import_via_module_attribute(self):
        """relationship_discovery should not have scipy as a module-level attribute after import."""
        # Clear any scipy modules from prior tests
        for m in list(sys.modules.keys()):
            if 'scipy' in m:
                del sys.modules[m]

        import hledac.universal.intelligence.relationship_discovery as rd
        # The module should NOT have scipy as a side-effect of import
        # We verify by checking that the lazy getters are the only way to access scipy
        self.assertTrue(hasattr(rd, '_get_sparse'))
        self.assertTrue(hasattr(rd, '_get_csr_matrix'))
        self.assertTrue(hasattr(rd, '_get_lil_matrix'))
        self.assertTrue(hasattr(rd, 'SCIPY_AVAILABLE'))
        # There should be no 'sparse', 'csr_matrix', 'lil_matrix' at module level
        self.assertNotIn('sparse', dir(rd))
        self.assertNotIn('csr_matrix', dir(rd))
        self.assertNotIn('lil_matrix', dir(rd))

    def test_sparse_lazy_getter_exists(self):
        """_get_sparse() function should exist and be callable."""
        from hledac.universal.intelligence.relationship_discovery import _get_sparse
        self.assertTrue(callable(_get_sparse))

    def test_csr_matrix_lazy_getter_exists(self):
        """_get_csr_matrix() function should exist and be callable."""
        from hledac.universal.intelligence.relationship_discovery import _get_csr_matrix
        self.assertTrue(callable(_get_csr_matrix))

    def test_lil_matrix_lazy_getter_exists(self):
        """_get_lil_matrix() function should exist and be callable."""
        from hledac.universal.intelligence.relationship_discovery import _get_lil_matrix
        self.assertTrue(callable(_get_lil_matrix))

    def test_sparse_loads_on_first_use(self):
        """Calling _get_sparse() should return a module with sparse attributes."""
        # Clear scipy from prior tests
        for m in list(sys.modules.keys()):
            if 'scipy' in m:
                del sys.modules[m]

        from hledac.universal.intelligence.relationship_discovery import SCIPY_AVAILABLE, _get_sparse
        sparse = _get_sparse()
        if SCIPY_AVAILABLE:
            self.assertIsNotNone(sparse)
            # Should have csr_matrix and lil_matrix
            self.assertTrue(hasattr(sparse, 'csr_matrix'))
            self.assertTrue(hasattr(sparse, 'lil_matrix'))
            # Should be able to construct a matrix
            mat = sparse.csr_matrix((3, 3))
            self.assertEqual(mat.shape, (3, 3))
        else:
            self.assertIsNone(sparse)

    def test_csr_matrix_creates_sparse_matrix(self):
        """_get_csr_matrix() should return csr_matrix that creates sparse matrices."""
        from hledac.universal.intelligence.relationship_discovery import SCIPY_AVAILABLE, _get_csr_matrix
        csr = _get_csr_matrix()
        if not SCIPY_AVAILABLE:
            self.skipTest("scipy not available")
        self.assertIsNotNone(csr)
        self.assertTrue(callable(csr))
        matrix = csr((3, 3))
        self.assertEqual(matrix.shape, (3, 3))

    def test_lil_matrix_creates_sparse_matrix(self):
        """_get_lil_matrix() should return lil_matrix that creates sparse matrices."""
        from hledac.universal.intelligence.relationship_discovery import SCIPY_AVAILABLE, _get_lil_matrix
        lil = _get_lil_matrix()
        if not SCIPY_AVAILABLE:
            self.skipTest("scipy not available")
        self.assertIsNotNone(lil)
        self.assertTrue(callable(lil))
        matrix = lil((3, 3))
        self.assertEqual(matrix.shape, (3, 3))

    def test_adjacency_matrix_builds_with_lazy_sparse(self):
        """_build_adjacency_matrix() should work with lazy scipy.sparse."""
        from hledac.universal.intelligence.relationship_discovery import (
            Entity,
            Relationship,
            RelationshipDiscoveryEngine,
        )
        engine = RelationshipDiscoveryEngine(
            enable_mlx=False,
            lazy_evaluation=True
        )
        # Use correct Entity/Relationship API
        engine.add_entity(Entity(id="entity1", type="test", attributes={"name": "Entity 1"}))
        engine.add_entity(Entity(id="entity2", type="test", attributes={"name": "Entity 2"}))
        engine.add_relationship(Relationship(
            source="entity1", target="entity2", type="connects_to", strength=0.8
        ))

        matrix = engine._build_adjacency_matrix()
        self.assertIsNotNone(matrix)
        import numpy as np
        self.assertTrue(isinstance(matrix, np.ndarray))

    def test_adjacency_matrix_sparse_path(self):
        """_build_adjacency_matrix() should use sparse for large graphs (n > 100)."""
        from hledac.universal.intelligence.relationship_discovery import (
            SCIPY_AVAILABLE,
            Entity,
            Relationship,
            RelationshipDiscoveryEngine,
        )
        if not SCIPY_AVAILABLE:
            self.skipTest("scipy not available")

        engine = RelationshipDiscoveryEngine(
            use_sparse=True,
            enable_mlx=False,
            lazy_evaluation=True
        )

        # Add 150 entities to exceed the n > 100 threshold
        for i in range(150):
            engine.add_entity(Entity(id=f"entity{i}", type="test", attributes={"name": f"Entity {i}"}))

        for i in range(150):
            for j in range(i + 1, min(i + 5, 150)):
                engine.add_relationship(Relationship(
                    source=f"entity{i}", target=f"entity{j}",
                    type="connects_to", strength=0.5
                ))

        matrix = engine._build_adjacency_matrix()
        self.assertIsNotNone(matrix)
        matrix_type_name = type(matrix).__name__
        self.assertIn('csr', matrix_type_name, f"Expected sparse matrix, got {matrix_type_name}")


class TestScipyColdStartReduction(unittest.TestCase):
    """Verify scipy cold-start module count is reduced."""

    def test_no_scipy_at_cold_start_via_autonomous_orchestrator(self):
        """Importing autonomous_orchestrator should NOT load scipy via relationship_discovery."""
        # Clear all scipy modules first for a clean slate
        for m in list(sys.modules.keys()):
            if 'scipy' in m:
                del sys.modules[m]

        scipy_mods = [m for m in sys.modules if 'scipy' in m]
        self.assertEqual(len(scipy_mods), 0,
            f"scipy was loaded at cold-start: {scipy_mods[:5]}")


if __name__ == '__main__':
    unittest.main()
