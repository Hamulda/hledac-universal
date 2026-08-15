"""
Inkrementální přidávání vektorů do USearch indexu s asyncio.Lock.
Lock chrání add i query, aby se předešlo race condition.
M1 8GB: usearch C++ HNSW s Metal SIMD — rychlejší než hnswlib.

"""
import asyncio
import logging
import numpy as np
from core import aclose
logger = logging.getLogger(__name__)
try:
    import usearch
    USEARCH_AVAILABLE = True
except ImportError:
    USEARCH_AVAILABLE = False
    usearch = None

class IncrementalHNSW:
    """
    Inkrementální USearch index s asyncio.Lock pro thread-safe add i query.
    Mapuje string ID na interní integery.
    """
    __slots__ = tuple(('_id_to_int', '_int_to_id', '_lock', '_next_id', 'current_count', 'dim', 'index', 'max_elements', 'ef_search'))

    def __init__(self, dim: int, max_elements: int=100000, ef_construction: int=200, M: int=16, ef_search: int=50):
        """
        Inicializuje inkrementální USearch index.

        Args:
            dim: Dimenze vektorů
            max_elements: Maximální počet vektorů (hard limit)
            ef_construction: Parameter pro konstrukci indexu
            M: Počet propojení na uzel
            ef_search: Query-time expansion factor (higher = better recall, slower)
        """
        if not USEARCH_AVAILABLE:
            raise RuntimeError('usearch not available, cannot create IncrementalHNSW')
        self.dim = dim
        self.max_elements = max_elements
        self.ef_search = ef_search
        import usearch.index
        # Adaptive expansion_add: higher for large indices (>100k vectors) for better HNSW quality
        # usearch supports up to 1024, use 200-300 range for large indices
        if max_elements > 100_000:
            exp_add = min(ef_construction, 300)
        else:
            exp_add = min(ef_construction, 200)
        self.index = usearch.index.Index(ndim=dim, metric='cos', dtype='f32', connectivity=M, expansion_add=exp_add, expansion_search=ef_search)
        self.current_count = 0
        self._lock = asyncio.Lock()
        self._id_to_int: dict[str, int] = {}
        self._int_to_id: dict[int, str] = {}
        self._next_id = 0

    async def add_items(self, vectors: np.ndarray, ids: list[str]):
        """
        Přidá vektory s jejich string ID. ID se mapují na interní integery.

        Args:
            vectors: Numpy array tvaru (n, dim)
            ids: Seznam string ID pro každý vektor
        """
        if len(vectors) != len(ids):
            raise ValueError('Number of vectors must match number of IDs')
        if self.current_count + len(vectors) > self.max_elements:
            raise RuntimeError(f'Cannot add {len(vectors)} vectors, would exceed max_elements limit')
        int_ids = []
        for id_str in ids:
            if id_str not in self._id_to_int:
                self._id_to_int[id_str] = self._next_id
                self._int_to_id[self._next_id] = id_str
                self._next_id += 1
            int_ids.append(self._id_to_int[id_str])
        async with self._lock:
            for id_str, vec in zip(ids, vectors):
                self.index.add(self._id_to_int[id_str], vec.astype(np.float32))
            self.current_count += len(ids)
            logger.debug(f'Added {len(ids)} vectors, total: {self.current_count}')

    async def knn_query(self, query: np.ndarray, k: int) -> tuple[list[str], list[float]]:
        """
        Provede KNN dotaz.

        Args:
            query: Query vektor tvaru (dim,) nebo (1, dim)
            k: Počet nejbližších sousedů

        Returns:
            Tuple of (string_ids, distances)
        """
        if query.ndim == 1:
            query = query.reshape(1, -1)
        async with self._lock:
            results = self.index.search(query[0].astype(np.float32), count=k)
        string_ids = []
        distances = []
        for r in results:
            key = int(getattr(r, 'key', 0))
            dist = float(getattr(r, 'distance', 2.0))
            string_ids.append(self._int_to_id.get(key, str(key)))
            distances.append(dist)
        return (string_ids, distances)

    def get_count(self) -> int:
        """Vrátí aktuální počet vektorů v indexu."""
        return self.current_count

    def save(self, path: str):
        """Uloží index na disk."""
        self.index.save(path)

    def load(self, path: str, max_elements: int | None=None):
        """Načte index z disku."""
        self.index.load(path)
        if max_elements is not None:
            self.max_elements = max_elements
        else:
            # Restore max_elements from actual index size if available
            actual_size = getattr(self.index, 'size', 0)
            if actual_size > 0:
                self.max_elements = actual_size

    async def close(self):
        """Uzavře index a uvolní prostředky."""
        self._id_to_int.clear()
        self._int_to_id.clear()
        self.current_count = 0