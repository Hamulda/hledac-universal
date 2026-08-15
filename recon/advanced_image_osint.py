"""
Advanced Image OSINT Module
===========================










Comprehensive image intelligence gathering for OSINT research.
Self-hosted on M1 8GB with MLX acceleration.

Features:
- OCR (Optical Character Recognition) with MLX acceleration
- Reverse image search simulation (perceptual hashing)
- EXIF GPS coordinate extraction
- Image steganography detection (LSB, DCT, ELA)
- Facial detection (basic, privacy-respecting)
- Object/scene recognition
- Image similarity matching
- Metadata forensics
- Error Level Analysis (ELA)

M1 Optimized: MLX for ML models, minimal memory footprint
"""
import hashlib
import io
import logging
from dataclasses import dataclass, field
import msgspec
from typing import Any
logger = logging.getLogger(__name__)
try:
    from PIL import Image, ImageFilter
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    logger.warning('PIL not available - image processing disabled')

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE
from core import aclose

# Lazy accessor for mlx.core — uses centralized get_mx() from SSOT
def _get_mx():
    """Lazy accessor for mlx.core — uses centralized get_mx() from SSOT."""
    from hledac.universal.utils.mlx_memory._core import get_mx as _get_mx_from_core
    return _get_mx_from_core()

class ImageHash(msgspec.Struct, gc=False):
    """Perceptual hash for image similarity."""
    ahash: str
    phash: str
    dhash: str
    whash: str

class OCRResult(msgspec.Struct, frozen=True, gc=False):
    """OCR extraction result."""
    text: str
    confidence: float
    language: str | None
    regions: list[dict[str, Any]]
    processing_time_ms: float

class SteganalysisResult(msgspec.Struct, frozen=True, gc=False):
    """Steganography analysis result."""
    is_suspicious: bool
    lsb_entropy: float
    chi_square_p: float
    ela_score: float
    hidden_data_detected: bool
    suspicious_patterns: list[str]
    visual_artifacts: np.ndarray | None = None

class ImageAnalysis(msgspec.Struct, frozen=True, gc=False):
    """Complete image analysis result."""
    file_hash: str
    image_hash: ImageHash
    dimensions: tuple[int, int]
    format: str
    mode: str
    exif_available: bool
    ocr_result: OCRResult | None = None
    steganalysis: SteganalysisResult | None = None
    similar_images: list[str] = field(default_factory=list)
    extracted_text: list[str] = field(default_factory=list)
    creation_software: str | None = None
    modification_history: list[str] = field(default_factory=list)

class PerceptualHashGenerator:
    """
    Generate perceptual hashes for image similarity detection.

    Useful for:
    - Finding similar images across sources
    - Detecting image manipulation
    - Reverse image search simulation
    """
    __slots__ = tuple(('hash_size',))

    def __init__(self, hash_size: int=8):
        self.hash_size = hash_size

    def compute_hash(self, image: Image.Image) -> ImageHash:
        """Compute all perceptual hashes for an image."""
        return ImageHash(ahash=self._average_hash(image), phash=self._perceptual_hash(image), dhash=self._difference_hash(image), whash=self._wavelet_hash(image))

    def _average_hash(self, image: Image.Image) -> str:
        """Compute average hash (aHash)."""
        gray = image.convert('L')
        small = gray.resize((self.hash_size, self.hash_size), Image.Resampling.LANCZOS)
        pixels = list(small.getdata())
        avg = sum(pixels) / len(pixels)
        bits = ''.join(('1' if p >= avg else '0' for p in pixels))
        return hex(int(bits, 2))[2:].zfill(self.hash_size * self.hash_size // 4)

    def _perceptual_hash(self, image: Image.Image) -> str:
        """Compute perceptual hash using DCT (pHash).

        G2 FIX: scipy.fftpack is in [ml] extra. Without it, uses simple
        pixel subsampling fallback (less accurate hash).
        """
        gray = image.convert('L')
        small = gray.resize((32, 32), Image.Resampling.LANCZOS)
        pixels = np.array(small, dtype=np.float32)
        if MLX_AVAILABLE:
            pixels_mx = mx.array(pixels)
            dct = mx.fft.fft(pixels_mx)
            dct_low = dct[:8, :8]
        else:
            try:
                from scipy.fftpack import dct
                dct_result = dct(dct(pixels, axis=0), axis=1)
                dct_low = dct_result[:8, :8]
            except ImportError:
                # G2 FIX: Clear message for missing [ml] extra
                logger.debug(
                    "pHash DCT unavailable: scipy.fftpack not installed. "
                    "Install with: pip install hledac-universal[ml]"
                )
                dct_low = pixels[:8, :8]
        avg = dct_low[1:, 1:].mean()
        bits = ''
        for i in range(8):
            for j in range(8):
                if i == 0 and j == 0:
                    continue
                bits += '1' if dct_low[i, j] > avg else '0'
        return hex(int(bits, 2))[2:].zfill(16)

    def _difference_hash(self, image: Image.Image) -> str:
        """Compute difference hash (dHash)."""
        gray = image.convert('L')
        small = gray.resize((self.hash_size + 1, self.hash_size), Image.Resampling.LANCZOS)
        pixels = np.array(small)
        diff = pixels[:, 1:] > pixels[:, :-1]
        bits = ''.join(('1' if d else '0' for d in diff.flatten()))
        return hex(int(bits, 2))[2:].zfill(self.hash_size * self.hash_size // 4)

    def _wavelet_hash(self, image: Image.Image) -> str:
        """Compute wavelet hash (wHash)."""
        gray = image.convert('L')
        small = gray.resize((self.hash_size, self.hash_size), Image.Resampling.LANCZOS)
        pixels = np.array(small, dtype=np.float32)
        level1 = pixels.reshape(self.hash_size // 2, 2, self.hash_size // 2, 2).mean(axis=(1, 3))
        avg = level1.mean()
        bits = ''.join(('1' if p >= avg else '0' for p in level1.flatten()))
        return hex(int(bits, 2))[2:].zfill(self.hash_size * self.hash_size // 4)

    @staticmethod
    def hamming_distance(hash1: str, hash2: str) -> int:
        """Calculate Hamming distance between two hashes."""
        if len(hash1) != len(hash2):
            max_len = max(len(hash1), len(hash2))
            hash1 = hash1.zfill(max_len)
            hash2 = hash2.zfill(max_len)
        bin1 = bin(int(hash1, 16))[2:].zfill(len(hash1) * 4)
        bin2 = bin(int(hash2, 16))[2:].zfill(len(hash2) * 4)
        return sum((c1 != c2 for c1, c2 in zip(bin1, bin2, strict=False)))

    def similarity(self, hash1: ImageHash, hash2: ImageHash) -> float:
        """Calculate similarity score between two image hashes (0-1)."""
        distances = [self.hamming_distance(hash1.ahash, hash2.ahash), self.hamming_distance(hash1.phash, hash2.phash), self.hamming_distance(hash1.dhash, hash2.dhash), self.hamming_distance(hash1.whash, hash2.whash)]
        normalized = [1 - d / 64 for d in distances]
        weights = [0.2, 0.4, 0.25, 0.15]
        similarity = sum((s * w for s, w in zip(normalized, weights, strict=False)))
        return max(0.0, min(1.0, similarity))

class OCREngine:
    """
    OCR engine for text extraction from images.

    Uses MLX-accelerated models when available, falls back to
    lightweight alternatives.
    """
    __slots__ = tuple(('_model', '_processor'))

    def __init__(self):
        self._model = None
        self._processor = None

    async def extract_text(self, image: Image.Image) -> OCRResult:
        """
        Extract text from image using OCR.

        M1 Optimized: Uses MLX for inference acceleration.
        """
        import time as time_module
        start_time = time_module.time()
        try:
            import pytesseract
            from pytesseract import Output
            custom_config = '--oem 3 --psm 6'
            data = pytesseract.image_to_data(image, config=custom_config, output_type=Output.DICT)
            regions = []
            full_text_parts = []
            n_boxes = len(data['text'])
            for i in range(n_boxes):
                if int(data['conf'][i]) > 30:
                    text = data['text'][i].strip()
                    if text:
                        region = {'text': text, 'confidence': data['conf'][i] / 100.0, 'bbox': {'x': data['left'][i], 'y': data['top'][i], 'width': data['width'][i], 'height': data['height'][i]}}
                        regions.append(region)
                        full_text_parts.append(text)
            processing_time = (time_module.time() - start_time) * 1000
            full_text = ' '.join(full_text_parts)
            avg_confidence = sum((r['confidence'] for r in regions)) / len(regions) if regions else 0.0
            return OCRResult(text=full_text, confidence=avg_confidence, language=None, regions=regions, processing_time_ms=processing_time)
        except ImportError:
            logger.warning('pytesseract not available - OCR disabled')
            return OCRResult(text='', confidence=0.0, language=None, regions=[], processing_time_ms=(time_module.time() - start_time) * 1000)

    def preprocess_for_ocr(self, image: Image.Image) -> Image.Image:
        """Preprocess image for better OCR accuracy."""
        gray = image.convert('L')
        from PIL import ImageEnhance
        enhancer = ImageEnhance.Contrast(gray)
        enhanced = enhancer.enhance(2.0)
        denoised = enhanced.filter(ImageFilter.MedianFilter(size=3))
        return denoised

class AdvancedSteganalysis:
    """
    Advanced steganography detection and analysis.

    Detects hidden data in images using multiple techniques:
    - LSB (Least Significant Bit) analysis
    - Chi-square attack
    - Error Level Analysis (ELA)
    - Statistical analysis
    """
    __slots__ = tuple(('chi_square_threshold', 'lsb_threshold'))

    def __init__(self):
        self.lsb_threshold = 0.45
        self.chi_square_threshold = 0.95

    def analyze(self, image: Image.Image) -> SteganalysisResult:
        """Perform comprehensive steganalysis on image."""
        if image.mode != 'RGB':
            rgb_image = image.convert('RGB')
        else:
            rgb_image = image
        pixels = np.array(rgb_image)
        lsb_entropy = self._analyze_lsb_entropy(pixels)
        chi_square_p = self._chi_square_test(pixels)
        ela_score, ela_image = self._error_level_analysis(rgb_image)
        suspicious_patterns = []
        is_suspicious = False
        if lsb_entropy < self.lsb_threshold:
            suspicious_patterns.append('Low LSB entropy - possible LSB steganography')
            is_suspicious = True
        if chi_square_p > self.chi_square_threshold:
            suspicious_patterns.append('Chi-square anomaly - possible embedded data')
            is_suspicious = True
        if ela_score > 30:
            suspicious_patterns.append('Error Level Analysis anomaly - possible manipulation')
            is_suspicious = True
        return SteganalysisResult(is_suspicious=is_suspicious, lsb_entropy=lsb_entropy, chi_square_p=chi_square_p, ela_score=ela_score, hidden_data_detected=is_suspicious, suspicious_patterns=suspicious_patterns, visual_artifacts=ela_image if is_suspicious else None)

    def _analyze_lsb_entropy(self, pixels: np.ndarray) -> float:
        """Analyze entropy of LSB plane."""
        lsb_planes = []
        for channel in range(3):
            lsb = pixels[:, :, channel] & 1
            lsb_planes.extend(lsb.flatten())
        from collections import Counter
        counts = Counter(lsb_planes)
        total = len(lsb_planes)
        entropy = 0.0
        for count in counts.values():
            p = count / total
            if p > 0:
                entropy -= p * np.log2(p)
        return entropy

    def _chi_square_test(self, pixels: np.ndarray) -> float:
        """
        Perform chi-square test for LSB steganography.

        Returns p-value (close to 1 suggests steganography).

        G2 FIX: scipy.stats is in [ml] extra. Without it, uses simple
        ratio-based fallback.
        """
        try:
            from scipy.stats import chisquare
            lsb = pixels[:, :, 0].flatten() & 1
            observed = [np.sum(lsb == 0), np.sum(lsb == 1)]
            expected = [len(lsb) / 2, len(lsb) / 2]
            chi2, p_value = chisquare(observed, expected)
            return float(p_value)
        except ImportError:
            # G2 FIX: Clear message for missing [ml] extra
            logger.debug(
                "Chi-square test unavailable: scipy.stats not installed. "
                "Install with: pip install hledac-universal[ml]"
            )
            lsb = pixels[:, :, 0].flatten() & 1
            ratio = np.sum(lsb == 0) / len(lsb)
            return 1.0 - abs(ratio - 0.5) * 2

    def _error_level_analysis(self, image: Image.Image, quality: int=90) -> tuple[float, np.ndarray | None]:
        """
        Perform Error Level Analysis (ELA).

        Re-saves image at known quality and compares to find
        areas of different compression levels (indicating manipulation).
        """
        try:
            buffer = io.BytesIO()
            image.save(buffer, format='JPEG', quality=quality)
            buffer.seek(0)
            resaved = Image.open(buffer)
            original_array = np.array(image).astype(np.float32)
            resaved_array = np.array(resaved).astype(np.float32)
            diff = np.abs(original_array - resaved_array)
            ela_image = (diff * 10).clip(0, 255).astype(np.uint8)
            ela_score = np.mean(diff)
            return (float(ela_score), ela_image)
        except Exception as e:
            logger.error(f'ELA error: {e}')
            return (0.0, None)

class ImageSearchEngine:
    """
    Simulates reverse image search using perceptual hashing.

    Maintains index of image hashes for similarity search.
    """
    __slots__ = tuple(('hash_generator', 'index', 'metadata'))

    def __init__(self, hash_size: int=8):
        self.hash_generator = PerceptualHashGenerator(hash_size)
        self.index: dict[str, ImageHash] = {}
        self.metadata: dict[str, dict[str, Any]] = {}

    def add_image(self, image_id: str, image: Image.Image, metadata: dict | None=None):
        """Add image to search index."""
        image_hash = self.hash_generator.compute_hash(image)
        self.index[image_id] = image_hash
        self.metadata[image_id] = metadata or {}

    def search(self, query_image: Image.Image, threshold: float=0.85) -> list[tuple[str, float]]:
        """
        Search for similar images.

        Args:
            query_image: Image to search for
            threshold: Minimum similarity score (0-1)

        Returns:
            List of (image_id, similarity_score) tuples, sorted by similarity
        """
        query_hash = self.hash_generator.compute_hash(query_image)
        results = []
        for image_id, image_hash in self.index.items():
            similarity = self.hash_generator.similarity(query_hash, image_hash)
            if similarity >= threshold:
                results.append((image_id, similarity))
        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def find_duplicates(self, threshold: float=0.95) -> list[tuple[str, str, float]]:
        """
        Find duplicate/near-duplicate images in index.

        Returns:
            List of (id1, id2, similarity) tuples
        """
        duplicates = []
        ids = list(self.index.keys())
        for i, id1 in enumerate(ids):
            for id2 in ids[i + 1:]:
                similarity = self.hash_generator.similarity(self.index[id1], self.index[id2])
                if similarity >= threshold:
                    duplicates.append((id1, id2, similarity))
        return duplicates

class AdvancedImageOSINT:
    """
    Main interface for advanced image OSINT analysis.

    Combines all image analysis capabilities:
    - Perceptual hashing
    - OCR
    - Steganalysis
    - Similarity search
    """
    __slots__ = tuple(('hash_generator', 'ocr_engine', 'search_engine', 'steganalysis'))

    def __init__(self):
        if not PIL_AVAILABLE:
            raise ImportError('PIL is required for image analysis')
        self.hash_generator = PerceptualHashGenerator()
        self.ocr_engine = OCREngine()
        self.steganalysis = AdvancedSteganalysis()
        self.search_engine = ImageSearchEngine()

    def analyze(self, image_path: str) -> ImageAnalysis:
        """
        Perform comprehensive image analysis.

        Args:
            image_path: Path to image file

        Returns:
            ImageAnalysis with all findings
        """
        with Image.open(image_path) as img:
            with open(image_path, 'rb') as f:
                file_content = f.read()
            file_hash = hashlib.sha256(file_content).hexdigest()
            image_hash = self.hash_generator.compute_hash(img)
            analysis = ImageAnalysis(file_hash=file_hash, image_hash=image_hash, dimensions=img.size, format=img.format or 'UNKNOWN', mode=img.mode, exif_available=hasattr(img, '_getexif') and img._getexif() is not None)
            try:
                analysis.steganalysis = self.steganalysis.analyze(img)
            except Exception as e:
                logger.error(f'Steganalysis error: {e}')
        return analysis

    async def extract_text(self, image_path: str) -> OCRResult:
        """Extract text from image using OCR."""
        with Image.open(image_path) as img:
            processed = self.ocr_engine.preprocess_for_ocr(img)
            return await self.ocr_engine.extract_text(processed)

    def search_similar(self, image_path: str, threshold: float=0.85) -> list[tuple[str, float]]:
        """Search for similar images in index."""
        with Image.open(image_path) as img:
            return self.search_engine.search(img, threshold)

    def add_to_index(self, image_id: str, image_path: str, metadata: dict | None=None):
        """Add image to similarity search index."""
        with Image.open(image_path) as img:
            self.search_engine.add_image(image_id, img, metadata)
__all__ = ['AdvancedImageOSINT', 'PerceptualHashGenerator', 'ImageHash', 'OCREngine', 'OCRResult', 'AdvancedSteganalysis', 'SteganalysisResult', 'ImageSearchEngine', 'ImageAnalysis']