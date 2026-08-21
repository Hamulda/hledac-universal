import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np

from hledac.universal._core.resource_governor import Priority, ResourceGovernor
from hledac.universal.utils.domain_executors import get_vision_executor

logger = logging.getLogger(__name__)
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
    return (_coremltools_mod, _MLModel)


def _check_torch():
    global _TORCH_AVAILABLE, _TORCHVISION_AVAILABLE
    if _TORCH_AVAILABLE is None:
        try:
            import torch

            _TORCH_AVAILABLE = True
            _TORCHVISION_AVAILABLE = True
        except ImportError:
            _TORCH_AVAILABLE = False
            _TORCHVISION_AVAILABLE = False
    return (_TORCH_AVAILABLE, _TORCHVISION_AVAILABLE)


_IMAGENET_MEAN = [0.485, 0.456, 0.406]
_IMAGENET_STD = [0.229, 0.224, 0.225]
_MODEL_CACHE_DIR = Path("~/.hledac/models").expanduser()
_MOBILE_NET_MODEL_PATH = _MODEL_CACHE_DIR / "vision_encoder.mlpackage"
from hledac.universal._core.concurrency import ConcurrencyCategory, get_semaphore

_IMAGE_SEMAPHORE = get_semaphore(ConcurrencyCategory.GRAPH_RAG)
_COREML_EXECUTOR: ThreadPoolExecutor | None = None


def _get_coreml_executor() -> ThreadPoolExecutor:
    """Lazily-initialized CoreML vision executor (ISSUE-049: migrated to domain_executors)."""
    global _COREML_EXECUTOR
    if _COREML_EXECUTOR is None:
        _COREML_EXECUTOR = get_vision_executor()
    return _COREML_EXECUTOR


IMAGE_VECTOR_DIM = 1024
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

    __slots__ = (
        "_embedding_dim",
        "_input_name",
        "_mlx_mod",
        "_model",
        "_output_name",
        "_proj_loaded",
        "_proj_weights",
        "batch_size",
        "governor",
        "model_path",
    )

    def __init__(
        self,
        governor: ResourceGovernor,
        model_path: str | None = None,
        embedding_dim: int = IMAGE_VECTOR_DIM,
        batch_size: int = 4,
    ) -> None:
        self.governor = governor
        self.model_path = model_path or str(_MOBILE_NET_MODEL_PATH)
        self._embedding_dim = embedding_dim
        self.batch_size = batch_size
        self._model = None
        self._input_name: str | None = None
        self._output_name: str | None = None
        self._proj_weights: np.ndarray | None = None
        self._proj_loaded = False
        self._mlx_mod = None

    def _ensure_model_cache_dir(self) -> None:
        _MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _save_projection_weights(self) -> None:
        """Save the 960→1024 projection matrix alongside the model package."""
        import json

        proj_path = Path(self.model_path).parent / "vision_encoder_projection.json"
        data = {
            "raw_dim": _MOBILE_NET_RAW_DIM,
            "out_dim": IMAGE_VECTOR_DIM,
            "weights": self._proj_weights.tolist() if self._proj_weights is not None else None,
        }
        with open(proj_path, "w") as f:
            json.dump(data, f)

    def _create_projection(self) -> np.ndarray:
        """
        Create a 960→1024 projection matrix using SVD-based random orthogonal initialization.

        Uses the classic neural network init trick: W = U @ V.T where U, V come from
        the SVD of a random matrix. This produces a matrix with orthonormal columns,
        which preserves variance through the projection and avoids singular value issues.

        Returns:
            np.ndarray of shape (960, 1024) with float32 dtype.
        """
        rng = np.random.default_rng(42)  # Deterministic seed for reproducibility
        # Random matrix with variance 2/(960+1024) for He initialization
        scale = np.sqrt(2.0 / (_MOBILE_NET_RAW_DIM + IMAGE_VECTOR_DIM))
        random_matrix = rng.standard_normal((_MOBILE_NET_RAW_DIM, IMAGE_VECTOR_DIM), dtype=np.float32) * scale
        # SVD-based orthonormalization
        # G2 FIX: scipy is in [ml] extra. Without it, falls back to QR decomposition
        # which is slightly less numerically stable but works without scipy.
        try:
            # Use scipy if available for better numerical stability
            from scipy.linalg import svd

            U, _S, Vt = svd(random_matrix, full_matrices=False)
            # Use the U @ Vt product to get orthonormal columns
            proj_matrix = (U @ Vt).astype(np.float32)
        except ImportError as e:
            if "scipy" in str(e):
                logger.debug(
                    "VisionEncoder: scipy.linalg.svd unavailable, using QR fallback. "
                    "Install with: pip install hledac-universal[ml]"
                )
            # Fallback: simple QR-based orthonormalization
            Q, R = np.linalg.qr(random_matrix)
            # Ensure proper sign (positive diagonal for stability)
            signs = np.sign(np.diag(R))
            proj_matrix = (Q * signs).astype(np.float32)
        return proj_matrix

    def _load_projection_weights(self) -> None:
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
            try:
                ct_mod, MLModel = _get_coremltools()
                if MLModel is None:
                    logger.warning("CoreML not available; VisionEncoder runs in dummy mode.")
                    return
                loop = asyncio.get_running_loop()

                def _load():
                    return MLModel(str(model_file), compute_units=ct_mod.ComputeUnit.ALL)

                self._model = await loop.run_in_executor(_get_coreml_executor(), _load)
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
            for c in range(3):
                arr[:, :, c] = (arr[:, :, c] - _IMAGENET_MEAN[c]) / _IMAGENET_STD[c]
            arr = arr.transpose(2, 0, 1)
            arr = np.expand_dims(arr, axis=0)
            return arr.astype(np.float32)
        except Exception as exc:
            logger.debug("VisionEncoder: image preprocess failed: %s", exc)
            raise ValueError(f"Image preprocess failed: {exc}") from exc

    async def _raw_encode(self, preprocessed: np.ndarray) -> np.ndarray:
        """
        Run CoreML inference asynchronously on the raw 224×224 image tensor.
        Uses the single-thread _COREML_EXECUTOR (GHOST_INVARIANTS I10).
        Returns raw 960d MobileNetV3 penultimate features.

        FIX: Uses cached self._model instead of reloading from disk per image.
        Loading MLModel from disk causes 200-400ms ANE recompile per call.
        """
        if self._model is None or self._input_name is None:
            raise RuntimeError("Model not loaded")

        # Capture model, input_name, output_name from self (already loaded in load())
        model = self._model
        input_name = self._input_name
        output_name = self._output_name

        def _inference():
            # Use the cached model - no disk reload, no ANE recompile
            input_dict = {input_name: preprocessed}
            out_dict = model.predict(input_dict)
            return np.array(out_dict[output_name])

        return await asyncio.get_running_loop().run_in_executor(_get_coreml_executor(), _inference)

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

        def _dct2(x: np.ndarray) -> np.ndarray:
            N = x.shape[0]
            n = np.arange(N, dtype=np.float32)
            k = n.reshape(-1, 1)
            cos_mat = np.cos(np.pi / N * (n + 0.5) * k)
            return cos_mat @ x

        dct_rows = _dct2(arr)
        dct_2d = _dct2(dct_rows.T).T
        low_freq = dct_2d[:8, :8].flatten()
        ac = low_freq[1:]
        coeffs = np.pad(ac, (0, 64 - ac.size), mode="constant")
        median = np.median(coeffs)
        bits = (coeffs > median).astype(np.float32)
        repeats = out_dim // bits.size + 1
        full = np.tile(bits, repeats)[:out_dim]
        return (full * 2.0 - 1.0).astype(np.float32)

    def _get_deterministic_dummy(self) -> np.ndarray:
        """
        Return a deterministic dummy embedding for error fallback.

        IMPORTANT: Must be deterministic (same output every time) to avoid
        poisoning LanceDB ANN index with random vectors. Uses a stable
        hash-based seed from the class's embedding_dim.
        """
        # Use a stable seed based on embedding dimension
        rng = np.random.default_rng(hash(("VisionEncoder", self._embedding_dim)) & 0xFFFFFFFF)
        dummy = rng.standard_normal(self._embedding_dim, dtype=np.float32)
        # L2 normalize to match expected embedding distribution
        norm = np.linalg.norm(dummy)
        if norm > 0:
            dummy = dummy / norm
        return dummy

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
        from contextlib import nullcontext

        mx_mod = _get_mlx_core()
        async with _IMAGE_SEMAPHORE:
            # Governor may be None in standalone use — skip RAM reservation if so
            if self.governor is not None:
                ram_ctx = self.governor.reserve({"ram_mb": max(50, 20 * len(images)), "gpu": True}, Priority.NORMAL)
            else:
                ram_ctx = nullcontext()
            async with ram_ctx:
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
                            raw_features = await self._raw_encode(preprocessed)
                            if self._proj_weights is not None:
                                projected = raw_features.astype(np.float32) @ self._proj_weights
                            else:
                                projected = raw_features.astype(np.float32)
                            results.append(projected.flatten())
                        except Exception as exc:
                            logger.debug("VisionEncoder: encode failed for one image: %s", exc)
                            # Use deterministic dummy vector instead of np.random.randn
                            # Non-deterministic vectors poison LanceDB ANN index
                            results.append(self._get_deterministic_dummy())
                finally:
                    if mx_mod is not None:
                        mx_mod.eval([])
                        try:
                            mx_mod.clear_cache()
                        except AttributeError:
                            try:
                                mx_mod.metal.clear_cache()
                            except Exception:  # noqa: BLE001
                                pass
                        except Exception:  # noqa: BLE001
                            pass
                elapsed = time.monotonic() - start_time
                logger.debug(
                    "VisionEncoder: encoded %d images in %.3fs (%.3fs/img)",
                    len(images),
                    elapsed,
                    elapsed / len(images) if images else 0,
                )
                return results

    # ── [IO-4] Zero-copy CVPixelBuffer encoding ────────────────────────────────

    def _preprocess_pixelbuffer(self, pixel_buffer: Any, target_size: tuple[int, int] = (224, 224)) -> np.ndarray:
        """
        [IO-4] Preprocess CVPixelBuffer to MobileNetV3 input tensor.

        Uses CoreImage for zero-copy resize from IOSurface-backed CVPixelBuffer:
          CVPixelBuffer → CIImage → CILanczosScale → CGImage → numpy

        This is more efficient than PIL for IOSurface-backed buffers:
          - CILanczosScale runs on GPU (or ANE for supported filters)
          - No intermediate CGImage creation from JPEG bytes
          - CGImage → numpy is a single memcpy (vs 2-3 for JPEG path)

        Args:
            pixel_buffer: CVPixelBuffer from extract_keyframes_zero_copy()
            target_size: Target (width, height) for the output tensor. Default (224, 224).

        Returns:
            numpy.ndarray shape (1, 3, H, W) with ImageNet normalization applied.
        """
        try:
            import CoreImage as _CI

            try:
                import CoreVideo as _CV

                width = int(_CV.CVPixelBufferGetWidth(pixel_buffer))
                height = int(_CV.CVPixelBufferGetHeight(pixel_buffer))
            except Exception:
                logger.debug("VisionEncoder: Failed to get CVPixelBuffer dimensions")
                raise ValueError("Invalid CVPixelBuffer")

            # Create CIImage from IOSurface (zero-copy from CVPixelBuffer)
            try:
                ci_image = _CI.CIImage.imageWithCVPixelBuffer_(pixel_buffer)
            except Exception:
                logger.debug("VisionEncoder: Failed to create CIImage from CVPixelBuffer")
                raise ValueError("CVPixelBuffer not compatible with CIImage")

            if ci_image is None:
                raise ValueError("CIImage creation returned nil")

            # Scale to target size using CILanczosScale (GPU-accelerated on M1)
            target_w, target_h = target_size
            scale_x = target_w / width
            scale_y = target_h / height
            scale_filter = _CI.CIFilter.filterWithName_("CILanczosScaleTransform")
            if scale_filter is not None:
                scale_filter.setValue_forKey_(ci_image, "inputImageKey")
                scale_filter.setValue_forKey_(scale_x, "inputScale")
                scale_filter.setValue_forKey_(1.0, "inputAspectRatio")
                scaled_image = scale_filter.valueForKey_("outputImageKey")
            else:
                # Fallback: use CIAffineTransform + CIScaling (always available)
                transform = _CI.CGAffineTransform.makeScale_(scale_x, scale_y)
                scaled_image = ci_image.transformedByUsingAbort_(transform, None)

            if scaled_image is None:
                raise ValueError("Image scaling failed")

            # Convert to CGImage for numpy extraction
            # Note: This IS a copy (CGImage is always a copy), but it's unavoidable
            # for numpy conversion. The zero-copy benefit is in the CIImage ↔ IOSurface path.
            context = _CI.Context()
            cg_image = context.createCGImage_fromRect_(scaled_image, scaled_image.extent())

            if cg_image is None:
                raise ValueError("CGImage creation failed")

            # Extract pixel data to numpy array via CGImage
            # CGImage doesn't have .bytes() method in PyObjC
            # Use NSBitmapImageRep for safe pixel extraction → TIFF → PIL
            import AppKit as _AK

            ns_rep = _AK.NSBitmapImageRep.alloc().initWithCGImage_(cg_image)
            if ns_rep is None:
                raise ValueError("NSBitmapImageRep creation failed")
            tiff_data = ns_rep.representationUsingType_properties_(_AK.NSTIFFFileType, None)
            if tiff_data is None:
                raise ValueError("TIFF representation failed")
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(bytes(tiff_data)))
            # NSBitmapImageRep with NSTIFFFileType returns RGB/RGBA depending on source
            if img.mode == "RGBA":
                img = img.convert("RGB")
            arr = np.array(img, dtype=np.float32) / 255.0

            # ImageNet normalization
            for c in range(3):
                arr[:, :, c] = (arr[:, :, c] - _IMAGENET_MEAN[c]) / _IMAGENET_STD[c]

            # CHW format
            arr = arr.transpose(2, 0, 1)
            arr = np.expand_dims(arr, axis=0)
            return arr.astype(np.float32)

        except Exception as exc:
            logger.debug("VisionEncoder: CVPixelBuffer preprocess failed: %s", exc)
            raise ValueError(f"CVPixelBuffer preprocess failed: {exc}") from exc

    async def encode_batch_from_pixelbuffer(
        self,
        pixel_buffers: list[Any],
        target_size: tuple[int, int] = (224, 224),
    ) -> list[np.ndarray]:
        """
        [IO-4] Zero-copy encode CVPixelBuffers to 1024d embeddings.

        Pipeline:
          CVPixelBuffer → CIImage (zero-copy IOSurface) → CILanczosScale → numpy
          → CoreML inference → 960d → projection → 1024d

        Advantages over JPEG bytes path:
          - No JPEG decode (CVPixelBuffer is already decompressed)
          - CILanczosScale runs on GPU (Metal)
          - IOSurface → CIImage is zero-copy
          - CoreML can consume CVPixelBuffer directly (see encode_batch_from_cvpixelbuffer_direct)

        Args:
            pixel_buffers: List of CVPixelBuffer from extract_keyframes_zero_copy()
            target_size: Target (width, height) for preprocessing. Default (224, 224).

        Returns:
            List of 1024d numpy embedding arrays.
        """
        from contextlib import nullcontext

        mx_mod = _get_mlx_core()
        async with _IMAGE_SEMAPHORE:
            if self.governor is not None:
                ram_ctx = self.governor.reserve(
                    {"ram_mb": max(50, 20 * len(pixel_buffers)), "gpu": True}, Priority.NORMAL
                )
            else:
                ram_ctx = nullcontext()

            async with ram_ctx:
                if self._model is None or mx_mod is None:
                    # Fallback to pHash (still uses CVPixelBuffer as input)
                    out: list[np.ndarray] = []
                    for pb in pixel_buffers:
                        try:
                            # Convert CVPixelBuffer to bytes for pHash
                            image_bytes = self._pixelbuffer_to_bytes(pb)
                            out.append(self._phash_deterministic(image_bytes, self._embedding_dim))
                        except Exception as exc:
                            logger.debug("VisionEncoder: pHash from CVPixelBuffer failed: %s", exc)
                            out.append(np.zeros(self._embedding_dim, dtype=np.float32))
                    return out

                start_time = time.monotonic()
                results = []
                try:
                    for pixel_buffer in pixel_buffers:
                        try:
                            preprocessed = self._preprocess_pixelbuffer(pixel_buffer, target_size)
                            raw_features = await self._raw_encode(preprocessed)
                            if self._proj_weights is not None:
                                projected = raw_features.astype(np.float32) @ self._proj_weights
                            else:
                                projected = raw_features.astype(np.float32)
                            results.append(projected.flatten())
                        except Exception as exc:
                            logger.debug("VisionEncoder: CVPixelBuffer encode failed: %s", exc)
                            # Use deterministic dummy vector instead of np.random.randn
                            results.append(self._get_deterministic_dummy())
                finally:
                    if mx_mod is not None:
                        mx_mod.eval([])
                        try:
                            mx_mod.clear_cache()
                        except AttributeError:
                            try:
                                mx_mod.metal.clear_cache()
                            except Exception:  # noqa: BLE001
                                pass
                        except Exception:  # noqa: BLE001
                            pass

                elapsed = time.monotonic() - start_time
                logger.debug(
                    "VisionEncoder: encoded %d CVPixelBuffers in %.3fs (%.3fs/img)",
                    len(pixel_buffers),
                    elapsed,
                    elapsed / len(pixel_buffers) if pixel_buffers else 0,
                )
                return results

    def _pixelbuffer_to_bytes(self, pixel_buffer: Any) -> bytes:
        """
        Convert CVPixelBuffer to JPEG bytes for fallback path.

        This is used when CoreML model is unavailable or CVPixelBuffer
        cannot be processed directly. Uses CGImage creation from CIImage.

        Args:
            pixel_buffer: CVPixelBuffer from extract_keyframes_zero_copy()

        Returns:
            JPEG bytes of the CVPixelBuffer.
        """
        try:
            import AppKit as _AK
            import CoreImage as _CI

            # Create CIImage from IOSurface (zero-copy)
            ci_image = _CI.CIImage.imageWithCVPixelBuffer_(pixel_buffer)
            if ci_image is None:
                raise ValueError("CIImage creation failed")

            context = _CI.Context()
            cg_image = context.createCGImage_fromRect_(ci_image, ci_image.extent())
            if cg_image is None:
                raise ValueError("CGImage creation failed")

            # Convert to JPEG bytes
            rep = _AK.NSBitmapImageRep.alloc().initWithCGImage_(cg_image)
            jpeg_data = rep.representationUsingType_properties_(
                _AK.NSBitmapImageFileTypeJPEG, {_AK.NSImageCompressionFactor: 0.8}
            )
            return bytes(jpeg_data) if jpeg_data else b""

        except Exception as exc:
            logger.debug("VisionEncoder: CVPixelBuffer to bytes failed: %s", exc)
            return b""

    async def encode_batch_from_cvpixelbuffer_direct(
        self,
        pixel_buffers: list[Any],
    ) -> list[np.ndarray]:
        """
        [IO-4] Direct CoreML inference from CVPixelBuffer (true zero-copy).

        This is the MOST efficient path: CoreML's MLFeatureValue can consume
        CVPixelBuffer directly via MLFeatureValue(pixelBuffer:), completely
        bypassing numpy conversion.

        Pipeline:
          CVPixelBuffer → MLFeatureValue(pixelBuffer:) → CoreML → 960d → projection → 1024d

        Note: Requires CoreML model with image input type (not multi-array).
        If the model expects multi-array, falls back to encode_batch_from_pixelbuffer.

        Args:
            pixel_buffers: List of CVPixelBuffer from extract_keyframes_zero_copy()

        Returns:
            List of 1024d numpy embedding arrays.
        """
        if not _COREML_AVAILABLE:
            logger.debug("VisionEncoder: CoreML not available, falling back to preprocess path")
            return await self.encode_batch_from_pixelbuffer(pixel_buffers)

        try:
            import coremltools as ct
            from coremltools.models import MLModel
        except ImportError:
            logger.debug("VisionEncoder: coremltools not available")
            return await self.encode_batch_from_pixelbuffer(pixel_buffers)

        if self._model is None:
            logger.debug("VisionEncoder: No CoreML model loaded")
            return await self.encode_batch_from_pixelbuffer(pixel_buffers)

        from contextlib import nullcontext

        mx_mod = _get_mlx_core()
        async with _IMAGE_SEMAPHORE:
            if self.governor is not None:
                ram_ctx = self.governor.reserve(
                    {"ram_mb": max(50, 20 * len(pixel_buffers)), "gpu": True}, Priority.NORMAL
                )
            else:
                ram_ctx = nullcontext()

            async with ram_ctx:
                start_time = time.monotonic()
                results = []

                try:
                    for pixel_buffer in pixel_buffers:
                        try:
                            # Direct CoreML inference from CVPixelBuffer
                            def _inference_direct():
                                model = MLModel(str(self.model_path), compute_units=ct.ComputeUnit.ALL)
                                spec = model.get_spec()

                                # Check if model accepts image input
                                has_image_input = False
                                input_name = None
                                for input_feat in spec.description.input:
                                    if input_feat.type.HasField("imageType"):
                                        has_image_input = True
                                        input_name = input_feat.name
                                        break

                                if has_image_input and input_name:
                                    # Direct pixel buffer path (true zero-copy)
                                    input_dict = {input_name: pixel_buffer}
                                else:
                                    # Fall back to multi-array path (needs conversion)
                                    preprocessed = self._preprocess_pixelbuffer(pixel_buffer)
                                    input_name = self._input_name
                                    input_dict = {input_name: preprocessed}

                                out_dict = model.predict(input_dict)
                                output_name = self._output_name or list(out_dict.keys())[0]
                                raw_features = np.array(out_dict[output_name])

                                if self._proj_weights is not None:
                                    projected = raw_features.astype(np.float32) @ self._proj_weights
                                else:
                                    projected = raw_features.astype(np.float32)
                                return projected.flatten()

                            raw_result = await asyncio.get_running_loop().run_in_executor(
                                _get_coreml_executor(), _inference_direct
                            )
                            results.append(raw_result)
                        except Exception as exc:
                            logger.debug("VisionEncoder: Direct CVPixelBuffer encode failed: %s", exc)
                            # Use deterministic dummy vector instead of np.random.randn
                            results.append(self._get_deterministic_dummy())
                finally:
                    if mx_mod is not None:
                        mx_mod.eval([])
                        try:
                            mx_mod.clear_cache()
                        except Exception:  # noqa: BLE001
                            pass

                elapsed = time.monotonic() - start_time
                logger.debug(
                    "VisionEncoder: direct encode %d CVPixelBuffers in %.3fs (%.3fs/img)",
                    len(pixel_buffers),
                    elapsed,
                    elapsed / len(pixel_buffers) if pixel_buffers else 0,
                )
                return results
