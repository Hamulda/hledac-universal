"""
Test ANE pipelines - Sprint 71
"""
import unittest
from unittest.mock import MagicMock


class TestANEPipelines(unittest.TestCase):
    """Test ANE pipeline functions."""

    def test_compute_safe_batch_size(self):
        """Test batch size computation."""
        from hledac.universal.utils.ane_pipelines import _compute_safe_batch_size

        # Test basic calculation
        result = _compute_safe_batch_size(seq_len=512, hidden=768)
        self.assertGreaterEqual(result, 1)
        self.assertLessEqual(result, 64)

    def test_get_hidden_size_from_model(self):
        """Test hidden size extraction from model."""
        from hledac.universal.utils.ane_pipelines import _get_hidden_size_from_model

        # Test with object that has config
        mock_model = MagicMock()
        mock_model.config.hidden_size = 1024
        result = _get_hidden_size_from_model(mock_model)
        self.assertEqual(result, 1024)

    def test_get_hidden_size_dict_config(self):
        """Test hidden size from dict config."""
        from hledac.universal.utils.ane_pipelines import _get_hidden_size_from_model

        mock_model = MagicMock()
        mock_model.config = {'hidden_size': 512}
        result = _get_hidden_size_from_model(mock_model)
        self.assertEqual(result, 512)

    def test_get_hidden_size_fallback(self):
        """Test hidden size fallback to 768."""
        from hledac.universal.utils.ane_pipelines import _get_hidden_size_from_model

        mock_model = MagicMock()
        mock_model.config = {}
        result = _get_hidden_size_from_model(mock_model)
        self.assertEqual(result, 768)

    def test_estimate_memory_usage(self):
        """Test memory usage estimation."""
        from hledac.universal.utils.ane_pipelines import estimate_memory_usage

        result = estimate_memory_usage(batch_size=8, seq_len=512, hidden=768)
        # 8 * 512 * 768 * 2 = 6,291,456 bytes
        self.assertEqual(result, 6291456)


if __name__ == '__main__':
    unittest.main()
