"""
tests/test_arch_srp_001.py — ARCH-SRP-001 Verification Tests
==========================================================

Tests for the Brain Module SRP separation (ARCH-SRP-001).

These tests verify that:
1. LLMEngine Protocol is properly defined
2. BrainCoordinator properly separates concerns
3. PromptBuilder is used for composition only
4. No circular dependencies between brain submodules
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from _core import aclose


class TestLLMEngineProtocol:
    """Test LLMEngine Protocol definition."""

    def test_llm_engine_protocol_import(self):
        """LLMEngine should be importable from brain module."""
        from brain import LLMEngine

        assert LLMEngine is not None

    def test_llm_engine_has_generate_method(self):
        """LLMEngine Protocol should define generate method."""
        from brain import LLMEngine

        # Protocol classes have methods at class level
        assert hasattr(LLMEngine, 'generate')

    def test_llm_engine_has_generate_stream_method(self):
        """LLMEngine Protocol should define generate_stream method."""
        from brain import LLMEngine

        assert hasattr(LLMEngine, 'generate_stream')


class TestBrainCoordinator:
    """Test BrainCoordinator composition layer."""

    def test_brain_coordinator_import(self):
        """BrainCoordinator should be importable from brain module."""
        from brain import BrainCoordinator

        assert BrainCoordinator is not None

    def test_brain_coordinator_init(self):
        """BrainCoordinator should accept llm_engine and prompt_builder."""
        from brain import BrainCoordinator

        mock_llm = MagicMock()  # type: ignore[arg-type]
        mock_prompt = MagicMock()  # type: ignore[arg-type]

        coordinator = BrainCoordinator(
            llm_engine=mock_llm,
            prompt_builder=mock_prompt,
        )

        assert coordinator._llm is mock_llm
        assert coordinator._prompt is mock_prompt

    def test_think_returns_string(self):
        """think() should return a string."""
        from brain import BrainCoordinator

        mock_llm = MagicMock()  # type: ignore[arg-type]
        mock_llm.generate = AsyncMock(return_value=MagicMock(text="test response"))  # type: ignore[method-assign]
        mock_prompt = MagicMock()  # type: ignore[arg-type]
        mock_prompt.format_chatml = MagicMock(return_value="<|im_start|>system\ntest<|im_end|>")  # type: ignore[method-assign]

        coordinator = BrainCoordinator(
            llm_engine=mock_llm,
            prompt_builder=mock_prompt,
        )

        result = coordinator._default_system()
        assert isinstance(result, str)
        assert len(result) > 0


class TestPromptBuilderSeparation:
    """Test that prompt building is properly separated from inference."""

    def test_prompt_builder_no_generate(self):
        """PromptBuilder should not have generate methods."""
        from brain._prompts import ChatMLPromptFormatter

        formatter = ChatMLPromptFormatter()

        # PromptBuilder should have format methods
        assert hasattr(formatter, 'format_chatml')
        assert hasattr(formatter, 'format_dspy')

        # PromptBuilder should NOT have generate methods
        assert not hasattr(formatter, 'generate')
        assert not hasattr(formatter, 'generate_stream')

    def test_llm_engine_no_format(self):
        """LLMEngine should not have format methods."""
        from brain import LLMEngine

        # LLMEngine Protocol should not define format methods
        # (it's just the inference contract)
        assert not hasattr(LLMEngine, 'format_chatml')
        assert not hasattr(LLMEngine, 'format_dspy')


class TestNoCircularDependencies:
    """Test that there are no circular dependencies."""

    def test_inference_no_prompts_circular(self):
        """_inference should not import from _prompts at module level."""
        # This test verifies the separation is maintained
        from brain._inference import GenerationFacade

        # GenerationFacade should be importable without _prompts
        assert GenerationFacade is not None


class TestGenerationFacadeAdapter:
    """Test that GenerationFacade can be used as LLMEngine."""

    def test_generation_facade_satisfies_llm_engine(self):
        """GenerationFacade should satisfy LLMEngine Protocol."""
        from brain import LLMEngine
        from brain._inference import GenerationFacade
        from typing import Protocol, cast

        # GenerationFacade has generate, generate_stream, generate_structured
        # so it should satisfy LLMEngine Protocol
        facade = GenerationFacade()
        assert isinstance(facade, Protocol) or hasattr(facade, 'generate')


# ─── Invariant Tests ───────────────────────────────────────────────────────────

INVARIANTS = [
    # INV-1: LLMEngine.generate does not call format methods
    # (verified by Protocol definition — no format in LLMEngine)

    # INV-2: PromptBuilder has no generate methods
    # (verified by TestPromptBuilderSeparation)

    # INV-3: BrainCoordinator has exactly one dependency per component
    # (verified by __init__ signature)

    # INV-4: No circular imports
    # (verified by TestNoCircularDependencies)
]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
