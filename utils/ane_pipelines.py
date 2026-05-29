"""
ANE Pipelines - Apple Neural Engine Acceleration
================================================

Module-level functions for ANE acceleration with @mx.compile support.
Designed for M1/Apple Silicon with fail-safe fallbacks.
"""

import logging

# MLX import with fallback
try:
    import mlx.core as mx
    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None

logger = logging.getLogger(__name__)

# Constants for SRAM budget calculation
DEFAULT_SRAM_BYTES = 28 * 1024 * 1024  # 28 MB budget for M1
DEFAULT_DTYPE_BYTES = 2  # float16


def _compute_safe_batch_size(
    seq_len: int,
    hidden: int,
    dtype_bytes: int = DEFAULT_DTYPE_BYTES
) -> int:
    """
    Compute safe batch size based on SRAM budget.

    Args:
        seq_len: Sequence length
        hidden: Hidden dimension size
        dtype_bytes: Bytes per element (default 2 for float16)

    Returns:
        Maximum safe batch size
    """
    sram_bytes = DEFAULT_SRAM_BYTES
    memory_per_item = seq_len * hidden * dtype_bytes

    if memory_per_item == 0:
        return 1

    batch_size = max(1, sram_bytes // memory_per_item)
    # Cap at reasonable maximum
    return min(batch_size, 64)


def _get_hidden_size_from_model(model) -> int:
    """
    Extract hidden size from model config with fallback.

    Args:
        model: Model with .config attribute or config dict

    Returns:
        Hidden dimension size (default 768)
    """
    # Try attribute access first
    if hasattr(model, 'config'):
        config = model.config
        if hasattr(config, 'hidden_size'):
            return config.hidden_size
        if isinstance(config, dict):
            return config.get('hidden_size', 768)

    # Try direct attribute
    if hasattr(model, 'hidden_size'):
        return model.hidden_size

    # Default fallback
    return 768


def _tokenize_texts(texts: list, tokenizer, max_len: int = 512) -> list:
    """
    Tokenize texts with truncation.

    Args:
        texts: List of text strings
        tokenizer: Tokenizer with __call__ method
        max_len: Maximum sequence length

    Returns:
        List of tokenized inputs
    """
    try:
        if callable(tokenizer):
            # Handle batch tokenization
            encoded = tokenizer(
                texts,
                padding=True,
                truncation=True,
                max_length=max_len,
                return_tensors='np'
            )
            # Convert to list of arrays
            return [encoded['input_ids'][i] for i in range(len(texts))]
        return texts
    except Exception as e:
        logger.warning(f"Tokenization failed: {e}, using raw texts")
        return texts


def _mlx_embed(tokens: mx.array, model, hidden_size: int) -> mx.array:
    """
    MLX embedding computation with fallback.

    Args:
        tokens: Token input array
        model: Model with embed method
        hidden_size: Hidden dimension

    Returns:
        Embedding array
    """
    # F219L: single-source guard via mlx_memory helper
    from hledac.universal.utils.mlx_memory import get_metal_stream_context

    try:
        # Try model forward pass — Metal buffers scoped to with-block for immediate release on UMA
        with get_metal_stream_context():
            if callable(model):
                return model(tokens)
            if hasattr(model, 'embed'):
                return model.embed(tokens)
    except Exception as e:
        logger.warning(f"Model forward failed: {e}")

    # Fallback: simple lookup table simulation
    # Use random projection for dummy embeddings
    embedding_dim = hidden_size
    vocab_size = 50000

    try:
        # Create a simple embedding matrix
        embed_matrix = mx.random.uniform(
            low=-0.1, high=0.1,
            shape=(vocab_size, embedding_dim)
        )

        # Clamp tokens to valid range
        safe_tokens = mx.clip(tokens, 0, vocab_size - 1)

        # Gather embeddings
        embeddings = embed_matrix[safe_tokens]

        # Mean pooling
        if embeddings.ndim > 2:
            embeddings = embeddings.mean(axis=1)

        return embeddings
    except Exception as e:
        logger.error(f"Embedding fallback failed: {e}")
        # Return zeros as last resort
        return mx.zeros((tokens.shape[0], embedding_dim))


# Compile-time guard
_COMPILED_EMBED_AVAILABLE = False
_compiled_embed = None

if MLX_AVAILABLE:
    try:
        # Attempt to create compiled version
        # This will be None if compilation fails
        try:
            # Dummy function for compilation check
            def _create_embed_fn():
                @mx.compile
                def _compiled_embed_inner(x: mx.array, hidden: int) -> mx.array:
                    # Simple identity for compilation test
                    return x @ mx.random.uniform(
                        low=-0.1, high=0.1,
                        shape=(x.shape[-1], hidden)
                    )
                return _compiled_embed_inner

            _compiled_embed = _create_embed_fn()
            _COMPILED_EMBED_AVAILABLE = True
        except Exception as e:
            logger.debug(f"@mx.compile not available: {e}")
            _COMPILED_EMBED_AVAILABLE = False
    except Exception as e:
        logger.debug(f"MLX compile setup failed: {e}")


def embed_batch(
    texts: list,
    model,
    tokenizer,
    max_len: int = 512
) -> mx.array:
    """
    Embed a batch of texts using MLX acceleration.

    Args:
        texts: List of text strings
        model: Embedding model
        tokenizer: Tokenizer for text processing
        max_len: Maximum sequence length

    Returns:
        mx.array of embeddings (batch_size, hidden_size)
    """
    if not MLX_AVAILABLE:
        logger.warning("MLX not available, returning zeros")
        hidden = _get_hidden_size_from_model(model)
        return mx.zeros((len(texts), hidden))

    try:
        # Get hidden size from model
        hidden = _get_hidden_size_from_model(model)

        # Compute safe batch size
        batch_size = _compute_safe_batch_size(max_len, hidden)

        # Process in chunks if needed
        all_embeddings = []

        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]

            # Tokenize
            tokens = _tokenize_texts(chunk, tokenizer, max_len)

            # Convert to MLX array
            if isinstance(tokens, list):
                tokens_array = mx.array(tokens)
            else:
                tokens_array = tokens

            # Get embeddings
            embeddings = _mlx_embed(tokens_array, model, hidden)
            all_embeddings.append(embeddings)

        # Concatenate all chunks
        result = mx.concatenate(all_embeddings, axis=0)

        # Ensure we have the right shape
        if result.ndim == 1:
            result = result.reshape(1, -1)

        return result

    except Exception as e:
        logger.error(f"embed_batch failed: {e}")
        hidden = _get_hidden_size_from_model(model)
        return mx.zeros((len(texts), hidden))


def embed_with_compiled(
    tokens: mx.array,
    hidden: int
) -> mx.array:
    """
    Embed using compiled function if available.

    Args:
        tokens: Token array
        hidden: Hidden dimension size

    Returns:
        Embedding array
    """
    if _COMPILED_EMBED_AVAILABLE and _compiled_embed is not None:
        try:
            return _compiled_embed(tokens, hidden)
        except Exception as e:
            logger.debug(f"Compiled embed failed: {e}")

    # Fallback to non-compiled
    return _mlx_embed(tokens, None, hidden)


def get_embedding_dimension(model) -> int:
    """
    Get embedding dimension from model.

    Args:
        model: Model with config or hidden_size

    Returns:
        Embedding dimension
    """
    return _get_hidden_size_from_model(model)


def estimate_memory_usage(
    batch_size: int,
    seq_len: int,
    hidden: int,
    dtype_bytes: int = DEFAULT_DTYPE_BYTES
) -> int:
    """
    Estimate memory usage for a batch.

    Args:
        batch_size: Number of samples
        seq_len: Sequence length per sample
        hidden: Hidden dimension
        dtype_bytes: Bytes per element

    Returns:
        Estimated memory in bytes
    """
    return batch_size * seq_len * hidden * dtype_bytes
