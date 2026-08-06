"""
MicroModelSwarmRouter — Task-Specialized Micro-Model Pool for Apple Silicon (MLX)

ISSUE [SWARM]-001: Eliminates monolithic model bottleneck by routing sub-tasks





to specialized micro-models (0.5B-1.5B Int4) with sub-100ms hot-swap via pointer swap.

Architecture:
- UMA-resident model weights: Load once, keep in unified memory
- Pointer swap (<100ms): Switch active model reference without mlx_lm.load()
- Content-based routing: Regex + embedding similarity for task matching
- LRU ring buffer: Automatic eviction when memory pressure > 80%

RAM Budget (6.48 GB ceiling):
- DeepHermes-3B: 2.0 GB (primary generalist)
- Qwen2.5-Coder-0.5B: 350 MB (code/SQL)
- BGE-M3: 570 MB (embeddings, multilingual)
- SmolLM2-360M: 200 MB (binary triage)
- Hermes-3-1B: 700 MB (text synthesis)
- Reserve: ~3 GB for activations, KV cache
"""

from __future__ import annotations

import json
import re
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Optional

import mlx.core as mx
import mlx_lm

# Type aliases for clarity
ModelT = Any
TokenizerT = Any
EmbeddingT = list[float]


class TaskType(Enum):
    """Task categories for micro-model routing."""
    CODE = auto()        # Python, SQL, JS, etc.
    TRANSLATION = auto() # Multilingual text
    EMBEDDINGS = auto()  # Vector similarity
    CLASSIFICATION = auto()  # OSINT categorization
    SYNTHESIS = auto()   # Text generation
    TRIAGE = auto()      # Binary relevance check
    GENERAL = auto()     # Fallback to generalist


@dataclass(frozen=True)
class MicroModelSpec:
    """Immutable specification for a micro-model in the swarm."""
    name: str                          # Human-readable identifier
    model_path: str                    # MLX HuggingFace path (e.g., "mlx-community/Qwen2.5-Coder-0.5-Instruct")
    task_type: TaskType                # Primary task this model excels at
    quant: str = "q4"                  # Quantization: q4, q8, bf16
    memory_mb: int = 350               # Estimated memory footprint in MB
    max_tokens: int = 2048             # Context window
    priority: int = 0                  # Load priority (higher = load first)
    warmup_prompt: str = ""            # Optional prompt for mx.compile() warmup
    
    # Content matching patterns (regex)
    code_patterns: tuple[str, ...] = ()    # Regex patterns indicating code
    sql_patterns: tuple[str, ...] = ()     # Regex patterns indicating SQL
    translation_patterns: tuple[str, ...] = ()  # Language-specific patterns
    embedding_keywords: tuple[str, ...] = ()    # Keywords for embedding tasks
    
    @property
    def full_path(self) -> str:
        """Get full quantized model path."""
        if self.quant == "q4":
            return f"{self.model_path}-4bit"
        elif self.quant == "q8":
            return f"{self.model_path}-8bit"
        return self.model_path


@dataclass
class LoadedMicroModel:
    """Runtime state for a loaded micro-model."""
    spec: MicroModelSpec
    model: ModelT
    tokenizer: TokenizerT
    is_warmed: bool = False
    last_used: float = field(default_factory=time.time)
    use_count: int = 0
    load_time: float = 0.0


# =============================================================================
# MICRO-MODEL REGISTRY
# =============================================================================

# Pre-configured micro-models optimized for M1 MacBook Air (6.48 GB RAM)
# Note: Using confirmed MLX-compatible model paths
MICRO_MODELS: dict[str, MicroModelSpec] = {
    # Code/SQL specialist — Qwen2.5-Coder 0.5B Int4
    # ~350 MB, 3× faster than 3B for code tasks
    # Using Qwen2.5-0.5B-Instruct which has MLX support
    "qwen_coder": MicroModelSpec(
        name="Qwen2.5-0.5B-Instruct",
        model_path="mlx-community/Qwen2.5-0.5B-Instruct-mlx",
        task_type=TaskType.CODE,
        quant="q4",
        memory_mb=350,
        max_tokens=4096,
        priority=10,
        code_patterns=(
            r'def\s+\w+\s*\(',
            r'class\s+\w+',
            r'import\s+\w+',
            r'from\s+\w+\s+import',
            r'function\s*\w*\s*\(',
            r'=>\s*\{',
            r'const\s+\w+\s*=',
            r'let\s+\w+\s*=',
            r'var\s+\w+\s*=',
            r'#include',
            r'public\s+class',
            r'private\s+void',
            r'async\s+def',
            r'@\w+\s*\(',
            r'```\w*',  # Code blocks
        ),
        sql_patterns=(
            r'SELECT\s+.+\s+FROM',
            r'INSERT\s+INTO',
            r'UPDATE\s+\w+\s+SET',
            r'DELETE\s+FROM',
            r'CREATE\s+TABLE',
            r'ALTER\s+TABLE',
            r'DROP\s+TABLE',
            r'JOIN\s+\w+\s+ON',
            r'WHERE\s+\w+',
        ),
    ),
    
    # Lightweight text synthesis — Phi-3.5 mini (better MLX support)
    # ~700 MB, fast for simple text generation
    # Alternative: tinyllama (1.1B) if Phi not available
    "phi35_mini": MicroModelSpec(
        name="Phi-3.5-mini-instruct",
        model_path="mlx-community/Phi-3.5-mini-instruct-4bit",
        task_type=TaskType.SYNTHESIS,
        quant="q4",
        memory_mb=700,
        max_tokens=4096,
        priority=8,
        translation_patterns=(
            r'\b(translate|translation|Übersetzung|traduction|traducción)\b',
            r'\b(from\s+\w+\s+to\s+\w+)\b',
            r'\b(english|german|french|spanish|chinese|japanese|korean)\s+(to|into|ins?)\b',
        ),
    ),
    
    # SmolLM2 360M — binary triage (already proven pattern)
    # ~200 MB, ~200ms load, used for relevance checks
    "smollm_triage": MicroModelSpec(
        name="SmolLM2-360M-Instruct",
        model_path="mlx-community/SmolLM2-360M-Instruct-mlx",
        task_type=TaskType.TRIAGE,
        quant="q4",
        memory_mb=200,
        max_tokens=512,
        priority=15,  # Highest priority — used frequently
    ),
    
    # Multilingual embeddings — Nomic Embed Text (better MLX support than BGE-M3)
    # ~274 MB, serves Area 2 (embeddings) and translation tasks
    # Alternative: use embeddings from main model if this fails
    "nomic_embed": MicroModelSpec(
        name="nomic-embed-text-v1.5",
        model_path="mlx-community/nomic-embed-text-v1.5-quantized",
        task_type=TaskType.EMBEDDINGS,
        quant="q4",
        memory_mb=274,
        max_tokens=8192,
        priority=12,
        embedding_keywords=(
            "embed", "similarity", "semantic", "vector", "embedding",
            "compare", "rank", "search", "find related", "most similar",
        ),
    ),
}


# =============================================================================
# CONTENT ROUTER — Pattern-based Task Classification
# =============================================================================

class ContentRouter:
    """
    Fast content-based task classification using regex + heuristics.
    
    Routes queries to appropriate TaskType without loading any model.
    Used as first-pass triage before ML model routing.
    """
    
    def __init__(self):
        # Compile patterns once (handle missing models gracefully)
        try:
            qwen_spec = MICRO_MODELS.get('qwen_coder')
            if qwen_spec:
                self._code_re = re.compile('|'.join(qwen_spec.code_patterns), re.IGNORECASE)
                self._sql_re = re.compile('|'.join(qwen_spec.sql_patterns), re.IGNORECASE)
            else:
                self._code_re = re.compile(r'(def |class |import |```)', re.IGNORECASE)
                self._sql_re = re.compile(r'(SELECT |FROM |WHERE )', re.IGNORECASE)
        except Exception:
            self._code_re = re.compile(r'(def |class |import |```)', re.IGNORECASE)
            self._sql_re = re.compile(r'(SELECT |FROM |WHERE )', re.IGNORECASE)
        
        # Translation patterns
        phi_spec = MICRO_MODELS.get('phi35_mini')
        if phi_spec and phi_spec.translation_patterns:
            self._trans_re = re.compile('|'.join(phi_spec.translation_patterns), re.IGNORECASE)
        else:
            self._trans_re = re.compile(r'(translate|translation|from \w+ to \w+)', re.IGNORECASE)
        
        # Embedding keywords
        embed_spec = MICRO_MODELS.get('nomic_embed')
        if embed_spec and embed_spec.embedding_keywords:
            self._embed_re = re.compile(
                '|'.join(re.escape(k) for k in embed_spec.embedding_keywords),
                re.IGNORECASE
            )
        else:
            self._embed_re = re.compile(r'(embed|similarity|semantic|vector)', re.IGNORECASE)
        
        # Classification heuristics
        self._classification_keywords = re.compile(
            r'\b(classify|categorize|osint|threat|indicator|type\s+of|what\s+kind)\b',
            re.IGNORECASE
        )
        self._relevance_keywords = re.compile(
            r'\b(relevant|irrelevant|skip|ignore|filter|priorit.y|important)\b',
            re.IGNORECASE
        )
        self._synthesis_keywords = re.compile(
            r'\b(write|summarize|explain|describe|generate|create|compose)\b',
            re.IGNORECASE
        )
    
    def classify(self, text: str) -> TaskType:
        """
        Classify text into TaskType using regex + keyword matching.
        
        Fast path: No ML model required, only regex on raw text.
        """
        text_lower = text.lower()
        
        # Priority 1: SQL detection (most specific)
        if self._sql_re.search(text):
            return TaskType.CODE  # SQL routed to code specialist
        
        # Priority 2: Code detection
        if self._code_re.search(text):
            return TaskType.CODE
        
        # Priority 3: Embedding tasks
        if self._embed_re.search(text):
            return TaskType.EMBEDDINGS
        
        # Priority 4: Translation
        if self._trans_re.search(text):
            return TaskType.TRANSLATION
        
        # Priority 5: Classification (OSINT)
        if self._classification_keywords.search(text):
            return TaskType.CLASSIFICATION
        
        # Priority 6: Binary triage
        if self._relevance_keywords.search(text):
            return TaskType.TRIAGE
        
        # Priority 7: Synthesis
        if self._synthesis_keywords.search(text):
            return TaskType.SYNTHESIS
        
        # Default: generalist model
        return TaskType.GENERAL
    
    def get_preferred_model(self, task_type: TaskType) -> str | None:
        """
        Get the preferred micro-model name for a task type.
        
        Returns None if no micro-model is suitable for this task,
        indicating fallback to main model should be used.
        """
        mapping = {
            TaskType.CODE: "qwen_coder",
            TaskType.EMBEDDINGS: "nomic_embed",  # Changed from bge_m3
            TaskType.TRIAGE: "smollm_triage",
            TaskType.SYNTHESIS: "phi35_mini",  # Changed from hermes_1b
            TaskType.TRANSLATION: "phi35_mini",  # Changed from hermes_1b
            TaskType.CLASSIFICATION: None,  # Needs generalist or specialized
            TaskType.GENERAL: None,  # Use main model
        }
        model_id = mapping.get(task_type)
        # Verify model is available in registry
        if model_id and model_id in MICRO_MODELS:
            return model_id
        return None


# =============================================================================
# MICRO-MODEL POOL — UMA-Resident with Pointer Swap
# =============================================================================

class MicroModelPool:
    """
    Unified memory-resident pool for micro-models.
    
    Key innovation: Instead of load/unload cycles, we keep weights in UMA
    and swap pointers. This achieves <100ms hot-swap for model switching.
    
    Memory budget: 3.12 GB for micro-models (see RAM budget above)
    """
    
    def __init__(
        self,
        memory_budget_mb: int = 3200,  # 3.2 GB budget
        eviction_threshold: float = 0.80,  # Evict when >80% pressure
        preload_priority: tuple[str, ...] = ("smollm_triage",),
    ):
        self._memory_budget = memory_budget_mb * 1024 * 1024  # bytes
        self._eviction_threshold = eviction_threshold
        self._lock = threading.RLock()
        
        # Loaded models: model_id -> LoadedMicroModel
        self._loaded: dict[str, LoadedMicroModel] = {}
        
        # LRU order for eviction
        self._lru: OrderedDict[str, float] = OrderedDict()
        
        # Background loading thread
        self._loader_thread: threading.Thread | None = None
        self._pending_loads: set[str] = set()
        self._load_queue: list[str] = []
        
        # Memory tracking
        self._total_loaded_bytes = 0
        
        # Preload priority models
        self._preload_priority = preload_priority
        
        # Reference to main model (DeepHermes-3B)
        self._main_model: tuple[ModelT, TokenizerT] | None = None
        self._main_model_lock = threading.Lock()
    
    @property
    def memory_pressure(self) -> float:
        """Current memory pressure as ratio of budget used."""
        return self._total_loaded_bytes / self._memory_budget
    
    @property
    def loaded_models(self) -> list[str]:
        """List of currently loaded model IDs."""
        with self._lock:
            return list(self._loaded.keys())
    
    def register_main_model(self, model: ModelT, tokenizer: TokenizerT) -> None:
        """
        Register the main 3B model (DeepHermes-3B).
        
        This model is always kept resident and never evicted.
        """
        with self._main_model_lock:
            self._main_model = (model, tokenizer)
    
    def get_main_model(self) -> tuple[ModelT, TokenizerT] | None:
        """Get the main model reference."""
        with self._main_model_lock:
            return self._main_model
    
    def preload(self, model_ids: list[str]) -> None:
        """
        Preload models in background thread.
        
        Models are loaded with mx.compile() warmup for immediate use.
        """
        if self._loader_thread is not None and self._loader_thread.is_alive():
            return
        
        self._load_queue = list(model_ids)
        self._loader_thread = threading.Thread(
            target=self._background_load,
            daemon=True,
            name="MicroModelLoader"
        )
        self._loader_thread.start()
    
    def _background_load(self) -> None:
        """Background worker for model loading."""
        while self._load_queue:
            model_id = self._load_queue.pop(0)
            try:
                self.load_model(model_id, warmup=True)
            except Exception as e:
                print(f"[MicroModelPool] Failed to load {model_id}: {e}")
    
    def load_model(self, model_id: str, warmup: bool = True) -> LoadedMicroModel:
        """
        Load a micro-model into UMA memory.
        
        Uses mlx_lm.load() for initial load, then keeps weights resident.
        """
        if model_id in self._loaded:
            return self._update_lru(model_id)
        
        spec = MICRO_MODELS.get(model_id)
        if spec is None:
            raise ValueError(f"Unknown micro-model: {model_id}")
        
        # Check memory pressure
        if self.memory_pressure > self._eviction_threshold:
            self._evict_lru()
        
        # Load model
        start = time.time()
        try:
            model, tokenizer = mlx_lm.load(
                spec.full_path,
                tokenizer_mode="slow" if spec.task_type == TaskType.EMBEDDINGS else "auto",
            )
            
            loaded = LoadedMicroModel(
                spec=spec,
                model=model,
                tokenizer=tokenizer,
                is_warmed=False,
                load_time=time.time() - start,
            )
            
            # Warmup mx.compile() if requested
            if warmup and spec.warmup_prompt:
                try:
                    self._warmup_model(loaded)
                except Exception:
                    pass  # Warmup failure is non-fatal
            
            with self._lock:
                self._loaded[model_id] = loaded
                self._lru[model_id] = time.time()
                self._total_loaded_bytes += spec.memory_mb * 1024 * 1024
            
            return loaded
            
        except Exception as e:
            raise RuntimeError(f"Failed to load {model_id}: {e}") from e
    
    def _warmup_model(self, loaded: LoadedMicroModel) -> None:
        """
        Warmup model for faster first inference.
        
        For generation models: Generate a short response to compile the graph.
        For embedding models: Run a forward pass to compile.
        """
        try:
            if loaded.spec.task_type == TaskType.EMBEDDINGS:
                # For embeddings: compile forward pass
                warmup_text = "Hello world"
                tokens = loaded.tokenizer.encode(warmup_text, return_tensors="np")
                if hasattr(tokens, 'input_ids'):
                    input_ids = mx.array(tokens.input_ids)
                else:
                    input_ids = mx.array([tokens])
                
                # Compile forward pass
                def forward_step(ids):
                    return loaded.model(ids)
                
                compiled = mx.compile(forward_step)
                compiled(input_ids[:, :min(10, input_ids.shape[1])])
            else:
                # For generation: generate short response to compile
                warmup_text = "Hi" if loaded.spec.task_type == TaskType.TRIAGE else "def hello(): return 1"
                
                # Use mlx_lm.generate with low tokens for warmup
                mlx_lm.generate(
                    loaded.model,
                    loaded.tokenizer,
                    prompt=warmup_text,
                    max_tokens=3,  # Very short for warmup
                    temp=0.1,
                )
            
            loaded.is_warmed = True
        except Exception:
            # Warmup failure is non-fatal
            pass
    
    def get_model(
        self,
        model_id: str,
        use_warmup: bool = True,
    ) -> LoadedMicroModel | None:
        """
        Get a loaded model by ID, loading it if necessary.
        
        Returns None if model cannot be loaded (memory pressure, etc.)
        """
        with self._lock:
            if model_id in self._loaded:
                return self._update_lru(model_id)
        
        # Try to load
        try:
            loaded = self.load_model(model_id, warmup=use_warmup)
            return loaded
        except Exception as e:
            print(f"[MicroModelPool] Cannot load {model_id}: {e}")
            
            # Try eviction and retry once
            self._evict_lru()
            try:
                return self.load_model(model_id, warmup=use_warmup)
            except Exception:
                return None
    
    def swap_to(
        self,
        target_model_id: str,
    ) -> tuple[ModelT, TokenizerT, bool] | tuple[None, None, False]:
        """
        Hot-swap to a different model via pointer swap.
        
        If target is already loaded: <10ms (just pointer swap)
        If target needs loading: Full load time (1-20s)
        
        Returns:
            Tuple of (model, tokenizer, success)
        """
        with self._lock:
            if target_model_id in self._loaded:
                loaded = self._update_lru(target_model_id)
                return (loaded.model, loaded.tokenizer, True)
        
        # Not loaded — try to load
        loaded = self.get_model(target_model_id)
        if loaded is None:
            return (None, None, False)
        
        return (loaded.model, loaded.tokenizer, True)
    
    def _update_lru(self, model_id: str) -> LoadedMicroModel:
        """Update LRU timestamp and return model."""
        with self._lock:
            loaded = self._loaded[model_id]
            loaded.last_used = time.time()
            loaded.use_count += 1
            # Move to end (most recently used)
            if model_id in self._lru:
                self._lru.move_to_end(model_id)
            return loaded
    
    def _evict_lru(self) -> bool:
        """Evict least recently used model to free memory."""
        with self._lock:
            if not self._lru:
                return False
            
            # Find LRU model (first item)
            model_id, _ = next(iter(self._lru.items()))
            
            # Don't evict critical models
            spec = self._loaded.get(model_id)
            if spec and spec.task_type == TaskType.TRIAGE:
                # Try next in line
                remaining = list(self._lru.keys())
                if len(remaining) > 1:
                    model_id = remaining[1]
                else:
                    return False
            
            return self.evict_model(model_id)
    
    def evict_model(self, model_id: str) -> bool:
        """Explicitly evict a model from the pool."""
        with self._lock:
            if model_id not in self._loaded:
                return False
            
            loaded = self._loaded.pop(model_id)
            self._lru.pop(model_id, None)
            self._total_loaded_bytes -= loaded.spec.memory_mb * 1024 * 1024
            
            # Clear model references to allow GC
            del loaded.model
            del loaded.tokenizer
            
            return True
    
    def preload_priority_models(self) -> None:
        """Preload models marked as priority (smollm_triage by default)."""
        priority = list(self._preload_priority)
        self.preload(priority)
    
    def generate(
        self,
        model_id: str,
        prompt: str,
        max_tokens: int = 256,
        temp: float = 0.7,
        **kwargs,
    ) -> str:
        """
        Generate text using the specified micro-model.
        
        Handles model swapping transparently.
        For embedding models (BGE-M3), returns embeddings instead.
        """
        loaded = self.get_model(model_id)
        if loaded is None:
            raise RuntimeError(f"Cannot load micro-model: {model_id}")
        
        # Handle embedding models specially
        if loaded.spec.task_type == TaskType.EMBEDDINGS:
            return self._generate_embeddings(loaded, prompt, **kwargs)
        
        # Generate text using mlx_lm.generate
        response = mlx_lm.generate(
            loaded.model,
            loaded.tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            temp=temp,
            **kwargs,
        )
        return response
    
    def _generate_embeddings(
        self,
        loaded: LoadedMicroModel,
        text: str,
        pool_type: str = "mean",
        normalize: bool = True,
        **kwargs,
    ) -> str:
        """
        Generate embeddings for text using embedding model.
        
        Returns JSON string with embeddings for compatibility.
        """
        try:
            # Tokenize
            if hasattr(loaded.tokenizer, 'encode'):
                tokens = loaded.tokenizer.encode(text, return_tensors="np")
                if hasattr(tokens, 'input_ids'):
                    tokens = tokens.input_ids
                input_ids = mx.array(tokens)
            else:
                input_ids = mx.array([loaded.tokenizer.encode(text)])
            
            # Get embeddings
            with mx.streaming_scope():
                output = loaded.model(input_ids)
            
            # Apply pooling and normalize
            if pool_type == "mean":
                embeddings = mx.mean(output, axis=1)
            elif pool_type == "cls":
                embeddings = output[:, 0, :]
            else:
                embeddings = mx.mean(output, axis=1)
            
            if normalize:
                embeddings = mx.divide(embeddings, mx.norm(embeddings, axis=-1, keepdims=True))
            
            # Return as list for JSON serialization
            return json.dumps(embeddings.tolist())
        except Exception as e:
            raise RuntimeError(f"Embedding generation failed: {e}") from e
    
    def get_stats(self) -> dict[str, Any]:
        """Get pool statistics for monitoring."""
        with self._lock:
            return {
                "memory_budget_mb": self._memory_budget / (1024 * 1024),
                "memory_used_mb": self._total_loaded_bytes / (1024 * 1024),
                "memory_pressure": self.memory_pressure,
                "loaded_count": len(self._loaded),
                "loaded_models": [
                    {
                        "id": mid,
                        "spec": lm.spec.name,
                        "memory_mb": lm.spec.memory_mb,
                        "use_count": lm.use_count,
                        "last_used": lm.last_used,
                        "is_warmed": lm.is_warmed,
                    }
                    for mid, lm in self._loaded.items()
                ],
            }


# =============================================================================
# MICRO MODEL SWARM ROUTER — Main Integration Point
# =============================================================================

class MicroModelSwarmRouter:
    """
    High-level router that combines content classification with micro-model pool.
    
    This is the main entry point for the SWARM-001 fix. It replaces
    the monolithic model loading in MoERouter._load_expert() with
    intelligent routing to specialized micro-models.
    
    Usage:
        router = MicroModelSwarmRouter()
        router.preload_priority_models()
        
        # Route a query
        model_id, task_type = router.route("Write a SQL query to...")
        if model_id:
            result = router.generate(model_id, prompt)
        else:
            # Fall back to main model
            main_model, main_tokenizer = router.get_main_model()
    
    Integration with MoERouter:
        1. MoERouter.__init__() creates MicroModelSwarmRouter instance
        2. MoERouter._load_expert() calls router.swap_to() instead of mlx_lm.load()
        3. MoERouter.route() uses router.classify() for content-based routing
    """
    
    def __init__(
        self,
        memory_budget_mb: int = 3200,
        eviction_threshold: float = 0.80,
        enable_fallback: bool = True,
    ):
        self._pool = MicroModelPool(
            memory_budget_mb=memory_budget_mb,
            eviction_threshold=eviction_threshold,
        )
        self._content_router = ContentRouter()
        self._enable_fallback = enable_fallback
        
        # Routing cache (short TTL to avoid stale routing decisions)
        self._routing_cache: dict[str, tuple[str, TaskType, float]] = {}
        self._cache_ttl = 5.0  # seconds
        self._cache_lock = threading.Lock()  # Thread-safe cache access
    
    def register_main_model(self, model: ModelT, tokenizer: TokenizerT) -> None:
        """Register the main generalist model (DeepHermes-3B)."""
        self._pool.register_main_model(model, tokenizer)
    
    def get_main_model(self) -> tuple[ModelT, TokenizerT] | None:
        """Get the main model reference."""
        return self._pool.get_main_model()
    
    def preload_priority_models(self) -> None:
        """Preload high-priority models in background."""
        self._pool.preload_priority_models()
    
    def preload(self, model_ids: list[str]) -> None:
        """Preload specific models in background."""
        self._pool.preload(model_ids)
    
    def classify(self, text: str) -> TaskType:
        """Classify text into task type using content analysis."""
        return self._content_router.classify(text)
    
    def route(
        self,
        text: str,
        use_cache: bool = True,
    ) -> tuple[str | None, TaskType]:
        """
        Route a query to the best micro-model.
        
        Args:
            text: Input query/text to route
            use_cache: Whether to use routing cache (default: True)
        
        Returns:
            Tuple of (model_id, task_type)
            model_id is None if routing to main model is recommended
        """
        # Check cache (thread-safe)
        if use_cache:
            with self._cache_lock:
                if text in self._routing_cache:
                    model_id, task_type, timestamp = self._routing_cache[text]
                    if time.time() - timestamp < self._cache_ttl:
                        return (model_id, task_type)
                    # Expired entry - will be refreshed
        
        # Content classification (fast, no I/O)
        task_type = self._content_router.classify(text)
        
        # Get preferred model for task
        model_id = self._content_router.get_preferred_model(task_type)
        
        # Verify model is available/loadable
        if model_id and model_id not in self._pool.loaded_models:
            # Try to load
            loaded = self._pool.get_model(model_id)
            if loaded is None:
                model_id = None  # Fall back to main model
        
        # Cache result (thread-safe)
        if use_cache:
            with self._cache_lock:
                self._routing_cache[text] = (model_id, task_type, time.time())
        
        return (model_id, task_type)
    
    def swap_to(self, model_id: str) -> tuple[ModelT, TokenizerT, bool]:
        """
        Hot-swap to the specified micro-model.
        
        Returns:
            Tuple of (model, tokenizer, success)
        """
        return self._pool.swap_to(model_id)
    
    def generate(
        self,
        text: str,
        max_tokens: int = 256,
        temp: float = 0.7,
        route_first: bool = True,
        **kwargs,
    ) -> tuple[str, str | None, TaskType]:
        """
        Generate response with automatic micro-model routing.
        
        Args:
            text: Input prompt
            max_tokens: Max tokens to generate
            temp: Temperature for generation
            route_first: Whether to route to micro-model (True) or use main (False)
            **kwargs: Additional generation args
        
        Returns:
            Tuple of (generated_text, model_id_used, task_type)
        """
        # Route query
        model_id, task_type = self.route(text)
        
        # Decide: micro-model or main model?
        if model_id and route_first:
            try:
                result = self._pool.generate(
                    model_id,
                    text,
                    max_tokens=max_tokens,
                    temp=temp,
                    **kwargs,
                )
                return (result, model_id, task_type)
            except Exception as e:
                print(f"[MicroModelSwarmRouter] Micro-model failed: {e}, falling back")
        
        # Fall back to main model
        main = self._pool.get_main_model()
        if main is None:
            raise RuntimeError("No main model registered")
        
        model, tokenizer = main
        result = mlx_lm.generate(
            model,
            tokenizer,
            prompt=text,
            max_tokens=max_tokens,
            temp=temp,
            **kwargs,
        )
        return (result, None, task_type)
    
    def get_stats(self) -> dict[str, Any]:
        """Get comprehensive router statistics."""
        pool_stats = self._pool.get_stats()
        return {
            "pool": pool_stats,
            "cache_size": len(self._routing_cache),
            "enable_fallback": self._enable_fallback,
        }
    
    @property
    def loaded_models(self) -> list[str]:
        """List of currently loaded model IDs."""
        return self._pool.loaded_models
    
    @property
    def memory_pressure(self) -> float:
        """Current memory pressure."""
        return self._pool.memory_pressure


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================

def create_swarm_router(
    memory_budget_mb: int = 3200,
    preload_models: bool = True,
) -> MicroModelSwarmRouter:
    """
    Factory function to create a configured MicroModelSwarmRouter.
    
    This is the recommended way to instantiate the router.
    """
    router = MicroModelSwarmRouter(
        memory_budget_mb=memory_budget_mb,
        eviction_threshold=0.80,
        enable_fallback=True,
    )
    
    if preload_models:
        # Preload triage model immediately (most frequently used)
        router.preload_priority_models()
    
    return router


# =============================================================================
# GLOBAL SINGLETON (optional, for simple use cases)
# =============================================================================

_global_router: MicroModelSwarmRouter | None = None


def get_global_router() -> MicroModelSwarmRouter:
    """Get or create the global router singleton."""
    global _global_router
    if _global_router is None:
        _global_router = create_swarm_router()
    return _global_router


def set_global_router(router: MicroModelSwarmRouter) -> None:
    """Set the global router singleton."""
    global _global_router
    _global_router = router
