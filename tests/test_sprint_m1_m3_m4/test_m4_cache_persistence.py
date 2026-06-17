"""
Sprint M4 — System-prompt cache save/load round-trip.

Verifies that:
  1. _save_cache stores keys/values PER LAYER (not mx.array(tuple) which
     silently breaks shape on some MLX versions).
  2. _load_cache actually populates self._system_prompt_cache (it was
     previously dead code: returned True without ever touching the cache).
  3. PromptCache-level offset is persisted and restored.
  4. Fail-soft: missing file → False, corrupt file → False, no crash.

Tests bypass MagicMock(spec=...) and bind the real unbound methods
directly to a lightweight fake self — hermetic, no real MLX load.
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


def _setup_mlx_stubs():
    """Stub mlx + mlx_lm so deephermes3_engine imports succeed."""
    if "mlx_lm" not in sys.modules:
        mlx_lm_stub = types.ModuleType("mlx_lm")
        mlx_lm_stub.load = MagicMock()
        mlx_lm_stub.generate = MagicMock()
        mlx_lm_stub.stream_generate = MagicMock()
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
    # `import mlx.core as mx` resolves via __import__('mlx.core') which
    # returns the top-level mlx; then `mlx.core` is attribute-resolved.
    # Set both paths.
    if not hasattr(sys.modules["mlx"], "core"):
        sys.modules["mlx"].core = sys.modules["mlx.core"]

    from hledac.universal.brain import deephermes3_engine as hermes_mod  # type: ignore

    return hermes_mod


class _FakeArray:
    """Stand-in for an mlx.core array: nbytes, item(), and a stable repr."""

    def __init__(self, marker: str, nbytes: int = 64) -> None:
        self._marker = marker
        self.nbytes = nbytes

    def item(self):
        return self._marker


class _FakeKVLayer:
    """A single PromptCache layer: .state, .keys, .values."""

    def __init__(self, k_marker: str, v_marker: str) -> None:
        self.keys = _FakeArray(k_marker)
        self.values = _FakeArray(v_marker)

    @property
    def state(self):
        return (self.keys, self.values)

    @state.setter
    def state(self, st):
        self.keys, self.values = st


class _FakePromptCache(list):
    """PromptCache-like list subclass with .offset."""

    def __init__(self, layers, offset: int = 0) -> None:
        super().__init__(layers)
        self.offset = offset


def _make_engine_with_cache(hermes_mod, layers, offset: int = 0):
    """Lightweight fake object: real methods bound, fake data attrs.

    Bind the real unbound methods to the fake so calling them executes
    the real code, with self bound to the fake.
    """
    engine = types.SimpleNamespace()
    engine._system_prompt_cache = _FakePromptCache(layers, offset=offset)
    # Bind real methods (closures over `self` via descriptor protocol).
    # We do this by setting the function as a class attribute and then
    # accessing it through the instance — Python's descriptor protocol
    # binds self automatically.
    cls = type("FakeEngine", (), {
        "_save_cache": hermes_mod.DeepHermes3Engine._save_cache,
        "_load_cache": hermes_mod.DeepHermes3Engine._load_cache,
        "_init_system_prompt_cache": hermes_mod.DeepHermes3Engine._init_system_prompt_cache,
    })
    inst = cls()
    inst._system_prompt_cache = engine._system_prompt_cache
    return inst


def _patch_mx_core(saved_arrays: dict):
    """Patch mlx.core so mx.savez captures into saved_arrays, and
    mx.load returns the previously-saved dict. hermetic & round-trip safe."""
    mx_mock = MagicMock(name="mlx.core")

    def _savez(path, **kwargs):
        for k, v in kwargs.items():
            saved_arrays[k] = v

    def _load(path):
        return dict(saved_arrays)

    def _array(value, dtype=None):
        # For offset which is a list[int]
        if isinstance(value, list) and value and isinstance(value[0], int):
            return _FakeArray(value[0], nbytes=8)
        return _FakeArray(str(value))

    mx_mock.savez = MagicMock(side_effect=_savez)
    mx_mock.load = MagicMock(side_effect=_load)
    mx_mock.array = MagicMock(side_effect=_array)
    # Bump hasattr(...) tests
    sys.modules["mlx.core"] = mx_mock
    sys.modules["mlx"].core = mx_mock
    return mx_mock


class TestM4SaveCacheStructure(unittest.TestCase):
    """M4 save: keys/values stored separately per layer + offset."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _setup_mlx_stubs()

    def _run_save(self, layers, offset: int = 0):
        engine = _make_engine_with_cache(self.mod, layers, offset=offset)
        saved_arrays: dict = {}
        _patch_mx_core(saved_arrays)

        # Override Path.home() so the test never touches real ~/.hledac/
        with patch_pathlib_home("/tmp/hledac_m4_test_home"):
            import asyncio
            asyncio.run(engine._save_cache())
        return saved_arrays

    def test_m4_1_save_stores_keys_values_separately(self) -> None:
        layers = [
            _FakeKVLayer("k0", "v0"),
            _FakeKVLayer("k1", "v1"),
        ]
        saved = self._run_save(layers, offset=42)
        self.assertIn("layer_0_keys", saved)
        self.assertIn("layer_0_values", saved)
        self.assertIn("layer_1_keys", saved)
        self.assertIn("layer_1_values", saved)
        # NO combined "layer_0" key (the old bug stored mx.array(tuple))
        self.assertNotIn("layer_0", saved)

    def test_m4_2_save_persists_offset(self) -> None:
        layers = [_FakeKVLayer("k0", "v0")]
        saved = self._run_save(layers, offset=128)
        self.assertIn("_offset", saved)
        # The fake array.item() returns the int we passed
        self.assertEqual(saved["_offset"].item(), 128)

    def test_m4_3_save_omits_offset_when_missing(self) -> None:
        # Build cache without offset attribute
        cache = _FakePromptCache([_FakeKVLayer("k0", "v0")])
        del cache.offset
        engine = type("E", (), {
            "_save_cache": self.mod.DeepHermes3Engine._save_cache,
        })()
        engine._system_prompt_cache = cache
        saved: dict = {}
        _patch_mx_core(saved)
        with patch_pathlib_home("/tmp/fake"):
            import asyncio
            asyncio.run(engine._save_cache())
        self.assertNotIn("_offset", saved)

    def test_m4_4_save_no_cache_is_noop(self) -> None:
        engine = type("E", (), {
            "_save_cache": self.mod.DeepHermes3Engine._save_cache,
        })()
        engine._system_prompt_cache = None
        saved: dict = {}
        _patch_mx_core(saved)
        with patch_pathlib_home("/tmp/fake"):
            import asyncio
            # Must NOT raise
            asyncio.run(engine._save_cache())
        self.assertEqual(saved, {})


class TestM4LoadCacheRestores(unittest.TestCase):
    """M4 load: actually populates the cache (was dead code previously)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _setup_mlx_stubs()

    def _run_load_with_disk(self, layers, saved_arrays, monkey_path_exists: bool = True):
        engine = _make_engine_with_cache(self.mod, layers, offset=0)
        _patch_mx_core(saved_arrays)

        from pathlib import Path
        from unittest.mock import patch
        # Patch home to a Path (not str) so `Path.home() / sub / sub` works.
        with patch("pathlib.Path.home", return_value=Path("/tmp/hledac_m4_test_home")), \
             patch.object(Path, "exists", return_value=monkey_path_exists), \
             patch.object(Path, "mkdir", return_value=None):
            import asyncio
            ok = asyncio.run(engine._load_cache())
        return ok, engine

    def test_m4_5_load_restores_keys_values(self) -> None:
        layers = [_FakeKVLayer("k_OLD", "v_OLD"), _FakeKVLayer("k1_OLD", "v1_OLD")]
        saved = {
            "layer_0_keys": _FakeArray("k_NEW"),
            "layer_0_values": _FakeArray("v_NEW"),
            "layer_1_keys": _FakeArray("k1_NEW"),
            "layer_1_values": _FakeArray("v1_NEW"),
            "_offset": _FakeArray(64),
        }
        ok, engine = self._run_load_with_disk(layers, saved)
        self.assertTrue(ok)
        self.assertEqual(engine._system_prompt_cache[0].keys.item(), "k_NEW")
        self.assertEqual(engine._system_prompt_cache[0].values.item(), "v_NEW")
        self.assertEqual(engine._system_prompt_cache[1].keys.item(), "k1_NEW")
        self.assertEqual(engine._system_prompt_cache[1].values.item(), "v1_NEW")

    def test_m4_6_load_restores_offset(self) -> None:
        layers = [_FakeKVLayer("k0", "v0")]
        saved = {
            "layer_0_keys": _FakeArray("k0"),
            "layer_0_values": _FakeArray("v0"),
            "_offset": _FakeArray(256),
        }
        ok, engine = self._run_load_with_disk(layers, saved)
        self.assertTrue(ok)
        self.assertEqual(engine._system_prompt_cache.offset, 256)

    def test_m4_7_load_missing_file_returns_false(self) -> None:
        layers = [_FakeKVLayer("k0", "v0")]
        ok, engine = self._run_load_with_disk(layers, {}, monkey_path_exists=False)
        self.assertFalse(ok)
        self.assertEqual(engine._system_prompt_cache[0].keys.item(), "k0")

    def test_m4_8_load_empty_disk_returns_false(self) -> None:
        layers = [_FakeKVLayer("k0", "v0")]
        ok, engine = self._run_load_with_disk(layers, {})
        self.assertFalse(ok)

    def test_m4_9_load_with_none_cache_returns_false(self) -> None:
        engine = type("E", (), {
            "_load_cache": self.mod.DeepHermes3Engine._load_cache,
        })()
        engine._system_prompt_cache = None
        from unittest.mock import patch
        with patch("pathlib.Path.home", return_value="/tmp/fake"):
            import asyncio
            ok = asyncio.run(engine._load_cache())
        self.assertFalse(ok)

    def test_m4_10_load_corrupt_layer_does_not_crash(self) -> None:
        layers = [_FakeKVLayer("k0", "v0"), _FakeKVLayer("k1", "v1")]
        saved = {
            "layer_0_keys": _FakeArray("k_new"),
            # NOTE: no layer_0_values
            "layer_1_keys": _FakeArray("k1_new"),
            "layer_1_values": _FakeArray("v1_new"),
        }
        ok, engine = self._run_load_with_disk(layers, saved)
        self.assertTrue(ok)
        self.assertEqual(engine._system_prompt_cache[1].keys.item(), "k1_new")
        self.assertEqual(engine._system_prompt_cache[0].keys.item(), "k0")


class TestM4InitOrder(unittest.TestCase):
    """M4 init: probe disk first, skip prefill on cache hit."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.mod = _setup_mlx_stubs()

    def test_m4_11_init_skips_prefill_when_disk_exists(self) -> None:
        from unittest.mock import patch

        engine = type("E", (), {
            "_init_system_prompt_cache": self.mod.DeepHermes3Engine._init_system_prompt_cache,
            "supports_stream_generate": True,
        })()
        engine._model = MagicMock()
        engine._tokenizer = MagicMock()
        engine._system_prompt = "you are a test bot"
        engine._system_prompt_cache = None
        engine._supports_stream_generate = True
        engine._supports_kv_quant = False
        engine._kv_cache_stats = {"cache_uses": 0, "cache_prefills": 0, "quantized_count": 0}

        stream_called = {"n": 0}
        def _fake_stream(*a, **kw):
            stream_called["n"] += 1
            return iter([])

        def _fake_make_prompt_cache(model, max_kv_size=512):
            return _FakePromptCache([_FakeKVLayer("k0", "v0")], offset=0)

        saved: dict = {}
        _patch_mx_core(saved)

        from pathlib import Path
        with patch.object(self.mod, "KV_CACHE_AVAILABLE", True), \
             patch("pathlib.Path.home", return_value=Path("/tmp/fake_home")), \
             patch.object(Path, "exists", return_value=True), \
             patch.object(Path, "mkdir", return_value=None), \
             patch("mlx_lm.stream_generate", side_effect=_fake_stream, create=True), \
             patch("mlx_lm.models.cache.make_prompt_cache", _fake_make_prompt_cache, create=True):
            import asyncio
            asyncio.run(engine._init_system_prompt_cache())
        self.assertEqual(stream_called["n"], 0)

    def test_m4_12_init_prefills_when_disk_missing(self) -> None:
        from unittest.mock import patch

        engine = type("E", (), {
            "_init_system_prompt_cache": self.mod.DeepHermes3Engine._init_system_prompt_cache,
        })()
        engine._model = MagicMock()
        engine._tokenizer = MagicMock()
        engine._system_prompt = "you are a test bot"
        engine._system_prompt_cache = None
        engine._supports_stream_generate = True
        engine._supports_kv_quant = False
        engine._kv_cache_stats = {"cache_uses": 0, "cache_prefills": 0, "quantized_count": 0}

        stream_called = {"n": 0}
        def _fake_stream(*a, **kw):
            stream_called["n"] += 1
            return iter([])  # empty iterator so the for-loop terminates

        def _fake_make_prompt_cache(model, max_kv_size=512):
            return _FakePromptCache([_FakeKVLayer("k0", "v0")], offset=0)

        _patch_mx_core({})

        from pathlib import Path
        # Force KV_CACHE_AVAILABLE = True in the deephermes3_engine module so
        # the function proceeds past its first guard. Use patch.object on
        # the module attribute to make the change visible inside the
        # function (it reads the module-level name, not an instance attr).
        with patch.object(self.mod, "KV_CACHE_AVAILABLE", True), \
             patch("pathlib.Path.home", return_value=Path("/tmp/fake_home")), \
             patch.object(Path, "exists", return_value=False), \
             patch.object(Path, "mkdir", return_value=None), \
             patch("mlx_lm.stream_generate", side_effect=_fake_stream, create=True), \
             patch("mlx_lm.models.cache.make_prompt_cache", _fake_make_prompt_cache, create=True):
            import asyncio
            asyncio.run(engine._init_system_prompt_cache())
        self.assertEqual(stream_called["n"], 1)


def patch_pathlib_home(path: str):
    """Context manager: patch pathlib.Path.home() to return a Path."""
    from contextlib import contextmanager
    from pathlib import Path
    from unittest.mock import patch

    @contextmanager
    def _cm():
        # Path.home() must return a Path so subsequent `path / sub / sub`
        # works. We also patch mkdir to no-op so the test never touches
        # the real filesystem.
        with patch("pathlib.Path.home", return_value=Path(path)), \
             patch.object(Path, "mkdir", return_value=None):
            yield

    return _cm()


if __name__ == "__main__":
    unittest.main()
