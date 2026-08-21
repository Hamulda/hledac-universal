from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass
__all__ = ["get_lsh_domain"]


class _PythonLshIndex:
    """Python fallback LSH index — pure-Python OrderedDict bucketing (slow path).

    Mirrors ``rust.lsh.LSHIndex`` API for fail-soft fallback.
    """

    __slots__ = ("_fingerprints", "_num_rows", "_num_tables", "_tables")

    def __init__(self, num_tables: int = 16, num_rows: int = 4) -> None:
        self._num_tables = num_tables
        self._num_rows = num_rows
        self._tables: list[dict[int, list[tuple[str, int]]]] = [{} for _ in range(num_tables)]
        self._fingerprints: dict[str, int] = {}

    def insert(self, doc_id: str, fingerprint: int) -> None:
        self._fingerprints[doc_id] = fingerprint
        for band_idx in range(self._num_tables):
            band_hash = self._compute_band_hash(fingerprint, band_idx)
            self._tables[band_idx].setdefault(band_hash, []).append((doc_id, fingerprint))

    def query(self, fingerprint: int, max_results: int = 100) -> list[tuple[str, float]]:
        candidate_counts: dict[str, int] = {}
        for band_idx in range(self._num_tables):
            band_hash = self._compute_band_hash(fingerprint, band_idx)
            for doc_id, _ in self._tables[band_idx].get(band_hash, ()):
                candidate_counts[doc_id] = candidate_counts.get(doc_id, 0) + 1
        threshold = self._num_rows
        matching = [did for did, cnt in candidate_counts.items() if cnt >= threshold]
        scored = []
        for doc_id in matching:
            stored_fp = self._fingerprints.get(doc_id)
            if stored_fp is not None:
                distance = (fingerprint ^ stored_fp).bit_count()
                similarity = 1.0 - distance / 64.0
                scored.append((doc_id, similarity))
        scored.sort(key=lambda x: -x[1])
        return scored[:max_results]

    def batch_insert(self, items: list[tuple[str, int]]) -> None:
        for doc_id, fp in items:
            self.insert(doc_id, fp)

    def batch_query(self, fingerprints: list[int], max_results: int = 100) -> list[list[tuple[str, float]]]:
        return [self.query(fp, max_results) for fp in fingerprints]

    def clear(self) -> None:
        for table in self._tables:
            table.clear()
        self._fingerprints.clear()

    def cluster_size(self) -> int:
        return len(self._fingerprints)

    def _compute_band_hash(self, fingerprint: int, band_idx: int) -> int:
        import hashlib

        data = f"{fingerprint}:{band_idx}".encode()
        return int(hashlib.sha256(data).hexdigest()[:16], 16)


class _RustLshDomain:
    """Rust-backed LSH domain — delegates to hledac_rust_extensions.lsh_index."""

    __slots__ = ("_ext",)

    def __init__(self, ext: Any) -> None:
        self._ext = ext

    def lsh_index_new(self, num_tables: int = 16, num_rows: int = 4) -> Any:
        return self._ext.lsh_index_new(num_tables=num_tables, num_rows=num_rows)

    def LSHIndex(self, num_tables: int = 16, num_rows: int = 4) -> Any:
        return self._ext.LSHIndex(num_tables=num_tables, num_rows=num_rows)


class _PythonLshDomain:
    """Python fallback LSH domain."""

    __slots__ = ()

    def lsh_index_new(self, num_tables: int = 16, num_rows: int = 4) -> Any:
        return _PythonLshIndex(num_tables=num_tables, num_rows=num_rows)

    def LSHIndex(self, num_tables: int = 16, num_rows: int = 4) -> Any:
        return _PythonLshIndex(num_tables=num_tables, num_rows=num_rows)


def get_lsh_domain(ext: Any | None) -> Any:
    """Return Rust or Python LSH domain based on extension availability."""
    if ext is not None:
        return _RustLshDomain(ext)
    return _PythonLshDomain()
