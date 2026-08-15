"""
planning/prm_model_export.py — PRM Model Compilation for Apple Neural Engine
============================================================================

SILICON-06: Compiles a 16→32→1 MLP step-reward model to CoreML .mlpackage
format optimized for ANE (Apple Neural Engine).

Architecture:
    PRM Feature (16-dim) → MLP (16→32→1) → Step Reward [-1, 1]

Model specs:
- Input: 16-dim feature vector (PRMFeatureVector)
- Hidden: 32 units with ReLU activation
- Output: 1 unit (step reward in [-1, 1])
- Compute unit: Neural Engine (ANE) — 16 cores, 11 TOPS int8

M1 8GB safe:
- ANE uses dedicated memory (not main RAM budget)
- Max 2 models in ANE registry simultaneously
- Model footprint: ~50 MB compiled

Usage:
    from planning.prm_model_export import compile_prm_model
    compile_prm_model(target_dir=Path.home() / '.hledac' / 'models')

Python 3.14:
    pip install --extra-index-url https://pypi.anaconda.org/apple/repo/simple coremltools
"""
from __future__ import annotations

import logging
import platform
import sys
from pathlib import Path
from typing import Any

import numpy as np
from _core import aclose

logger = logging.getLogger(__name__)

# Model constants
_PRM_FEATURE_DIM = 16
_PRM_HIDDEN_DIM = 32
_PRM_OUTPUT_DIM = 1
_PRM_MODEL_VERSION = "1.0.0"  # Model architecture version (for cache invalidation)

# Model output paths
_MODELS_DIR = Path.home() / '.hledac' / 'models'
_PRM_MODEL_PATH = _MODELS_DIR / 'prm_step.mlpackage'
_PRM_VERSION_FILE = _MODELS_DIR / 'prm_step.version'


def _check_platform() -> bool:
    """Check if we're on Apple Silicon."""
    if platform.system() != 'Darwin':
        logger.warning('[PRM-Export] Not on macOS — cannot target ANE')
        return False
    if platform.machine() != 'arm64':
        logger.warning('[PRM-Export] Not on Apple Silicon — ANE unavailable')
        return False
    return True


def _check_coremltools() -> tuple[bool, Any]:
    """Check if coremltools is available."""
    if sys.version_info >= (3, 14):
        try:
            import coremltools as ct
            return True, ct
        except ImportError:
            logger.error(
                '[PRM-Export] Python 3.14: coremltools not found.\n'
                '  Install from Apple channel:\n'
                '    pip install --extra-index-url https://pypi.anaconda.org/apple/repo/simple coremltools'
            )
            return False, None
    else:
        try:
            import coremltools as ct
            return True, ct
        except ImportError:
            logger.error('[PRM-Export] coremltools not installed')
            return False, None


def _create_numpy_model() -> dict[str, np.ndarray]:
    """
    Create PRM model as NumPy weights.

    Simple 2-layer MLP: 16→32→1
    Weights initialized with Xavier-like initialization.
    """
    rng = np.random.default_rng(42)

    weights = {
        'w1': rng.normal(0, np.sqrt(2.0 / (_PRM_FEATURE_DIM + _PRM_HIDDEN_DIM)),
                          (_PRM_FEATURE_DIM, _PRM_HIDDEN_DIM)).astype(np.float32),
        'b1': np.zeros(_PRM_HIDDEN_DIM, dtype=np.float32),
        'w2': rng.normal(0, np.sqrt(2.0 / (_PRM_HIDDEN_DIM + _PRM_OUTPUT_DIM)),
                          (_PRM_HIDDEN_DIM, _PRM_OUTPUT_DIM)).astype(np.float32),
        'b2': np.zeros(_PRM_OUTPUT_DIM, dtype=np.float32),
    }
    return weights


def _build_coreml_model(weights: dict[str, np.ndarray], ct: Any) -> Any:
    """
    Build CoreML model using coremltools neural network builder.

    This is the preferred method as it handles ANE optimization automatically.
    """
    # Use NeuralNetworkBuilder for cleaner API
    input_shape = (_PRM_FEATURE_DIM,)
    output_shape = (_PRM_OUTPUT_DIM,)

    builder = ct.neuralnetwork.builder.NeuralNetworkBuilder(
        input_names=['features'],
        output_names=['reward'],
        input_shapes=[input_shape],
        output_shape_ranges=[output_shape],
    )

    # Layer 1: 16 → 32 with ReLU
    w1 = weights['w1'].T  # (out, in) format for CoreML
    builder.add_inner_product(
        name='fc1',
        input_names=['features'],
        output_name='h1',
        weight_matrix=w1,
        bias=weights['b1'],
        has_bias=True,
        input_channels=_PRM_FEATURE_DIM,
        output_channels=_PRM_HIDDEN_DIM,
    )

    builder.add_activation(
        name='relu1',
        input_name='h1',
        output_name='h1_relu',
        non_linearity='RELU',
    )

    # Layer 2: 32 → 1 with Tanh
    w2 = weights['w2'].T
    builder.add_inner_product(
        name='fc2',
        input_names=['h1_relu'],
        output_name='raw_output',
        weight_matrix=w2,
        bias=weights['b2'],
        has_bias=True,
        input_channels=_PRM_HIDDEN_DIM,
        output_channels=_PRM_OUTPUT_DIM,
    )

    builder.add_activation(
        name='tanh_out',
        input_name='raw_output',
        output_name='reward',
        non_linearity='TANH',
    )

    # Set compute units to ANE
    # This is critical for ANE acceleration
    builder.set_compute_units(compute_units=ct.ComputeUnit.ALL)  # Prefer ANE

    return builder.spec


def compile_prm_model(
    target_dir: Path | None = None,
    force: bool = False,
) -> Path | None:
    """
    Compile PRM model to CoreML .mlpackage optimized for ANE.

    Args:
        target_dir: Directory to save the model (default: ~/.hledac/models)
        force: Force recompilation even if model exists

    Returns:
        Path to compiled model, or None on failure.
    """
    # Check platform
    if not _check_platform():
        return None

    # Check coremltools
    available, ct = _check_coremltools()
    if not available:
        return None

    # Set target path
    target_dir = target_dir or _MODELS_DIR
    model_path = target_dir / 'prm_step.mlpackage'

    # Check existing model
    if model_path.exists() and not force:
        logger.info(f'[PRM-Export] Model already exists at {model_path}')
        return model_path

    # Create target directory
    target_dir.mkdir(parents=True, exist_ok=True)

    # Create model
    logger.info('[PRM-Export] Creating PRM MLP (16→32→1)...')
    weights = _create_numpy_model()

    # Build CoreML model
    logger.info('[PRM-Export] Building CoreML model...')
    spec = _build_coreml_model(weights, ct)

    # Convert to MLModel
    logger.info('[PRM-Export] Converting to MLModel...')
    mlmodel = ct.models.MLModel(spec)

    # Save
    logger.info(f'[PRM-Export] Saving to {model_path}...')
    try:
        # Remove existing if force
        if model_path.exists():
            import shutil
            shutil.rmtree(model_path)
        mlmodel.save(str(model_path))

        # Write version file for cache invalidation
        version_path = target_dir / 'prm_step.version'
        version_path.write_text(f"{_PRM_MODEL_VERSION}\n")

        logger.info('[PRM-Export] PRM model compiled successfully!')
        return model_path
    except Exception as e:
        logger.error(f'[PRM-Export] Failed to save model: {e}')
        return None


def ensure_prm_model() -> bool:
    """
    Ensure PRM model exists and is current version, compiling if necessary.

    Returns True if model is available.
    """
    # Check if model exists
    if not _PRM_MODEL_PATH.exists():
        logger.info('[PRM] PRM model not found, attempting compilation...')
        result = compile_prm_model()
        return result is not None

    # Check version (for cache invalidation on architecture changes)
    if _PRM_VERSION_FILE.exists():
        try:
            version = _PRM_VERSION_FILE.read_text().strip()
            if version == _PRM_MODEL_VERSION:
                return True
            else:
                logger.info(f'[PRM] Model version mismatch: {version} != {_PRM_MODEL_VERSION}, recompiling...')
                result = compile_prm_model(force=True)
                return result is not None
        except Exception as e:
            logger.warning(f'[PRM] Version check failed: {e}, recompiling...')
            result = compile_prm_model(force=True)
            return result is not None

    # No version file (legacy model) — recompile
    logger.info('[PRM] Legacy model without version file, recompiling...')
    result = compile_prm_model(force=True)
    return result is not None


def get_model_info() -> dict[str, Any]:
    """Get information about the PRM model."""
    info = {
        'path': str(_PRM_MODEL_PATH),
        'exists': _PRM_MODEL_PATH.exists(),
        'version': _PRM_MODEL_VERSION,
        'input_dim': _PRM_FEATURE_DIM,
        'hidden_dim': _PRM_HIDDEN_DIM,
        'output_dim': _PRM_OUTPUT_DIM,
        'architecture': 'MLP (16→32→1) with ReLU + Tanh',
        'compute_unit': 'ANE preferred (Neural Engine)',
        'platform': 'Apple Silicon (macOS arm64)',
    }

    # Add current version if exists
    if _PRM_VERSION_FILE.exists():
        try:
            info['compiled_version'] = _PRM_VERSION_FILE.read_text().strip()
        except Exception:
            pass

    return info


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)

    print('=' * 60)
    print('PRM Model Compilation for Apple Neural Engine')
    print('=' * 60)
    print()

    # Check platform
    print(f'Platform: {platform.system()} {platform.machine()}')
    print(f'Python: {sys.version_info.major}.{sys.version_info.minor}')
    print()

    # Check coremltools
    available, ct = _check_coremltools()
    if available:
        print(f'coremltools: OK ({ct.__version__})')
    else:
        print('coremltools: NOT FOUND')
        print()
        print('Install instructions:')
        print('  pip install --extra-index-url https://pypi.anaconda.org/apple/repo/simple coremltools')
        sys.exit(1)

    print()
    print('Model specs:')
    print(f'  Input:  {_PRM_FEATURE_DIM} features (PRMFeatureVector)')
    print(f'  Hidden: {_PRM_HIDDEN_DIM} units (ReLU)')
    print(f'  Output: {_PRM_OUTPUT_DIM} reward (Tanh → [-1, 1])')
    print(f'  Target: Apple Neural Engine (ANE)')
    print()

    # Compile
    print(f'Target: {_PRM_MODEL_PATH}')
    print()

    if _PRM_MODEL_PATH.exists():
        print('Model already exists.')
        response = input('Recompile? [y/N]: ').strip().lower()
        if response != 'y':
            print('Skipped.')
            sys.exit(0)

    print('Compiling...')
    result = compile_prm_model(force=True)

    if result:
        print()
        print(f'SUCCESS: Model saved to {result}')
        print()
        print('Next steps:')
        print('  1. PRM will auto-load this model on first use')
        print('  2. ToT branch scoring will use ANE-accelerated inference')
    else:
        print()
        print('FAILED: Model compilation failed')
        sys.exit(1)
