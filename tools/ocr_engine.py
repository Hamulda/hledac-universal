"""
VisionOCR - macOS Vision framework wrapper for OCR tasks.

Provides OCR capabilities using macOS Vision framework via ocrmac library.
Optimized for M1 Mac with fail-safe handling.
"""

import logging
import os

logger = logging.getLogger(__name__)

# Maximum image size for OCR processing (fail-safe bound)
MAX_OCR_IMAGE_SIZE_MB = 20


class VisionOCR:
    """
    OCR wrapper using macOS Vision framework.

    Handles image text recognition with fail-safe guards:
    - File size limit (MAX_OCR_IMAGE_SIZE_MB)
    - ImportError handling (ocrmac optional dependency)
    - Runtime error handling
    """

    def recognize(self, image_path: str) -> list[str]:
        """
        Perform OCR on an image file.

        Args:
            image_path: Path to the image file to process.

        Returns:
            List of recognized text strings (may be empty).
        """
        # Check file size first (fail-safe)
        try:
            file_size = os.path.getsize(image_path)
            max_size_bytes = MAX_OCR_IMAGE_SIZE_MB * 1024 * 1024
            if file_size > max_size_bytes:
                logger.warning(
                    f"[VisionOCR] File too large: {image_path} ({file_size} bytes > {max_size_bytes} bytes), skipping"
                )
                return []
        except OSError as e:
            logger.warning(f"[VisionOCR] Could not get file size for {image_path}: {e}")
            return []

        # Perform OCR with full error handling
        try:
            import ocrmac

            ocr = ocrmac.OCR(image_path)
            results = ocr.recognize()

            # Normalize output to list[str]
            if isinstance(results, list):
                return [str(item) for item in results]
            else:
                logger.warning(f"[VisionOCR] Unexpected result type: {type(results)}")
                return []

        except ImportError:
            logger.warning("[VisionOCR] ocrmac not installed - OCR unavailable")
            return []
        except Exception as e:
            logger.warning(f"[VisionOCR] OCR failed for {image_path}: {e}")
            return []

    def recognize_bytes(self, image_bytes: bytes) -> list[str]:
        """
        Perform OCR on image bytes with size guard and temp file.

        Args:
            image_bytes: Raw image bytes to process.

        Returns:
            List of recognized text strings (may be empty).
        """
        max_bytes = MAX_OCR_IMAGE_SIZE_MB * 1024 * 1024
        if len(image_bytes) > max_bytes:
            logger.warning(f"[VisionOCR] Image too large: {len(image_bytes)} bytes, skipping")
            return []
        try:
            import os
            import tempfile

            import ocrmac

            with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                f.write(image_bytes)
                tmp = f.name
            try:
                ocr = ocrmac.OCR(tmp)
                result = ocr.recognize()
                return [str(r).strip() for r in result if str(r).strip()]
            finally:
                os.unlink(tmp)
        except ImportError:
            logger.warning("[VisionOCR] ocrmac not installed")
            return []
        except Exception as e:
            logger.warning(f"[VisionOCR] recognize_bytes failed: {e}")
            return []


async def recognize_async(image_path: str) -> list[str]:
    """Async wrapper for OCR recognition."""
    import asyncio
    loop = asyncio.get_running_loop()

    def _read_and_recognize():
        with open(image_path, 'rb') as f:
            return VisionOCR().recognize_bytes(f.read())

    return await loop.run_in_executor(None, _read_and_recognize)
