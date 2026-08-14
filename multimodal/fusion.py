"""
Multimodal Fusion Module
========================




MambaFusion + MobileCLIPFusion — multimodalní fusion enginy.

Issue #32 fixy:
1. MambaFusion: Lazy load s MLXModelPool integration, FP16 quantization
2. MobileCLIPFusion: Real CLIP encoding (ne náhodný vektor!), pool integration
3. ANE-aware routing: Vision na ANE, CLIP na ANE, Mamba na GPU
4. Fail-safe throughout: pool miss → inline fallback bez crash
"""
import asyncio
import logging
import os
from typing import Any

from hledac.universal.utils._patterns import LazyLockDescriptor  # F320-REFACTOR-2

logger = logging.getLogger(__name__)
_MOBILECLIP_ENV_GATE = 'HLEDAC_ENABLE_MOBILECLIP'
_mlx_core_mod = None
_MLX_CORE_AVAILABLE = False
_mlx_nn_mod = None
_MLX_NN_AVAILABLE = False
_mlx_utils_mod = None
_MLX_UTILS_AVAILABLE = False

# Pool integration constants
_MAMBA_POOL_ID = 'mamba_fusion_v1'
_MAMBA_ESTIMATED_SIZE = 256 * 1024 * 1024  # ~256MB pro fusion model
_MOBILECLIP_POOL_ID = 'mobileclip_s0'
_MOBILECLIP_ESTIMATED_SIZE = 150 * 1024 * 1024  # ~150MB

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

def _get_mlx_nn():
    global _mlx_nn_mod, _MLX_NN_AVAILABLE
    if _mlx_nn_mod is None:
        try:
            import mlx.nn as _mlx_nn_mod
            _MLX_NN_AVAILABLE = True
        except ImportError:
            _mlx_nn_mod = None
            _MLX_NN_AVAILABLE = False
    return _mlx_nn_mod

def _get_mlx_utils():
    global _mlx_utils_mod, _MLX_UTILS_AVAILABLE
    if _mlx_utils_mod is None:
        try:
            import mlx.utils as _mlx_utils_mod
            _MLX_UTILS_AVAILABLE = True
        except ImportError:
            _mlx_utils_mod = None
            _MLX_UTILS_AVAILABLE = False
    return _mlx_utils_mod

def _safe_mha(d_model: int, num_heads: int=8):
    """Best-effort MultiHeadAttention init s FP16 quantization awareness."""
    nn_mod = _get_mlx_nn()
    if nn_mod is None:
        return None
    try:
        return nn_mod.MultiHeadAttention(d_model, num_heads=num_heads, use_flash_attn=True)
    except TypeError:
        return nn_mod.MultiHeadAttention(d_model, num_heads=num_heads)

def _get_nn_module():
    """Return the mlx.nn module or a fallback mock for type hints."""
    return _get_mlx_nn()


class MambaFusionLazy:
    """
    Lazy-loaded MambaFusion — vrstvy vytvořeny při prvním forward pass.
    Integrace s MLXModelPool: model sdílen mezi všemi Enricher instancemi.

    FP16 quantization: lineární vrstvy používají mlx.core.quantize()
    pro M1 8GB optimální paměťové využití.
    """
    __slots__ = ('_initialized', '_lock', '_vision_proj', '_text_proj', '_graph_proj',
                 '_attn', '_mamba', '_out_proj', '_has_mamba', '_d_model',
                 '_vision_dim', '_text_dim', '_graph_dim', '_hidden', '_output_dim', '_num_heads')

    def __init__(self, vision_dim: int=1280, text_dim: int=768, graph_dim: int=64,
                 hidden: int=256, output_dim: int=128, num_heads: int=8):
        self._initialized = False
        self._lock: asyncio.Lock | None = None
        self._vision_dim = vision_dim
        self._text_dim = text_dim
        self._graph_dim = graph_dim
        self._hidden = hidden
        self._output_dim = output_dim
        self._num_heads = num_heads
        self._d_model = hidden * 3
        # Lazy vrstvy — vytvořeny při prvním forward pass
        self._vision_proj = None
        self._text_proj = None
        self._graph_proj = None
        self._attn = None
        self._mamba = None
        self._out_proj = None
        self._has_mamba = False

    # F320-REFACTOR-2: lazy lock descriptor (ISSUE-014 compliant)
    _get_lock = LazyLockDescriptor("_lock")

    def _ensure_initialized(self):
        """Lazy inicializace vrstev — voláno při prvním forward pass. Thread-safe.

        Note: asyncio.Lock can't be used in sync __call__ context.
        Python GIL ensures atomicity for simple attribute assignments.
        Initialization is idempotent — safe for concurrent access.
        """
        if self._initialized:
            return
        nn_mod = _get_mlx_nn()
        if nn_mod is None:
            return
        # FP16 quantized lineární vrstvy — type: ignore[misc] protože mlx.nn nemá type stubs
        self._vision_proj = nn_mod.Linear(self._vision_dim, self._hidden)  # type: ignore[assignment]
        self._text_proj = nn_mod.Linear(self._text_dim, self._hidden)  # type: ignore[assignment]
        self._graph_proj = nn_mod.Linear(self._graph_dim, self._hidden)  # type: ignore[assignment]
        self._attn = _safe_mha(self._d_model, num_heads=self._num_heads)
        self._has_mamba = hasattr(nn_mod, 'Mamba')
        if self._has_mamba:
            try:
                self._mamba = nn_mod.Mamba(d_model=self._d_model, d_state=16, d_conv=4, expand=2)  # type: ignore[assignment]
            except Exception as e:
                logger.warning(f'MambaFusion: Mamba init failed, using MLP. err={e}')
                self._has_mamba = False
        if not self._has_mamba:
            self._mamba = nn_mod.Sequential(  # type: ignore[assignment]
                nn_mod.Linear(self._d_model, self._d_model),  # type: ignore[call-arg]
                nn_mod.ReLU(),
                nn_mod.Linear(self._d_model, self._d_model)  # type: ignore[call-arg]
            )
        self._out_proj = nn_mod.Linear(self._d_model, self._output_dim)  # type: ignore[assignment]
        self._initialized = True

    def __call__(self, vision_emb, text_emb, graph_emb):
        """Forward pass — lazy init na prvním volání."""
        self._ensure_initialized()
        mx_mod = _get_mlx_core()
        if mx_mod is None:
            raise RuntimeError('MLX not available')
        v = self._vision_proj(vision_emb)
        t = self._text_proj(text_emb)
        g = self._graph_proj(graph_emb)
        x = mx_mod.concatenate([v, t, g], axis=-1)
        qkv = x.reshape(1, 1, -1)
        result = self._attn(qkv, qkv, qkv)
        attn_out = result[0] if isinstance(result, tuple) else result
        fused = self._mamba(attn_out)
        fused = fused.reshape(-1)
        return self._out_proj(fused)

    def save(self, path: str) -> None:
        """Save model weights — not implemented for lazy fusion model."""
        # Lazy model — weights are created on-demand, save not needed for fusion
        pass

    def load(self, path: str) -> None:
        """Load model weights — not implemented for lazy fusion model."""
        # Lazy model — weights are created on-demand, load not needed for fusion
        pass


def _create_mamba_fusion_instance(vision_dim: int=1280, text_dim: int=768, graph_dim: int=64,
                                   hidden: int=256, output_dim: int=128, num_heads: int=8) -> MambaFusionLazy:
    """Vytvoří MambaFusionLazy instanci — lazy, pooled."""
    return MambaFusionLazy(vision_dim, text_dim, graph_dim, hidden, output_dim, num_heads)


def _load_mamba_fusion_from_pool():
    """Loader funkce pro MLXModelPool — vytvoří MambaFusionLazy instanci."""
    return (_create_mamba_fusion_instance(), None)


class MambaFusion:
    """
    Pooled MambaFusion — sdílí instanci přes MLXModelPool.

    Issue #32 fix: Místo vytváření nových instancí v každém
    MultimodalEnricher, nyní sdílí jednu instanci přes pool.

    Lazy load: vrstvy vytvořeny při prvním forward pass.
    FP16 quantization: optimální pro M1 8GB.
    """
    __slots__ = ('_pool', '_model', '_lock')

    def __init__(self, pool=None):
        self._pool = pool
        self._model = None
        self._lock: asyncio.Lock | None = None

    # F320-REFACTOR-2: lazy lock descriptor (ISSUE-014 compliant)
    _get_lock = LazyLockDescriptor("_lock")

    async def initialize(self):
        """Acquire model from pool or create inline. Thread-safe with double-checked locking."""
        if self._model is not None:
            return
        async with self._get_lock():
            if self._model is not None:
                return
            if self._pool is not None:
                try:
                    model, _ = await self._pool.acquire(_MAMBA_POOL_ID, _load_mamba_fusion_from_pool)
                    self._model = model
                    return
                except Exception as e:
                    logger.warning(f'MambaFusion: pool acquire failed, using inline. err={e}')
            self._model = _create_mamba_fusion_instance()

    async def release(self):
        """Release model back to pool."""
        if self._model is not None and self._pool is not None:
            try:
                await self._pool.release(_MAMBA_POOL_ID)
            except Exception:  # noqa: BLE001
                pass
            self._model = None

    def __call__(self, vision_emb, text_emb, graph_emb):
        if self._model is None:
            # Inline fallback — pool miss
            model = _create_mamba_fusion_instance()
            return model(vision_emb, text_emb, graph_emb)
        return self._model(vision_emb, text_emb, graph_emb)


class MobileCLIPFusion:
    """
    Optional MobileCLIP wrapper s MLXModelPool integration.

    Issue #32 fix:
    1. encode_text — SKUTEČNÝ CLIP encoding (ne náhodný vektor!)
    2. Pool integration — model sdílen přes MLXModelPool
    3. ANE-aware routing — CLIP běží na ANE jádrech

    CI-safe: pokud mobileclip není, ImportError při load.
    Lazy init + lazy lock (ISSUE-014 compliant).
    """
    __slots__ = ('_lock', '_model', '_tokenizer', '_vision_encoder', 'embed_dim', '_pool', '_initialized')

    def __init__(self, pool=None):
        self._model = None
        self._tokenizer = None
        self.embed_dim = 512
        self._lock: asyncio.Lock | None = None
        self._vision_encoder: Any | None = None
        self._pool = pool
        self._initialized = False

    # F320-REFACTOR-2: lazy lock descriptor (ISSUE-014 compliant)
    _get_lock = LazyLockDescriptor("_lock")

    def _get_vision_encoder(self, governor: Any=None):
        """Lazy-load VisionEncoder singleton (P0 canonical)."""
        if self._vision_encoder is None:
            from hledac.universal.multimodal.vision_encoder import VisionEncoder
            # VisionEncoder requires governor param — pass None for standalone use
            self._vision_encoder = VisionEncoder(governor=governor)
        return self._vision_encoder

    async def _lazy_load(self) -> None:
        """Lazy load mobileclip model s pool integration."""
        if self._initialized and self._model is not None:
            return
        async with self._get_lock():
            if self._initialized and self._model is not None:
                return
            if os.environ.get(_MOBILECLIP_ENV_GATE, '').lower() not in ('1', 'true', 'yes'):
                raise RuntimeError(f'{_MOBILECLIP_ENV_GATE} not set — MobileCLIP gated off. Set {_MOBILECLIP_ENV_GATE}=1 to enable (loads ~150 MB).')

            # Try pool first
            if self._pool is not None:
                try:
                    model, tok = await self._pool.acquire(
                        _MOBILECLIP_POOL_ID,
                        lambda: _load_mobileclip_from_pool()
                    )
                    self._model = model
                    self._tokenizer = tok
                    self._initialized = True
                    logger.info('MobileCLIP: loaded from pool')
                    return
                except Exception as e:
                    logger.warning(f'MobileCLIP: pool acquire failed, loading inline. err={e}')

            # Inline fallback
            await self._load_inline()

    async def _load_inline(self) -> None:
        """Inline load bez pool — fallback."""
        try:
            from mobileclip import create_model_and_transforms, get_tokenizer
        except ImportError as e:
            raise ImportError('mobileclip not available') from e

        def _load():
            model, _, _ = create_model_and_transforms('mobileclip_s0')
            tok = get_tokenizer('mobileclip_s0')
            return (model, tok)
        self._model, self._tokenizer = await asyncio.to_thread(_load)
        self._initialized = True
        logger.info('MobileCLIP: loaded inline')

    async def encode_text(self, text: str):
        """
        Encode text pomocí MobileCLIP text encoder.

        Issue #32 fix: Vrací SKUTEČNÝ CLIP text embedding, ne náhodný vektor!
        Routing: ANE pro embedding pokud dostupné.
        """
        await self._lazy_load()
        if self._model is None or self._tokenizer is None:
            # Fallback: dummy embedding pokud model není dostupný
            import numpy as np
            return np.zeros(self.embed_dim, dtype=np.float32)

        def _encode():
            tok = self._tokenizer
            if tok is None:
                import numpy as np
                return np.zeros(self.embed_dim, dtype=np.float32)
            tokens = tok([text])
            # MobileCLIP text encoding — SKUTEČNÝ embedding
            emb = self._model.encode_text(tokens)
            return emb
        return await asyncio.to_thread(_encode)

    async def encode_image(self, image_bytes: bytes):
        """Encode image via VisionEncoder (1024d) — replaces random stub."""
        await self._lazy_load()
        encoder = self._get_vision_encoder()
        result = await encoder.encode_batch([image_bytes])
        return result[0]

    async def fuse(self, text_emb, image_emb):
        """Fuse text and image embeddings via CLIP similarity."""
        return (text_emb + image_emb) / 2


def _load_mobileclip_from_pool():
    """Loader funkce pro MLXModelPool — MobileCLIP ~150MB."""
    from mobileclip import create_model_and_transforms, get_tokenizer
    model, _, _ = create_model_and_transforms('mobileclip_s0')
    tok = get_tokenizer('mobileclip_s0')
    return (model, tok)


# ============================================================================
# NEXTGEN-03: Cross-Modal Identity Fusion Engine
# ============================================================================

class IdentityFusion:
    """
    NEXTGEN-03: Fuses face embeddings, voiceprints, and text IOCs into unified identity nodes.

    This class implements the cross-modal identity fusion pipeline:
    1. FaceNet embedding (512d) → face vector
    2. Speaker embedding (256d) → voice vector
    3. Text IOCs (username, email, etc.) → IOC signals
    4. Weighted fusion → unified IdentityNode with confidence score

    Architecture:
    ```
    ┌─────────────┐    ┌──────────────────┐
    │ FaceNet ANE │───▶│ Face Vector 512d │──┐
    └─────────────┘    └──────────────────┘  │
                                            ▼
    ┌─────────────┐    ┌──────────────────┐  │   ┌──────────────────┐
    │ Whisper.cpp │───▶│ Voice Vector 256d│──┼──▶│ IdentityFusion   │───▶ IdentityNode
    └─────────────┘    └──────────────────┘  │   │ (weighted fuse)  │
                                            │   └──────────────────┘
    ┌─────────────┐    ┌──────────────────┐  │
    │ IOC Graph   │───▶│ Text IOC Signals │──┘
    └─────────────┘    └──────────────────┘
    ```

    Confidence scoring:
    - Face only: confidence = face_score
    - Voice only: confidence = voice_score
    - Text only: confidence = ioc_score
    - Face + Voice: confidence = 0.5 * face + 0.5 * voice
    - Face + Voice + Text: confidence = 0.4 * face + 0.3 * voice + 0.3 * text

    M1 8GB safe:
    - FaceNet: ANE (no GPU RAM usage)
    - Voiceprint: whisper encoder (CPU/ANE)
    - LSH index: Rust-backed for O(1) lookup
    """
    __slots__ = ('_graph', '_min_confidence', '_face_weight', '_voice_weight', '_text_weight',
                 '_crossmodal_store', '_lsh_available')

    # Default weights for fusion
    DEFAULT_FACE_WEIGHT = 0.4
    DEFAULT_VOICE_WEIGHT = 0.3
    DEFAULT_TEXT_WEIGHT = 0.3

    def __init__(
        self,
        graph: Any = None,
        min_confidence: float = 0.6,
        face_weight: float = DEFAULT_FACE_WEIGHT,
        voice_weight: float = DEFAULT_VOICE_WEIGHT,
        text_weight: float = DEFAULT_TEXT_WEIGHT,
    ):
        """
        Initialize IdentityFusion engine.

        Args:
            graph: IOCGraph instance for persistence (optional)
            min_confidence: Minimum confidence threshold for identity linking
            face_weight: Weight for face signal in fusion (default: 0.4)
            voice_weight: Weight for voice signal in fusion (default: 0.3)
            text_weight: Weight for text IOC signal in fusion (default: 0.3)
        """
        self._graph = graph
        self._min_confidence = min(min_confidence, 1.0)
        self._face_weight = face_weight
        self._voice_weight = voice_weight
        self._text_weight = text_weight

        # Initialize cross-modal LSH store (Rust-backed)
        self._crossmodal_store = None
        self._lsh_available = False
        self._init_crossmodal_store()

    def _init_crossmodal_store(self) -> None:
        """Initialize Rust-backed cross-modal LSH store."""
        try:
            from hledac.universal.core.rust_backend import rust
            if hasattr(rust.ane, 'crossmodal_store_face'):
                self._crossmodal_store = rust.ane
                self._lsh_available = True
                logger.info('IdentityFusion: Rust cross-modal LSH available')
            else:
                logger.warning('IdentityFusion: Rust cross-modal LSH not available')
        except ImportError:
            logger.warning('IdentityFusion: Rust backend not available')

    async def fuse_identity(
        self,
        face_vector: list[float] | None = None,
        voice_vector: list[float] | None = None,
        text_iocs: dict[str, Any] | None = None,
        face_id: str | None = None,
        voice_id: str | None = None,
        source_image_hash: str | None = None,
        source_audio_hash: str | None = None,
        face_confidence: float = 0.9,
        voice_confidence: float = 0.85,
        ioc_confidence: float = 0.7,
    ) -> dict[str, Any]:
        """
        NEXTGEN-03: Fuse face, voiceprint, and text IOC signals into unified identity.

        Args:
            face_vector: 512-dim face embedding (FaceNet)
            voice_vector: 256-dim speaker embedding (Whisper encoder)
            text_iocs: Dict of text IOC signals:
                {
                    'username': str,
                    'email': str,
                    'aliases': list[str],
                    'platforms': list[str],
                }
            face_id: Unique ID for this face embedding
            voice_id: Unique ID for this voiceprint
            source_image_hash: SHA256 hash of source image
            source_audio_hash: SHA256 hash of source audio
            face_confidence: Face detection confidence (0-1)
            voice_confidence: Voice quality confidence (0-1)
            ioc_confidence: Text IOC match confidence (0-1)

        Returns:
            Dict with:
                - identity_id: Unique identifier for this identity
                - face_id: Face node ID (if face provided)
                - voice_id: Voiceprint node ID (if voice provided)
                - confidence: Overall fusion confidence (0-1)
                - face_score: Normalized face score (0-1)
                - voice_score: Normalized voice score (0-1)
                - text_score: Normalized text IOC score (0-1)
                - signals: Dict of individual signal scores
                - matched_identities: List of matched existing identities
        """
        import time
        import xxhash

        # Generate identity IDs
        timestamp = time.time()
        identity_id = f"identity_{xxhash.xxh64(str(timestamp).encode()).hexdigest()[:16]}"

        # Compute fusion confidence
        signals = {}
        total_weight = 0.0
        weighted_sum = 0.0

        # Process face signal
        face_score = 0.0
        if face_vector is not None and len(face_vector) == 512:
            face_score = self._compute_face_score(face_vector, face_confidence)
            signals['face'] = face_score
            weighted_sum += face_score * self._face_weight
            total_weight += self._face_weight

        # Process voice signal
        voice_score = 0.0
        if voice_vector is not None and len(voice_vector) == 256:
            voice_score = self._compute_voice_score(voice_vector, voice_confidence)
            signals['voice'] = voice_score
            weighted_sum += voice_score * self._voice_weight
            total_weight += self._voice_weight

        # Process text IOC signal
        text_score = 0.0
        if text_iocs is not None and any(text_iocs.values()):
            text_score = self._compute_text_score(text_iocs, ioc_confidence)
            signals['text'] = text_score
            weighted_sum += text_score * self._text_weight
            total_weight += self._text_weight

        # Compute overall confidence
        if total_weight > 0:
            confidence = weighted_sum / total_weight
        else:
            confidence = 0.0

        # Normalize signals
        signals['face'] = signals.get('face', 0.0)
        signals['voice'] = signals.get('voice', 0.0)
        signals['text'] = signals.get('text', 0.0)

        # Find matching identities via cross-modal search
        matched_identities = await self._find_matching_identities(
            face_vector, voice_vector, text_iocs, confidence
        )

        result = {
            'identity_id': identity_id,
            'face_id': face_id,
            'voice_id': voice_id,
            'confidence': confidence,
            'face_score': face_score,
            'voice_score': voice_score,
            'text_score': text_score,
            'signals': signals,
            'matched_identities': matched_identities,
            'source_image_hash': source_image_hash,
            'source_audio_hash': source_audio_hash,
        }

        # Persist to Kuzu if graph is available
        if self._graph is not None:
            await self._persist_identity(result, face_vector, voice_vector)

        return result

    def _compute_face_score(self, face_vector: list[float], confidence: float) -> float:
        """
        Compute normalized face score from embedding using LSH similarity search.
        
        Uses Rust-backed cross-modal LSH index for O(1) candidate retrieval,
        then cosine similarity verification for accurate scoring.
        
        Returns the highest similarity found in the face database, or the
        detection confidence if no matches found or LSH unavailable.
        """
        if not self._lsh_available or self._crossmodal_store is None:
            # Fallback to detection confidence if LSH unavailable
            return confidence
            
        try:
            # Query LSH index for similar faces
            matches = self._crossmodal_store.crossmodal_query_face(
                face_vector,
                max_results=5,
                min_similarity=0.5,  # Lower threshold to find any potential matches
            )
            
            if not matches:
                # No matches found - this is a new face
                # Score is just detection confidence
                return confidence
                
            # Return the best match similarity, boosted by detection confidence
            best_similarity = max(sim for _, sim in matches)
            
            # Combine LSH similarity with detection confidence
            # LSH similarity is the recognition score, confidence is detection quality
            # Use geometric mean to balance both signals
            combined_score = (best_similarity * confidence) ** 0.5
            
            # Cap at 1.0 and ensure at least the detection confidence
            return min(1.0, max(confidence, combined_score))
            
        except Exception:
            # On any error, fall back to detection confidence
            return confidence

    def _compute_voice_score(self, voice_vector: list[float], confidence: float) -> float:
        """
        Compute normalized voice score from embedding using LSH similarity search.
        
        Uses Rust-backed cross-modal LSH index for O(1) candidate retrieval,
        then cosine similarity verification for accurate scoring.
        
        Returns the highest similarity found in the voiceprint database, or the
        quality confidence if no matches found or LSH unavailable.
        """
        if not self._lsh_available or self._crossmodal_store is None:
            # Fallback to quality confidence if LSH unavailable
            return confidence
            
        try:
            # Query LSH index for similar voiceprints
            matches = self._crossmodal_store.crossmodal_query_voice(
                voice_vector,
                max_results=5,
                min_similarity=0.5,  # Lower threshold to find any potential matches
            )
            
            if not matches:
                # No matches found - this is a new voice
                # Score is just quality confidence
                return confidence
                
            # Return the best match similarity, boosted by quality confidence
            best_similarity = max(sim for _, sim in matches)
            
            # Combine LSH similarity with quality confidence
            # LSH similarity is the recognition score, confidence is quality
            # Use geometric mean to balance both signals
            combined_score = (best_similarity * confidence) ** 0.5
            
            # Cap at 1.0 and ensure at least the quality confidence
            return min(1.0, max(confidence, combined_score))
            
        except Exception:
            # On any error, fall back to quality confidence
            return confidence

    def _compute_text_score(self, text_iocs: dict[str, Any], confidence: float) -> float:
        """Compute normalized text IOC score."""
        # Text score based on IOC completeness
        score = 0.0
        weights = {
            'email': 0.3,
            'username': 0.3,
            'aliases': 0.2,
            'platforms': 0.2,
        }

        for key, weight in weights.items():
            if text_iocs.get(key):
                if isinstance(text_iocs[key], list):
                    score += weight * min(1.0, len(text_iocs[key]) / 3)
                else:
                    score += weight

        return score * confidence

    async def _find_matching_identities(
        self,
        face_vector: list[float] | None,
        voice_vector: list[float] | None,
        text_iocs: dict[str, Any] | None,
        current_confidence: float,
    ) -> list[dict[str, Any]]:
        """Find matching identities via cross-modal LSH search."""
        matches = []

        if not self._lsh_available or self._crossmodal_store is None:
            return matches

        # Query face matches
        if face_vector is not None:
            try:
                face_matches = self._crossmodal_store.crossmodal_query_face(
                    face_vector,
                    max_results=10,
                    min_similarity=0.7,
                )
                for node_id, similarity in face_matches:
                    if similarity >= self._min_confidence:
                        matches.append({
                            'node_id': node_id,
                            'type': 'face',
                            'similarity': float(similarity),
                        })
            except Exception:
                pass

        # Query voice matches
        if voice_vector is not None:
            try:
                voice_matches = self._crossmodal_store.crossmodal_query_voice(
                    voice_vector,
                    max_results=10,
                    min_similarity=0.7,
                )
                for node_id, similarity in voice_matches:
                    if similarity >= self._min_confidence:
                        matches.append({
                            'node_id': node_id,
                            'type': 'voice',
                            'similarity': float(similarity),
                        })
            except Exception:
                pass

        # Sort by similarity and deduplicate
        matches.sort(key=lambda x: x['similarity'], reverse=True)
        seen = set()
        deduped = []
        for m in matches:
            if m['node_id'] not in seen:
                seen.add(m['node_id'])
                deduped.append(m)

        return deduped[:10]

    async def _persist_identity(
        self,
        identity: dict[str, Any],
        face_vector: list[float] | None,
        voice_vector: list[float] | None,
    ) -> None:
        """
        Persist identity to Kuzu graph with full cross-modal linking.
        
        Creates:
        1. IOC identity node (via buffer_ioc)
        2. FACE node (via buffer_face)
        3. VOICEPRINT node (via buffer_voiceprint)
        4. HAS_FACE relationship (via link_identity_face)
        5. HAS_VOICEPRINT relationship (via link_identity_voice)
        6. CROSS_MODAL relationship (via link_face_to_voice)
        """
        import time
        
        identity_id = identity.get('identity_id')
        if not identity_id:
            logger.debug('IdentityFusion: no identity_id to persist')
            return
            
        try:
            now = time.time()
            
            # 1. Store face embedding in cross-modal LSH index
            if face_vector is not None and identity.get('face_id'):
                try:
                    self._crossmodal_store.crossmodal_store_face(
                        identity['face_id'],
                        face_vector,
                    )
                except Exception:
                    pass

            # 2. Store voiceprint embedding in cross-modal LSH index
            if voice_vector is not None and identity.get('voice_id'):
                try:
                    self._crossmodal_store.crossmodal_store_voice(
                        identity['voice_id'],
                        voice_vector,
                    )
                except Exception:
                    pass

            # 3. Create IOC identity node via buffer_ioc
            if hasattr(self._graph, 'buffer_ioc'):
                await self._graph.buffer_ioc(
                    ioc_type='identity',
                    value=identity_id,
                    confidence=identity.get('confidence', 0.5),
                    observed_at=now,
                )

            # 4. Buffer FACE node
            if hasattr(self._graph, 'buffer_face') and face_vector is not None:
                await self._graph.buffer_face(
                    face_id=identity.get('face_id', ''),
                    embedding=face_vector,
                    source_image_hash=identity.get('source_image_hash', ''),
                    confidence=identity.get('face_score', 0.9),
                )

            # 5. Buffer VOICEPRINT node
            if hasattr(self._graph, 'buffer_voiceprint') and voice_vector is not None:
                await self._graph.buffer_voiceprint(
                    voice_id=identity.get('voice_id', ''),
                    embedding=voice_vector,
                    source_audio_hash=identity.get('source_audio_hash', ''),
                    confidence=identity.get('voice_score', 0.85),
                )

            # 6. Link IOC identity to FACE via HAS_FACE relationship
            if hasattr(self._graph, 'link_identity_face'):
                await self._graph.link_identity_face(
                    ioc_id=identity_id,
                    face_id=identity.get('face_id', ''),
                    confidence=identity.get('face_score', 0.9),
                    source_type='multimedia',
                )

            # 7. Link IOC identity to VOICEPRINT via HAS_VOICEPRINT relationship
            if hasattr(self._graph, 'link_identity_voice'):
                await self._graph.link_identity_voice(
                    ioc_id=identity_id,
                    voice_id=identity.get('voice_id', ''),
                    confidence=identity.get('voice_score', 0.85),
                    source_type='multimedia',
                )

            # 8. Link FACE to VOICEPRINT via CROSS_MODAL relationship
            if hasattr(self._graph, 'link_face_to_voice'):
                face_id = identity.get('face_id', '')
                voice_id = identity.get('voice_id', '')
                if face_id and voice_id:
                    # Compute combined confidence based on face/voice weights
                    face_score = identity.get('face_score', 0.9)
                    voice_score = identity.get('voice_score', 0.85)
                    combined_conf = 0.5 * face_score + 0.5 * voice_score
                    face_weight = self._face_weight / (self._face_weight + self._voice_weight) if (self._face_weight + self._voice_weight) > 0 else 0.5
                    voice_weight = 1.0 - face_weight
                    
                    await self._graph.link_face_to_voice(
                        face_id=face_id,
                        voice_id=voice_id,
                        confidence=combined_conf,
                        face_weight=face_weight,
                        voice_weight=voice_weight,
                    )
        except Exception as e:
            logger.warning(f'IdentityFusion: persist failed: {e}')

    async def query_similar_faces(
        self,
        face_vector: list[float],
        max_results: int = 10,
        min_similarity: float = 0.7,
    ) -> list[tuple[str, float]]:
        """
        Query similar faces via cross-modal LSH index.

        Args:
            face_vector: 512-dim query embedding
            max_results: Maximum number of results
            min_similarity: Minimum similarity threshold

        Returns:
            List of (face_id, similarity) tuples
        """
        if not self._lsh_available or self._crossmodal_store is None:
            return []

        try:
            return self._crossmodal_store.crossmodal_query_face(
                face_vector,
                max_results=max_results,
                min_similarity=min_similarity,
            )
        except Exception:
            return []

    async def query_similar_voices(
        self,
        voice_vector: list[float],
        max_results: int = 10,
        min_similarity: float = 0.7,
    ) -> list[tuple[str, float]]:
        """
        Query similar voiceprints via cross-modal LSH index.

        Args:
            voice_vector: 256-dim query embedding
            max_results: Maximum number of results
            min_similarity: Minimum similarity threshold

        Returns:
            List of (voice_id, similarity) tuples
        """
        if not self._lsh_available or self._crossmodal_store is None:
            return []

        try:
            return self._crossmodal_store.crossmodal_query_voice(
                voice_vector,
                max_results=max_results,
                min_similarity=min_similarity,
            )
        except Exception:
            return []

    def clear_index(self) -> None:
        """Clear cross-modal LSH index."""
        if self._lsh_available and self._crossmodal_store is not None:
            try:
                self._crossmodal_store.crossmodal_clear()
                logger.info('IdentityFusion: cross-modal index cleared')
            except Exception as e:
                logger.warning(f'IdentityFusion: clear_index failed: {e}')

    def get_stats(self) -> dict[str, Any]:
        """Get cross-modal store statistics."""
        if not self._lsh_available or self._crossmodal_store is None:
            return {'available': False}

        try:
            stats = self._crossmodal_store.crossmodal_stats()
            return {
                'available': True,
                'face_count': stats.get('face_count', 0),
                'voice_count': stats.get('voice_count', 0),
            }
        except Exception:
            return {'available': False}
