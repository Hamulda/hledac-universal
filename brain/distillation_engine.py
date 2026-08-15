"""
Distillation Engine - MLX-based Reasoning Chain Quality Scoring

Tento modul implementuje distillation engine pro hodnocení kvality




reasoning chainů pomocí MLX MLP critic network. Optimalizováno pro
M1 MacBook Air 8GB s SQLite storage.

Example:
    >>> from hledac.universal.brain.distillation_engine import DistillationEngine, DistillationExample
    >>> engine = await create_distillation_engine()
    >>> example = DistillationExample(
    ...     query="What is the capital of France?",
    ...     chain=["Step 1: Identify the country", "Step 2: Recall capital"],
    ...     score=0.95
    ... )
    >>> await engine.add_example(example)
    >>> score = engine.score_chain(query, chain)
"""
import asyncio
import functools
import gc
import msgspec
import msgspec.json as _json
import logging
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer
import numpy as np
from hledac.universal.utils.asyncx import parallel
from hledac.universal.utils.mlx_cache import MLX_AVAILABLE, get_mx
logger = logging.getLogger(__name__)

# M1-OPT: Use shared domain executor instead of per-module TPE
# embed preset = 1 worker (MLX embed sync bridge)
from hledac.universal.utils.domain_executors import get_or_create
from core import aclose


def _get_embed_executor() -> ThreadPoolExecutor:
    """Return shared 'embed' domain executor for CPU-bound embedding extraction."""
    return get_or_create("embed")


# Lazy-loaded mlx.nn module (avoids importing MLX at module load time)
_mlx_nn_mod: Any = None  # type: ignore[assignment]


def _get_mlx_nn() -> Any:  # type: ignore[type-arg]
    """Lazily import mlx.nn, returning None if unavailable."""
    global _mlx_nn_mod
    if _mlx_nn_mod is None:
        try:
            import mlx.nn as _mlx_nn_mod  # type: ignore[assignment]
        except ImportError:
            _mlx_nn_mod = None
    return _mlx_nn_mod


_MLX_NN_AVAILABLE: bool = _get_mlx_nn() is not None

class DistillationExample(msgspec.Struct, gc=False):
    """
    Dataclass pro training example pro distillation.

    Attributes:
        query: Vstupní dotaz
        chain: Seznam reasoning kroků
        score: Kvalita chainu (0-1)
        metadata: Volitelná metadata
        timestamp: Čas vytvoření (unix timestamp)
    """
    query: str
    chain: list[str]
    score: float
    metadata: dict[str, Any] | None = None
    timestamp: float | None = None

    def __post_init__(self) -> None:
        """Post-init validace a default hodnoty."""
        if self.metadata is None:
            self.metadata = {}
        if self.timestamp is None:
            self.timestamp = time.time()
        self.score = max(0.0, min(1.0, float(self.score)))

    def to_dict(self) -> dict[str, Any]:
        """Konvertovat na slovník."""
        return {'query': self.query, 'chain': self.chain, 'score': self.score, 'metadata': self.metadata, 'timestamp': self.timestamp}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DistillationExample:
        """Vytvořit z slovníku."""
        return cls(query=data['query'], chain=data['chain'], score=data['score'], metadata=data.get('metadata', {}), timestamp=data.get('timestamp', time.time()))

class _CriticMLPBase:
    """Base mixin for neural network backend — provides fallback scoring."""

    @functools.lru_cache(maxsize=1)
    def _heuristic_score(self, reasoning_chain: tuple[str, ...], _query: str = "") -> float:
        """Fallback scoring when MLX unavailable — simple chain length heuristic."""
        del _query  # unused in heuristic fallback, kept for API compatibility
        if not reasoning_chain:
            return 0.3
        length_score = min(len(reasoning_chain) / 10.0, 0.7)
        avg_step_len = sum((len(s) for s in reasoning_chain)) / max(len(reasoning_chain), 1)
        detail_score = min(avg_step_len / 100.0, 0.3)
        return min(length_score + detail_score, 1.0)

if _MLX_NN_AVAILABLE:
    nn = _get_mlx_nn()
    if nn is None:
        raise ImportError('MLX nn unavailable')

    class CriticMLP(nn.Module):
        """MLX-based critic network for reasoning chain quality scoring."""
        __slots__ = tuple(('hidden_dims', 'input_dim', 'layers'))

        def __init__(self, input_dim: int, hidden_dims: list[int] | None=None):
            super().__init__()
            if hidden_dims is None:
                hidden_dims = [128, 64]
            self.input_dim = input_dim
            self.hidden_dims = hidden_dims
            layers = []
            prev_dim = input_dim
            for hidden_dim in hidden_dims:
                layers.append(nn.Linear(prev_dim, hidden_dim))
                prev_dim = hidden_dim
            layers.append(nn.Linear(prev_dim, 1))
            self.layers = layers

        def __call__(self, x):
            mx = get_mx()
            if mx is None:
                return np.array([0.5])
            for _i, layer in enumerate(self.layers[:-1]):
                x = layer(x)
                x = mx.maximum(x, 0)
            x = self.layers[-1](x)
            x = mx.sigmoid(x)
            return x

        def predict(self, embedding: np.ndarray) -> float:
            mx = get_mx()
            if mx is None:
                return 0.5
            try:
                x = mx.array(embedding.reshape(1, -1))
                score = self(x)
                return float(score.flatten()[0])
            except Exception as e:
                logger.warning(f'MLX scoring failed: {e}, using heuristic fallback')
                return 0.5
else:

    class CriticMLP(_CriticMLPBase):
        """Fallback critic when MLX unavailable — uses heuristic scoring."""
        __slots__ = tuple(('hidden_dims', 'input_dim'))

        def __init__(self, input_dim: int, hidden_dims: list[int] | None=None):
            self.input_dim = input_dim
            self.hidden_dims = hidden_dims or [128, 64]
            logger.debug('CriticMLP running in heuristic mode (MLX unavailable)')

        def __call__(self, _x) -> np.ndarray:
            return np.array([0.5])

        def predict(self, _embedding: np.ndarray) -> float:
            return 0.5

class DistillationEngine:
    """
    Engine pro distillation reasoning chain quality scoring.

    Features:
    - MLX MLP critic network pro hodnocení chainů
    - SQLite storage pro training examples
    - Lazy loading embedding modelu
    - Memory cleanup po heavy operations

    Args:
        embedding_model: Volitelný embedding model (None = použít default)
        db_path: Cesta k SQLite databázi (None = EVIDENCE_ROOT/distillation.db)
        embedding_dim: Dimenze embedding vektoru (default: 384)
    """
    DEFAULT_DB_DIR = None
    DEFAULT_DB_NAME = 'distillation.db'
    DEFAULT_EMBEDDING_DIM = 768
    MAX_CHAIN_LENGTH = 50
    __slots__ = tuple(('_critic', '_db_path', '_initialized', 'embedding_dim', 'embedding_model'))

    def __init__(self, embedding_model: SentenceTransformer | None=None, db_path: str | Path | None=None, embedding_dim: int=DEFAULT_EMBEDDING_DIM):
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self._critic: CriticMLP | None = None
        if db_path is None:
            from hledac.universal.paths import EVIDENCE_ROOT
            self._db_path = EVIDENCE_ROOT / 'distillation.db'
        else:
            self._db_path = Path(db_path)
        self._initialized = False

    async def initialize(self, embedding_model: SentenceTransformer | None=None) -> None:
        """
        Inicializovat engine.

        Args:
            embedding_model: Volitelný embedding model pro přepsání
        """
        if self._initialized:
            return
        if embedding_model:
            self.embedding_model = embedding_model
        try:
            await self._init_database()
            if MLX_AVAILABLE:
                self._critic = CriticMLP(input_dim=self.embedding_dim)
                logger.info(f'✓ Critic MLP initialized (input_dim={self.embedding_dim})')
            else:
                logger.warning('MLX not available, critic will not function')
            self._initialized = True
            logger.info('✓ DistillationEngine initialized')
        except Exception as e:
            logger.error(f'Failed to initialize DistillationEngine: {e}')
            raise

    async def _init_database(self) -> None:
        """Inicializovat SQLite databázi."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

            def _init_db():
                with closing(sqlite3.connect(str(self._db_path))) as conn:
                    cursor = conn.cursor()
                    cursor.execute('\n                        CREATE TABLE IF NOT EXISTS examples (\n                            id INTEGER PRIMARY KEY AUTOINCREMENT,\n                            query TEXT NOT NULL,\n                            chain TEXT NOT NULL,\n                            score REAL NOT NULL,\n                            metadata TEXT,\n                            timestamp REAL NOT NULL\n                        )\n                    ')
                    cursor.execute('\n                        CREATE INDEX IF NOT EXISTS idx_timestamp ON examples(timestamp)\n                    ')
                    conn.commit()
            await asyncio.to_thread(_init_db)
            logger.info(f'✓ Database initialized at {self._db_path}')
        except Exception as e:
            logger.error(f'Failed to initialize database: {e}')
            raise

    async def add_example(self, example: DistillationExample) -> bool:
        """
        Uložit training example do databáze.

        Args:
            example: DistillationExample k uložení

        Returns:
            True pokud se podařilo uložit
        """
        if not self._initialized:
            logger.error('Engine not initialized')
            return False
        try:

            def _do_insert():
                with closing(sqlite3.connect(str(self._db_path))) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        '\n                        INSERT INTO examples (query, chain, score, metadata, timestamp)\n                        VALUES (?, ?, ?, ?, ?)\n                        ',
                        (example.query, _json.encode(example.chain).decode('utf-8'), example.score, _json.encode(example.metadata).decode('utf-8'), example.timestamp),
                    )
                    conn.commit()

            await asyncio.to_thread(_do_insert)
            logger.debug(f'Added example with score {example.score:.3f}')
            return True
        except Exception as e:
            logger.error(f'Failed to add example: {e}')
            return False

    async def get_all_examples(self) -> list[DistillationExample]:
        """
        Načíst všechny training examples.

        Returns:
            Seznam DistillationExample
        """
        if not self._initialized:
            logger.error('Engine not initialized')
            return []

        def _do_select() -> list[tuple]:
            with closing(sqlite3.connect(str(self._db_path))) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT query, chain, score, metadata, timestamp FROM examples ORDER BY timestamp DESC LIMIT 10000')
                return cursor.fetchall()

        try:
            rows = await asyncio.to_thread(_do_select)
            examples = []
            for row in rows:
                examples.append(DistillationExample(query=row[0], chain=_json.decode(row[1]), score=row[2], metadata=_json.decode(row[3]) if row[3] else {}, timestamp=row[4]))
            return examples
        except Exception as e:
            logger.error(f'Failed to get examples: {e}')
            return []

    async def train(self, n_epochs: int=10) -> dict[str, float | int | str]:
        """
        Trénovat critic na uložených examples.

        Args:
            n_epochs: Počet epoch tréninku

        Returns:
            Dict s metrikami tréninku (loss, accuracy)
        """
        if not self._initialized:
            logger.error('Engine not initialized')
            return {'loss': float('inf'), 'accuracy': 0.0}
        if not MLX_AVAILABLE or self._critic is None:
            logger.warning('MLX not available, skipping training')
            return {'loss': 0.0, 'accuracy': 0.0}
        try:
            examples = await self.get_all_examples()
            if len(examples) < 2:
                logger.warning('Not enough examples for training (need >= 2)')
                return {'loss': 0.0, 'accuracy': 0.0, 'n_examples': len(examples)}
            logger.info(f'Training on {len(examples)} examples for {n_epochs} epochs')
            loop = asyncio.get_running_loop()
            executor = _get_embed_executor()
            embedding_tasks = [loop.run_in_executor(executor, self._get_chain_embedding, example.chain) for example in examples]
            embeddings = await parallel(embedding_tasks, policy="log", ctx='distillation_engine:391')
            X_list = embeddings
            y_list = [example.score for example in examples]
            mx = get_mx()
            if mx is None:
                return {'loss': float('inf'), 'accuracy': 0.0, 'error': 'MLX not available'}
            X = mx.array(np.array(X_list))
            y = mx.array(np.array(y_list).reshape(-1, 1))
            losses = []
            for epoch in range(n_epochs):
                predictions = self._critic(X)
                loss = mx.mean((predictions - y) ** 2)
                loss_value = float(np.array(loss))
                losses.append(loss_value)
                if epoch % max(1, n_epochs // 5) == 0:
                    logger.debug(f'Epoch {epoch}/{n_epochs}, Loss: {loss_value:.4f}')
            final_predictions = np.array(self._critic(X)).flatten()
            actual = np.array(y).flatten()
            if len(final_predictions) > 1:
                correlation = np.corrcoef(final_predictions, actual)[0, 1]
                if np.isnan(correlation):
                    correlation = 0.0
            else:
                correlation = 0.0
            del X, y
            if mx is not None:
                mx.eval([])
                mx.clear_cache()
            gc.collect()
            metrics = {'loss': losses[-1] if losses else 0.0, 'initial_loss': losses[0] if losses else 0.0, 'correlation': correlation, 'n_examples': len(examples), 'n_epochs': n_epochs}
            logger.info(f"✓ Training complete: loss={metrics['loss']:.4f}, corr={metrics['correlation']:.3f}")
            return metrics
        except Exception as e:
            logger.error(f'Training failed: {e}')
            return {'loss': float('inf'), 'accuracy': 0.0, 'error': str(e)}

    def score_chain(self, query: str, chain: list[str]) -> float:
        """
        Ohodnotit kvalitu reasoning chainu.

        Args:
            query: Vstupní dotaz
            chain: Seznam reasoning kroků

        Returns:
            Skóre 0-1 (vyšší = lepší)
        """
        if not self._initialized:
            logger.error('Engine not initialized')
            return 0.5
        try:
            embedding = self._get_chain_embedding(chain)
            if self._critic is not None:
                score = self._critic.predict(embedding)
            else:
                score = self._heuristic_score(query, tuple(chain))
            return score
        except Exception as e:
            logger.error(f'Failed to score chain: {e}')
            return 0.5

    def _get_chain_embedding(self, chain: list[str]) -> np.ndarray:
        """
        Konvertovat chain na embedding vektor.

        Args:
            chain: Seznam reasoning kroků

        Returns:
            NumPy array embeddingu tvaru (embedding_dim,)
        """
        try:
            chain = chain[:self.MAX_CHAIN_LENGTH]
            if self.embedding_model is not None:
                embeddings = self.embedding_model.encode(chain)
                embedding = np.mean(embeddings, axis=0)
            else:
                embedding = self._fallback_chain_embedding(chain)
            if len(embedding) != self.embedding_dim:
                if len(embedding) < self.embedding_dim:
                    embedding = np.pad(embedding, (0, self.embedding_dim - len(embedding)))
                else:
                    embedding = embedding[:self.embedding_dim]
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm
            return embedding
        except Exception as e:
            logger.error(f'Failed to get chain embedding: {e}')
            return np.zeros(self.embedding_dim, dtype=np.float32)

    def _fallback_chain_embedding(self, chain: list[str]) -> np.ndarray:
        """
        Fallback embedding když není dostupný model.

        Args:
            chain: Seznam reasoning kroků

        Returns:
            Simple embedding vektor
        """
        embedding = np.zeros(self.embedding_dim, dtype=np.float32)
        for step_idx, step in enumerate(chain[:self.MAX_CHAIN_LENGTH]):
            words = step.lower().split()
            for word_idx, word in enumerate(words[:20]):
                for char_idx, char in enumerate(word[:10]):
                    idx = (ord(char) + step_idx * 31 + word_idx * 17 + char_idx * 7) % self.embedding_dim
                    embedding[idx] += 1.0
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm
        return embedding

    @functools.lru_cache(maxsize=1)
    def _heuristic_score(self, query: str, chain: tuple[str, ...]) -> float:
        """
        Heuristické skóre když není dostupný critic.

        Args:
            query: Vstupní dotaz
            chain: Seznam reasoning kroků

        Returns:
            Heuristické skóre 0-1
        """
        if not chain:
            return 0.0
        scores = []
        chain_len = len(chain)
        if 3 <= chain_len <= 10:
            scores.append(1.0)
        elif chain_len < 3:
            scores.append(0.5)
        else:
            scores.append(0.7)
        step_scores = []
        for step in chain:
            step_score = 0.5
            reasoning_words = ['because', 'therefore', 'thus', 'hence', 'since', 'as', 'so']
            if any((word in step.lower() for word in reasoning_words)):
                step_score += 0.2
            if len(step) > 20:
                step_score += 0.1
            query_words = set(query.lower().split())
            step_words = set(step.lower().split())
            if query_words & step_words:
                step_score += 0.2
            step_scores.append(min(step_score, 1.0))
        avg_step_score = sum(step_scores) / len(step_scores) if step_scores else 0.5
        scores.append(avg_step_score)
        unique_steps = len(set(chain))
        diversity_score = unique_steps / len(chain) if chain else 0.0
        scores.append(diversity_score)
        weights = [0.3, 0.5, 0.2]
        final_score = sum((s * w for s, w in zip(scores, weights, strict=False)))
        return min(max(final_score, 0.0), 1.0)

    async def cleanup(self) -> None:
        """Cleanup paměti a resources."""
        logger.info('Cleaning up DistillationEngine...')
        self._critic = None
        self.embedding_model = None
        mx = get_mx()
        if mx is not None:
            mx.eval([])
        gc.collect()
        if mx is not None:
            mx.clear_cache()
        self._initialized = False
        logger.info('✓ DistillationEngine cleaned up')

    def get_status(self) -> dict[str, Any]:
        """
        Get engine status.

        Returns:
            Dict s informacemi o engine
        """
        return {'initialized': self._initialized, 'mlx_available': MLX_AVAILABLE, 'critic_initialized': self._critic is not None, 'embedding_dim': self.embedding_dim, 'db_path': str(self._db_path)}

    async def get_stats(self) -> dict[str, Any]:
        """
        Get statistics o uložených examples.

        Returns:
            Dict s statistikami
        """
        if not self._initialized:
            return {'error': 'Engine not initialized'}

        def _do_stats() -> dict[str, Any]:
            with closing(sqlite3.connect(str(self._db_path))) as conn:
                cursor = conn.cursor()
                cursor.execute('SELECT COUNT(*) FROM examples')
                count = cursor.fetchone()[0]
                cursor.execute('SELECT AVG(score), MIN(score), MAX(score) FROM examples')
                stats = cursor.fetchone()
            return {'n_examples': count, 'avg_score': stats[0] if stats[0] else 0.0, 'min_score': stats[1] if stats[1] else 0.0, 'max_score': stats[2] if stats[2] else 0.0}

        try:
            return await asyncio.to_thread(_do_stats)
        except Exception as e:
            logger.error(f'Failed to get stats: {e}')
            return {'error': str(e)}
DISTILLATION_AVAILABLE = False
DistillationEngineClass = None
DistillationExampleClass = None

def _load_distillation():
    """Lazy loading funkce pro distillation module."""
    global DISTILLATION_AVAILABLE, DistillationEngineClass, DistillationExampleClass
    if DISTILLATION_AVAILABLE:
        return
    try:
        DistillationEngineClass = DistillationEngine
        DistillationExampleClass = DistillationExample
        DISTILLATION_AVAILABLE = True
        logger.debug('Distillation module loaded successfully')
    except ImportError as e:
        logger.warning(f'Failed to load distillation module: {e}')
        DISTILLATION_AVAILABLE = False

async def distil(findings: list[dict], _max_tokens: int = 2000) -> str:
    """
    Předprocesuje findings přes DistillationEngine před synthesis.

    Výstup: komprimovaná esence ve formátu vhodném pro LLM kontext.
    Fallback: first N findings jako plaintext pokud engine není dostupný.

    Args:
        findings: List of finding dicts s poli text/snippet/title/source
        _max_tokens: Cílový počet tokenů (přibližně) — rezervováno pro budoucí use

    Returns:
        Komprimovaný text
    """
    if not findings:
        return ''
    try:
        engine = await create_distillation_engine()
        if engine is not None:
            chains = []
            for f in findings:
                text = f.get('text', '') or f.get('snippet', '') or f.get('title', '')
                if text:
                    chains.append([text[:500]])
            if chains:
                query = findings[0].get('query', 'summarize') if findings else ''
                best_chain = max(chains, key=lambda c: engine._heuristic_score(query, tuple(c)))
                return best_chain[0] if best_chain else _findings_to_text(findings)
            await engine.cleanup()
    except Exception:  # noqa: BLE001
        pass
    try:
        from hledac.universal.brain.ane_embedder import rerank_findings_cosine, semantic_dedup_findings
        findings = await semantic_dedup_findings(findings, threshold=0.9)
        _query_for_ane = getattr(findings[0], 'query', None) if findings else None
        if _query_for_ane and len(findings) > 5:
            findings = rerank_findings_cosine(findings, _query_for_ane, top_k=min(20, len(findings)))
        logger.debug('[ANE:distil] %d findings after dedup+rerank', len(findings))
    except Exception as _ane_err:
        logger.debug('[ANE:distil] skipped: %s', _ane_err)
    return _findings_to_text(findings)

def _findings_to_text(findings: list[dict], max_items: int=20) -> str:
    """Helper: convert findings list to plain text."""
    lines = []
    for f in findings[:max_items]:
        source = f.get('source', '?')
        title = f.get('title', '')
        snippet = (f.get('text', '') or f.get('snippet', ''))[:200]
        lines.append(f'[{source}] {title} — {snippet}')
    return '\n'.join(lines)

async def create_distillation_engine(embedding_model: Any | None=None, db_path: str | Path | None=None, embedding_dim: int=384) -> DistillationEngine | None:
    """
    Factory funkce pro vytvoření DistillationEngine.

    Args:
        embedding_model: Volitelný embedding model
        db_path: Cesta k SQLite databázi
        embedding_dim: Dimenze embedding vektoru

    Returns:
        DistillationEngine instance nebo None
    """
    try:
        engine = DistillationEngine(embedding_model=embedding_model, db_path=db_path, embedding_dim=embedding_dim)
        await engine.initialize()
        return engine
    except Exception as e:
        logger.error(f'Failed to create DistillationEngine: {e}')
        return None


if __name__ == '__main__':
    # Smoke test — async factory, requires event loop
    async def _smoke() -> None:
        eng = await create_distillation_engine()
        if eng is not None:
            print(f'Status: {eng.get_status()}')
            await eng.cleanup()
            print('OK')
        else:
            print('FAILED: create_distillation_engine returned None')

    asyncio.run(_smoke())
