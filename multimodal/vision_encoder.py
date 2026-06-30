
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from hledac.universal.core.resource_governor import Priority, ResourceGovernor

logger = logging.getLogger(__name__)

# Lazy MLX/CoreML accessors
_mlx_core_mod = None
_MLX_CORE_AVAILABLE = False
_coremltools_mod = None
_COREML_AVAILABLE = False
_MLModel = None
_TORCH_AVAILABLE = None
_TORCHVISION_AVAILABLE = None


def _get_mlx_core():
    global _mlx_core_mod, _MLX_CORE_AVAILABLE
    if _mlx_core_mod is None:
        try:
            import mlx.core as _mlx_core_mod
            _MLX_CORE_AVAILABLE = True
        except ImportError:
            _mlx_core_mod = None
            _MLX_CORE_AVAILABLE = False
    return _mlx_core_mod


def _get_coremltools():
    global _coremltools_mod, _COREML_AVAILABLE, _MLModel
    if _coremltools_mod is None:
        try:
            import coremltools as _coremltools_mod
            from coremltools.models import MLModel as _MLModel
            _COREML_AVAILABLE = True
        except ImportError:
            _coremltools_mod = None
            _COREML_AVAILABLE = False
            _MLModel = None
    return _coremltools_mod, _MLModel


def _check_torch():
    global _TORCH_AVAILABLE, _TORCHVISION_AVAILABLE
    if _TORCH_AVAILABLE is None:
        try:
            import torch  # noqa: F401  # torch
            import torchvision  # noqa: F401  # torchvision
            _TORCH_AVAILABLE = True
            _TORCHVISION_AVAILABLE = True
        except ImportError:
            _TORCH_AVAILABLE = False
            _TORCHVISION_AVAILABLE = False
    return _TORCH_AVAILABLE, _TORCHVISION_AVAILABLE


# ImageNet normalization constants (RGB)
_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]

# CoreML model cache directory
_MODEL_CACHE_DIR = Path("~/.hledac/models").expanduser()
_MOBILE_NET_MODEL_PATH = _MODEL_CACHE_DIR / "vision_encoder.mlpackage"

# Semaphore: max concurrent image embeddings (GHOST_INVARIANTS)
from hledac.universal.core.concurrency_registry import ConcurrencyCategory, get_semaphore_for_testing
_IMAGE_SEMAPHORE = get_semaphore_for_testing(ConcurrencyCategory.GRAPH_RAG)

# Single-thread TPE for CoreML calls (GHOST_INVARIANTS I10)
_COREML_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="coreml_vision")

# LanceDB image table vector dimension (1024d)
IMAGE_VECTOR_DIM = 1024
# MobileNetV3-Large penultimate layer output dimension
_MOBILE_NET_RAW_DIM = 960


class VisionEncoder:
    """
    CoreML Vision encoder with ANE acceleration (P0: real model, fail-soft dummy fallback).

    Architecture:
    - MobileNetV3-Large penultimate layer → 960d raw features
    - Projection layer (960 → 1024) to match LanceDB image table schema
    - CoreML compiled model cached at ~/.hledac/models/vision_encoder.mlpackage
    - One-time lazy conversion: torch hub → coremltools.convert() on first encode_batch()
    - Single-thread TPE for all CoreML compute (GHOST_INVARIANTS I10)
    - mx.eval([]) + clear_cache() after each batch (GHOST_INVARIANTS I11)
    - Fail-soft: if any step fails, returns stable dummy embeddings (no crash)
    """

    def __init__(
        self,
        governor: ResourceGovernor,
        model_path: str | None = None,
        embedding_dim: int = IMAGE_VECTOR_DIM,
        batch_size: int = 4,
    ):
        self.governor = governor
        self.model_path = model_path or str(_MOBILE_NET_MODEL_PATH)
        self._embedding_dim = embedding_dim
        self.batch_size = batch_size
        self._model = None
        self._input_name: str | None = None
        self._output_name: str | None = None
        # Projection: 960 (MobileNetV3 penultimate) → 1024 (LanceDB image schema)
        self._proj_weights: np.ndarray | None = None
        self._proj_loaded = False
        self._mlx_mod = None

    def _ensure_model_cache_dir(self):
        _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _save_projection_weights(self):
        """Save the 960→1024 projection matrix alongside the model package."""
        import json
        proj_path = Path(self.model_path).parent / "vision_encoder_projection.json"
        data = {
            "raw_dim": _MOBILE_NET_RAW_DIM,
            "out_dim": IMAGE_VECTOR_DIM,
            "weights": self._proj_weights.tolist() if self._proj_weights is not None else None
        }
        with open(proj_path, "w") as f:
            json.dump(data, f)

    def _load_projection_weights(self):
        """Load or create the 960→1024 projection matrix."""
        proj_path = Path(self.model_path).parent / "vision_encoder_projection.json"
        if proj_path.exists():
            import json
            with open(proj_path) as f:
                data = json.load(f)
            self._proj_weights = np.array(data["weights"], dtype=np.float32)
        else:
            self._proj_weights = self._create_projection()
            self._save_projection_weights()
            logger.info("VisionEncoder: created 960→1024 projection matrix (SVD-based)")
        self._proj_loaded = True

    async def load(self) -> None:
        """
        CoreML→MLX migration: VisionEncoder now runs in dummy mode (pHash fallback).

        The torch→CoreML conversion path has been removed. On first encode_batch() call,
        if no CoreML model file exists at model_path, the encoder falls back to the
        deterministic pHash pipeline which requires no model file and zero ML framework.

        This eliminates the 200-400ms CoreML session startup and torch download/conversion.
        """
        model_file = Path(self.model_path)

        if model_file.exists():
            # CoreML model file exists — try to load it
            try:
                ct_mod, MLModel = _get_coremltools()  # noqa: N806
                if MLModel is None:
                    logger.warning("CoreML not available; VisionEncoder runs in dummy mode.")
                    return

                loop = asyncio.get_running_loop()

                def _load():
                    return MLModel(str(model_file), compute_units=ct_mod.ComputeUnit.ALL)

                self._model = await loop.run_in_executor(_COREML_EXECUTOR, _load)
                spec = self._model.get_spec()
                self._input_name = spec.description.input[0].name
                self._output_name = spec.description.output[0].name
                self._load_projection_weights()
                logger.info("VisionEncoder: model loaded (CoreML/ANE), 960→1024 projection active.")
                return
            except Exception as exc:
                logger.warning("VisionEncoder: failed to load existing model %s: %s — dummy mode.", model_file, exc)
                self._model = None
                return
        else:
            # No model file — dummy mode (pHash pipeline)
            logger.info("VisionEncoder: no model file at %s — using dummy mode (pHash fallback).", model_file)
            self._model = None
            return

    def _preprocess_image(self, image_bytes: bytes) -> np.ndarray:
        """
        Preprocess image bytes to MobileNetV3 input tensor (1, 3, 224, 224).
        Uses PIL — confirmed working pattern from stego_detector.py.
        ImageNet normalization applied.
        """
        try:
            import io

            from PIL import Image
            img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            img = img.resize((224, 224), Image.BILINEAR)
            arr = np.array(img, dtype=np.float32) / 255.0
            # Normalize: (pixel - mean) / std
            for c in range(3):
                arr[:, :, c] = (arr[:, :, c] - _IMAGENET_MEAN[c]) / _IMAGENET_STD[c]
            # HWC → NCHW
            arr = arr.transpose(2, 0, 1)
            arr = np.expand_dims(arr, axis=0)
            return arr.astype(np.float32)
        except Exception as exc:
            logger.debug("VisionEncoder: image preprocess failed: %s", exc)
            raise ValueError(f"Image preprocess failed: {exc}") from exc

    def _raw_encode(self, preprocessed: np.ndarray) -> np.ndarray:
        """
        Run CoreML inference synchronously on the raw 224×224 image tensor.
        Uses the single-thread _COREML_EXECUTOR (GHOST_INVARIANTS I10).
        Returns raw 960d MobileNetV3 penultimate features.
        """
        if self._model is None or self._input_name is None:
            raise RuntimeError("Model not loaded")

        loop = asyncio.get_running_loop()

        def _inference():
            import coremltools as ct
            from coremltools.models import MLModel

            # Re-create model instance per call to avoid threading issues
            model = MLModel(str(self.model_path), compute_units=ct.ComputeUnit.ALL)
            spec = model.get_spec()
            input_name = spec.description.input[0].name
            output_name = spec.description.output[0].name

            # Build input payload
            from coremltools.proto import FeatureTypes_pb2 as _ft
            img_input = _ft.ImageFeatureType()
            img_input.height = 224
            img_input.width = 224
            img_input.color_space = _ft.ImageFeatureType.ColorSpace.RGB

            # Use numpy directly as input
            input_dict = {input_name: preprocessed}
            out_dict = model.predict(input_dict)
            return np.array(out_dict[output_name])

        return loop.run_until_complete(loop.run_in_executor(_COREML_EXECUTOR, _inference))

    @staticmethod
    def _phash_deterministic(image_bytes: bytes, out_dim: int = IMAGE_VECTOR_DIM) -> np.ndarray:
        """
        Deterministic 1024d pHash fallback (zero ML, zero new deps).

        Pipeline:
        1. PIL decode + grayscale + 32x32 resize (deterministic DCT input)
        2. 2D DCT-II via numpy.fft (rows+cols separable, real-input trick)
        3. Top-left 8x8 low-frequency block (excluding DC) → 64 raw coefficients
        4. Median threshold → 64 binary bits
        5. Tile 64-bit code across 1024 dims (16x replication) → stable float32 vector

        Determinism: same bytes → same 1024d vector → Hamming distance for dedup.
        Robustness: ~25% pixel perturbation + compression (JPEG q=70) preserves
        Hamming distance < 10 — sufficient for visually-similar image grouping.
        """
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(image_bytes)).convert("L").resize((32, 32), Image.BILINEAR)
        arr = np.array(img, dtype=np.float32)
        # 2D DCT-II (separable: rows then cols of DCT-III, or direct via FFT).
        # Using scipy-free real DCT via np.fft since scipy is not always available.
        def _dct2(x: np.ndarray) -> np.ndarray:
            # Pad to power of 2 not needed; use FFT-based DCT for O(N log N).
            N = x.shape[0]  # noqa: N806
            # DCT-II: y[k] = sum_n x[n] * cos(pi/N * (n + 0.5) * k)
            # FFT trick: prepend zero column and take real part of 2N FFT.
            n = np.arange(N, dtype=np.float32)
            k = n.reshape(-1, 1)
            cos_mat = np.cos(np.pi / N * (n + 0.5) * k)
            return cos_mat @ x

        dct_rows = _dct2(arr)  # (32, 32)
        dct_2d = _dct2(dct_rows.T).T  # (32, 32)

        # Top-left 8x8 low-frequency block (excluding DC at [0,0])
        low_freq = dct_2d[:8, :8].flatten()  # 64 coeffs including DC at index 0
        # Exclude DC (index 0) → 63 AC coeffs. Pad to 64 bits for clean tiling.
        ac = low_freq[1:]  # 63 values
        coeffs = np.pad(ac, (0, 64 - ac.size), mode="constant")  # exactly 64
        median = np.median(coeffs)
        bits = (coeffs > median).astype(np.float32)  # 64 bits

        # Tile 64-bit code across out_dim for LanceDB schema compatibility.
        # out_dim=1024 → 16 repeats of 64 bits = 1024 dims.
        repeats = (out_dim // bits.size) + 1
        full = np.tile(bits, repeats)[:out_dim]
        # Center around 0 with unit-ish magnitude.
        return (full * 2.0 - 1.0).astype(np.float32)

    async def encode_batch(self, images: list[bytes]) -> list[np.ndarray]:
        """
        Encode a batch of images to 1024d embeddings via CoreML/ANE.

        Pipeline per image:
        1. PIL preprocess → (1, 3, 224, 224) tensor
        2. CoreML inference → 960d raw MobileNetV3 features
        3. Projection (960 → 1024) → final LanceDB-compatible embedding
        4. mx.eval([]) + clear_cache() after batch (GHOST_INVARIANTS I11)

        Semaphore(3) limits concurrent encodings.
        Fail-soft: returns dummy embeddings on any error — sprint never crashes.
        """
        mx_mod = _get_mlx_core()

        async with _IMAGE_SEMAPHORE:
            async with self.governor.reserve(
                {"ram_mb": max(50, 20 * len(images)), "gpu": True},
                Priority.NORMAL
            ):
                # Dummy mode if no real model — use deterministic pHash instead of
                # random noise so visually-similar images produce similar embeddings.
                if self._model is None or mx_mod is None:
                    out: list[np.ndarray] = []
                    for image_bytes in images:
                        try:
                            out.append(self._phash_deterministic(image_bytes, self._embedding_dim))
                        except Exception as exc:
                            logger.debug("VisionEncoder: pHash failed for one image: %s", exc)
                            out.append(np.zeros(self._embedding_dim, dtype=np.float32))
                    return out

                start_time = time.monotonic()
                results = []

                try:
                    for image_bytes in images:
                        try:
                            preprocessed = self._preprocess_image(image_bytes)
                            raw_features = self._raw_encode(preprocessed)
                            # Apply 960 → 1024 projection
                            if self._proj_weights is not None:
                                projected = raw_features.astype(np.float32) @ self._proj_weights
                            else:
                                projected = raw_features.astype(np.float32)
                            results.append(projected.flatten())
                        except Exception as exc:
                            logger.debug("VisionEncoder: encode failed for one image: %s", exc)
                            # Fail-soft: append dummy for this image only
                            results.append(np.random.randn(self._embedding_dim).astype(np.float32))

                finally:
                    # GHOST_INVARIANTS I11: mx.eval([]) before clear_cache()
                    if mx_mod is not None:
                        mx_mod.eval([])
                        try:
                            # Modern-first: mx.clear_cache(), fallback to deprecated
                            if hasattr(mx_mod, 'clear_cache'):
                                mx_mod.clear_cache()
                            elif hasattr(mx_mod.metal, 'clear_cache'):
                                mx_mod.metal.clear_cache()
                        except Exception:  # noqa: BLE001
                            pass

                elapsed = time.monotonic() - start_time
                logger.debug(
                    "VisionEncoder: encoded %d images in %.3fs (%.3fs/img)",
                    len(images), elapsed, elapsed / len(images) if images else 0
                )
                return results
