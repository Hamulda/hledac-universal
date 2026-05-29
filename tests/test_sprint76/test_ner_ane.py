"""
Tests for ANE-accelerated NER (Sprint 76).
"""

import asyncio
from unittest.mock import MagicMock

import pytest


class TestNERANE:
    """Test ANE acceleration in NER engine."""

    def test_ane_attributes_exist(self):
        """Test that ANE-related attributes exist."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        assert hasattr(engine, '_nl_available')
        assert hasattr(engine, '_coreml_ner_model')
        assert hasattr(engine, '_ane_predictions')

    def test_ane_prediction_count(self):
        """Test get_ane_prediction_count method."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        assert engine.get_ane_prediction_count() == 0
        engine._ane_predictions = 5
        assert engine.get_ane_prediction_count() == 5

    def test_nl_process_returns_list(self):
        """Test _nl_process_sync returns list (even if NL not available)."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        engine._nl_available = False
        result = engine._nl_process_sync("test text")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_predict_async_ane_first(self):
        """Test predict_async uses ANE-first strategy."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        engine._nl_available = True

        # Mock _nl_process_sync
        engine._nl_process_sync = MagicMock(return_value=[
            {'text': 'John', 'type': 'PERSON', 'confidence': 0.9}
        ])

        result = await engine.predict_async("John works at Google", ["person"])

        assert len(result) == 1
        assert result[0]['text'] == 'John'

    @pytest.mark.asyncio
    async def test_predict_async_coreml_fallback(self):
        """Test predict_async falls back to CoreML."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        engine._nl_available = False
        engine._coreml_ner_model = MagicMock()
        engine._coreml_ner_model.predict = MagicMock(return_value={
            'entities': [{'text': 'Test', 'type': 'ORG'}]
        })

        await engine.predict_async("Test company", [])

        assert engine._ane_predictions == 1

    def test_load_coreml_model_method_exists(self):
        """Test _load_coreml_model method exists."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        assert hasattr(engine, '_load_coreml_model')
        assert asyncio.iscoroutinefunction(engine._load_coreml_model)


class TestNERNLFramework:
    """Test NaturalLanguage framework integration."""

    def test_nl_check_method_exists(self):
        """Test _nl_process_sync exists."""
        from hledac.universal.brain.ner_engine import NEREngine

        engine = NEREngine()
        assert hasattr(engine, '_nl_process_sync')

    def test_nl_available_detection(self):
        """Test NL availability is detected."""
        from hledac.universal.brain.ner_engine import _NL_AVAILABLE

        # Should be boolean
        assert isinstance(_NL_AVAILABLE, bool)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
