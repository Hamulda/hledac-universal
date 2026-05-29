"""
Test MPS Graph - Sprint 71
"""
import unittest


class TestMPSGraph(unittest.TestCase):
    """Test MPSGraph utilities."""

    def test_has_mps_graph(self):
        """Test MPSGraph availability check."""
        from hledac.universal.utils.mps_graph import has_mps_graph
        # Function is callable
        self.assertTrue(callable(has_mps_graph))

    def test_batch_dot_product_fallback(self):
        """Test batch dot product fallback."""
        from hledac.universal.utils.mps_graph import _fallback_dot_product

        query = [1.0, 2.0, 3.0]
        docs = [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]]

        result = _fallback_dot_product(query, docs)
        # 1*1 + 2*2 + 3*3 = 14
        # 1*4 + 2*5 + 3*6 = 32
        self.assertEqual(result, [14.0, 32.0])

    def test_get_metal_memory_info(self):
        """Test Metal memory info retrieval."""
        from hledac.universal.utils.mps_graph import get_metal_memory_info

        result = get_metal_memory_info()
        # Should return dict (empty if not available)
        self.assertIsInstance(result, dict)

    def test_has_ane(self):
        """Test ANE availability check."""
        from hledac.universal.utils.mps_graph import has_ane

        result = has_ane()
        # Should return bool
        self.assertIsInstance(result, bool)


if __name__ == '__main__':
    unittest.main()
