# Stub for ocrmac (Darwin-only; pyobjc Vision wrapper)
# Minimal surface — only what hledac uses.
from typing import Any

__version__: str

class OCR:
    """OCR facade around Vision.VNImageRequestHandler.

    Real class lives in `ocrmac.ocrmac.OCR`; stub exposes the public surface
    consumed by tools/ocr_engine.py: detect(), recognize(), recognize_text().
    """
    def __init__(self, image_path: str, language_preference: list[str] | None = None, recognition_level: str = "accurate", min_confidence: float = 0.0) -> None: ...
    def recognize(self) -> list[Any]: ...
    def recognize_text(self) -> list[tuple[str, float, tuple[float, float, float, float]]]: ...
    @property
    def image(self) -> Any: ...
    @property
    def res(self) -> list[Any] | None: ...
    def annotate_PIL(self, color: str = "red", fontsize: int = 12) -> Any: ...
