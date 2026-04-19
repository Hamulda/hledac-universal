"""
Sprint F192B: Model-plane authority probe tests.

Tests the 5-file model-plane cluster:
- brain/model_manager.py      — runtime-wide load owner
- brain/model_lifecycle.py    — helper / shadow-state / emergency seam
- brain/hermes3_engine.py     — engine (unload authority for Hermes)
- model_lifecycle.py          — compat wrapper (COMPAT_BACKWARD)
- core/resource_governor.py   — UMA policy / memory admission

Tests:
  F192B-A: hermes3_engine._run_inference has mx.eval([]) barrier after mlx_generate
  F192B-B: hermes3_engine._run_sustain_inference has mx.eval([]) barrier after mlx_generate
  F192B-C: _init_draft_model uses consistent system_used_gib threshold 7.0 GB
  F192B-D: model_lifecycle async loop detection comment cleaned up
  F192B-E: model_manager _check_memory_admission runs AFTER mx.eval([]) barrier
  F192B-F: root model_lifecycle is COMPAT_BACKWARD wrapper
  F192B-G: resource_governor sample_uma_status is one-shot snapshot
  F192B-H: hermes3_engine.unload() follows canonical 7K order
"""
import asyncio
import gc
import inspect
import weakref
from unittest import mock

import pytest


class TestF192BA:
    """F192B-A: _run_inference has mx.eval([]) barrier after mlx_generate."""

    def test_run_inference_has_eval_barrier(self):
        """Source inspection: _run_inference contains mlx.eval([]) after mlx_generate."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._run_inference)
        # Must have mlx_generate call
        assert "mlx_generate" in source
        # Must have mx.eval([]) barrier after the generate
        assert "mx.eval([])" in source or "_mx.eval([])" in source

    def test_run_inference_barrier_order(self):
        """
        Structural: mlx_generate must appear BEFORE mx.eval([]) in source.
        This is the canonical 7K order used in unload().
        """
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._run_inference)
        gen_pos = source.find("mlx_generate")
        eval_pos = source.find("mx.eval([])") if "mx.eval([])" in source else source.find("_mx.eval([])")
        assert gen_pos != -1, "mlx_generate not found"
        assert eval_pos != -1, "mx.eval([]) barrier not found"
        assert gen_pos < eval_pos, "mx.eval([]) must come AFTER mlx_generate"


class TestF192BB:
    """F192B-B: _run_sustain_inference has mx.eval([]) barrier after mlx_generate."""

    def test_sustain_inference_has_eval_barrier(self):
        """Source inspection: _run_sustain_inference contains mx.eval([])."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._run_sustain_inference)
        assert "mlx_generate" in source
        assert "mx.eval([])" in source or "_mx.eval([])" in source

    def test_sustain_barrier_order(self):
        """mlx_generate must appear BEFORE mx.eval([]) in _run_sustain_inference."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._run_sustain_inference)
        gen_pos = source.find("mlx_generate")
        eval_pos = source.find("mx.eval([])") if "mx.eval([])" in source else source.find("_mx.eval([])")
        assert gen_pos != -1
        assert eval_pos != -1
        assert gen_pos < eval_pos

    def test_sustain_has_clear_cache(self):
        """F192B-B: _run_sustain_inference must call clear_cache after mx.eval([])."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._run_sustain_inference)
        assert "clear_cache" in source, "_run_sustain_inference must call clear_cache (canonical 7K order)"


class TestF192BC:
    """F192B-C: _init_draft_model uses system_used_gib >= 7.0 as EMERGENCY threshold."""

    def test_emergency_threshold_7_0(self):
        """Source: _init_draft_model checks system_used_gib >= 7.0 for EMERGENCY."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._init_draft_model)
        # Must use system_used_gib as threshold driver
        assert "system_used_gib" in source
        # Must have >= 7.0 threshold for EMERGENCY
        assert "7.0" in source
        # Must not have the old 5.5 threshold for EMERGENCY check
        assert ">=" not in source[source.find("system_used_gib"):source.find("system_used_gib") + 50] or \
               "7.0" in source[source.find("system_used_gib"):source.find("system_used_gib") + 50]


class TestF192BD:
    """F192B-D: model_lifecycle async loop detection comments cleaned up."""

    def test_load_async_loop_comment(self):
        """load_model async loop detection comment references F192B not old F650G."""
        from hledac.universal.brain.model_lifecycle import load_model
        source = inspect.getsource(load_model)
        assert "F650G" not in source
        assert "F192B" in source or "caller must await" in source.lower()

    def test_unload_async_loop_comment(self):
        """unload_model async loop detection comment references F192B not old F650G."""
        from hledac.universal.brain.model_lifecycle import unload_model
        source = inspect.getsource(unload_model)
        assert "F650G" not in source
        assert "F192B" in source or "caller must await" in source.lower()


class TestF192BE:
    """F192B-E: model_manager._load_model_async runs mx.eval([]) before _check_memory_admission."""

    def test_barrier_before_admission_check(self):
        """Structural: mx.eval([]) must appear before _check_memory_admission in source."""
        from hledac.universal.brain.model_manager import ModelManager
        source = inspect.getsource(ModelManager._load_model_async)

        # Find positions
        eval_marker = "F192B" if "F192B" in source else "mx.eval([])"
        eval_pos = source.find("mx.eval([])")
        admission_pos = source.find("_check_memory_admission")

        assert eval_pos != -1, "mx.eval([]) not found in _load_model_async"
        assert admission_pos != -1, "_check_memory_admission not found in _load_model_async"
        assert eval_pos < admission_pos, \
            "mx.eval([]) must come BEFORE _check_memory_admission (settle GPU state first)"

    def test_mx_eval_mentions_f192b(self):
        """Comment for mx.eval([]) barrier should reference F192B."""
        from hledac.universal.brain.model_manager import ModelManager
        source = inspect.getsource(ModelManager._load_model_async)
        assert "F192B" in source, "mx.eval([]) barrier comment should reference F192B sprint"


class TestF192BF:
    """F192B-F: root model_lifecycle.py is COMPAT_BACKWARD wrapper (no canonical state)."""

    def test_is_compat_wrapper(self):
        """model_lifecycle.py must be COMPAT_BACKWARD with star-export."""
        import hledac.universal.model_lifecycle as ml
        # Must have __deprecated__ = True (prevents use as canonical path)
        assert getattr(ml, '__deprecated__', False) is True
        # Must have __all__ = [] (no public surface)
        assert getattr(ml, '__all__', None) == []

    def test_no_canonical_state(self):
        """model_lifecycle.py must NOT have _lifecycle_state or _emergency_unload_requested."""
        import hledac.universal.model_lifecycle as ml
        # These should be in brain.model_lifecycle, not here
        assert not hasattr(ml, '_lifecycle_state'), \
            "COMPAT_BACKWARD wrapper must not hold canonical state"
        assert not hasattr(ml, '_emergency_unload_requested'), \
            "COMPAT_BACKWARD wrapper must not hold emergency flag"

    def test_no_model_registry(self):
        """model_lifecycle.py must NOT have MODEL_REGISTRY or _loaded_models."""
        import hledac.universal.model_lifecycle as ml
        assert not hasattr(ml, 'MODEL_REGISTRY'), \
            "COMPAT_BACKWARD wrapper must not hold model registry"
        assert not hasattr(ml, '_loaded_models'), \
            "COMPAT_BACKWARD wrapper must not hold loaded models"


class TestF192BG:
    """F192B-G: resource_governor.sample_uma_status is one-shot (not cached/delayed)."""

    def test_is_one_shot_function(self):
        """sample_uma_status must be a plain function, not a method or cached property."""
        from hledac.universal.core.resource_governor import sample_uma_status
        # Must be a function, not a method
        assert callable(sample_uma_status)
        # Must have docstring
        assert sample_uma_status.__doc__ is not None
        # Must mention "one-shot" in docstring
        assert "one-shot" in sample_uma_status.__doc__.lower() or \
               "GOVERNOR-LOCAL" in sample_uma_status.__doc__

    def test_returns_umastatus(self):
        """sample_uma_status() must return a UMAStatus dataclass."""
        from hledac.universal.core.resource_governor import sample_uma_status, UMAStatus
        result = sample_uma_status()
        assert isinstance(result, UMAStatus)
        # Must have all required fields
        assert hasattr(result, 'system_used_gib')
        assert hasattr(result, 'state')
        assert hasattr(result, 'io_only')
        assert hasattr(result, 'swap_detected')


class TestF192BH:
    """F192B-H: hermes3_engine.unload() follows canonical 7K order."""

    def test_unload_7k_order(self):
        """unload() must have batch shutdown → cache evict → GC → mx.clear."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine.unload)

        # All 7K steps must be present
        steps = [
            "_shutdown_batch_worker",   # 1. batch shutdown
            "_batch_queue = None",       # 2. queue clear
            "_warmup_cache",             # 3. warmup eviction
            "_save_cache",               # 4. save cache
            "_prompt_cache",             # 5. prompt cache eviction
            "invalidate_prefix_cache",   # 6. prefix cache
            "_model = None",             # 7. model null
            "gc.collect",                # 8. gc
            "mx.eval([])",               # 9. eval barrier
            "clear_cache",               # 10. cache clear
        ]
        for step in steps:
            assert step in source, f"7K step '{step}' not found in unload()"

    def test_unload_does_not_auto_clear_emergency(self):
        """unload() must NOT call clear_emergency_unload_request() — caller decides."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine.unload)
        assert "clear_emergency_unload_request" not in source, \
            "unload() must NOT auto-clear emergency flag — caller decides"

    def test_unload_comment_7k_order(self):
        """unload() docstring must reference Sprint 7K."""
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        doc = Hermes3Engine.unload.__doc__
        assert doc is not None
        assert "7K" in doc, "unload() docstring must reference canonical 7K order"


class TestF192B_Integration:
    """F192B-INT: Cross-file authority consistency checks."""

    def test_hermes_uses_resource_governor_for_draft_admission(self):
        """
        hermes3_engine._init_draft_model must use resource_governor.sample_uma_status
        (not raw psutil) for memory admission — consistent with model_manager.
        """
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine._init_draft_model)
        assert "resource_governor" in source or "sample_uma_status" in source, \
            "_init_draft_model must use resource_governor for UMA measurement"

    def test_model_manager_uses_resource_governor_for_admission(self):
        """
        model_manager._check_memory_admission must use resource_governor.sample_uma_status
        (not raw psutil) — single authority for UMA admission.
        """
        from hledac.universal.brain.model_manager import ModelManager
        source = inspect.getsource(ModelManager._check_memory_admission)
        assert "resource_governor" in source or "sample_uma_status" in source, \
            "_check_memory_admission must use resource_governor for UMA measurement"

    def test_hermes_emergency_seam_consumer(self):
        """
        hermes3_engine must import and check is_emergency_unload_requested
        at top of batch-sensitive methods.
        """
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine)
        # Emergency seam import
        assert "is_emergency_unload_requested" in source
        # Must be checked in batch submit path
        batch_submit = Hermes3Engine._submit_structured_batch
        batch_source = inspect.getsource(batch_submit)
        assert "is_emergency_unload_requested" in batch_source

    def test_model_lifecycle_emergency_flag_is_module_level(self):
        """
        brain.model_lifecycle must have _emergency_unload_requested as module-level flag.
        """
        from hledac.universal.brain import model_lifecycle as blc
        # Must be module-level (not class attribute)
        assert hasattr(blc, '_emergency_unload_requested')
        assert isinstance(blc._emergency_unload_requested, bool)

    def test_no_cross_plane_coupling_in_brain_model_lifecycle(self):
        """
        brain.model_lifecycle must NOT call get_model_manager() — no cross-plane coupling.
        This is a windup-local vs runtime-wide boundary.
        """
        from hledac.universal.brain import model_lifecycle as blc
        source = inspect.getsource(blc)
        assert "get_model_manager" not in source, \
            "brain.model_lifecycle must NOT call get_model_manager() — cross-plane coupling"


class TestF192BI:
    """F192B-I: hermes3_engine.initialize() guards draft model load with emergency check."""

    def test_initialize_guards_draft_model_with_emergency_check(self):
        """
        initialize() must check is_emergency_unload_requested() before _init_draft_model().
        This is the only model-loading path that didn't check the emergency flag.
        """
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine.initialize)
        # Must have the emergency check
        assert "is_emergency_unload_requested" in source, \
            "initialize() must check is_emergency_unload_requested before draft model load"
        # Must guard _init_draft_model with the check
        assert "if is_emergency" in source or "if is_emergency_unload_requested" in source, \
            "initialize() must guard _init_draft_model with emergency check"
        # Must have F192B comment
        assert "F192B" in source, "initialize() emergency guard should reference F192B sprint"

    def test_initialize_emergency_guard_skips_rather_than_raises(self):
        """
        When emergency is requested during initialize(), draft model is skipped
        but main model initialization continues. This is fail-soft for initialization.
        """
        from hledac.universal.brain.hermes3_engine import Hermes3Engine
        source = inspect.getsource(Hermes3Engine.initialize)
        # The guard should log a warning and skip, not raise
        assert "logger.warning" in source or "skipping" in source.lower(), \
            "initialize() should log warning when skipping draft model due to emergency"
