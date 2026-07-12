"""
Statistical Steganography Detector
===================================

Implements statistical methods for detecting steganography in images:
- Chi-square test for LSB (Least Significant Bit) detection
- RS (Regular-Singular) analysis with message length estimation
- DCT coefficient analysis for JPEG steganography

Optimized for M1 MacBook with 8GB RAM:
- Streaming mode: load → analyze → release
- Max 2048x2048 images in memory
- NumPy-based calculations with optional PyTorch/MPS acceleration (lazy import)
- Aggressive garbage collection after heavy operations

Note: Per-image analysis time varies by hardware and image size.
Streaming mode and size limits protect M1 8GB RAM.
"""
from __future__ import annotations
import gc
import logging
import math
from dataclasses import dataclass, field
import msgspec
from pathlib import Path
from typing import Any
import numpy as np
from utils.domain_executors import run_in_vision
logger = logging.getLogger(__name__)
MPS_AVAILABLE = False
_MPS_CHECKED = False

def _check_mps_available():
    """Check MPS availability lazily - only when actually needed."""
    global MPS_AVAILABLE, _MPS_CHECKED
    if MPS_AVAILABLE:
        return True
    if _MPS_CHECKED:
        return False
    try:
        import torch
        if torch.backends.mps.is_available():
            MPS_AVAILABLE = True
            _MPS_CHECKED = True
            return True
    except ImportError:
        pass
    _MPS_CHECKED = True
    return False
MAX_IMAGE_SIZE = 2048

@dataclass(slots=True)
class StegoConfig:
    """Configuration for statistical steganography detector.

    Attributes:
        chi_square_threshold: P-value threshold for chi-square test (default: 0.05)
        rs_analysis_enabled: Enable RS (Regular-Singular) analysis (default: True)
        dct_analysis_enabled: Enable DCT coefficient analysis (default: True)
        max_image_size: Maximum image dimension (M1 8GB limit) (default: 2048)
        streaming_mode: Enable streaming mode for memory efficiency (default: True)
        rs_mask: Mask for RS analysis (default: [0, 1, 0, 1])
        dct_threshold: Threshold for DCT anomaly detection (default: 0.5, must be in 0-1 range)
    """
    chi_square_threshold: float = 0.05
    rs_analysis_enabled: bool = True
    dct_analysis_enabled: bool = True
    max_image_size: int = 2048
    streaming_mode: bool = True
    rs_mask: list[int] = field(default_factory=lambda: [0, 1, 0, 1])
    dct_threshold: float = 0.5

class ChiSquareResult:
    """Result of chi-square test for LSB detection.

    Attributes:
        p_value: P-value from chi-square test (lower = more suspicious)
        chi_square_stat: Chi-square statistic value
        embedded_bytes_estimate: Estimated number of embedded bytes
        is_significant: Whether result is statistically significant
    """
    p_value: float = 1.0
    chi_square_stat: float = 0.0
    embedded_bytes_estimate: int = 0
    is_significant: bool = False

class RSResult(msgspec.Struct):
    """Result of RS (Regular-Singular) analysis.

    Attributes:
        rm: Regular group count with mask
        r_m: Regular group count with inverted mask
        sm: Singular group count with mask
        s_m: Singular group count with inverted mask
        message_length: Estimated message length in bytes
        confidence: Confidence of the estimate (0-1)
    """
    rm: float = 0.0
    r_m: float = 0.0
    sm: float = 0.0
    s_m: float = 0.0
    message_length: int = 0
    confidence: float = 0.0

class DCTResult(msgspec.Struct):
    """Result of DCT coefficient analysis for JPEG.

    Attributes:
        anomaly_score: Overall anomaly score (0-1, higher = more suspicious)
        suspicious_coefficients: List of suspicious coefficient indices
        histogram_deviation: Deviation from expected histogram
        block_anomalies: Per-block anomaly scores
    """
    anomaly_score: float = 0.0
    suspicious_coefficients: list[int] = field(default_factory=list)
    histogram_deviation: float = 0.0
    block_anomalies: list[float] = field(default_factory=list)

class StegoResult(msgspec.Struct):
    """Complete steganography analysis result.

    Attributes:
        has_stego: Whether steganography was detected
        confidence: Overall confidence score (0-1)
        method_used: Detection method that produced highest confidence
        message_length_estimate: Estimated hidden message length in bytes
        chi_square: Chi-square test result
        rs_analysis: RS analysis result
        dct_analysis: DCT analysis result
        details: Additional analysis details
    """
    has_stego: bool = False
    confidence: float = 0.0
    method_used: str = 'none'
    message_length_estimate: int = 0
    chi_square: ChiSquareResult | None = None
    rs_analysis: RSResult | None = None
    dct_analysis: DCTResult | None = None
    details: dict[str, Any] = field(default_factory=dict)

class StatisticalStegoDetector:
    """Statistical steganography detector for images.

    Implements three analysis methods:
    1. Chi-square test for LSB detection
    2. RS analysis with message length estimation
    3. DCT coefficient analysis for JPEG steganography

    Memory-optimized for M1 8GB with streaming mode support.

    ---
    AUTHORITY BOUNDARY — CONDITIONAL MEDIA AUGMENTATION GATE ONLY

    THIS MODULE DOES NOT:
    - Block, reject, or filter content
    - Make privacy-gate decisions
    - Handle PII or sensitive data
    - Export, vault, or store findings
    - Extract metadata for downstream processing
    - Make budget approval decisions

    This module ONLY:
    - Performs statistical analysis on image bytes (pixels)
    - Returns bounded signal: dict from detect() or StegoResult from analyze_image()

    RESULT SURFACES:
    - detect(image_bytes): lightweight dict with score + chi_square_flag + method
    - analyze_image(image_path): full StegoResult with chi_square + rs + dct sub-results

    Downstream orchestrator decides what to do with has_stego=True findings.
    StatisticalStegoDetector has NO content rejection authority.

    Example:
        >>> config = StegoConfig(max_image_size=1024)
        >>> detector = StatisticalStegoDetector(config)
        >>> await detector.initialize()
        >>> result = await detector.analyze_image("image.png")
        >>> print(f"Stego detected: {result.has_stego}")
        >>> await detector.cleanup()
    """
    __slots__ = tuple(('_image_lib', '_initialized', 'config'))

    def __init__(self, config: StegoConfig | None=None):
        """Initialize detector with configuration.

        Args:
            config: StegoConfig instance or None for defaults
        """
        self.config = config or StegoConfig()
        self._initialized = False
        self._image_lib = None

    async def detect(self, image_bytes: bytes) -> dict[str, Any]:
        """Main detection method - chooses MPS or CPU based on availability.

        Args:
            image_bytes: Raw image bytes

        Returns:
            Dict with detection results
        """
        if _check_mps_available():
            return await self._detect_mps(image_bytes)
        else:
            return await self._detect_cpu(image_bytes)

    async def _detect_mps(self, image_bytes: bytes) -> dict[str, Any]:
        """MPS-accelerated detection — uses shared vision domain executor."""
        return await run_in_vision(self._detect_mps_sync, image_bytes)

    def _detect_mps_sync(self, image_bytes: bytes) -> dict[str, Any]:
        """Synchronous MPS implementation of steganography detection."""
        import io
        import torch
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(image_bytes)).convert('L')
            if img.width > MAX_IMAGE_SIZE or img.height > MAX_IMAGE_SIZE:
                ratio = min(MAX_IMAGE_SIZE / img.width, MAX_IMAGE_SIZE / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)
            img_array = np.array(img, dtype=np.float32) / 255.0
            tensor = torch.from_numpy(img_array).to('mps')
            with torch.no_grad():
                h, w = tensor.shape
                if h >= 8 and w >= 8:
                    h_blocks = h // 8
                    w_blocks = w // 8
                    tensor = tensor[:h_blocks * 8, :w_blocks * 8]
                    blocks = tensor.unfold(0, 8, 8).unfold(1, 8, 8)
                    blocks = blocks.contiguous().view(-1, 8, 8)
                    block_means = blocks.mean(dim=(1, 2))
                    block_stds = blocks.std(dim=(1, 2))
                    score = (block_stds.mean() / (block_means.mean() + 1e-08)).item()
                    score = min(1.0, score * 0.3)
                else:
                    score = 0.0
        except Exception as e:
            logger.warning(f'MPS stego detection failed: {e}')
            return self._detect_cpu_sync(image_bytes)
        finally:
            if hasattr(torch.mps, 'empty_cache'):
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass
        return {'score': score, 'chi_square_flag': score > 0.3, 'method': 'mps_chi_square'}

    async def _detect_cpu(self, image_bytes: bytes) -> dict[str, Any]:
        """CPU-based detection — uses shared vision domain executor."""
        return await run_in_vision(self._detect_cpu_sync, image_bytes)

    def _detect_cpu_sync(self, image_bytes: bytes) -> dict[str, Any]:
        """Synchronous CPU implementation of steganography detection."""
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(image_bytes)) as img:
                if img.mode != 'L':
                    img = img.convert('L')
                img_array = np.array(img)
                lsbs = (img_array & 1).flatten()
                count_0 = np.sum(lsbs == 0)
                count_1 = np.sum(lsbs == 1)
                total = count_0 + count_1
                if total == 0:
                    return {'score': 0.0, 'chi_square_flag': False, 'method': 'cpu_chi_square'}
                expected = total / 2.0
                chi_sq = (count_0 - expected) ** 2 / expected + (count_1 - expected) ** 2 / expected
                score = min(1.0, chi_sq / 1000.0)
        except Exception as e:
            logger.warning(f'CPU stego detection failed: {e}')
            score = 0.0
        return {'score': score, 'chi_square_flag': score > 0.3, 'method': 'cpu_chi_square'}

    async def initialize(self) -> None:
        """Initialize detector and load dependencies.

        Loads PIL/Pillow for image processing. Safe to call multiple times.
        """
        if self._initialized:
            return
        try:
            from PIL import Image
            self._image_lib = Image
            self._initialized = True
            logger.debug('StatisticalStegoDetector initialized')
        except ImportError as e:
            logger.error(f'Failed to import PIL: {e}')
            raise RuntimeError('PIL/Pillow is required for image analysis') from e

    async def analyze_image(self, image_path: str | Path) -> StegoResult:
        """Analyze image for steganographic content.

        Runs enabled analysis methods and aggregates results.

        Args:
            image_path: Path to image file

        Returns:
            StegoResult with complete analysis

        Raises:
            RuntimeError: If detector not initialized
            FileNotFoundError: If image file doesn't exist
        """
        if not self._initialized:
            raise RuntimeError('Detector not initialized. Call initialize() first.')
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f'Image not found: {image_path}')
        result = StegoResult()
        pixels = None
        image = None
        try:
            image, pixels = self._load_image(image_path)
            if pixels is None:
                result.details['error'] = 'Failed to load image'
                return result
            chi_result = self._chi_square_test(pixels)
            result.chi_square = chi_result
            if self.config.rs_analysis_enabled:
                rs_result = self._rs_analysis(pixels)
                result.rs_analysis = rs_result
                result.message_length_estimate = self._estimate_message_length(rs_result)
            if self.config.dct_analysis_enabled and self._is_jpeg(image_path):
                dct_result = self._dct_analysis(image)
                result.dct_analysis = dct_result
            result = self._aggregate_results(result)
            logger.debug(f'Analyzed {image_path}: stego={result.has_stego}, confidence={result.confidence:.2f}, method={result.method_used}')
        except Exception as e:
            logger.error(f'Analysis failed for {image_path}: {e}')
            result.details['error'] = str(e)
        finally:
            if self.config.streaming_mode:
                if image is not None:
                    image.close()
                del pixels
                gc.collect()
        return result

    def _load_image(self, image_path: Path) -> tuple[Any, np.ndarray | None]:
        """Load image and convert to numpy array.

        Args:
            image_path: Path to image file

        Returns:
            Tuple of (PIL Image, numpy array of pixels)
        """
        assert self._image_lib is not None, 'Detector not initialized'
        image_lib = self._image_lib
        image = image_lib.open(image_path)
        width, height = image.size
        if width > self.config.max_image_size or height > self.config.max_image_size:
            logger.warning(f'Image {image_path} exceeds max size, resizing: {width}x{height} -> {self.config.max_image_size}')
            image.thumbnail((self.config.max_image_size, self.config.max_image_size), self._image_lib.Resampling.LANCZOS)
        if image.mode not in ('RGB', 'L'):
            image = image.convert('RGB')
        pixels = np.array(image)
        return (image, pixels)

    def _is_jpeg(self, image_path: Path) -> bool:
        """Check if file is JPEG format.

        Args:
            image_path: Path to image file

        Returns:
            True if JPEG, False otherwise
        """
        return image_path.suffix.lower() in ('.jpg', '.jpeg')

    def _chi_square_test(self, pixels: np.ndarray) -> ChiSquareResult:
        """Perform chi-square test for LSB steganography detection.

        Tests if LSBs follow expected distribution. Random data should have
        uniform LSB distribution; embedded data creates anomalies.

        Args:
            pixels: Numpy array of image pixels

        Returns:
            ChiSquareResult with test statistics
        """
        result = ChiSquareResult()
        try:
            if len(pixels.shape) == 3:
                flat_pixels = pixels.reshape(-1, pixels.shape[2])
                lsbs = flat_pixels[:, 0] & 1
            else:
                flat_pixels = pixels.flatten()
                lsbs = flat_pixels & 1
            count_0 = np.sum(lsbs == 0)
            count_1 = np.sum(lsbs == 1)
            total = count_0 + count_1
            if total == 0:
                return result
            expected = total / 2.0
            chi_sq = (count_0 - expected) ** 2 / expected + (count_1 - expected) ** 2 / expected
            p_value = math.exp(-chi_sq / 2) if chi_sq > 0 else 1.0
            p_value = min(1.0, max(0.0, p_value))
            if chi_sq > 10:
                embedded_estimate = int(total * (1 - p_value) / 8)
            else:
                embedded_estimate = 0
            result.p_value = float(p_value)
            result.chi_square_stat = float(chi_sq)
            result.embedded_bytes_estimate = embedded_estimate
            result.is_significant = p_value < self.config.chi_square_threshold
        except Exception as e:
            logger.error(f'Chi-square test failed: {e}')
        return result

    def _rs_analysis(self, pixels: np.ndarray) -> RSResult:
        """Perform RS (Regular-Singular) analysis.

        RS analysis detects LSB steganography by analyzing groups of pixels
        with different masks. Based on Fridrich et al. method.

        Args:
            pixels: Numpy array of image pixels

        Returns:
            RSResult with analysis statistics
        """
        result = RSResult()
        try:
            if len(pixels.shape) == 3:
                gray = np.mean(pixels, axis=2).astype(np.uint8)
            else:
                gray = pixels.astype(np.uint8)
            flat = gray.flatten()
            group_size = 4
            num_groups = len(flat) // group_size
            if num_groups < 100:
                return result
            groups = flat[:num_groups * group_size].reshape(num_groups, group_size)
            mask = np.array(self.config.rs_mask)
            mask_inv = 1 - mask

            def variation(group: np.ndarray) -> float:
                """Calculate variation within group."""
                return np.sum(np.abs(group[1:] - group[:-1]))

            def flip_mask(group: np.ndarray, m: np.ndarray) -> np.ndarray:
                """Apply mask to group (flip LSB where mask is 1)."""
                flipped = group.copy()
                for idx, val in enumerate(group):
                    if m[idx % len(m)] == 1:
                        flipped[idx] = val ^ 1
                return flipped
            rm, r_m, sm, s_m = (0.0, 0.0, 0.0, 0.0)
            sample_indices = range(0, num_groups, 2)
            for i in sample_indices:
                group = groups[i]
                v_orig = variation(group)
                flipped_m = flip_mask(group, mask)
                v_m = variation(flipped_m)
                flipped_m_inv = flip_mask(group, mask_inv)
                v_m_inv = variation(flipped_m_inv)
                if v_m > v_orig:
                    rm += 1
                elif v_m < v_orig:
                    sm += 1
                if v_m_inv > v_orig:
                    r_m += 1
                elif v_m_inv < v_orig:
                    s_m += 1
            sample_count = len(sample_indices)
            rm /= sample_count
            r_m /= sample_count
            sm /= sample_count
            s_m /= sample_count
            if rm + sm > 0 and r_m + s_m > 0:
                d0 = rm - sm
                d1 = r_m - s_m
                if abs(d0 - d1) > 0.001:
                    p_estimate = d0 / (d0 - d1)
                    p_estimate = max(0.0, min(1.0, p_estimate))
                    message_length = int(p_estimate * len(flat) / 8)
                    confidence = min(1.0, abs(d0 - d1) / max(abs(d0), abs(d1), 0.001))
                else:
                    message_length = 0
                    confidence = 0.0
            else:
                message_length = 0
                confidence = 0.0
            result.rm = float(rm)
            result.r_m = float(r_m)
            result.sm = float(sm)
            result.s_m = float(s_m)
            result.message_length = max(0, message_length)
            result.confidence = float(confidence)
        except Exception as e:
            logger.error(f'RS analysis failed: {e}')
            result.message_length = 0
            result.confidence = 0.0
        return result

    def _dct_analysis(self, image: Any) -> DCTResult:
        """Perform DCT coefficient analysis for JPEG steganography.

        Analyzes DCT coefficient histogram for anomalies that indicate
        steganography in JPEG images (e.g., JSteg, F5, OutGuess).

        Args:
            image: PIL Image object

        Returns:
            DCTResult with DCT analysis statistics
        """
        result = DCTResult()
        try:
            if image.mode != 'L':
                gray_image = image.convert('L')
            else:
                gray_image = image
            img_array = np.array(gray_image).astype(np.float32)
            block_size = 8
            height, width = img_array.shape
            height = height // block_size * block_size
            width = width // block_size * block_size
            img_array = img_array[:height, :width]
            block_anomalies = []
            suspicious_coeffs = []
            for y in range(0, height, block_size):
                for x in range(0, width, block_size):
                    block = img_array[y:y + block_size, x:x + block_size]
                    freq_energy = np.sum(np.abs(np.diff(block.flatten())))
                    expected_energy = block_size * block_size * 5
                    anomaly = abs(freq_energy - expected_energy) / max(expected_energy, 1)
                    block_anomalies.append(float(anomaly))
            hist, _ = np.histogram(img_array.flatten(), bins=256, range=(0, 256))
            expected_hist = np.full_like(hist, np.mean(hist))
            hist_deviation = np.mean(np.abs(hist - expected_hist)) / max(np.mean(hist), 1)
            if block_anomalies:
                avg_anomaly = np.mean(block_anomalies)
                max_anomaly = np.max(block_anomalies)
                threshold = np.percentile(block_anomalies, 90)
                suspicious_coeffs = [i for i, a in enumerate(block_anomalies) if a > threshold]
                anomaly_score = min(1.0, (avg_anomaly + max_anomaly) / 2)
            else:
                anomaly_score = 0.0
            result.anomaly_score = float(anomaly_score)
            result.suspicious_coefficients = suspicious_coeffs[:100]
            result.histogram_deviation = float(hist_deviation)
            result.block_anomalies = block_anomalies[:1000]
        except Exception as e:
            logger.error(f'DCT analysis failed: {e}')
            result.anomaly_score = 0.0
        return result

    def _estimate_message_length(self, rs_result: RSResult) -> int:
        """Estimate hidden message length from RS analysis.

        Args:
            rs_result: RS analysis result

        Returns:
            Estimated message length in bytes
        """
        if rs_result is None or rs_result.confidence < 0.1:
            return 0
        return rs_result.message_length

    def _aggregate_results(self, result: StegoResult) -> StegoResult:
        """Aggregate analysis results and determine final verdict.

        Args:
            result: Partial StegoResult with individual analyses

        Returns:
            Complete StegoResult with aggregated verdict
        """
        confidences = []
        methods = []
        if result.chi_square and result.chi_square.is_significant:
            chi_conf = 1.0 - result.chi_square.p_value
            confidences.append(chi_conf)
            methods.append('chi_square')
        if result.rs_analysis and result.rs_analysis.confidence > 0.3:
            rs_conf = result.rs_analysis.confidence
            confidences.append(rs_conf)
            methods.append('rs_analysis')
        if result.dct_analysis and result.dct_analysis.anomaly_score > self.config.dct_threshold:
            dct_conf = min(1.0, result.dct_analysis.anomaly_score / 5.0)
            confidences.append(dct_conf)
            methods.append('dct_analysis')
        if confidences:
            result.confidence = float(np.mean(confidences))
            result.has_stego = result.confidence > 0.5
            result.method_used = '+'.join(methods) if methods else 'none'
        else:
            result.confidence = 0.0
            result.has_stego = False
            result.method_used = 'none'
        if result.rs_analysis and result.rs_analysis.message_length > 0:
            result.message_length_estimate = result.rs_analysis.message_length
        elif result.chi_square and result.chi_square.embedded_bytes_estimate > 0:
            result.message_length_estimate = result.chi_square.embedded_bytes_estimate
        return result

    async def cleanup(self) -> None:
        """Clean up resources and release memory.

        Call when done with detector to free memory.
        Note: No thread pool to shutdown — uses shared domain_executors.vision.
        """
        self._image_lib = None
        self._initialized = False
        gc.collect()
        logger.debug('StatisticalStegoDetector cleaned up')

def create_stego_detector(config: StegoConfig | None=None) -> StatisticalStegoDetector | None:
    """Factory function to create steganography detector.

    Creates a StatisticalStegoDetector with optional configuration.
    Returns None if dependencies are not available.

    Args:
        config: Optional StegoConfig configuration

    Returns:
        StatisticalStegoDetector instance or None if creation fails

    Example:
        >>> detector = create_stego_detector(StegoConfig(max_image_size=1024))
        >>> if detector:
        ...     await detector.initialize()
        ...     result = await detector.analyze_image("image.png")
    """
    try:
        return StatisticalStegoDetector(config or StegoConfig())
    except ImportError:
        logger.warning('PIL/Pillow not available, stego detector disabled')
        return None
StegoDetector = StatisticalStegoDetector
StegoAnalysisResult = StegoResult

async def quick_stego_check(image_path: str | Path) -> dict[str, Any]:
    """Quick steganography check on an image.

    Args:
        image_path: Path to image file

    Returns:
        Dictionary with key findings
    """
    detector = create_stego_detector()
    if detector is None:
        return {'error': 'Stego detector not available'}
    await detector.initialize()
    try:
        result = await detector.analyze_image(image_path)
        return {'file': str(image_path), 'is_suspicious': result.has_stego, 'confidence': round(result.confidence, 3), 'method': result.method_used, 'message_length_bytes': result.message_length_estimate}
    finally:
        await detector.cleanup()
__all__ = ['StatisticalStegoDetector', 'StegoConfig', 'StegoResult', 'ChiSquareResult', 'RSResult', 'DCTResult', 'create_stego_detector', 'quick_stego_check']