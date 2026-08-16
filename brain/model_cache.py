"""
brain/model_cache.py — Centralized HuggingFace model cache for M1 8GB

Single cache directory: ~/.cache/hledac/models/{model_id}
Size monitoring via uma_budget.py
"""


import asyncio
import logging
import os
from pathlib import Path
from _core import aclose

logger = logging.getLogger(__name__)

# Centralized cache directory — single source of truth
MODEL_CACHE_DIR = Path.home() / ".cache" / "hledac" / "models"

# Default max cache size (M1 8GB — leave room for model + OS)
DEFAULT_MAX_CACHE_SIZE_GB = 4.0

# Guard: never let cache exceed this even if disk is full
HARDCAP_CACHE_SIZE_GB = 8.0


def _get_model_dir(model_id: str) -> Path:
    """Get cache directory for a model. Sanitizes model_id for filesystem."""
    safe_id = model_id.replace("/", "--").replace(":", "_")
    return MODEL_CACHE_DIR / safe_id


def _get_cache_size_bytes() -> int:
    """Get total size of cache directory in bytes."""
    try:
        if not MODEL_CACHE_DIR.exists():
            return 0
        total = 0
        for entry in MODEL_CACHE_DIR.rglob("*"):
            if entry.is_file():
                total += entry.stat().st_size
        return total
    except OSError:
        return 0


def get_cache_size_gb() -> float:
    """Get total cache size in GB."""
    return _get_cache_size_bytes() / (1024**3)


def get_available_disk_gb() -> float:
    """Get available disk space in cache partition."""
    try:
        stat = os.statvfs(MODEL_CACHE_DIR) if MODEL_CACHE_DIR.exists() else os.statvfs(Path.home())
        return stat.f_bavail * stat.f_frsize / (1024**3)
    except OSError:
        return 0.0


async def get_or_download_model(
    model_id: str,
    max_size_gb: float | None = None,
    force_refresh: bool = False,
) -> Path | None:
    """
    Download model if not cached, with size guard.

    Args:
        model_id: HuggingFace model ID (e.g. "mlx-community/DeepHermes-3-...")
        max_size_gb: Max allowed cache size. Defaults to DEFAULT_MAX_CACHE_SIZE_GB.
        force_refresh: Re-download even if cached.

    Returns:
        Path to model directory, or None on failure.

    Raises:
        MemoryError: If download would exceed max_size_gb.
    """

    max_size = max_size_gb or DEFAULT_MAX_CACHE_SIZE_GB
    model_dir = _get_model_dir(model_id)

    # Check if already cached
    if model_dir.exists() and not force_refresh:
        cached_files = list(model_dir.iterdir())
        if cached_files:
            logger.debug(f"[model_cache] {model_id} already cached at {model_dir}")
            return model_dir

    # Check cache size limit
    current_size_gb = get_cache_size_gb()
    if current_size_gb >= min(max_size, HARDCAP_CACHE_SIZE_GB):
        # Try to evict oldest entries (simple: check mtime)
        _evict_oldest_if_needed(max_size)

    # Final size check after potential eviction
    current_size_gb = get_cache_size_gb()
    if current_size_gb >= min(max_size, HARDCAP_CACHE_SIZE_GB):
        logger.warning(
            f"[model_cache] Cache at {current_size_gb:.1f}GB, cannot download {model_id}"
    )
        return None

    # Check disk space (need ~2x model size for temp files)
    available_gb = get_available_disk_gb()
    estimated_model_gb = 2.5  # conservative estimate
    if available_gb < estimated_model_gb:
        logger.warning(
            f"[model_cache] Insufficient disk space: {available_gb:.1f}GB available"
    )
        return None

    # Download via snapshot_download
    try:
        from huggingface_hub import snapshot_download

        logger.info(f"[model_cache] Downloading {model_id}...")
        cache_dir = await asyncio.to_thread(
            _snapshot_download,
            model_id,
            cache_dir=str(MODEL_CACHE_DIR),
    )
        logger.info(f"[model_cache] ✓ {model_id} cached at {cache_dir}")
        return Path(cache_dir)

    except Exception as e:
        logger.error(f"[model_cache] Failed to download {model_id}: {e}")
        return None


def _snapshot_download(
    model_id: str,
    cache_dir: str,
) -> str:
    """Wrapper around snapshot_download for asyncio.to_thread."""
    from huggingface_hub import snapshot_download as _sd

    return _sd(repo_id=model_id, cache_dir=cache_dir)


def _evict_oldest_if_needed(max_size_gb: float) -> None:
    """Evict oldest model directories if cache exceeds max_size."""
    if not MODEL_CACHE_DIR.exists():
        return

    try:
        # Get all model directories with mtime
        model_dirs = []
        for entry in MODEL_CACHE_DIR.iterdir():
            if entry.is_dir():
                mtime = entry.stat().st_mtime
                size = sum(f.stat().st_size for f in entry.rglob("*") if f.is_file())
                model_dirs.append((mtime, size, entry))

        if not model_dirs:
            return

        # Sort by mtime (oldest first)
        model_dirs.sort(key=lambda x: x[0])

        # Evict oldest until under max_size
        current_size = sum(d[1] for d in model_dirs) / (1024**3)
        for _mtime, size, entry in model_dirs:
            if current_size <= max_size_gb * 0.8:  # Target 80% of max
                break
            logger.info(f"[model_cache] Evicting old cache: {entry.name}")
            try:
                import shutil
                shutil.rmtree(entry)
                current_size -= size / (1024**3)
            except OSError:  # noqa: BLE001
                pass

    except Exception as e:
        logger.warning(f"[model_cache] Eviction failed: {e}")


def get_cache_stats() -> dict:
    """Return cache statistics for telemetry."""
    size_gb = get_cache_size_gb()
    disk_gb = get_available_disk_gb()
    models = []
    if MODEL_CACHE_DIR.exists():
        for entry in MODEL_CACHE_DIR.iterdir():
            if entry.is_dir():
                size_mb = sum(
                    f.stat().st_size for f in entry.rglob("*") if f.is_file()
                ) / (1024**2)
                models.append({"id": entry.name, "size_mb": round(size_mb, 1)})

    return {
        "cache_size_gb": round(size_gb, 3),
        "available_disk_gb": round(disk_gb, 2),
        "max_cache_gb": DEFAULT_MAX_CACHE_SIZE_GB,
        "model_count": len(models),
        "models": models,
    }


def clear_cache() -> None:
    """Clear entire model cache. Use with caution."""
    try:
        import shutil

        if MODEL_CACHE_DIR.exists():
            shutil.rmtree(MODEL_CACHE_DIR)
            MODEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            logger.info("[model_cache] Cache cleared")
    except OSError as e:
        logger.warning(f"[model_cache] Failed to clear cache: {e}")
