"""
VisionAnalyzer - macOS Vision framework for image analysis.

Provides image analysis using macOS Vision framework:
- Text recognition (OCR)
- Barcode detection
- Face detection
- Feature print generation (for similarity/steganography detection)

Optimized for M1 Mac with ANE acceleration.
"""

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Lazy import guard
VISION_AVAILABLE = False
try:
    import Foundation
    import Vision
    VISION_AVAILABLE = True
except ImportError:
    logger.debug("Vision framework not available")


class VisionAnalyzer:
    """
    Image analysis using macOS Vision framework.

    Features:
    - OCR text recognition
    - Barcode detection
    - Face detection
    - Feature print generation (for similarity detection)
    """

    async def analyze_image(self, image_bytes: bytes) -> dict[str, Any]:
        """
        Analyze image bytes using Vision framework.

        Args:
            image_bytes: Raw image bytes to analyze.

        Returns:
            Dictionary with analysis results:
            - text: recognized text
            - barcodes: list of detected barcodes
            - faces: number of detected faces
            - feature_print: whether feature print was generated
        """
        if not VISION_AVAILABLE:
            logger.warning("[VisionAnalyzer] Vision framework not available")
            return {"text": "", "barcodes": [], "faces": 0, "feature_print": False}

        try:
            import Foundation
            import Vision

            # Convert bytes to NSData
            ns_data = Foundation.NSData.dataWithBytes_length_(image_bytes, len(image_bytes))

            # Create image request handler
            handler = Vision.VNImageRequestHandler.alloc().initWithData_options_(
                ns_data, None
            )

            # Create request objects
            text_request = Vision.VNRecognizeTextRequest.new()
            text_request.setRecognitionLevel_(Vision.VNRequestRecognitionLevelAccurate)

            barcode_request = Vision.VNDetectBarcodesRequest.new()

            face_request = Vision.VNDetectFaceRectanglesRequest.new()

            feature_request = Vision.VNGenerateImageFeaturePrintRequest.new()

            # Create requests array
            requests = [text_request, barcode_request, face_request, feature_request]

            # Run in executor to avoid blocking
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: handler.performRequests_error_(requests, None)
            )

            # Extract text results
            text_results = text_request.results() or []
            text = " ".join([
                obs.topCandidates_(1)[0].string()
                for obs in text_results
                if obs.topCandidates_(1) and obs.topCandidates_(1)[0]
            ])

            # Extract barcode results
            barcode_results = barcode_request.results() or []
            barcodes = [
                obs.payloadStringValue()
                for obs in barcode_results
                if obs.payloadStringValue()
            ]

            # Count faces
            faces = len(face_request.results() or [])

            # Check feature print
            feature_results = feature_request.results() or []
            feature_present = len(feature_results) > 0

            return {
                "text": text,
                "barcodes": barcodes,
                "faces": faces,
                "feature_print": feature_present
            }

        except Exception as e:
            logger.warning(f"[VisionAnalyzer] Image analysis failed: {e}")
            return {"text": "", "barcodes": [], "faces": 0, "feature_print": False}


async def analyze_image_async(image_bytes: bytes) -> dict[str, Any]:
    """Async wrapper for VisionAnalyzer.analyze_image."""
    analyzer = VisionAnalyzer()
    return await analyzer.analyze_image(image_bytes)
