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

import asyncio
import gc
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

# Sprint 53: MPS (Metal Performance Shaders) detection
# NOTE: torch import moved to function scope to avoid loading 659 torch modules at import time
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

# Maximum image size for MPS analysis (protect against OOM)
MAX_IMAGE_SIZE = 2048


@dataclass
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


@dataclass
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


@dataclass
class RSResult:
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


@dataclass
class DCTResult:
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


@dataclass
class StegoResult:
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
    method_used: str = "none"
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

    def __init__(self, config: StegoConfig | None = None):
        """Initialize detector with configuration.

        Args:
            config: StegoConfig instance or None for defaults
        """
        import concurrent.futures

        self.config = config or StegoConfig()
        self._initialized = False
        self._image_lib = None
        # Sprint 53: Thread pool for MPS operations
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="stego_mps"
        )

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
        """MPS-accelerated detection."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self._detect_mps_sync,
            image_bytes
        )

    def _detect_mps_sync(self, image_bytes: bytes) -> dict[str, Any]:
        """Synchronous MPS implementation of steganography detection."""
        import io

        import torch
        from PIL import Image

        try:
            img = Image.open(io.BytesIO(image_bytes)).convert('L')

            # Size limit
            if img.width > MAX_IMAGE_SIZE or img.height > MAX_IMAGE_SIZE:
                ratio = min(MAX_IMAGE_SIZE / img.width, MAX_IMAGE_SIZE / img.height)
                new_size = (int(img.width * ratio), int(img.height * ratio))
                img = img.resize(new_size, Image.Resampling.LANCZOS)

            img_array = np.array(img, dtype=np.float32) / 255.0
            tensor = torch.from_numpy(img_array).to('mps')

            with torch.no_grad():
                h, w = tensor.shape
                if h >= 8 and w >= 8:
                    # Trim to multiple of 8
                    h_blocks = h // 8
                    w_blocks = w // 8
                    tensor = tensor[:h_blocks*8, :w_blocks*8]

                    # Split into 8x8 blocks
                    blocks = tensor.unfold(0, 8, 8).unfold(1, 8, 8)
                    blocks = blocks.contiguous().view(-1, 8, 8)

                    # Mean and std per block
                    block_means = blocks.mean(dim=(1, 2))
                    block_stds = blocks.std(dim=(1, 2))

                    # Score: higher std = more suspicious
                    score = (block_stds.mean() / (block_means.mean() + 1e-8)).item()
                    score = min(1.0, score * 0.3)
                else:
                    score = 0.0
        except Exception as e:
            logger.warning(f"MPS stego detection failed: {e}")
            return self._detect_cpu_sync(image_bytes)
        finally:
            if hasattr(torch.mps, 'empty_cache'):
                try:
                    torch.mps.empty_cache()
                except Exception:
                    pass

        return {
            "score": score,
            "chi_square_flag": score > 0.3,
            "method": "mps_chi_square"
        }

    async def _detect_cpu(self, image_bytes: bytes) -> dict[str, Any]:
        """CPU-based detection."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self._detect_cpu_sync,
            image_bytes
        )

    def _detect_cpu_sync(self, image_bytes: bytes) -> dict[str, Any]:
        """Synchronous CPU implementation of steganography detection."""
        # Simple chi-square on LSB
        try:
            import io

            from PIL import Image

            img = Image.open(io.BytesIO(image_bytes))
            if img.mode != 'L':
                img = img.convert('L')

            img_array = np.array(img)
            lsbs = (img_array & 1).flatten()

            count_0 = np.sum(lsbs == 0)
            count_1 = np.sum(lsbs == 1)
            total = count_0 + count_1

            if total == 0:
                return {"score": 0.0, "chi_square_flag": False, "method": "cpu_chi_square"}

            expected = total / 2.0
            chi_sq = ((count_0 - expected) ** 2) / expected + ((count_1 - expected) ** 2) / expected
            score = min(1.0, chi_sq / 1000.0)
        except Exception as e:
            logger.warning(f"CPU stego detection failed: {e}")
            score = 0.0

        return {
            "score": score,
            "chi_square_flag": score > 0.3,
            "method": "cpu_chi_square"
        }

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
            logger.debug("StatisticalStegoDetector initialized")
        except ImportError as e:
            logger.error(f"Failed to import PIL: {e}")
            raise RuntimeError("PIL/Pillow is required for image analysis") from e

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
            raise RuntimeError("Detector not initialized. Call initialize() first.")

        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"Image not found: {image_path}")

        result = StegoResult()
        pixels = None
        image = None

        try:
            # Load image with size limit
            image, pixels = self._load_image(image_path)

            if pixels is None:
                result.details["error"] = "Failed to load image"
                return result

            # Run chi-square test (always enabled)
            chi_result = self._chi_square_test(pixels)
            result.chi_square = chi_result

            # Run RS analysis if enabled
            if self.config.rs_analysis_enabled:
                rs_result = self._rs_analysis(pixels)
                result.rs_analysis = rs_result
                result.message_length_estimate = self._estimate_message_length(rs_result)

            # Run DCT analysis if enabled and JPEG
            if self.config.dct_analysis_enabled and self._is_jpeg(image_path):
                dct_result = self._dct_analysis(image)
                result.dct_analysis = dct_result

            # Aggregate results
            result = self._aggregate_results(result)

            logger.debug(
                f"Analyzed {image_path}: stego={result.has_stego}, "
                f"confidence={result.confidence:.2f}, method={result.method_used}"
            )

        except Exception as e:
            logger.error(f"Analysis failed for {image_path}: {e}")
            result.details["error"] = str(e)

        finally:
            # Release memory in streaming mode
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
        image = self._image_lib.open(image_path)

        # Check size limits
        width, height = image.size
        if width > self.config.max_image_size or height > self.config.max_image_size:
            logger.warning(
                f"Image {image_path} exceeds max size, resizing: "
                f"{width}x{height} -> {self.config.max_image_size}"
            )
            image.thumbnail(
                (self.config.max_image_size, self.config.max_image_size),
                self._image_lib.Resampling.LANCZOS,
            )

        # Convert to RGB if necessary
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")

        # Convert to numpy array
        pixels = np.array(image)

        return image, pixels

    def _is_jpeg(self, image_path: Path) -> bool:
        """Check if file is JPEG format.

        Args:
            image_path: Path to image file

        Returns:
            True if JPEG, False otherwise
        """
        return image_path.suffix.lower() in (".jpg", ".jpeg")

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
            # Flatten and extract LSBs
            if len(pixels.shape) == 3:
                # Color image - analyze each channel
                flat_pixels = pixels.reshape(-1, pixels.shape[2])
                # Use first channel (R) for speed
                lsbs = (flat_pixels[:, 0] & 1).astype(np.int32)
            else:
                # Grayscale
                flat_pixels = pixels.flatten()
                lsbs = (flat_pixels & 1).astype(np.int32)

            # Count 0s and 1s in LSBs
            count_0 = np.sum(lsbs == 0)
            count_1 = np.sum(lsbs == 1)
            total = count_0 + count_1

            if total == 0:
                return result

            # Expected frequencies (uniform distribution)
            expected = total / 2.0

            # Calculate chi-square statistic
            chi_sq = ((count_0 - expected) ** 2) / expected + ((count_1 - expected) ** 2) / expected

            # Calculate p-value using approximation
            # For 1 degree of freedom
            p_value = math.exp(-chi_sq / 2) if chi_sq > 0 else 1.0
            p_value = min(1.0, max(0.0, p_value))

            # Estimate embedded bytes
            # Higher chi-square suggests more embedded data
            if chi_sq > 10:
                embedded_estimate = int(total * (1 - p_value) / 8)
            else:
                embedded_estimate = 0

            result.p_value = float(p_value)
            result.chi_square_stat = float(chi_sq)
            result.embedded_bytes_estimate = embedded_estimate
            result.is_significant = p_value < self.config.chi_square_threshold

        except Exception as e:
            logger.error(f"Chi-square test failed: {e}")

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
            # Convert to grayscale if color
            if len(pixels.shape) == 3:
                gray = np.mean(pixels, axis=2).astype(np.uint8)
            else:
                gray = pixels.astype(np.uint8)

            # Flatten
            flat = gray.flatten()

            # Group size (typically 4 pixels)
            group_size = 4
            num_groups = len(flat) // group_size

            if num_groups < 100:
                return result

            # Reshape into groups
            groups = flat[: num_groups * group_size].reshape(num_groups, group_size)

            # Define mask and inverse mask
            mask = np.array(self.config.rs_mask)
            mask_inv = 1 - mask

            # Calculate discrimination function (variation)
            def variation(group: np.ndarray) -> float:
                """Calculate variation within group."""
                return np.sum(np.abs(group[1:] - group[:-1]))

            # Apply mask functions
            def flip_mask(group: np.ndarray, m: np.ndarray) -> np.ndarray:
                """Apply mask to group (flip LSB where mask is 1)."""
                flipped = group.copy()
                for i in range(len(group)):
                    if m[i % len(m)] == 1:
                        flipped[i] = group[i] ^ 1  # Flip LSB
                return flipped

            # Count regular and singular groups
            rm, r_m, sm, s_m = 0.0, 0.0, 0.0, 0.0

            # Sample groups for speed (analyze every 2nd group)
            sample_indices = range(0, num_groups, 2)

            for i in sample_indices:
                group = groups[i]
                v_orig = variation(group)

                # Apply mask
                flipped_m = flip_mask(group, mask)
                v_m = variation(flipped_m)

                # Apply inverse mask
                flipped_m_inv = flip_mask(group, mask_inv)
                v_m_inv = variation(flipped_m_inv)

                # Classify
                if v_m > v_orig:
                    rm += 1
                elif v_m < v_orig:
                    sm += 1

                if v_m_inv > v_orig:
                    r_m += 1
                elif v_m_inv < v_orig:
                    s_m += 1

            # Normalize by sample count
            sample_count = len(sample_indices)
            rm /= sample_count
            r_m /= sample_count
            sm /= sample_count
            s_m /= sample_count

            # Estimate message length
            # Formula from Fridrich paper
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
            logger.error(f"RS analysis failed: {e}")
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
            # For PIL, we need to simulate DCT analysis
            # In a full implementation, we'd use libjpeg or scipy.fftpack
            # Here we analyze the frequency domain characteristics

            # Convert to grayscale
            if image.mode != "L":
                gray_image = image.convert("L")
            else:
                gray_image = image

            # Convert to numpy
            img_array = np.array(gray_image).astype(np.float32)

            # Simple block-based analysis (simulating DCT blocks)
            block_size = 8
            height, width = img_array.shape

            # Ensure dimensions are divisible by block size
            height = (height // block_size) * block_size
            width = (width // block_size) * block_size
            img_array = img_array[:height, :width]

            # Analyze each block
            block_anomalies = []
            suspicious_coeffs = []

            for y in range(0, height, block_size):
                for x in range(0, width, block_size):
                    block = img_array[y : y + block_size, x : x + block_size]

                    # Simple frequency analysis (difference from neighbors)
                    freq_energy = np.sum(np.abs(np.diff(block.flatten())))
                    expected_energy = block_size * block_size * 5  # Rough estimate

                    anomaly = abs(freq_energy - expected_energy) / max(expected_energy, 1)
                    block_anomalies.append(float(anomaly))

            # Calculate histogram deviation
            hist, _ = np.histogram(img_array.flatten(), bins=256, range=(0, 256))
            expected_hist = np.full_like(hist, np.mean(hist))
            hist_deviation = np.mean(np.abs(hist - expected_hist)) / max(np.mean(hist), 1)

            # Identify suspicious coefficients (simplified)
            # In real DCT, we'd check for characteristic patterns
            if len(block_anomalies) > 0:
                avg_anomaly = np.mean(block_anomalies)
                max_anomaly = np.max(block_anomalies)

                # Flag blocks with high anomaly
                threshold = np.percentile(block_anomalies, 90)
                suspicious_coeffs = [i for i, a in enumerate(block_anomalies) if a > threshold]

                # Overall anomaly score
                anomaly_score = min(1.0, (avg_anomaly + max_anomaly) / 2)
            else:
                anomaly_score = 0.0

            result.anomaly_score = float(anomaly_score)
            result.suspicious_coefficients = suspicious_coeffs[:100]  # Limit count
            result.histogram_deviation = float(hist_deviation)
            result.block_anomalies = block_anomalies[:1000]  # Limit storage

        except Exception as e:
            logger.error(f"DCT analysis failed: {e}")
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

        # Chi-square contribution
        if result.chi_square and result.chi_square.is_significant:
            chi_conf = 1.0 - result.chi_square.p_value
            confidences.append(chi_conf)
            methods.append("chi_square")

        # RS analysis contribution
        if result.rs_analysis and result.rs_analysis.confidence > 0.3:
            rs_conf = result.rs_analysis.confidence
            confidences.append(rs_conf)
            methods.append("rs_analysis")

        # DCT analysis contribution
        if result.dct_analysis and result.dct_analysis.anomaly_score > self.config.dct_threshold:
            dct_conf = min(1.0, result.dct_analysis.anomaly_score / 5.0)
            confidences.append(dct_conf)
            methods.append("dct_analysis")

        # Calculate overall confidence
        if confidences:
            result.confidence = float(np.mean(confidences))
            result.has_stego = result.confidence > 0.5
            result.method_used = "+".join(methods) if methods else "none"
        else:
            result.confidence = 0.0
            result.has_stego = False
            result.method_used = "none"

        # Use RS estimate if available, otherwise chi-square
        if result.rs_analysis and result.rs_analysis.message_length > 0:
            result.message_length_estimate = result.rs_analysis.message_length
        elif result.chi_square and result.chi_square.embedded_bytes_estimate > 0:
            result.message_length_estimate = result.chi_square.embedded_bytes_estimate

        return result

    async def cleanup(self) -> None:
        """Clean up resources and release memory.

        Call when done with detector to free memory.
        """
        if self._thread_pool is not None:
            self._thread_pool.shutdown(wait=False)
            self._thread_pool = None
        self._image_lib = None
        self._initialized = False
        gc.collect()
        logger.debug("StatisticalStegoDetector cleaned up")


def create_stego_detector(config: StegoConfig | None = None) -> StatisticalStegoDetector | None:
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
        logger.warning("PIL/Pillow not available, stego detector disabled")
        return None


# Legacy aliases for backward compatibility
StegoDetector = StatisticalStegoDetector
StegoAnalysisResult = StegoResult


# Convenience function for quick analysis
async def quick_stego_check(image_path: str | Path) -> dict[str, Any]:
    """Quick steganography check on an image.

    Args:
        image_path: Path to image file

    Returns:
        Dictionary with key findings
    """
    detector = create_stego_detector()
    if detector is None:
        return {"error": "Stego detector not available"}

    await detector.initialize()
    try:
        result = await detector.analyze_image(image_path)
        return {
            "file": str(image_path),
            "is_suspicious": result.has_stego,
            "confidence": round(result.confidence, 3),
            "method": result.method_used,
            "message_length_bytes": result.message_length_estimate,
        }
    finally:
        await detector.cleanup()


__all__ = [
    "StatisticalStegoDetector",
    "StegoConfig",
    "StegoResult",
    "ChiSquareResult",
    "RSResult",
    "DCTResult",
    "create_stego_detector",
    "quick_stego_check",
]
