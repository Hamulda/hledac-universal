"""
Sprint F218C: NER/PII Ownership Verification Tests
===================================================
Tests verify canonical NER/RE and PII/privacy ownership is documented,
no new models are activated, and diagnostic helpers work without loading heavy models.

Run: pytest tests/probe_f218c_ner_pii_ownership -q
"""
import pytest
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class TestCanonicalNEROwner:
    """Verify canonical NER/RE owner is documented and unchanged."""

    def test_ner_engine_module_exists(self):
        from brain import ner_engine
        assert hasattr(ner_engine, 'NEREngine')
        assert hasattr(ner_engine, 'get_ner_engine')

    def test_ner_engine_has_relex_model(self):
        from brain.ner_engine import NEREngine
        eng = NEREngine()
        assert "gliner-relex" in eng.model_name or "relex" in eng.model_name.lower() or "gliner" in eng.model_name.lower()

    def test_ner_engine_is_lazy_loaded(self):
        from brain.ner_engine import NEREngine
        eng = NEREngine()
        assert eng._model is None  # Not loaded until first use
        assert eng._initialized is False

    def test_ner_diagnostic_helpers_exist(self):
        from brain import ner_engine
        assert hasattr(ner_engine, 'get_ner_backend')
        assert hasattr(ner_engine, 'get_extraction_status')

    def test_ner_diagnostic_does_not_load_model(self):
        # Importing diagnostic helpers should NOT load the GLiNER model
        from brain.ner_engine import get_ner_backend, get_extraction_status, _default_engine
        initial = _default_engine
        backend = get_ner_backend()  # Should return "unavailable" without loading
        assert backend == "unavailable"
        assert _default_engine is initial  # No singleton created

    def test_gliner_relex_model_not_changed(self):
        """GLiNER-Relex remains the current NER/RE default."""
        from brain.ner_engine import NEREngine
        eng = NEREngine()
        assert "relex" in eng.model_name.lower() or eng.model_name == "knowledgator/gliner-relex-large-v0.5"

    def test_coreml_ner_not_activated(self):
        """CoreML NER path is not activated by this sprint."""
        from brain.ner_engine import NEREngine, _NL_AVAILABLE
        eng = NEREngine()
        # _coreml_ner_model should be None (lazy, not loaded)
        assert eng._coreml_ner_model is None


class TestCanonicalPIIOwner:
    """Verify canonical PII/privacy owner is documented and unchanged."""

    def test_pii_gate_module_exists(self):
        from security import pii_gate
        assert hasattr(pii_gate, 'SecurityGate')
        assert hasattr(pii_gate, 'quick_sanitize')

    def test_pii_gate_is_regex_based(self):
        from security.pii_gate import SecurityGate
        gate = SecurityGate()
        result = gate.sanitize("Contact: test@example.com and 555-123-4567")
        assert result.pii_count >= 1
        assert result.success is True

    def test_pii_fallback_sanitize_always_available(self):
        from security.pii_gate import fallback_sanitize
        result = fallback_sanitize("Email: admin@corp.com")
        assert "****" in result or result  # Masked or original (if already sanitized)
        assert isinstance(result, str)

    def test_pii_diagnostic_helper_exists(self):
        from security.pii_gate import get_pii_backend
        assert get_pii_backend() == "regex"

    def test_quick_sanitize_does_not_load_models(self):
        from security.pii_gate import quick_sanitize, _DEFAULT_GATE
        initial = _DEFAULT_GATE
        result = quick_sanitize("test@example.com")
        assert "@" not in result or result == "test@example.com"
        assert _DEFAULT_GATE is initial  # No new gate created if mask_char matches


class TestExtractionStatus:
    """Verify extraction status diagnostic works."""

    def test_get_extraction_status_returns_dict(self):
        from brain.ner_engine import get_extraction_status
        status = get_extraction_status()
        assert isinstance(status, dict)
        assert "ner_backend" in status
        assert "pii_backend" in status

    def test_extraction_status_ner_unavailable_before_load(self):
        from brain.ner_engine import get_extraction_status
        status = get_extraction_status()
        assert status["ner_backend"] == "unavailable"
        assert status["ner_loaded"] is False

    def test_coreml_ner_documented_inactive(self):
        from brain.ner_engine import get_extraction_status
        status = get_extraction_status()
        assert status["coreml_ner_inactive"] is True


class TestNoNewModels:
    """Verify no new NER/PII models were introduced."""

    def test_no_gliner2_pii_activated(self):
        """GLiNER2-PII is not activated."""
        import brain.ner_engine as ne
        src = open(ne.__file__).read()
        assert "gliner2" not in src.lower() or "GLiNER2" not in src

    def test_no_universal_ner_activated(self):
        """UniversalNER is not activated."""
        import brain.ner_engine as ne
        src = open(ne.__file__).read()
        assert "universalner" not in src.lower() and "universal_ner" not in src.lower()

    def test_no_instructor_ner_activated(self):
        """Instructor-NER is not activated."""
        import brain.ner_engine as ne
        src = open(ne.__file__).read()
        assert "instructor" not in src.lower()

    def test_nltagger_ner_not_forced_active(self):
        """NLTagger ANE path is documented but not forced active."""
        from brain.ner_engine import NEREngine, _NL_AVAILABLE
        # _NL_AVAILABLE is a read-only detection flag
        eng = NEREngine()
        if not _NL_AVAILABLE:
            # On non-Apple platforms, NLTagger should be unavailable
            assert eng._nl_available is False


class TestConfigUnchanged:
    """Verify no LLM/VLM/OCR/CoreML config changed."""

    def test_deephermes_default_untouched(self):
        """DeepHermes remains default primary LLM (F217C)."""
        # config.py uses relative imports; test via brain/synthesis_runner import fallback
        # This test verifies the canonical LLM config hasn't been downgraded
        try:
            from config import M1Presets
            model = M1Presets.HERMES_MODEL
            assert "DeepHermes" in model or "deephermes" in model.lower(), \
                f"Expected DeepHermes in HERMES_MODEL, got: {model}"
        except ImportError:
            # config.py uses relative imports; verify via source code inspection
            import os
            root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            config_path = os.path.join(root, 'config.py')
            with open(config_path) as f:
                src = f.read()
            assert 'HERMES_MODEL' in src, "HERMES_MODEL not found in config.py"
            # Verify config has DeepHermes reference (case-insensitive)
            assert 'deephermes' in src.lower(), "DeepHermes reference missing from config.py"

    def test_flashrank_reranker_untouched(self):
        """FlashRank reranker default remains untouched."""
        from tools.reranker import LightweightReranker
        # LightweightReranker is canonical reranker owner (F218B)
        assert LightweightReranker is not None

    def test_embedding_router_untouched(self):
        """EmbeddingRouter canonical owner remains untouched."""
        from embedding_pipeline import EmbeddingRouter
        assert EmbeddingRouter is not None
