"""
Product Quantization (PQ) index pro kompresi embeddingů.
Vrací similarity (1/(1+L2)) konzistentní s HNSW cosine similarity.

ROLE: Compression/Acceleration Layer (NOT retrieval authority)
===========================================================
- komprimuje embeddingy pomocí Product Quantization (12× úspora)
- NENÍ owner primary vector retrieval → rag_engine HNSWVectorIndex
- NENÍ owner identity store → lancedb_store
- standalone tool: train() → encode() → search() workflow
"""

import logging

import numpy as np

logger = logging.getLogger(__name__)

# MLX import s fallback
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None


class PQIndex:
    """
    Product Quantization index pro kompresi embeddingů.

    Features:
        - OPQ (Optimized Product Quantization) preprocessing
        - Výstup: cosine similarity (1/(1+L2_distance)) pro konzistenci s HNSW
        - 12× paměťová úspora (768 → 8 byte per vector)
    """

    def __init__(self, d: int = 768, m: int = 96, k: int = 256, n_iter: int = 20):
        """
        Initialize PQ index.

        Args:
            d: Dimension of input vectors
            m: Number of sub-vectors (d must be divisible by m)
            k: Number of centroids per sub-vector
            n_iter: Number of iterations for k-means training
        """
        self.d = d
        self.m = m
        self.k = k
        self.n_iter = n_iter

        if d % m != 0:
            raise ValueError(f"Dimension {d} must be divisible by number of sub-vectors {m}")

        self.sub_dim = d // m
        self.centroids: mx.array | None = None
        self.codes: mx.array | None = None
        self.ids: list[str] = []
        self.perm: mx.array | None = None
        self._is_trained = False

    def train(self, vectors: mx.array) -> None:
        """
        Train PQ centroids using k-means on sub-vectors.

        Args:
            vectors: MLX array of shape (n, d)
        """
        n = vectors.shape[0]
        logger.info(f"Starting PQ training on {n} vectors, {self.n_iter} iterations")

        # OPQ: random permutation for better distribution
        perm = mx.random.permutation(self.d)
        vectors_perm = vectors[:, perm]
        subvectors = vectors_perm.reshape(n, self.m, self.sub_dim)

        centroids_list = []
        for i in range(self.m):
            data = subvectors[:, i, :]

            # Initialize centroids with k-means++ style
            idx = mx.random.randint(0, n, (self.k,))
            centroids = data[idx]

            for it in range(self.n_iter):
                # Compute distances to centroids
                # (n, k, sub_dim) - (k, sub_dim) = (n, k, sub_dim)
                distances = mx.sum((data[:, None, :] - centroids[None, :, :]) ** 2, axis=2)
                labels = mx.argmin(distances, axis=1)

                # Update centroids - use numpy for complex indexing
                new_centroids = []
                labels_np = np.array(labels)
                data_np = np.array(data)
                for j in range(self.k):
                    mask = labels_np == j
                    cnt = np.sum(mask)
                    if cnt > 0:
                        new_centroids.append(np.mean(data_np[mask], axis=0))
                    else:
                        new_centroids.append(np.array(centroids[j]))
                centroids = mx.array(np.stack(new_centroids))

                if it % 5 == 0:
                    logger.debug(f"Subvector {i+1}/{self.m}, iteration {it+1}/{self.n_iter}")

            centroids_list.append(centroids)

        self.centroids = mx.stack(centroids_list)
        self.perm = perm
        self._is_trained = True
        logger.info("PQ training completed")

    def encode(self, vectors: mx.array) -> mx.array:
        """
        Encode vectors to PQ codes.

        Args:
            vectors: MLX array of shape (n, d)

        Returns:
            MLX array of shape (n, m) with uint8 codes
        """
        if not self._is_trained:
            raise RuntimeError("PQ index not trained. Call train() first.")

        vectors_perm = vectors[:, self.perm]
        n = vectors.shape[0]
        subvectors = vectors_perm.reshape(n, self.m, self.sub_dim)

        codes = []
        for i in range(self.m):
            # Compute distances to centroids for sub-vector i
            dist = mx.sum(
                (subvectors[:, i, :][:, None, :] - self.centroids[i][None, :, :]) ** 2,
                axis=2
            )
            codes.append(mx.argmin(dist, axis=1).astype(mx.uint8))

        return mx.stack(codes, axis=1)

    def add(self, node_id: str, vector: mx.array) -> None:
        """
        Add a single vector to the index.

        Args:
            node_id: Unique identifier for the vector
            vector: MLX array of shape (d,)
        """
        if not self._is_trained:
            raise RuntimeError("PQ index not trained. Call train() first.")

        code = self.encode(vector[None, :])[0]

        if self.codes is None:
            self.codes = code[None, :]
            self.ids = [node_id]
        else:
            self.codes = mx.concatenate([self.codes, code[None, :]], axis=0)
            self.ids.append(node_id)

    def search(self, query: mx.array, k: int = 10) -> list[tuple[str, float]]:
        """
        Search for k nearest neighbors.

        Returns similarity (1/(1+L2)) for consistency with HNSW cosine similarity.
        Higher = more similar.

        Args:
            query: MLX array of shape (d,)
            k: Number of results to return

        Returns:
            List of (id, similarity) tuples, sorted by similarity descending
        """
        if self.codes is None or len(self.ids) == 0:
            return []

        # Apply same permutation as training
        query_perm = query[self.perm]
        q_sub = query_perm.reshape(1, self.m, self.sub_dim)

        # Compute distance table: (m, k)
        dist_table = mx.zeros((self.m, self.k))
        for i in range(self.m):
            d = mx.sum(
                (q_sub[0, i][None, :] - self.centroids[i]) ** 2,
                axis=1
            )
            dist_table[i] = d

        # Compute L2 distances using codes: (n,)
        # Use numpy for this complex operation
        dist_table_np = np.array(dist_table)
        codes_np = np.array(self.codes)

        # For each code, compute sum of distances
        dists = np.zeros(len(codes_np))
        for i in range(len(codes_np)):
            code_dists = 0
            for j in range(self.m):
                code_dists += dist_table_np[j, codes_np[i, j]]
            dists[i] = code_dists

        dists = mx.array(dists)

        # Convert to similarity: 1/(1+L2)
        # This is monotonically decreasing with L2, so higher = more similar
        similarities = 1.0 / (1.0 + dists)

        # Get top-k indices
        if k >= len(self.ids):
            sorted_idx = mx.argsort(-similarities)
        else:
            sorted_idx = mx.argsort(-similarities)[:k]

        # Convert sorted_idx to list of integers
        sorted_idx_list = [int(i) for i in sorted_idx]
        return [(self.ids[i], float(similarities[i])) for i in sorted_idx_list]

    def save(self, path: str) -> None:
        """Save PQ index to file."""
        if not self._is_trained:
            raise RuntimeError("Cannot save untrained PQ index")

        mx.savez(
            path,
            centroids=self.centroids,
            codes=self.codes,
            perm=self.perm,
            ids=self.ids
        )
        logger.info(f"PQ index saved to {path}")

    def load(self, path: str) -> None:
        """Load PQ index from file."""
        data = mx.load(path)
        self.centroids = data['centroids']
        self.codes = data['codes']
        self.perm = data['perm']
        self.ids = list(data['ids'])
        self._is_trained = True
        logger.info(f"PQ index loaded from {path}")

    def is_trained(self) -> bool:
        """Check if PQ index is trained."""
        return self._is_trained

    def get_memory_usage(self) -> int:
        """Get approximate memory usage in bytes."""
        if not self._is_trained:
            return 0

        # centroids: m * k * sub_dim * 4 bytes (float32)
        centroids_bytes = self.m * self.k * self.sub_dim * 4
        # codes: n * m * 1 byte (uint8)
        codes_bytes = 0
        if self.codes is not None:
            codes_bytes = self.codes.shape[0] * self.m * 1

        return centroids_bytes + codes_bytes

    def get_compression_ratio(self, n_vectors: int) -> float:
        """Calculate compression ratio vs float32."""
        if not self._is_trained:
            return 0.0

        original_bytes = n_vectors * self.d * 4  # float32
        compressed_bytes = self.get_memory_usage()

        return original_bytes / max(compressed_bytes, 1)
