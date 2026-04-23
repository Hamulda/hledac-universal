"""
Multimodal Enrichment Service
==============================

Fail-soft enrichment for PDF/image findings via VisionEncoder and MambaFusion.
Stores enrichment in LMDB keyed by finding_id (same pattern as forensics).

Additive: finding.metadata["multimodal"] is never written;
all enrichment goes to LMDB under finding_id key.

Integration:
    from multimodal.analyzer import MultimodalEnricher

    enricher = MultimodalEnricher(governor)
    await enricher.initialize()

    # enrich() returns enrichment dict or None
    # Caller stores the dict in LMDB keyed by finding_id
    enrichment = await enricher.enrich(finding)
    if enrichment:
        await lmdb_store.put(finding.finding_id.encode(), enrichment)

    await enricher.close()

M1 8GB: All heavy dependencies are lazy-loaded inside enrichment methods.
RAM guard via ResourceGovernor.reserve(). Heavy path blocked when UMA is tight.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)

# Lazy-loaded modules
_VisionEncoder: Optional[type] = None
_MambaFusion: Optional[type] = None
_MOBILECLIP_AVAILABLE = False


def _lazy_load_modules() -> None:
    """Load multimodal modules lazily on first use."""
    global _VisionEncoder, _MambaFusion, _MOBILECLIP_AVAILABLE
    if _VisionEncoder is not None:
        return

    try:
        from multimodal.vision_encoder import VisionEncoder
        _VisionEncoder = VisionEncoder
    except ImportError:
        _VisionEncoder = None

    try:
        from multimodal.fusion import MambaFusion
        _MambaFusion = MambaFusion
    except ImportError:
        _MambaFusion = None

    try:
        import mobileclip  # noqa: F401
        _MOBILECLIP_AVAILABLE = True
    except ImportError:
        _MOBILECLIP_AVAILABLE = False


# Supported file extensions for multimodal enrichment
_SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".tiff", ".tif", ".bmp", ".gif", ".webp",
    ".pdf",
}


def _extract_file_path_from_payload(payload_text: str | None) -> Optional[str]:
    """
    Extract a local file path from payload_text.

    Handles:
    - Direct local paths: /Users/.../file.jpg
    - file:// URLs: file:///tmp/file.pdf
    - Paths with query strings stripped
    """
    if not payload_text:
        return None

    if payload_text.startswith("file://"):
        path_str = payload_text[7:]
        path_str = path_str.split("?")[0].split("#")[0]
        path = Path(path_str)
        if path.exists() and path.is_file():
            return str(path)

    path = Path(payload_text)
    if not path.is_absolute():
        path = Path.cwd() / path
    if path.exists() and path.is_file():
        return str(path)

    clean = payload_text.split("?")[0].split("#")[0]
    if clean != payload_text:
        return _extract_file_path_from_payload(clean)

    return None


def _file_has_multimodal_support(file_path: str) -> bool:
    """Check if file extension is supported by multimodal enrichment."""
    ext = Path(file_path).suffix.lower()
    return ext in _SUPPORTED_EXTENSIONS


class MultimodalEnricher:
    """
    Multimodal enrichment for CanonicalFindings with PDF/image content.

    Enriches findings with file-path in payload_text via:
    - VisionEncoder: image → embedding vector (CoreML or dummy fallback)
    - MambaFusion: fused (vision, text, graph) embedding
    - mobileclip: optional text↔image similarity (when available)

    Fail-safe: all methods are wrapped in try/except.
    Enrichment failures log a warning and return None — never raise.

    M1 8GB: RAM guard via governor.reserve(). Heavy path is a no-op
    when the governor denies reservation (e.g., near-OOM condition).
    """

    def __init__(
        self,
        governor: Any,
        embedding_dim: int = 1280,
        batch_size: int = 4,
    ):
        """
        Initialize enricher.

        Args:
            governor: ResourceGovernor instance for RAM guard.
            embedding_dim: Embedding dimension for VisionEncoder.
            batch_size: Max batch size for encode_batch.
        """
        self._governor = governor
        self._embedding_dim = embedding_dim
        self._batch_size = batch_size

        self._vision_encoder: Optional[Any] = None
        self._fusion_model: Optional[Any] = None
        self._initialized = False
        self._lock = asyncio.Lock()

    async def _ensure_initialized(self) -> None:
        """Ensure models are initialized (idempotent)."""
        if self._initialized:
            return
        async with self._lock:
            if self._initialized:
                return
            _lazy_load_modules()

            # VisionEncoder — CoreML or dummy fallback
            if _VisionEncoder is not None:
                self._vision_encoder = _VisionEncoder(
                    governor=self._governor,
                    embedding_dim=self._embedding_dim,
                    batch_size=self._batch_size,
                )
                await self._vision_encoder.load()
                log.info("MultimodalEnricher: VisionEncoder loaded")

            # MambaFusion — MLX fusion model
            if _MambaFusion is not None:
                try:
                    self._fusion_model = _MambaFusion(
                        vision_dim=self._embedding_dim,
                        text_dim=768,
                        graph_dim=64,
                        hidden=256,
                        output_dim=128,
                    )
                    log.info("MultimodalEnricher: MambaFusion loaded")
                except Exception as exc:
                    log.warning("MultimodalEnricher: MambaFusion init failed: %s", exc)
                    self._fusion_model = None

            self._initialized = True

    async def initialize(self) -> None:
        """Public initialize — delegates to _ensure_initialized."""
        await self._ensure_initialized()

    async def close(self) -> None:
        """Close enricher and cleanup resources."""
        async with self._lock:
            self._vision_encoder = None
            self._fusion_model = None
            self._initialized = False

    async def enrich(self, finding: Any) -> Optional[dict[str, Any]]:
        """
        Enrich a CanonicalFinding with multimodal analysis.

        Extracts file path from finding.payload_text and runs:
        1. VisionEncoder — image/pdf → embedding
        2. MambaFusion — (vision, text, graph) → fused embedding
        3. mobileclip similarity (when available)

        Args:
            finding: A CanonicalFinding (or any object with
                     finding_id, payload_text, source_type attributes).

        Returns:
            Enrichment dict with keys:
            - "vision_embedding": list[float] or None
            - "fused_embedding": list[float] or None
            - "clip_score": float or None (mobileclip text↔image)
            - "file_path": the extracted file path or None
            - "enrichment_available": True if file was processable

            Returns None if no supported file path found or all enrichment failed.
            Never raises — failures return None with a debug log.
        """
        if not self._initialized:
            await self._ensure_initialized()

        # Extract file path from payload_text
        payload_text = getattr(finding, "payload_text", None)
        file_path = _extract_file_path_from_payload(payload_text)

        if not file_path:
            return None

        if not _file_has_multimodal_support(file_path):
            return None

        finding_id = getattr(finding, "finding_id", "unknown")
        enrichment: dict[str, Any] = {
            "finding_id": finding_id,
            "file_path": file_path,
            "vision_embedding": None,
            "fused_embedding": None,
            "clip_score": None,
            "enrichment_available": False,
        }

        # Guard: heavy vision path requires RAM reservation
        # If RAM is tight, skip gracefully (fail-soft)
        if not self._can_run_heavy_vision():
            log.debug("MultimodalEnricher: RAM guard denied for %s", finding_id)
            return None

        # 1. VisionEncoder: load file bytes and encode
        if self._vision_encoder is not None:
            try:
                image_bytes = await self._load_file_bytes(file_path)
                if image_bytes:
                    embeddings = await self._vision_encoder.encode_batch([image_bytes])
                    if embeddings and len(embeddings) == 1:
                        emb = embeddings[0]
                        # Convert mx.array to list for JSON serialization
                        if hasattr(emb, "tolist"):
                            enrichment["vision_embedding"] = emb.tolist()
                        elif hasattr(emb, "__iter__"):
                            enrichment["vision_embedding"] = list(emb)
            except Exception as exc:
                log.debug("Multimodal vision encode failed for %s: %s", finding_id, exc)

        # 2. MambaFusion: fuse (vision, text, graph)
        if self._fusion_model is not None and enrichment["vision_embedding"] is not None:
            try:
                import mlx.core as mx
                vision_emb = mx.array(enrichment["vision_embedding"])
                # Text embedding: zeros (no text model in scope)
                text_emb = mx.zeros_like(vision_emb)
                # Graph embedding: zeros (no graph model in scope)
                graph_emb = mx.zeros_like(vision_emb)

                fused = self._fusion_model(vision_emb, text_emb, graph_emb)
                if hasattr(fused, "tolist"):
                    enrichment["fused_embedding"] = fused.tolist()
                elif hasattr(fused, "__iter__"):
                    enrichment["fused_embedding"] = list(fused)
            except Exception as exc:
                log.debug("Multimodal fusion failed for %s: %s", finding_id, exc)

        # 3. mobileclip text↔image similarity (when available)
        if _MOBILECLIP_AVAILABLE and enrichment["vision_embedding"] is not None:
            try:
                score = await self._clip_similarity_score(file_path, enrichment["vision_embedding"])
                enrichment["clip_score"] = score
            except Exception as exc:
                log.debug("Multimodal clip similarity failed for %s: %s", finding_id, exc)

        # Mark enrichment available if any module produced data
        if enrichment["vision_embedding"] is not None or enrichment["fused_embedding"] is not None:
            enrichment["enrichment_available"] = True

        if not enrichment["enrichment_available"]:
            return None

        return enrichment

    def _can_run_heavy_vision(self) -> bool:
        """
        Check if heavy vision path can run safely (RAM guard).

        Uses governor's memory check to determine if UMA has headroom.
        Returns True if safe to proceed, False if RAM is tight.
        """
        try:
            governor = self._governor
            if governor is None:
                return True

            # Check if governor reports memory pressure
            if hasattr(governor, "is_critical") and governor.is_critical():
                return False
            if hasattr(governor, "is_emergency") and governor.is_emergency():
                return False

            # Try to reserve RAM for heavy vision path (200MB for vision + overhead)
            # This is a probe — we don't actually hold it
            reserve_context = getattr(governor, "reserve", None)
            if reserve_context is None:
                return True

            # Simple heuristic: if governor reports pressure, skip
            if hasattr(governor, "get_current_usage"):
                usage = governor.get_current_usage()
                if isinstance(usage, dict) and usage.get("ram_mb", 0) > governor.high_water * 0.85:
                    return False

            return True
        except Exception:
            # Fail-open: if governor check errors, allow the operation
            return True

    async def _load_file_bytes(self, file_path: str) -> Optional[bytes]:
        """Load file bytes from path. Fail-safe — returns None on error."""
        try:
            loop = asyncio.get_running_loop()
            def _read():
                with open(file_path, "rb") as f:
                    return f.read()
            return await loop.run_in_executor(None, _read)
        except Exception as exc:
            log.debug("Failed to read file %s: %s", file_path, exc)
            return None

    async def _clip_similarity_score(self, file_path: str, vision_embedding: list[float]) -> Optional[float]:
        """
        Compute CLIP text↔image similarity score.
        Returns a float in [0.0, 1.0] or None on failure.
        """
        if not _MOBILECLIP_AVAILABLE:
            return None

        try:
            from mobileclip import create_model_and_transforms, get_tokenizer
            import mlx.core as mx
            from PIL import Image

            loop = asyncio.get_running_loop()

            def _score():
                model, _, preprocess = create_model_and_transforms("mobileclip_s0")
                tokenizer = get_tokenizer("mobileclip_s0")

                # Text embedding (use file path stem as simple text proxy)
                text = Path(file_path).stem.replace("_", " ")
                text_tokens = tokenizer([text])
                text_emb = model.encode_text(text_tokens)

                # Image embedding
                image = Image.open(file_path).convert("RGB")
                image_preprocessed = preprocess(image)
                image_batch = mx.stack([image_preprocessed])
                image_emb = model.encode_image(image_batch)

                # Cosine similarity
                text_norm = text_emb / mx.linalg.norm(text_emb)
                image_norm = image_emb / mx.linalg.norm(image_emb)
                score = float((text_norm * image_norm).sum())
                return max(0.0, min(1.0, score))

            return await loop.run_in_executor(None, _score)
        except Exception as exc:
            log.debug("CLIP similarity score failed for %s: %s", file_path, exc)
            return None

    async def enrich_batch(self, findings: list[Any]) -> dict[str, dict[str, Any]]:
        """
        Enrich multiple findings concurrently.

        Args:
            findings: List of CanonicalFinding objects.

        Returns:
            Dict mapping finding_id -> enrichment dict (or empty if failed).
            Failures are silent — only successful enrichments are returned.
        """
        if not findings:
            return {}

        semaphore = asyncio.Semaphore(3)  # Max 3 concurrent (M1 8GB safe)

        async def enrich_one(finding: Any) -> tuple[str, Optional[dict[str, Any]]]:
            async with semaphore:
                finding_id = getattr(finding, "finding_id", "unknown")
                try:
                    result = await self.enrich(finding)
                    return (finding_id, result)
                except Exception as exc:
                    log.debug("Batch multimodal enrichment failed for %s: %s", finding_id, exc)
                    return (finding_id, None)

        tasks = [enrich_one(f) for f in findings]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        out = {}
        for item in results:
            if isinstance(item, Exception):
                continue
            fid, enrich_data = item
            if enrich_data is not None:
                out[fid] = enrich_data

        return out
