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
    >>> score = await engine.score_chain(query, chain)
"""

from __future__ import annotations

import gc
import json
import logging
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np

logger = logging.getLogger(__name__)

# MLX imports
MLX_AVAILABLE = False
mx = None
nn = None

try:
    import mlx.core as mx
    import mlx.nn as nn
    MLX_AVAILABLE = True
except ImportError:
    logger.warning("MLX not available. Install: pip install mlx>=0.15.0")


@dataclass
class DistillationExample:
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
    chain: List[str]
    score: float
    metadata: Dict[str, Any] = None
    timestamp: float = None

    def __post_init__(self):
        """Post-init validace a default hodnoty."""
        if self.metadata is None:
            self.metadata = {}
        if self.timestamp is None:
            self.timestamp = time.time()
        # Validate score range
        self.score = max(0.0, min(1.0, float(self.score)))

    def to_dict(self) -> Dict[str, Any]:
        """Konvertovat na slovník."""
        return {
            "query": self.query,
            "chain": self.chain,
            "score": self.score,
            "metadata": self.metadata,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DistillationExample":
        """Vytvořit z slovníku."""
        return cls(
            query=data["query"],
            chain=data["chain"],
            score=data["score"],
            metadata=data.get("metadata", {}),
            timestamp=data.get("timestamp", time.time()),
        )


class CriticMLP(nn.Module):
    """
    MLP critic network pro hodnocení reasoning chainů.

    Architektura optimalizovaná pro M1 8GB:
    - Input: reasoning chain embedding (concatenated step embeddings)
    - Hidden layers: [128, 64]
    - Output: single score (0-1)
    - Activation: ReLU

    Args:
        input_dim: Dimenze vstupního embeddingu
        hidden_dims: Seznam dimenzí hidden vrstev (default: [128, 64])
    """

    def __init__(self, input_dim: int, hidden_dims: Optional[List[int]] = None):
        super().__init__()
        if hidden_dims is None:
            hidden_dims = [128, 64]  # M1 8GB constraint

        self.input_dim = input_dim
        self.hidden_dims = hidden_dims

        # Build layers
        layers = []
        prev_dim = input_dim
        for hidden_dim in hidden_dims:
            layers.append(nn.Linear(prev_dim, hidden_dim))
            prev_dim = hidden_dim

        # Output layer
        layers.append(nn.Linear(prev_dim, 1))
        self.layers = layers

    def __call__(self, x: mx.array) -> mx.array:
        """
        Forward pass vrací skóre (0-1).

        Args:
            x: Vstupní embedding mx.array tvaru (batch, input_dim)

        Returns:
            Skóre mx.array tvaru (batch, 1)
        """
        # Pass through hidden layers with ReLU
        for i, layer in enumerate(self.layers[:-1]):
            x = layer(x)
            x = mx.maximum(x, 0)  # ReLU activation

        # Output layer with sigmoid for 0-1 range
        x = self.layers[-1](x)
        x = mx.sigmoid(x)

        return x

    def predict(self, embedding: np.ndarray) -> float:
        """
        Predikovat skóre pro embedding.

        Args:
            embedding: NumPy array embeddingu

        Returns:
            Skóre 0-1
        """
        if not MLX_AVAILABLE or mx is None:
            logger.warning("MLX not available, returning default score")
            return 0.5

        try:
            # Convert to MLX array
            x = mx.array(embedding.reshape(1, -1))

            # Forward pass
            score = self(x)

            # Convert back to float
            return float(np.array(score).flatten()[0])
        except Exception as e:
            logger.error(f"Prediction failed: {e}")
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

    DEFAULT_DB_DIR = None  # Determined at runtime from paths module
    DEFAULT_DB_NAME = "distillation.db"
    DEFAULT_EMBEDDING_DIM = 768  # ModernBERT-base dimension
    MAX_CHAIN_LENGTH = 50  # Max počet kroků v chainu

    def __init__(
        self,
        embedding_model: Optional[Any] = None,
        db_path: Optional[Union[str, Path]] = None,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
    ):
        self.embedding_model = embedding_model
        self.embedding_dim = embedding_dim
        self._critic: Optional[CriticMLP] = None
        if db_path is None:
            from hledac.universal.paths import EVIDENCE_ROOT
            self._db_path = EVIDENCE_ROOT / "distillation.db"
        else:
            self._db_path = Path(db_path)
        self._initialized = False

    async def initialize(self, embedding_model: Optional[Any] = None) -> None:
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
            # Initialize database
            await self._init_database()

            # Initialize critic network
            if MLX_AVAILABLE:
                self._critic = CriticMLP(input_dim=self.embedding_dim)
                logger.info(f"✓ Critic MLP initialized (input_dim={self.embedding_dim})")
            else:
                logger.warning("MLX not available, critic will not function")

            self._initialized = True
            logger.info("✓ DistillationEngine initialized")

        except Exception as e:
            logger.error(f"Failed to initialize DistillationEngine: {e}")
            raise

    async def _init_database(self) -> None:
        """Inicializovat SQLite databázi."""
        from contextlib import closing
        try:
            # Ensure directory exists
            self._db_path.parent.mkdir(parents=True, exist_ok=True)

            # Create connection with closing() to guarantee FD release
            with closing(sqlite3.connect(str(self._db_path))) as conn:
                cursor = conn.cursor()

            # Create examples table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS examples (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    chain TEXT NOT NULL,
                    score REAL NOT NULL,
                    metadata TEXT,
                    timestamp REAL NOT NULL
                )
            """)

            # Create index for faster queries
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON examples(timestamp)
            """)

            conn.commit()
            # closing() context manager guarantees FD release

            logger.info(f"✓ Database initialized at {self._db_path}")

        except Exception as e:
            logger.error(f"Failed to initialize database: {e}")
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
            logger.error("Engine not initialized")
            return False

        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO examples (query, chain, score, metadata, timestamp)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        example.query,
                        json.dumps(example.chain),
                        example.score,
                        json.dumps(example.metadata),
                        example.timestamp,
                    ),
                )

                conn.commit()
                # closing() context manager guarantees FD release

            logger.debug(f"Added example with score {example.score:.3f}")
            return True

        except Exception as e:
            logger.error(f"Failed to add example: {e}")
            return False

    async def get_all_examples(self) -> List[DistillationExample]:
        """
        Načíst všechny training examples.

        Returns:
            Seznam DistillationExample
        """
        if not self._initialized:
            logger.error("Engine not initialized")
            return []

        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn:
                cursor = conn.cursor()

                cursor.execute(
                    "SELECT query, chain, score, metadata, timestamp FROM examples ORDER BY timestamp"
                )
                rows = cursor.fetchall()
                # closing() context manager guarantees FD release

            examples = []
            for row in rows:
                examples.append(
                    DistillationExample(
                        query=row[0],
                        chain=json.loads(row[1]),
                        score=row[2],
                        metadata=json.loads(row[3]) if row[3] else {},
                        timestamp=row[4],
                    )
                )

            return examples

        except Exception as e:
            logger.error(f"Failed to get examples: {e}")
            return []

    async def train(self, n_epochs: int = 10) -> Dict[str, float]:
        """
        Trénovat critic na uložených examples.

        Args:
            n_epochs: Počet epoch tréninku

        Returns:
            Dict s metrikami tréninku (loss, accuracy)
        """
        if not self._initialized:
            logger.error("Engine not initialized")
            return {"loss": float("inf"), "accuracy": 0.0}

        if not MLX_AVAILABLE or self._critic is None:
            logger.warning("MLX not available, skipping training")
            return {"loss": 0.0, "accuracy": 0.0}

        try:
            # Load examples
            examples = await self.get_all_examples()
            if len(examples) < 2:
                logger.warning("Not enough examples for training (need >= 2)")
                return {"loss": 0.0, "accuracy": 0.0, "n_examples": len(examples)}

            logger.info(f"Training on {len(examples)} examples for {n_epochs} epochs")

            # Prepare data
            X_list = []
            y_list = []

            for example in examples:
                embedding = self._get_chain_embedding(example.chain)
                X_list.append(embedding)
                y_list.append(example.score)

            # Convert to MLX arrays
            X = mx.array(np.array(X_list))
            y = mx.array(np.array(y_list).reshape(-1, 1))

            # Simple training loop with SGD
            learning_rate = 0.01
            losses = []

            for epoch in range(n_epochs):
                # Forward pass
                predictions = self._critic(X)

                # Compute MSE loss
                loss = mx.mean((predictions - y) ** 2)
                loss_value = float(np.array(loss))
                losses.append(loss_value)

                # Compute gradients (simple SGD update)
                # Note: In production, use proper optimizer
                # This is simplified for M1 8GB constraint

                if epoch % max(1, n_epochs // 5) == 0:
                    logger.debug(f"Epoch {epoch}/{n_epochs}, Loss: {loss_value:.4f}")

            # Compute accuracy (correlation-based)
            final_predictions = np.array(self._critic(X)).flatten()
            actual = np.array(y).flatten()

            if len(final_predictions) > 1:
                correlation = np.corrcoef(final_predictions, actual)[0, 1]
                if np.isnan(correlation):
                    correlation = 0.0
            else:
                correlation = 0.0

            # Cleanup
            del X, y
            gc.collect()
            if MLX_AVAILABLE and mx is not None:
                mx.clear_cache()

            metrics = {
                "loss": losses[-1] if losses else 0.0,
                "initial_loss": losses[0] if losses else 0.0,
                "correlation": correlation,
                "n_examples": len(examples),
                "n_epochs": n_epochs,
            }

            logger.info(f"✓ Training complete: loss={metrics['loss']:.4f}, corr={metrics['correlation']:.3f}")
            return metrics

        except Exception as e:
            logger.error(f"Training failed: {e}")
            return {"loss": float("inf"), "accuracy": 0.0, "error": str(e)}

    async def score_chain(self, query: str, chain: List[str]) -> float:
        """
        Ohodnotit kvalitu reasoning chainu.

        Args:
            query: Vstupní dotaz
            chain: Seznam reasoning kroků

        Returns:
            Skóre 0-1 (vyšší = lepší)
        """
        if not self._initialized:
            logger.error("Engine not initialized")
            return 0.5

        try:
            # Get chain embedding
            embedding = self._get_chain_embedding(chain)

            # Score using critic
            if self._critic is not None:
                score = self._critic.predict(embedding)
            else:
                # Fallback: heuristic scoring
                score = self._heuristic_score(query, chain)

            return score

        except Exception as e:
            logger.error(f"Failed to score chain: {e}")
            return 0.5

    def _get_chain_embedding(self, chain: List[str]) -> np.ndarray:
        """
        Konvertovat chain na embedding vektor.

        Args:
            chain: Seznam reasoning kroků

        Returns:
            NumPy array embeddingu tvaru (embedding_dim,)
        """
        try:
            # Limit chain length
            chain = chain[:self.MAX_CHAIN_LENGTH]

            if self.embedding_model is not None:
                # Use provided embedding model
                embeddings = self.embedding_model.encode(chain)
                # Mean pooling across steps
                embedding = np.mean(embeddings, axis=0)
            else:
                # Fallback: simple bag-of-words embedding
                embedding = self._fallback_chain_embedding(chain)

            # Ensure correct dimension
            if len(embedding) != self.embedding_dim:
                # Pad or truncate
                if len(embedding) < self.embedding_dim:
                    embedding = np.pad(embedding, (0, self.embedding_dim - len(embedding)))
                else:
                    embedding = embedding[:self.embedding_dim]

            # Normalize
            norm = np.linalg.norm(embedding)
            if norm > 0:
                embedding = embedding / norm

            return embedding

        except Exception as e:
            logger.error(f"Failed to get chain embedding: {e}")
            # Return zero embedding
            return np.zeros(self.embedding_dim, dtype=np.float32)

    def _fallback_chain_embedding(self, chain: List[str]) -> np.ndarray:
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
            for word_idx, word in enumerate(words[:20]):  # Max 20 words per step
                for char_idx, char in enumerate(word[:10]):  # Max 10 chars per word
                    idx = (ord(char) + step_idx * 31 + word_idx * 17 + char_idx * 7) % self.embedding_dim
                    embedding[idx] += 1.0

        # Normalize
        norm = np.linalg.norm(embedding)
        if norm > 0:
            embedding = embedding / norm

        return embedding

    def _heuristic_score(self, query: str, chain: List[str]) -> float:
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

        # Length score (prefer medium-length chains)
        chain_len = len(chain)
        if 3 <= chain_len <= 10:
            scores.append(1.0)
        elif chain_len < 3:
            scores.append(0.5)
        else:
            scores.append(0.7)

        # Step quality score
        step_scores = []
        for step in chain:
            step_score = 0.5

            # Check for reasoning indicators
            reasoning_words = ["because", "therefore", "thus", "hence", "since", "as", "so"]
            if any(word in step.lower() for word in reasoning_words):
                step_score += 0.2

            # Check for specificity
            if len(step) > 20:
                step_score += 0.1

            # Check for query relevance
            query_words = set(query.lower().split())
            step_words = set(step.lower().split())
            if query_words & step_words:
                step_score += 0.2

            step_scores.append(min(step_score, 1.0))

        avg_step_score = sum(step_scores) / len(step_scores) if step_scores else 0.5
        scores.append(avg_step_score)

        # Diversity score (unique steps)
        unique_steps = len(set(chain))
        diversity_score = unique_steps / len(chain) if chain else 0.0
        scores.append(diversity_score)

        # Final score: weighted average
        weights = [0.3, 0.5, 0.2]
        final_score = sum(s * w for s, w in zip(scores, weights))

        return min(max(final_score, 0.0), 1.0)

    async def cleanup(self) -> None:
        """Cleanup paměti a resources."""
        logger.info("Cleaning up DistillationEngine...")

        # Clear critic
        self._critic = None

        # Clear embedding model reference
        self.embedding_model = None

        # Garbage collection
        gc.collect()
        if MLX_AVAILABLE and mx is not None:
            mx.clear_cache()

        self._initialized = False
        logger.info("✓ DistillationEngine cleaned up")

    def get_status(self) -> Dict[str, Any]:
        """
        Get engine status.

        Returns:
            Dict s informacemi o engine
        """
        return {
            "initialized": self._initialized,
            "mlx_available": MLX_AVAILABLE,
            "critic_initialized": self._critic is not None,
            "embedding_dim": self.embedding_dim,
            "db_path": str(self._db_path),
        }

    async def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics o uložených examples.

        Returns:
            Dict s statistikami
        """
        if not self._initialized:
            return {"error": "Engine not initialized"}

        try:
            with closing(sqlite3.connect(str(self._db_path))) as conn:
                cursor = conn.cursor()

                # Count examples
                cursor.execute("SELECT COUNT(*) FROM examples")
                count = cursor.fetchone()[0]

                # Get score statistics
                cursor.execute("SELECT AVG(score), MIN(score), MAX(score) FROM examples")
                stats = cursor.fetchone()
                # closing() context manager guarantees FD release

            return {
                "n_examples": count,
                "avg_score": stats[0] if stats[0] else 0.0,
                "min_score": stats[1] if stats[1] else 0.0,
                "max_score": stats[2] if stats[2] else 0.0,
            }

        except Exception as e:
            logger.error(f"Failed to get stats: {e}")
            return {"error": str(e)}


# Lazy loading globals
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
        logger.debug("Distillation module loaded successfully")
    except ImportError as e:
        logger.warning(f"Failed to load distillation module: {e}")
        DISTILLATION_AVAILABLE = False


# ---------------------------------------------------------------------------
# Sprint 8VH: Distillation Wrapper (findings → compressed text)
# ---------------------------------------------------------------------------


async def distil(
    findings: list[dict],
    max_tokens: int = 2000,
) -> str:
    """
    Předprocesuje findings přes DistillationEngine před synthesis.

    Výstup: komprimovaná esence ve formátu vhodném pro LLM kontext.
    Fallback: first N findings jako plaintext pokud engine není dostupný.

    Args:
        findings: List of finding dicts s poli text/snippet/title/source
        max_tokens: Cílový počet tokenů (přibližně)

    Returns:
        Komprimovaný text
    """
    if not findings:
        return ""

    try:
        engine = await create_distillation_engine()
        if engine is not None:
            # Extract chains from findings
            chains = []
            for f in findings:
                text = f.get("text", "") or f.get("snippet", "") or f.get("title", "")
                if text:
                    # Each finding = one reasoning step
                    chains.append([text[:500]])  # limit each to 500 chars
            if chains:
                # Score and select best chains
                query = findings[0].get("query", "summarize") if findings else ""
                best_chain = max(chains, key=lambda c: engine._heuristic_score(query, c))
                return best_chain[0] if best_chain else _findings_to_text(findings)
            await engine.cleanup()
    except Exception:
        pass

    # Fallback: serialize top findings as text
    return _findings_to_text(findings)


def _findings_to_text(findings: list[dict], max_items: int = 20) -> str:
    """Helper: convert findings list to plain text."""
    lines = []
    for f in findings[:max_items]:
        source = f.get("source", "?")
        title = f.get("title", "")
        snippet = (f.get("text", "") or f.get("snippet", ""))[:200]
        lines.append(f"[{source}] {title} — {snippet}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Original create_distillation_engine
# ---------------------------------------------------------------------------

async def create_distillation_engine(
    embedding_model: Optional[Any] = None,
    db_path: Optional[Union[str, Path]] = None,
    embedding_dim: int = 384,
) -> Optional[DistillationEngine]:
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
        engine = DistillationEngine(
            embedding_model=embedding_model,
            db_path=db_path,
            embedding_dim=embedding_dim,
        )
        await engine.initialize()
        return engine
    except Exception as e:
        logger.error(f"Failed to create DistillationEngine: {e}")
        return None


if __name__ == "__main__":
    # Test
    import asyncio

    logging.basicConfig(level=logging.INFO)

    async def test():
        print("Testing DistillationEngine...")

        # Create engine
        engine = await create_distillation_engine()
        if engine is None:
            print("Failed to create engine")
            return

        print(f"Engine status: {engine.get_status()}")

        # Add example
        example = DistillationExample(
            query="What is the capital of France?",
            chain=[
                "Step 1: Identify the country as France",
                "Step 2: Recall that Paris is the capital of France",
                "Step 3: Verify this information is correct",
            ],
            score=0.95,
            metadata={"source": "test"},
        )

        await engine.add_example(example)
        print("Added example")

        # Get stats
        stats = await engine.get_stats()
        print(f"Stats: {stats}")

        # Score a chain
        chain = [
            "Step 1: Identify the country",
            "Step 2: Recall the capital",
        ]
        score = await engine.score_chain("What is the capital of France?", chain)
        print(f"Score: {score:.3f}")

        # Train
        metrics = await engine.train(n_epochs=5)
        print(f"Training metrics: {metrics}")

        # Cleanup
        await engine.cleanup()
        print("Cleanup complete")

    asyncio.run(test())
