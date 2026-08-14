"""
Universal Multimodal Coordinator
================================











Integrated multimodal processing from:
- MultimodalCoordinator: Modality handling, cross-modal fusion

Features:
- Automatic modality detection (text, image, audio, video, document)
- Cross-modal fusion
- Memory-efficient processing
- Unified embedding generation
- Modality-specific processing pipelines
"""
import logging
import time
from dataclasses import field
from enum import Enum
from typing import Any

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct

try:
    import numpy as np
    from numpy.typing import NDArray
    HAS_NUMPY = True
except ImportError:
    np = None
    NDArray = 'NDArray'
    HAS_NUMPY = False
from .base import DecisionResponse, ExecutionResult, OperationResult, OperationType, UniversalCoordinator

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE

try:
    import mlx.core as mx
    from mlx import nn
except ImportError:
    mx = None
    nn = None

logger = logging.getLogger(__name__)

class ModalityType(Enum):
    """Supported modalities."""
    TEXT = 'text'
    IMAGE = 'image'
    AUDIO = 'audio'
    VIDEO = 'video'
    DOCUMENT = 'document'
    CHART = 'chart'
    MOLECULAR = 'molecular'
    MIXED = 'mixed'

class ModalityInput(Struct):
    """Input with modality information."""
    content: Any
    modality: ModalityType
    metadata: dict[str, Any] = field(default_factory=dict)
    source: str | None = None

class ModalityOutput(Struct, frozen=True):
    """Output from modality processing."""
    modality: ModalityType
    embedding: Any | None = None
    features: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    confidence: float = 0.0

class FusedRepresentation(Struct, frozen=True):
    """Fused multimodal representation."""
    fused_embedding: Any
    modalities: list[ModalityType]
    weights: dict[ModalityType, float]
    metadata: dict[str, Any] = field(default_factory=dict)

class ContrastiveExample(Struct, frozen=True):
    """Example for contrastive learning."""
    text_embedding: Any
    image_embedding: Any
    label: int

class MLXMultimodalEncoder:
    """
    MLX-based multimodal encoder for M1 optimization.
    Implements vision, audio, and text encoders using MLX.
    """
    __slots__ = ('embedding_dim', 'mlx_available')

    def __init__(self, embedding_dim: int=768) -> None:
        self.embedding_dim = embedding_dim
        self.mlx_available = MLX_AVAILABLE
        if self.mlx_available:
            self._init_encoders()

    def _init_encoders(self) -> None:
        """Initialize MLX encoder models."""

        class VisionEncoder:
            __slots__ = ('conv1', 'conv2', 'fc')

            def __init__(self, embed_dim: int) -> None:
                self.conv1 = lambda x: mx.conv2d(x, weight=mx.random.normal((32, 3, 3, 3)))
                self.conv2 = lambda x: mx.conv2d(x, weight=mx.random.normal((64, 32, 3, 3)))
                self.fc = lambda x: mx.matmul(x, mx.random.normal((64 * 56 * 56, embed_dim)))

            def __call__(self, x):
                x = mx.relu(self.conv1(x))
                x = mx.relu(self.conv2(x))
                x = x.reshape(x.shape[0], -1)
                x = self.fc(x)
                return mx.l2_normalize(x, axis=-1)

        class AudioEncoder:
            __slots__ = ('conv1', 'conv2', 'fc')

            def __init__(self, embed_dim: int) -> None:
                self.conv1 = lambda x: mx.conv1d(x, weight=mx.random.normal((64, 1, 3)))
                self.conv2 = lambda x: mx.conv1d(x, weight=mx.random.normal((128, 64, 3)))
                self.fc = lambda x: mx.matmul(x, mx.random.normal((128 * 124, embed_dim)))

            def __call__(self, x):
                x = mx.relu(self.conv1(x))
                x = mx.relu(self.conv2(x))
                x = x.reshape(x.shape[0], -1)
                x = self.fc(x)
                return mx.l2_normalize(x, axis=-1)

        class TextEncoder:
            __slots__ = ('embedding', 'fc')

            def __init__(self, embed_dim: int, vocab_size: int=30000) -> None:
                self.embedding = lambda x: mx.take(mx.random.normal((vocab_size, 256)), x, axis=0)
                self.fc = lambda x: mx.matmul(x, mx.random.normal((256, embed_dim)))

            def __call__(self, x):
                x = self.embedding(x)
                x = mx.mean(x, axis=1)
                x = self.fc(x)
                return mx.l2_normalize(x, axis=-1)
        self.vision_encoder = VisionEncoder(self.embedding_dim)
        self.audio_encoder = AudioEncoder(self.embedding_dim)
        self.text_encoder = TextEncoder(self.embedding_dim)

    def encode_vision(self, image: Any) -> Any:
        """Encode image to embedding."""
        if not self.mlx_available:
            if not HAS_NUMPY:
                raise ImportError("numpy required for vision encoding — pip install 'hledac[dev]'")
            return self._fallback_vision_encode(image)
        try:
            if image.ndim == 3:
                image = image[np.newaxis, ...]
            x = mx.array(image.astype(np.float32))
            x = x / 255.0
            x = (x - mx.array([0.485, 0.456, 0.406])) / mx.array([0.229, 0.224, 0.225])
            embedding = self.vision_encoder(x)
            return np.array(embedding)
        except Exception as e:
            logger.warning(f'MLX vision encoding failed: {e}, using fallback')
            return self._fallback_vision_encode(image)

    def encode_audio(self, audio: Any) -> Any:
        """Encode audio to embedding."""
        if not self.mlx_available:
            if not HAS_NUMPY:
                raise ImportError("numpy required for audio encoding — pip install 'hledac[dev]'")
            return self._fallback_audio_encode(audio)
        try:
            if audio.ndim == 1:
                audio = audio[np.newaxis, np.newaxis, :]
            elif audio.ndim == 2:
                audio = audio[np.newaxis, ...]
            x = mx.array(audio.astype(np.float32))
            embedding = self.audio_encoder(x)
            return np.array(embedding)
        except Exception as e:
            logger.warning(f'MLX audio encoding failed: {e}, using fallback')
            return self._fallback_audio_encode(audio)

    def encode_text(self, text: str) -> Any:
        """Encode text to embedding."""
        if not self.mlx_available:
            return self._generate_text_embedding(text)
        try:
            tokens = self._simple_tokenize(text)
            x = mx.array(tokens[np.newaxis, :].astype(np.int32))
            embedding = self.text_encoder(x)
            return np.array(embedding)
        except Exception as e:
            logger.warning(f'MLX text encoding failed: {e}, using fallback')
            return self._fallback_text_encode(text)

    def _simple_tokenize(self, text: str, max_length: int=128) -> np.ndarray:
        """Simple whitespace tokenization."""
        words = text.lower().split()[:max_length]
        tokens = [hash(word) % 30000 for word in words]
        while len(tokens) < max_length:
            tokens.append(0)
        return np.array(tokens)

    def _fallback_vision_encode(self, image: np.ndarray) -> np.ndarray:
        """Fallback vision encoding using numpy."""
        if image.ndim == 3:
            features = []
            for i in range(3):
                hist, _ = np.histogram(image[..., i], bins=16, range=(0, 255))
                features.extend(hist / hist.sum() if hist.sum() > 0 else hist)
        else:
            features = np.histogram(image, bins=48, range=(0, 255))[0]
        # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
        embedding = self._get_deterministic_embedding('vision_fallback', scale=1.0)
        embedding[:len(features)] = features[:self.embedding_dim]
        embedding = embedding / np.linalg.norm(embedding)
        return embedding

    def _fallback_audio_encode(self, audio: np.ndarray) -> np.ndarray:
        """Fallback audio encoding using numpy."""
        if audio.ndim > 1:
            audio = audio.flatten()
        features = [np.mean(np.abs(audio)), np.std(audio), np.max(np.abs(audio))]
        fft = np.abs(np.fft.fft(audio[:min(len(audio), 1024)]))
        features.extend(fft[:10])
        # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
        embedding = self._get_deterministic_embedding('audio_fallback', scale=1.0)
        embedding[:len(features)] = features[:self.embedding_dim]
        embedding = embedding / np.linalg.norm(embedding)
        return embedding

    def _fallback_text_encode(self, text: str) -> np.ndarray:
        """Fallback text encoding using numpy."""
        words = text.lower().split()
        unique_words = list(set(words))
        embedding = np.zeros(self.embedding_dim)
        for word in unique_words:
            word_hash = hash(word) % self.embedding_dim
            embedding[word_hash] += 1
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    def _get_deterministic_embedding(self, seed: str | int, scale: float = 0.1) -> np.ndarray:
        """
        IO-4 fix: Deterministic fallback embedding using hash-based RNG.
        
        Replaces np.random.randn() fallbacks to avoid poisoning LanceDB ANN index
        with non-deterministic vectors. Uses a stable hash of the seed to
        produce reproducible embeddings for the same input.
        
        F6 FIX: When this embedding is used in ModalityOutput, set metadata['is_hash_fallback']=True
        to allow consumers to filter out noise vectors from ANN queries.
        
        Args:
            seed: String or int used to seed the RNG deterministically
            scale: Scale factor for the random vector (default 0.1)
            
        Returns:
            L2-normalized random vector of shape (embedding_dim,)
        """
        if isinstance(seed, str):
            seed_val = hash(seed) & 0xFFFFFFFF
        else:
            seed_val = seed & 0xFFFFFFFF
        rng = np.random.default_rng(seed_val)
        embedding = rng.standard_normal(self.embedding_dim, dtype=np.float32) * scale
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

class ContrastiveLearning:
    """
    CLIP-style contrastive learning for multimodal alignment.
    Aligns vision and text embeddings in shared space.
    """
    __slots__ = ('embedding_dim', 'image_projection', 'temperature', 'text_projection')

    def __init__(self, embedding_dim: int=768, temperature: float=0.07) -> None:
        self.embedding_dim = embedding_dim
        self.temperature = temperature
        self.text_projection = self._init_projection()
        self.image_projection = self._init_projection()

    def _init_projection(self):
        """Initialize projection layer."""
        if MLX_AVAILABLE:
            weight = mx.random.normal((self.embedding_dim, self.embedding_dim)) * 0.02
            return lambda x: mx.matmul(x, weight)
        else:
            weight = np.random.randn(self.embedding_dim, self.embedding_dim) * 0.02
            return lambda x: np.matmul(x, weight)

    def compute_contrastive_loss(self, text_embeddings: np.ndarray, image_embeddings: np.ndarray) -> float:
        """
        Compute InfoNCE contrastive loss.

        Args:
            text_embeddings: Text embeddings [batch_size, embed_dim]
            image_embeddings: Image embeddings [batch_size, embed_dim]

        Returns:
            Contrastive loss value
        """
        if MLX_AVAILABLE:
            text_proj = np.array(self.text_projection(mx.array(text_embeddings)))
            image_proj = np.array(self.image_projection(mx.array(image_embeddings)))
        else:
            text_proj = self.text_projection(text_embeddings)
            image_proj = self.image_projection(image_embeddings)
        text_proj = text_proj / np.linalg.norm(text_proj, axis=-1, keepdims=True)
        image_proj = image_proj / np.linalg.norm(image_proj, axis=-1, keepdims=True)
        logits = np.matmul(text_proj, image_proj.T) / self.temperature
        batch_size = text_embeddings.shape[0]
        labels = np.arange(batch_size)
        text_to_image_loss = self._cross_entropy(logits, labels)
        image_to_text_loss = self._cross_entropy(logits.T, labels)
        loss = (text_to_image_loss + image_to_text_loss) / 2
        return loss

    def _cross_entropy(self, logits: np.ndarray, labels: np.ndarray) -> float:
        """Compute cross-entropy loss."""
        exp_logits = np.exp(logits - np.max(logits, axis=-1, keepdims=True))
        probs = exp_logits / np.sum(exp_logits, axis=-1, keepdims=True)
        batch_size = logits.shape[0]
        loss = -np.log(probs[np.arange(batch_size), labels] + 1e-08).mean()
        return loss

    def find_best_matches(self, text_embeddings: np.ndarray, image_embeddings: np.ndarray, top_k: int=5) -> list[list[int]]:
        """
        Find best matching images for each text.

        Returns:
            List of top-k image indices for each text
        """
        text_norm = text_embeddings / np.linalg.norm(text_embeddings, axis=-1, keepdims=True)
        image_norm = image_embeddings / np.linalg.norm(image_embeddings, axis=-1, keepdims=True)
        similarity = np.matmul(text_norm, image_norm.T)
        matches = []
        for emb_idx in range(len(text_embeddings)):
            top_indices = np.argsort(similarity[emb_idx])[-top_k:][::-1]
            matches.append(top_indices.tolist())
        return matches

class UniversalMultimodalCoordinator(UniversalCoordinator):
    """
    Universal coordinator for multimodal processing.

    Features:
    - Automatic modality detection
    - Cross-modal fusion
    - Memory-efficient batching
    - Unified embeddings
    """
    __slots__ = ('_stats', 'contrastive_learner', 'embedding_dim', 'fusion_weights', 'mlx_encoder', 'modality_processors', 'use_mlx')

    def __init__(self, max_concurrent: int=5, embedding_dim: int=768, use_mlx: bool=True) -> None:
        super().__init__(name='universal_multimodal_coordinator', max_concurrent=max_concurrent, memory_aware=True)
        self.embedding_dim = embedding_dim
        self.use_mlx = use_mlx and MLX_AVAILABLE
        if self.use_mlx:
            logger.info('Initializing MLX multimodal encoder for M1 optimization')
            self.mlx_encoder = MLXMultimodalEncoder(embedding_dim)
        else:
            self.mlx_encoder = None
        self.contrastive_learner = ContrastiveLearning(embedding_dim)
        self.modality_processors: dict[ModalityType, callable] = {}
        self._initialize_processors()
        self.fusion_weights: dict[ModalityType, float] = {ModalityType.TEXT: 1.0, ModalityType.IMAGE: 0.9, ModalityType.AUDIO: 0.8, ModalityType.VIDEO: 0.85, ModalityType.DOCUMENT: 0.95, ModalityType.CHART: 0.7, ModalityType.MOLECULAR: 0.75}
        self._stats = {'processed_by_modality': dict.fromkeys(ModalityType, 0), 'fusions_performed': 0, 'modality_detection_accuracy': 0.95, 'mlx_used': self.use_mlx, 'contrastive_alignments': 0}

    def get_supported_operations(self) -> list[OperationType]:
        return [OperationType.RESEARCH, OperationType.SYNTHESIS]

    def _initialize_processors(self) -> None:
        """Initialize modality-specific processors."""
        self.modality_processors[ModalityType.TEXT] = self._process_text
        self.modality_processors[ModalityType.IMAGE] = self._process_image
        self.modality_processors[ModalityType.AUDIO] = self._process_audio
        self.modality_processors[ModalityType.VIDEO] = self._process_video
        self.modality_processors[ModalityType.DOCUMENT] = self._process_document
        self.modality_processors[ModalityType.CHART] = self._process_chart

    def _get_operation_type_for_tracking(self) -> str:
        """Return operation type for tracking."""
        return 'multimodal'

    async def _do_execute_decision(self, decision: DecisionResponse) -> ExecutionResult:
        """Handle multimodal processing request."""
        try:
            operation = decision.metadata.get('multimodal_operation', 'detect_and_process')
            if operation == 'detect_and_process':
                content = decision.metadata.get('content', '')
                result = await self.process_content(content)
            elif operation == 'fuse':
                contents = decision.metadata.get('contents', [])
                result = await self.fuse_multimodal(contents)
            else:
                result = {'success': False, 'error': f'Unknown operation: {operation}'}
            return ExecutionResult(
                status='completed' if result.get('success') else 'failed',
                result_summary=result.get('summary', 'Multimodal processing completed'),
                success=result.get('success', False),
                metadata=result,
            )
        except Exception as e:
            return ExecutionResult(
                status='failed',
                result_summary=f'Multimodal processing failed: {str(e)}',
                success=False,
                error_message=str(e),
            )

    async def detect_modality(self, content: Any) -> ModalityType:
        """
        Automatically detect modality of content.

        Args:
            content: Content to analyze

        Returns:
            Detected modality type
        """
        if isinstance(content, str):
            content.lower()
            if any(ext in content for ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp']):
                return ModalityType.IMAGE
            if any(ext in content for ext in ['.mp3', '.wav', '.ogg', '.flac']):
                return ModalityType.AUDIO
            if any(ext in content for ext in ['.mp4', '.avi', '.mov', '.mkv']):
                return ModalityType.VIDEO
            if any(ext in content for ext in ['.pdf', '.doc', '.docx', '.txt']):
                return ModalityType.DOCUMENT
            return ModalityType.TEXT
        if isinstance(content, np.ndarray):
            if content.ndim in {2, 3}:
                return ModalityType.IMAGE
            elif content.ndim == 1:
                return ModalityType.AUDIO
        return ModalityType.TEXT

    async def process_content(self, content: Any, modality: ModalityType | None=None) -> dict[str, Any]:
        """
        Process content with automatic modality detection.

        Args:
            content: Content to process
            modality: Optional forced modality

        Returns:
            Processing results with embedding and features
        """
        if modality is None:
            modality = await self.detect_modality(content)
        logger.info(f'Processing content with modality: {modality.value}')
        processor = self.modality_processors.get(modality, self._process_text)
        output = await processor(content)
        self._stats['processed_by_modality'][modality] += 1
        return {'success': True, 'modality': modality.value, 'embedding_shape': output.embedding.shape if output.embedding is not None else None, 'features': output.features, 'confidence': output.confidence, 'summary': f'Processed {modality.value} content'}

    async def fuse_multimodal(self, contents: list[Any | tuple[Any, ModalityType]]) -> dict[str, Any]:
        """
        Fuse multiple modalities into unified representation.

        Args:
            contents: List of content (or (content, modality) tuples)

        Returns:
            Fused representation
        """
        logger.info(f'Fusing {len(contents)} modalities')
        outputs: list[ModalityOutput] = []
        modalities: list[ModalityType] = []
        for item in contents:
            if isinstance(item, tuple) and len(item) == 2:
                content, modality = item
            else:
                content = item
                modality = await self.detect_modality(content)
            result = await self.process_content(content, modality)
            # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
            output = ModalityOutput(modality=modality, embedding=self._get_deterministic_embedding(f'fuse_{modality.value}'), confidence=result.get('confidence', 0.8))
            outputs.append(output)
            modalities.append(modality)
        weights = {}
        total_weight = 0.0
        for output in outputs:
            base_weight = self.fusion_weights.get(output.modality, 1.0)
            weight = base_weight * output.confidence
            weights[output.modality] = weight
            total_weight += weight
        if total_weight > 0:
            weights = {k: v / total_weight for k, v in weights.items()}
        fused = np.zeros(self.embedding_dim, dtype=np.float32)
        for output in outputs:
            w = weights.get(output.modality, 0.0)
            if output.embedding is not None:
                fused += w * output.embedding
        fused_norm = np.linalg.norm(fused)
        if fused_norm > 0:
            fused = fused / fused_norm
        self._stats['fusions_performed'] += 1
        return {'success': True, 'fused_embedding_shape': fused.shape, 'modalities': [m.value for m in modalities], 'weights': {k.value: v for k, v in weights.items()}, 'summary': f'Fused {len(outputs)} modalities'}

    async def _process_text(self, content: str) -> ModalityOutput:
        """Process text content."""
        words = content.split()
        features = {'word_count': len(words), 'char_count': len(content), 'avg_word_length': sum(len(w) for w in words) / max(len(words), 1)}
        embedding = self._generate_text_embedding(content)
        return ModalityOutput(modality=ModalityType.TEXT, embedding=embedding, features=features, confidence=0.95)

    async def _process_image(self, content: Any) -> ModalityOutput:
        """Process image content using MLX if available."""
        features = {'size': 'unknown', 'format': 'unknown'}
        try:
            if isinstance(content, np.ndarray):
                features['size'] = f'{content.shape}'
                features['format'] = 'numpy_array'
                if self.mlx_encoder:
                    embedding = self.mlx_encoder.encode_vision(content)
                    confidence = 0.92
                else:
                    embedding = self._generate_image_embedding_fallback(content)
                    confidence = 0.85
            elif isinstance(content, str):
                features['format'] = 'path'
                # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
                embedding = self._get_deterministic_embedding(f'image_path_{content}', scale=0.1)
                # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
                fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'path_not_processable'}
                return ModalityOutput(
                    modality=ModalityType.IMAGE, embedding=embedding, features=features,
                    metadata=fallback_metadata, confidence=0.8,
                )
            else:
                # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
                embedding = self._get_deterministic_embedding('image_unknown', scale=0.1)
                # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
                fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'unknown_format'}
                return ModalityOutput(
                    modality=ModalityType.IMAGE, embedding=embedding, features=features,
                    metadata=fallback_metadata, confidence=0.75,
                )
        except Exception as e:
            logger.warning(f'Image processing failed: {e}')
            # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
            embedding = self._get_deterministic_embedding('image_error', scale=0.1)
            # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
            fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'processing_error'}
            return ModalityOutput(
                modality=ModalityType.IMAGE, embedding=embedding, features=features,
                metadata=fallback_metadata, confidence=0.7,
            )
        return ModalityOutput(modality=ModalityType.IMAGE, embedding=embedding, features=features, confidence=confidence)

    def _generate_image_embedding_fallback(self, image: Any) -> Any:
        """Generate image embedding using numpy fallback."""
        if not HAS_NUMPY:
            raise ImportError("numpy required for image embedding — pip install 'hledac[dev]'")
        features = []
        if image.ndim == 3:
            for i in range(min(3, image.shape[2])):
                hist = np.histogram(image[..., i], bins=16, range=(0, 255))[0]
                features.extend(hist / (hist.sum() + 1e-08))
        embedding = np.zeros(self.embedding_dim)
        feature_array = np.array(features[:self.embedding_dim])
        embedding[:len(feature_array)] = feature_array
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding.astype(np.float32)

    async def _process_audio(self, content: Any) -> ModalityOutput:
        """
        [SILICON-07] Process audio content via canonical MediaIocPipeline.

        Accepts:
          - str: file path → MediaIocPipeline.process_audio()
            (AVFoundation decode → SFSpeechRecognizer ANE transcription →
             IocStreamScanner Rust Aho-Corasick SIMD IOC extraction)
          - np.ndarray: raw PCM samples → MLX encoder or numpy fallback

        Pipeline runs on dedicated silicon: VideoToolbox HW decode, ANE transcription,
        NEON SIMD IOC scan — zero CPU/GPU bandwidth stolen from MLX.

        Returns ModalityOutput with embedding, transcript features, and extracted IoCs.
        Fail-safe: returns random embedding on any error — never blocks the sprint.
        """
        features: dict[str, Any] = {
            'duration': 'unknown',
            'sample_rate': 0,
            'transcribed': False,
            'iocs': [],
            'ioc_count': 0,
            'ioc_scanner': '',
        }
        try:
            # ── String file path → canonical MediaIocPipeline ─────────────────
            if isinstance(content, str):
                from hledac.universal.multimodal import is_audio_file

                if is_audio_file(content):
                    from hledac.universal.multimodal import get_pipeline

                    pipeline = await get_pipeline(self._governor if hasattr(self, '_governor') else None)
                    result = await pipeline.process_audio(content)

                    # Map MediaIocResult → features dict
                    features['duration'] = result.duration_s
                    features['sample_rate'] = 16000
                    features['transcribed'] = bool(result.transcript)
                    features['transcript_preview'] = result.transcript[:500] if result.transcript else ''
                    features['confidence'] = result.transcript_confidence
                    features['segments'] = result.segments
                    features['iocs'] = result.iocs
                    features['ioc_count'] = result.ioc_count
                    features['ioc_scanner'] = result.ioc_scanner
                    # Performance metrics from the pipeline
                    features['decode_time_ms'] = result.decode_time_ms
                    features['ioc_scan_time_ms'] = result.ioc_scan_time_ms
                    features['total_time_ms'] = result.total_time_ms

                    if result.error:
                        logger.debug('[SILICON-07] Audio pipeline: %s', result.error)

                    # Generate embedding from transcript text for cross-modal fusion
                    text_for_embedding = result.transcript if result.transcript else ''
                    if text_for_embedding:
                        embedding = self._generate_text_embedding(text_for_embedding)
                        confidence = max(result.transcript_confidence, 0.75)
                    else:
                        # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
                        embedding = self._get_deterministic_embedding('audio_no_transcript', scale=0.1)
                        confidence = 0.65

                    return ModalityOutput(
                        modality=ModalityType.AUDIO,
                        embedding=embedding,
                        features=features,
                        confidence=confidence,
                    )

            # ── Raw numpy PCM samples → MLX encoder or numpy fallback ──────────
            if isinstance(content, np.ndarray):
                features['duration'] = len(content)
                features['sample_rate'] = 16000
                if self.mlx_encoder:
                    embedding = self.mlx_encoder.encode_audio(content)
                    confidence = 0.88
                else:
                    embedding = self._generate_audio_embedding_fallback(content)
                    confidence = 0.78
            else:
                # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
                embedding = self._get_deterministic_embedding('audio_non_array', scale=0.1)
                # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
                fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'non_array_input'}
                return ModalityOutput(
                    modality=ModalityType.AUDIO,
                    embedding=embedding,
                    features=features,
                    metadata=fallback_metadata,
                    confidence=0.7,
                )
        except Exception as e:
            logger.warning(f'Audio processing failed: {e}')
            # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
            embedding = self._get_deterministic_embedding('audio_error', scale=0.1)
            # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
            fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'processing_error'}
            return ModalityOutput(
                modality=ModalityType.AUDIO,
                embedding=embedding,
                features=features,
                metadata=fallback_metadata,
                confidence=0.7,
            )
        return ModalityOutput(
            modality=ModalityType.AUDIO,
            embedding=embedding,
            features=features,
            confidence=confidence,
        )

    async def _process_video(self, content: Any) -> ModalityOutput:
        """
        [SILICON-07] Process video content via canonical MediaIocPipeline.

        Accepts:
          - str: file path → MediaIocPipeline.process_video()
            (AVFoundation audio track → PCM decode → SFSpeechRecognizer ANE transcription
             → AVAssetImageGenerator keyframes → Vision ANE OCR
             → combined text → IocStreamScanner Rust Aho-Corasick SIMD IOC extraction)

        Pipeline:
          1. AVAssetReader → audio track as PCM float32 (VideoToolbox HW)
          2. SFSpeechRecognizer → text from audio (ANE)
          3. AVAssetImageGenerator → keyframes at 10s intervals
          4. Vision VNRecognizeTextRequest → OCR on keyframes (ANE)
          5. Combined text → IocStreamScanner.scan_bytes() → IoCs (NEON SIMD)
          6. Combined text → embedding for cross-modal fusion

        Returns ModalityOutput with embedding, combined features, and extracted IoCs.
        Fail-safe: returns ModalityOutput with random embedding on any error.
        """
        features: dict[str, Any] = {
            'duration': 'unknown',
            'transcribed': False,
            'frames_ocr': 0,
            'combined_text_len': 0,
            'iocs': [],
            'ioc_count': 0,
            'ioc_scanner': '',
        }
        try:
            if isinstance(content, str):
                from hledac.universal.multimodal import is_video_file

                if is_video_file(content):
                    from hledac.universal.multimodal import get_pipeline

                    pipeline = await get_pipeline(self._governor if hasattr(self, '_governor') else None)
                    result = await pipeline.process_video(content)

                    # Map MediaIocResult → features dict
                    features['duration'] = result.duration_s
                    features['transcribed'] = bool(result.transcript)
                    features['transcript_preview'] = result.transcript[:500] if result.transcript else ''
                    features['audio_confidence'] = result.transcript_confidence
                    features['frames_ocr'] = result.frame_count
                    features['frame_texts'] = result.frame_texts
                    features['frame_timestamps'] = result.frame_timestamps
                    features['combined_text_len'] = len(result.all_text)
                    features['iocs'] = result.iocs
                    features['ioc_count'] = result.ioc_count
                    features['ioc_scanner'] = result.ioc_scanner
                    # Performance metrics
                    features['decode_time_ms'] = result.decode_time_ms
                    features['ioc_scan_time_ms'] = result.ioc_scan_time_ms
                    features['total_time_ms'] = result.total_time_ms

                    if result.error:
                        logger.debug('[SILICON-07] Video pipeline: %s', result.error)

                    # Generate embedding from combined text for cross-modal fusion
                    text_for_embedding = result.all_text if result.all_text else ''
                    if text_for_embedding:
                        embedding = self._generate_text_embedding(text_for_embedding)
                        confidence = max(result.transcript_confidence, 0.6)
                    else:
                        # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
                        embedding = self._get_deterministic_embedding('video_no_text', scale=0.1)
                        # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
                        fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'no_text_content'}
                        return ModalityOutput(
                            modality=ModalityType.VIDEO,
                            embedding=embedding,
                            features=features,
                            metadata=fallback_metadata,
                            confidence=0.5,
                        )

                    return ModalityOutput(
                        modality=ModalityType.VIDEO,
                        embedding=embedding,
                        features=features,
                        confidence=confidence,
                    )

            # Fallback: no valid video content
            # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
            embedding = self._get_deterministic_embedding('video_no_content', scale=0.1)
            # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
            fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'no_valid_video'}
            return ModalityOutput(
                modality=ModalityType.VIDEO,
                embedding=embedding,
                features=features,
                metadata=fallback_metadata,
                confidence=0.65,
            )
        except Exception as e:
            logger.warning(f'Video processing failed: {e}')
            # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
            embedding = self._get_deterministic_embedding('video_error', scale=0.1)
            # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
            fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'processing_error'}
            return ModalityOutput(
                modality=ModalityType.VIDEO,
                embedding=embedding,
                features=features,
                metadata=fallback_metadata,
                confidence=0.6,
            )
        return ModalityOutput(
            modality=ModalityType.VIDEO,
            embedding=embedding,
            features=features,
            confidence=confidence,
        )

    def _generate_audio_embedding_fallback(self, audio: Any) -> Any:
        """Generate audio embedding using numpy fallback."""
        if not HAS_NUMPY:
            raise ImportError("numpy required for audio embedding — pip install 'hledac[dev]'")
        features = [np.mean(np.abs(audio)), np.std(audio), np.max(np.abs(audio)), np.mean(audio ** 2)]
        if audio:
            fft = np.abs(np.fft.fft(audio[:min(len(audio), 1024)]))
            features.extend(fft[:20])
        # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
        embedding = self._get_deterministic_embedding('audio_features', scale=0.05)
        embedding[:len(features)] = np.array(features[:self.embedding_dim])
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    async def _process_document(self, content: Any) -> ModalityOutput:
        """Process document content (placeholder)."""
        # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
        # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
        fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'placeholder_implementation'}
        return ModalityOutput(
            modality=ModalityType.DOCUMENT,
            embedding=self._get_deterministic_embedding('document', scale=0.1),
            features={'pages': 0, 'type': 'document'},
            metadata=fallback_metadata,
            confidence=0.9,
        )

    async def _process_chart(self, content: Any) -> ModalityOutput:
        """Process chart content (placeholder)."""
        # IO-4 fix: Use deterministic fallback to avoid poisoning LanceDB ANN index
        # F6 FIX: Mark hash-fallback embeddings so consumers can filter from ANN
        fallback_metadata = {'is_hash_fallback': True, 'fallback_reason': 'placeholder_implementation'}
        return ModalityOutput(
            modality=ModalityType.CHART,
            embedding=self._get_deterministic_embedding('chart', scale=0.1),
            features={'type': 'chart', 'data_points': 0},
            metadata=fallback_metadata,
            confidence=0.7,
        )

    def _generate_text_embedding(self, text: str) -> Any:
        """Generate text embedding using MLX if available, fast hash fallback otherwise."""
        if self.mlx_encoder:
            try:
                return self.mlx_encoder.encode_text(text)
            except Exception as e:
                logger.warning(f'MLX text encoding failed: {e}, using fallback')
        if not HAS_NUMPY:
            raise ImportError("numpy required for text embedding — pip install 'hledac[dev]'")
        # Fast hash-based bag-of-words — O(|words|) via builtin hash, no crypto
        embedding = np.zeros(self.embedding_dim, dtype=np.float32)
        for word in set(text.lower().split()):
            embedding[hash(word) % self.embedding_dim] += 1.0
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    async def align_vision_text(self, texts: list[str], images: list[Any]) -> dict[str, Any]:
        """
        Align vision and text using contrastive learning.

        Args:
            texts: List of text descriptions
            images: List of images

        Returns:
            Alignment results with similarity matrix
        """
        if len(texts) != len(images):
            raise ValueError('Number of texts and images must match')
        text_embeddings = np.array([self._generate_text_embedding(t) for t in texts])
        image_embeddings = []
        for img in images:
            if self.mlx_encoder:
                emb = self.mlx_encoder.encode_vision(img)
            else:
                emb = self._generate_image_embedding_fallback(img)
            image_embeddings.append(emb)
        image_embeddings = np.array(image_embeddings)
        loss = self.contrastive_learner.compute_contrastive_loss(text_embeddings, image_embeddings)
        matches = self.contrastive_learner.find_best_matches(text_embeddings, image_embeddings)
        self._stats['contrastive_alignments'] += 1
        return {'success': True, 'loss': loss, 'matches': matches, 'text_embeddings_shape': text_embeddings.shape, 'image_embeddings_shape': image_embeddings.shape}

    def get_statistics(self) -> dict[str, Any]:
        """Get multimodal processing statistics."""
        return {**self._stats, 'processed_by_modality': {k.value: v for k, v in self._stats['processed_by_modality'].items()}, 'embedding_dimension': self.embedding_dim}

    def _get_feature_list(self) -> list[str]:
        return ['Automatic modality detection', 'Cross-modal fusion', 'Text embedding generation', 'Memory-efficient processing', 'Modality-specific pipelines', 'Weighted fusion']
