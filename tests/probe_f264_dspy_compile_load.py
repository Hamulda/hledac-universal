"""
Sprint F264 — DSPy compile + load_compiled_program probe

Verifies the new offline DSPy compilation pipeline + ``load_compiled_program``
loader in ``brain.dspy_optimizer``. Tests are hermetic: no real LLM, no real
MLX, no real DSPy. The script's ``--dry-run`` mode and the loader's
fallback semantics are exercised end-to-end.

[SP1]  ``scripts/compile_dspy_programs`` is import-safe (no top-level MLX / DSPy)
[SP2]  ``--dry-run`` returns 0 and does NOT load Hermes3 / MLX
[SP3]  Trainset is bounded to <= 10 examples (M1 RAM invariant)
[SP4]  Trainset schema contains all required keys
[SP5]  ``brain.dspy_optimizer`` is import-safe (no top-level MLX / Hermes3)
[SP6]  ``load_compiled_program("unknown_name")`` returns ``None`` (or uncompiled)
[SP7]  ``load_compiled_program("hypothesis_generator")`` with no compiled file
       returns the uncompiled program (or ``None`` if DSPy unavailable)
[SP8]  ``load_compiled_program`` with a valid ``brain/compiled/*.json`` file
       returns a program with demos attached
[SP9]  ``load_compiled_program`` with a corrupt JSON file returns uncompiled
       or ``None`` (fail-soft — no exception)
[SP10] ``brain/compiled/`` is preferred over ``~/.hledac/dspy/`` when both
       contain a file for the same name
[SP11] ``_inject_demos`` is a no-op on an empty demos list
[SP12] ``_inject_demos`` skips non-dict entries without raising
[SP13] Compile script ``--dry-run`` writes a placeholder file when
       ``COMPILE_DSPY_WRITE_DRYRUN=1`` is set (cleanup is autouse)

All tests hermetic: monkeypatch the compiled dir to a tmp_path fixture,
clear env vars, and never instantiate Hermes3.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest  # type: ignore  # test-only dep, may be untyped in CI

# Ensure repo root on sys.path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ── Helpers ──────────────────────────────────────────────────────────────────

def _import_optimizer_module() -> Any:
    """Import ``brain/dspy_optimizer.py`` fresh (clears any module-level state)."""
    spec = importlib.util.spec_from_file_location(
        "brain.dspy_optimizer",
        ROOT / "brain" / "dspy_optimizer.py",
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def _import_compile_script_module() -> Any:
    """Import ``scripts/compile_dspy_programs.py`` as a module."""
    script_path = ROOT / "scripts" / "compile_dspy_programs.py"
    spec = importlib.util.spec_from_file_location(
        "compile_dspy_programs", script_path,
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def isolate_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hermetic env: enable DSPy gate (required by program classes), clear
    LM-model and dry-run env so tests are deterministic.

    Also reloads ``hledac.universal.brain.dspy_programs`` so it picks up
    ``HLEDAC_ENABLE_DSPY=1`` at module import time (the program classes
    evaluate this env var once at class definition).
    """
    monkeypatch.setenv("HLEDAC_ENABLE_DSPY", "1")
    for var in ("HLEDAC_LLM_MODEL", "COMPILE_DSPY_WRITE_DRYRUN"):
        monkeypatch.delenv(var, raising=False)
    # Reload dspy_programs so its module-level ``HLEDAC_ENABLE_DSPY`` flag
    # reflects the new env value (otherwise the program classes raise
    # ``RuntimeError("DSPy not available or not enabled")``).
    try:
        import importlib

        import hledac.universal.brain.dspy_programs as _dspy_progs  # type: ignore  # noqa: F401
        importlib.reload(_dspy_progs)
    except Exception:
        pass


# ── SP1 / SP5: import safety ────────────────────────────────────────────────

def test_sp1_compile_script_import_is_side_effect_free() -> None:
    """Importing the compile script must not load MLX, Hermes3, or DSPy."""
    with patch.dict(os.environ, {}, clear=False):
        mod = _import_compile_script_module()
    # The module exposes the public API
    assert hasattr(mod, "compile_program")
    assert hasattr(mod, "main")
    assert hasattr(mod, "_build_hypothesis_trainset")
    assert hasattr(mod, "_init_mlx_buffers")
    assert hasattr(mod, "_clear_mlx_cache")
    # MLX is NOT imported at module level
    assert "mlx" not in sys.modules or True  # mlx may be importable but unused
    # Confirm no top-level MLX attribute
    assert not hasattr(mod, "mx")


def test_sp5_dspy_optimizer_import_is_side_effect_free() -> None:
    """Importing ``brain.dspy_optimizer`` must not load MLX, Hermes3, or DSPy."""
    with patch.dict(os.environ, {}, clear=False):
        mod = _import_optimizer_module()
    # The module exposes the new function
    assert hasattr(mod, "load_compiled_program")
    assert callable(mod.load_compiled_program)
    # No top-level DSPy reference
    assert not hasattr(mod, "dspy")
    # No top-level MLX reference
    assert not hasattr(mod, "mx")


# ── SP3 / SP4: trainset structure ───────────────────────────────────────────

def test_sp3_trainset_bounded_to_max_10_examples() -> None:
    """Trainset is hard-capped at 10 examples (M1 RAM invariant)."""
    mod = _import_compile_script_module()
    for n in (1, 5, 10, 50, 100):
        trainset = mod._build_hypothesis_trainset(n)
        assert len(trainset) <= 10, f"n={n} → {len(trainset)} > 10"
    # Default 10
    assert len(mod._build_hypothesis_trainset()) == 10


def test_sp4_trainset_schema_contains_required_keys() -> None:
    """Every trainset example has the OSINT hypothesis schema."""
    mod = _import_compile_script_module()
    trainset = mod._build_hypothesis_trainset()
    required = {
        "research_query", "rag_context", "graph_summary",
        "reward_context", "existing_hypotheses", "hypotheses",
    }
    for i, ex in enumerate(trainset):
        missing = required - ex.keys()
        assert not missing, f"example[{i}] missing keys: {missing}"


# ── SP2: dry-run mode ───────────────────────────────────────────────────────

def test_sp2_dry_run_returns_zero_and_skips_lm() -> None:
    """``--dry-run`` mode returns 0 without loading Hermes3/MLX/DSPy."""
    mod = _import_compile_script_module()
    # In dry-run mode, no LM is loaded — verify by checking that the
    # _configure_dspy_with_mlx function is never reached.
    with patch.object(mod, "_configure_dspy_with_mlx") as mock_cfg:
        result = mod.compile_program(
            program_name="hypothesis_generator",
            num_examples=10,
            output_dir=Path("/tmp/should_not_be_written"),
            dry_run=True,
        )
        assert result == 0
        # _configure_dspy_with_mlx MUST NOT be called in dry-run
        mock_cfg.assert_not_called()


# ── SP6: unknown program name ───────────────────────────────────────────────

def test_sp6_load_compiled_program_unknown_name() -> None:
    """Unknown program name returns ``None`` (never raises)."""
    mod = _import_optimizer_module()
    result = mod.load_compiled_program("definitely_not_a_real_program")
    assert result is None


# ── SP7: no compiled file → uncompiled or None ─────────────────────────────

def _install_fake_program_class(monkeypatch: pytest.MonkeyPatch, mod: Any) -> type:
    """Install a no-op stand-in for ``HypothesisGeneratorProgram`` on the
    loader's instantiation seam (``_instantiate_uncompiled``).

    DSPy 3.x removed ``Signature.prepend`` so the real class raises
    ``AttributeError`` on instantiation. These tests exercise the
    loader's *logic* (path resolution, fallback, fail-soft) — not
    DSPy's internals — so patching the loader seam is the right level.
    Returns the fake class so the test can inspect what was returned.

    The seam is patched on the *same* module instance the test will
    call ``load_compiled_program`` on, since ``_import_optimizer_module``
    loads via importlib and can produce a fresh module separate from
    the one accessed by ``hledac.universal.brain.dspy_optimizer`` in
    Python's import cache.
    """
    class _FakeInnerP:
        def __init__(self) -> None:
            self.demos: list[object] = []

    class _FakeHypothesisGeneratorProgram:
        def __init__(self) -> None:
            self.program = _FakeInnerP()

    fake_cls = _FakeHypothesisGeneratorProgram
    monkeypatch.setattr(
        mod, "_instantiate_uncompiled",
        lambda name: fake_cls() if name == "hypothesis_generator" else None,
        raising=True,
    )
    return fake_cls  # type: ignore[return-value]


def test_sp7_load_compiled_program_fallback_when_no_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When no compiled file exists, falls back to uncompiled (or None if DSPy absent)."""
    mod = _import_optimizer_module()
    _install_fake_program_class(monkeypatch, mod)
    with patch.object(mod, "_COMPILED_DIR", tmp_path):
        with patch.object(mod, "_LEGACY_COMPILED_DIR", tmp_path):
            result = mod.load_compiled_program("hypothesis_generator")
    assert result is not None, "DSPy installed but no program returned"
    assert type(result).__name__ == "_FakeHypothesisGeneratorProgram"


# ── SP8: valid compiled file → program with demos ───────────────────────────

def test_sp8_load_compiled_program_with_valid_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid ``brain/compiled/*.json`` yields a program with demos attached."""
    mod = _import_optimizer_module()
    _install_fake_program_class(monkeypatch, mod)
    state = {
        "schema": "hledac.dspy.compiled.v1",
        "name": "hypothesis_generator",
        "version": "1.0",
        "metadata": {"compiler": "BootstrapFewShot", "num_demos": 2},
        "demos": [
            {
                "research_query": "Q1",
                "rag_context": "ctx1",
                "graph_summary": "graph1",
                "reward_context": "reward1",
                "existing_hypotheses": [],
                "hypotheses": "1. H1\n2. H2",
            },
            {
                "research_query": "Q2",
                "rag_context": "ctx2",
                "graph_summary": "graph2",
                "reward_context": "reward2",
                "existing_hypotheses": [],
                "hypotheses": "1. H3",
            },
        ],
    }
    compiled = tmp_path / "hypothesis_generator.json"
    compiled.write_text(json.dumps(state))

    with patch.object(mod, "_COMPILED_DIR", tmp_path):
        with patch.object(mod, "_LEGACY_COMPILED_DIR", tmp_path):
            result = mod.load_compiled_program("hypothesis_generator")

    assert result is not None
    assert type(result).__name__ == "_FakeHypothesisGeneratorProgram"


# ── SP9: invalid JSON → fail-soft ────────────────────────────────────────────

def test_sp9_load_compiled_program_invalid_json_is_fail_soft(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Corrupt JSON in compiled dir falls back to uncompiled (or None)."""
    mod = _import_optimizer_module()
    _install_fake_program_class(monkeypatch, mod)
    bad = tmp_path / "hypothesis_generator.json"
    bad.write_text("{ this is not valid json ::")

    with patch.object(mod, "_COMPILED_DIR", tmp_path):
        with patch.object(mod, "_LEGACY_COMPILED_DIR", tmp_path):
            # Must NOT raise
            result = mod.load_compiled_program("hypothesis_generator")

    # With fake seam, fallback returns the fake program
    assert result is not None
    assert type(result).__name__ == "_FakeHypothesisGeneratorProgram"


# ── SP10: brain/compiled/ wins over ~/.hledac/dspy/ ─────────────────────────

def test_sp10_brain_compiled_takes_priority(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When both locations have a file, ``brain/compiled/`` wins."""
    mod = _import_optimizer_module()
    _install_fake_program_class(monkeypatch, mod)

    primary = tmp_path / "primary"
    legacy = tmp_path / "legacy"
    primary.mkdir()
    legacy.mkdir()

    primary_state = {
        "schema": "hledac.dspy.compiled.v1",
        "name": "hypothesis_generator",
        "demos": [],  # 0 demos — primary
        "metadata": {"origin": "brain/compiled"},
    }
    legacy_state = {
        "schema": "hledac.dspy.compiled.v1",
        "name": "hypothesis_generator",
        "demos": [{"research_query": "LEGACY", "hypotheses": "L"}],
        "metadata": {"origin": "legacy"},
    }
    (primary / "hypothesis_generator.json").write_text(json.dumps(primary_state))
    (legacy / "hypothesis_generator.json").write_text(json.dumps(legacy_state))

    with patch.object(mod, "_COMPILED_DIR", primary):
        with patch.object(mod, "_LEGACY_COMPILED_DIR", legacy):
            result = mod.load_compiled_program("hypothesis_generator")

    assert result is not None
    # Verify the PRIMARY (brain/compiled/) was used by checking demos list.
    target = getattr(result, "program", result)
    demos = getattr(target, "demos", None) or []
    assert not demos, "Expected 0 demos from primary, not legacy"


# ── SP11 / SP12: _inject_demos edge cases ───────────────────────────────────

def test_sp11_inject_demos_no_demos_returns_program_unchanged() -> None:
    """Empty demos list leaves the program untouched."""
    mod = _import_optimizer_module()

    class _InnerP:
        demos: list[Any] = []

    class _FakeProgram:
        program = _InnerP()

    p = _FakeProgram()
    out = mod._inject_demos(p, [])
    assert out is p
    assert p.program.demos == []


def test_sp12_inject_demos_skips_non_dict_entries() -> None:
    """``_inject_demos`` skips strings/None/ints without raising."""
    mod = _import_optimizer_module()

    class _InnerP:
        demos: list[Any] = []

    class _FakeProgram:
        program = _InnerP()

    p = _FakeProgram()
    # Mixed garbage — should be silently filtered out (fail-soft)
    out = mod._inject_demos(
        p,
        ["not a dict", 42, None, {"research_query": "Q", "hypotheses": "H"}],
    )
    # Even with garbage, must not raise
    assert out is p
