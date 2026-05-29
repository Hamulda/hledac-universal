"""
SharedTensor – obálka nad mlx.core.array, umožňuje předávání referencí.
Skutečný zero-copy vyžaduje Metal buffer – to je zatím TODO.
"""


# MLX import s fallback
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None


class SharedTensor:
    """
    Obálka pro sdílení MLX tensorů mezi úlohami.
    Poznámka: Skutečný zero-copy vyžaduje Metal shared memory,
    což je TODO pro budoucí implementaci.
    """

    def __init__(self, data):
        """
        Inicializuje SharedTensor s MLX array.

        Args:
            data: MLX array nebo numpy array
        """
        if MLX_AVAILABLE and data is not None:
            if isinstance(data, mx.array):
                self.data = data
            else:
                # Konverze z numpy
                self.data = mx.array(data)
        else:
            self.data = data

        self._ref_count = 1

    @classmethod
    def from_array(cls, arr) -> SharedTensor:
        """Vytvoří SharedTensor z MLX array."""
        return cls(arr)

    @classmethod
    def from_numpy(cls, np_array) -> SharedTensor:
        """Vytvoří SharedTensor z numpy array."""
        if MLX_AVAILABLE:
            return cls(mx.array(np_array))
        else:
            return cls(np_array)

    def to_array(self):
        """Vrátí MLX array."""
        if MLX_AVAILABLE and isinstance(self.data, mx.array):
            return self.data
        return self.data

    def to_numpy(self):
        """Vrátí numpy array (kopie)."""
        if MLX_AVAILABLE and self.data is not None:
            return self.data.tolist()  # MLX .tolist() vrací Python list
        return None

    def size_bytes(self) -> int:
        """Vrátí velikost tensoru v bytech."""
        if MLX_AVAILABLE and self.data is not None:
            return self.data.nbytes
        return 0

    def shape(self):
        """Vrátí tvar tensoru."""
        if MLX_AVAILABLE and self.data is not None:
            return self.data.shape
        return None

    def dtype(self):
        """Vrátí datový typ tensoru."""
        if MLX_AVAILABLE and self.data is not None:
            return self.data.dtype
        return None

    def increment_ref(self):
        """Inkrementuje referenční počítadlo."""
        self._ref_count += 1

    def decrement_ref(self):
        """Dekrementuje referenční počítadlo."""
        self._ref_count -= 1
        return self._ref_count <= 0

    def is_available(self) -> bool:
        """Kontroluje dostupnost MLX."""
        return MLX_AVAILABLE and self.data is not None


def create_shared(embedding) -> SharedTensor:
    """
    Helper funkce pro vytvoření SharedTensor z embedding vektoru.

    Args:
        embedding: list[float] nebo numpy array

    Returns:
        SharedTensor instance
    """
    if MLX_AVAILABLE:
        return SharedTensor(mx.array(embedding, dtype=mx.float32))
    else:
        return SharedTensor(embedding)


def share_between_tasks(tensor: SharedTensor) -> SharedTensor:
    """
    Sdílí tensor mezi úlohami (increments ref count).

    Args:
        tensor: SharedTensor k sdílení

    Returns:
        Same tensor s inkrementovaným ref count
    """
    if tensor:
        tensor.increment_ref()
    return tensor
