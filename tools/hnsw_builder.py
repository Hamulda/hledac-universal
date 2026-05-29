"""
Inkrementální přidávání vektorů do HNSW indexu s asyncio.Lock.
Lock chrání add i query, aby se předešlo race condition.
"""

import asyncio
import logging

import numpy as np

logger = logging.getLogger(__name__)

try:
    import hnswlib
    HNSWLIB_AVAILABLE = True
except ImportError:
    HNSWLIB_AVAILABLE = False
    hnswlib = None


class IncrementalHNSW:
    """
    Inkrementální HNSW index s asyncio.Lock pro thread-safe add i query.
    Mapuje string ID na interní integery.
    """

    def __init__(self, dim: int, max_elements: int = 100000, ef_construction: int = 200, M: int = 16):
        """
        Inicializuje inkrementální HNSW index.

        Args:
            dim: Dimenze vektorů
            max_elements: Maximální počet vektorů (hard limit)
            ef_construction: Parameter pro konstrukci indexu
            M: Počet propojení na uzel
        """
        if not HNSWLIB_AVAILABLE:
            raise RuntimeError("hnswlib not available, cannot create IncrementalHNSW")

        self.dim = dim
        self.max_elements = max_elements
        self.index = hnswlib.Index(space='cosine', dim=dim)
        self.index.init_index(max_elements=max_elements, ef_construction=ef_construction, M=M)
        self.index.set_ef(50)
        self.current_count = 0
        self._lock = asyncio.Lock()
        self._id_to_int: dict[str, int] = {}  # mapování string ID na integer index
        self._int_to_id: dict[int, str] = {}  # reverse mapping
        self._next_id = 0

    async def add_items(self, vectors: np.ndarray, ids: list[str]):
        """
        Přidá vektory s jejich string ID. ID se mapují na interní integery.

        Args:
            vectors: Numpy array tvaru (n, dim)
            ids: Seznam string ID pro každý vektor
        """
        if len(vectors) != len(ids):
            raise ValueError("Number of vectors must match number of IDs")

        if self.current_count + len(vectors) > self.max_elements:
            raise RuntimeError(f"Cannot add {len(vectors)} vectors, would exceed max_elements limit")

        int_ids = []
        for id_str in ids:
            if id_str not in self._id_to_int:
                self._id_to_int[id_str] = self._next_id
                self._int_to_id[self._next_id] = id_str
                self._next_id += 1
            int_ids.append(self._id_to_int[id_str])

        async with self._lock:
            self.index.add_items(vectors, int_ids)
            self.current_count += len(ids)
            logger.debug(f"Added {len(ids)} vectors, total: {self.current_count}")

    async def knn_query(self, query: np.ndarray, k: int) -> tuple[np.ndarray, np.ndarray]:
        """
        Provede KNN dotaz.

        Args:
            query: Query vektor tvaru (dim,) nebo (1, dim)
            k: Počet nejbližších sousedů

        Returns:
            Tuple of (labels, distances)
        """
        if query.ndim == 1:
            query = query.reshape(1, -1)

        async with self._lock:
            labels, distances = self.index.knn_query(query, k=k)

        # Convert internal int IDs back to string IDs
        string_labels = []
        for label in labels[0]:
            string_labels.append(self._int_to_id.get(label, str(label)))

        return np.array([string_labels]), distances

    def get_count(self) -> int:
        """Vrátí aktuální počet vektorů v indexu."""
        return self.current_count

    def save(self, path: str):
        """Uloží index na disk."""
        self.index.save_index(path)

    def load(self, path: str, max_elements: int = None):
        """Načte index z disku."""
        if max_elements is None:
            max_elements = self.max_elements
        self.index.load_index(path, max_elements=max_elements)
        # Reconstruct ID mappings would need to be handled externally

    async def close(self):
        """Uzavře index a uvolní prostředky."""
        # HNSWLib doesn't have explicit close, but we can clear references
        self._id_to_int.clear()
        self._int_to_id.clear()
        self.current_count = 0
