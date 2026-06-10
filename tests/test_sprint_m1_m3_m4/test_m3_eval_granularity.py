"""
Sprint M3 — Granular eval/clear during token streaming.

Verifies that Hermes3Engine._stream_tokens issues mx.eval([]) every
EVAL_GRANULARITY_TOKENS (50) and mx.metal.clear_cache() every
CLEAR_GRANULARITY_TOKENS (200) when Metal memory pressure is high.
Fail-soft: any mlx exception during eval/clear must NOT break the stream.

Tests invoke the unbound `_stream_tokens` method directly with a
minimal fake `self` — no model load required, hermetic, fast.
"""

from __future__ import annotations

import os
import sys
import types
import unittest
from unittest.mock import MagicMock

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _setup_mlx_stubs() -> tuple[types.ModuleType, MagicMock]:
    """Stub mlx_lm + mlx.core so hermes3_engine imports succeed and
    stream_generate / make_prompt_cache are mockable.

    Returns (hermes_mod, mx_mock) where mx_mock is the patched
    mlx.core — assign attributes on it to control eval/clear behaviour.

    IMPORTANT: `import mlx.core as _m3_mx` in the test target binds to
    sys.modules["mlx.core"]. But Python's import of a dotted name
    first calls __import__('mlx.core') which returns the top-level
    'mlx' module; then `mlx.core` is resolved via attribute access.
    So we MUST also set sys.modules['mlx'].core = mx_mock to make the
    import bind to the right object.
    """
    if "mlx_lm" not in sys.modules:
        mlx_lm_stub = types.ModuleType("mlx_lm")
        mlx_lm_stub.load = MagicMock()
        mlx_lm_stub.generate = MagicMock()
        mlx_lm_stub.utils = MagicMock()
        sys.modules["mlx_lm"] = mlx_lm_stub
    if "mlx_lm.models" not in sys.modules:
        sys.modules["mlx_lm.models"] = types.ModuleType("mlx_lm.models")
    if "mlx_lm.models.cache" not in sys.modules:
        cache_mod = types.ModuleType("mlx_lm.models.cache")
        cache_mod.make_prompt_cache = MagicMock(return_value=MagicMock())
        sys.modules["mlx_lm.models.cache"] = cache_mod
    if "mlx" not in sys.modules:
        sys.modules["mlx"] = types.ModuleType("mlx")
    if "mlx.core" not in sys.modules:
        sys.modules["mlx.core"] = types.ModuleType("mlx.core")

    from hledac.universal.brain import hermes3_engine as hermes_mod  # type: ignore

    # Default mx_mock: low memory, no clear
    mx_mock = MagicMock(name="mlx.core")
    mx_mock.eval = MagicMock()
    mx_mock.metal = MagicMock()
    mx_mock.metal.clear_cache = MagicMock()
    mx_mock.metal.get_active_memory = MagicMock(return_value=0)
    mx_mock.get_active_memory = MagicMock(return_value=0)
    sys.modules["mlx.core"] = mx_mock
    # Set as attribute of mlx so `import mlx.core as _X` resolves correctly
    sys.modules["mlx"].core = mx_mock

    return hermes_mod, mx_mock


class _FakeGenerationToken:
    """Mimics mlx_lm stream_generate GenerationToken with .text attr."""

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text


def _stream_n_tokens(n: int):
    """Yield n fake tokens (mix of object + tuple to exercise both paths)."""
    for i in range(n):
        if i % 3 == 0:
            yield _FakeGenerationToken(f"t{i}")
        else:
            yield (f"t{i}", None)


def _make_fake_self(hermes_mod):
    """Build a MagicMock instance whose ._model / ._tokenizer are real
    MagicMocks, suitable for binding the real _stream_tokens method."""
    fake = MagicMock()
    fake._model = MagicMock()
    fake._tokenizer = MagicMock()
    return fake


class TestM3ModuleConstants(unittest.TestCase):
    """M3 module-level invariants: intervals are bounded and M1-safe."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod, _ = _setup_mlx_stubs()

    def test_m3_1_eval_granularity_bounded(self) -> None:
        v = int(self.mod.EVAL_GRANULARITY_TOKENS)
        self.assertGreaterEqual(v, 1)
        self.assertLessEqual(v, 256)

    def test_m3_2_clear_granularity_bounded(self) -> None:
        eval_v = int(self.mod.EVAL_GRANULARITY_TOKENS)
        clear_v = int(self.mod.CLEAR_GRANULARITY_TOKENS)
        self.assertGreaterEqual(clear_v, eval_v)
        self.assertLessEqual(clear_v, 4096)

    def test_m3_3_pressure_threshold_bounded(self) -> None:
        v = int(self.mod.M3_METAL_PRESSURE_BYTES)
        self.assertGreaterEqual(v, 1 * 1024 * 1024 * 1024)
        self.assertLessEqual(v, 3 * 1024 * 1024 * 1024)

    def test_m3_4_clear_is_multiple_of_eval(self) -> None:
        eval_v = int(self.mod.EVAL_GRANULARITY_TOKENS)
        clear_v = int(self.mod.CLEAR_GRANULARITY_TOKENS)
        self.assertEqual(clear_v % eval_v, 0)


class TestM3StreamGranularity(unittest.TestCase):
    """M3: _stream_tokens must call eval at the right cadence."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod, _ = _setup_mlx_stubs()

    def _run_stream(self, n_tokens: int, mx_mock, hermes_mod=None):
        """Invoke real _stream_tokens with a fake self; return list of
        yielded tokens + the patched mx_mock for assertion."""
        hermes_mod = hermes_mod or self.mod
        fake_self = _make_fake_self(hermes_mod)
        # Override stream_generate in sys.modules
        mlx_lm_mod = sys.modules["mlx_lm"]
        mlx_lm_mod.stream_generate = MagicMock(return_value=_stream_n_tokens(n_tokens))
        # Patch mlx.core in BOTH sys.modules["mlx.core"] AND as an
        # attribute of sys.modules["mlx"] — Python's import of dotted
        # names resolves via both paths.
        sys.modules["mlx.core"] = mx_mock
        sys.modules["mlx"].core = mx_mock

        # Direct unbound call — bypasses MagicMock's auto-binding
        stream_fn = hermes_mod.Hermes3Engine._stream_tokens
        try:
            tokens = list(stream_fn(fake_self, "hello", max_tok=10, temp=0.1))
        except Exception as e:
            import traceback
            print(f"DEBUG _run_stream exception: {e!r}")
            traceback.print_exc()
            raise
        return tokens, mx_mock

    def test_m3_5_eval_called_at_eval_interval(self) -> None:
        """For 200 tokens, expect 4 eval calls (200/50)."""
        mx_mock = MagicMock()
        mx_mock.eval = MagicMock()
        mx_mock.metal = MagicMock()
        mx_mock.metal.clear_cache = MagicMock()
        mx_mock.metal.get_active_memory = MagicMock(return_value=0)
        mx_mock.get_active_memory = MagicMock(return_value=0)
        tokens, mx_mock = self._run_stream(200, mx_mock)
        self.assertEqual(mx_mock.eval.call_count, 4)
        self.assertEqual(len(tokens), 200)

    def test_m3_6_clear_called_only_under_pressure(self) -> None:
        """Clear is gated on memory > threshold."""
        # Low memory → no clear
        mx_low = MagicMock()
        mx_low.eval = MagicMock()
        mx_low.metal = MagicMock()
        mx_low.metal.clear_cache = MagicMock()
        mx_low.metal.get_active_memory = MagicMock(
            return_value=self.mod.M3_METAL_PRESSURE_BYTES - 1
        )
        mx_low.get_active_memory = MagicMock(
            return_value=self.mod.M3_METAL_PRESSURE_BYTES - 1
        )
        _, mx_low = self._run_stream(250, mx_low)
        self.assertEqual(mx_low.metal.clear_cache.call_count, 0)

        # High memory → clear at token 200 (200 % 200 == 0)
        mx_high = MagicMock()
        mx_high.eval = MagicMock()
        mx_high.metal = MagicMock()
        mx_high.metal.clear_cache = MagicMock()
        mx_high.metal.get_active_memory = MagicMock(
            return_value=self.mod.M3_METAL_PRESSURE_BYTES + 1
        )
        mx_high.get_active_memory = MagicMock(
            return_value=self.mod.M3_METAL_PRESSURE_BYTES + 1
        )
        _, mx_high = self._run_stream(250, mx_high)
        self.assertEqual(mx_high.metal.clear_cache.call_count, 1)

    def test_m3_7_yields_all_tokens(self) -> None:
        mx_mock = MagicMock()
        mx_mock.eval = MagicMock()
        mx_mock.metal = MagicMock()
        mx_mock.metal.clear_cache = MagicMock()
        mx_mock.metal.get_active_memory = MagicMock(return_value=0)
        mx_mock.get_active_memory = MagicMock(return_value=0)
        tokens, _ = self._run_stream(10, mx_mock)
        self.assertEqual(len(tokens), 10)
        # First 3 should be t0,t1,t2 (mix of shapes)
        self.assertEqual(tokens[0], "t0")

    def test_m3_8_fail_soft_on_eval_exception(self) -> None:
        """mx.eval throws → stream keeps yielding, no crash."""
        mx_mock = MagicMock()
        mx_mock.eval = MagicMock(side_effect=RuntimeError("mlx down"))
        mx_mock.metal = MagicMock()
        mx_mock.metal.clear_cache = MagicMock()
        mx_mock.metal.get_active_memory = MagicMock(return_value=0)
        mx_mock.get_active_memory = MagicMock(return_value=0)
        tokens, _ = self._run_stream(80, mx_mock)
        # All 80 tokens yielded despite eval exception
        self.assertEqual(len(tokens), 80)


class TestM3MetalAPICompat(unittest.TestCase):
    """M3: handle MLX API drift gracefully (mx vs mx.metal memory API)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod, _ = _setup_mlx_stubs()

    def _run_stream(self, n_tokens, mx_mock):
        fake_self = _make_fake_self(self.mod)
        mlx_lm_mod = sys.modules["mlx_lm"]
        mlx_lm_mod.stream_generate = MagicMock(return_value=_stream_n_tokens(n_tokens))
        sys.modules["mlx.core"] = mx_mock
        sys.modules["mlx"].core = mx_mock
        return list(self.mod.Hermes3Engine._stream_tokens(fake_self, "x", max_tok=10, temp=0.1))

    def test_m3_9_uses_top_level_get_active_memory_when_present(self) -> None:
        """Prefer mx.get_active_memory() (newer MLX) over mx.metal.*."""
        mx_mock = MagicMock()
        mx_mock.eval = MagicMock()
        mx_mock.metal = MagicMock()
        mx_mock.metal.clear_cache = MagicMock()
        mx_mock.get_active_memory = MagicMock(
            return_value=self.mod.M3_METAL_PRESSURE_BYTES + 1
        )
        mx_mock.metal.get_active_memory = MagicMock(
            return_value=self.mod.M3_METAL_PRESSURE_BYTES + 1
        )
        tokens = self._run_stream(250, mx_mock)
        # top-level was queried (once, at token 200)
        self.assertEqual(mx_mock.get_active_memory.call_count, 1)
        self.assertEqual(len(tokens), 250)

    def test_m3_10_no_op_when_metal_cache_api_missing(self) -> None:
        """Older MLX without mx.metal.clear_cache → no crash, no clear."""
        mx_mock = MagicMock()
        mx_mock.eval = MagicMock()
        # No mx.metal.clear_cache at all
        del mx_mock.metal.clear_cache
        mx_mock.get_active_memory = MagicMock(
            return_value=self.mod.M3_METAL_PRESSURE_BYTES + 1
        )
        tokens = self._run_stream(250, mx_mock)
        self.assertEqual(len(tokens), 250)


if __name__ == "__main__":
    unittest.main()
