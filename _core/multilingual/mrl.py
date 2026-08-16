"""
Matryoshka Representation Learning (MRL) truncation utilities.

MRL allows truncating high-dimensional embeddings (e.g., 1024d BGE-M3)

to lower dimensions (e.g., 256d) with minimal quality loss.
This enables storing multilingual embeddings in the same USEARCH index
as monolingual English embeddings.

Based on: Kusupati et al., "Matryoshka Representation Learning" (NeurIPS 2022)
https://arxiv.org/abs/2205.13147

Author: Hledac Team
Issue: [SWARM]-002
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from _core._util import aclose

logger = logging.getLogger(__name__)

# MRL dimension ladder (compatible with common embedding dimensions)
# Order matters: truncated dimensions should be prefixes of full dimensions
MRL_DIMENSIONS = (
    8,     # Tiny (for fast approximate matching)
    16,    # Very small
    32,    # Small
    64,    # Medium-small
    128,   # Medium
    256,   # Standard (matches existing index dimension)
    384,   # BGE-small-en native
    512,   # Half
    768,   # ModernBERT native
    1024,  # BGE-M3 native (multilingual)
    )


class MRLTruncator:
    """
    Matryoshka Representation Learning truncation for embedding dimensionality reduction.
    
    MRL encodes information hierarchically:
    - First N dimensions capture most semantic information
    - Each additional layer adds finer details
    - Truncating to lower dimensions preserves approximate similarity
    
    For cross-lingual search:
    - BGE-M3 1024d → truncate to 256d for USEARCH index compatibility
    - Cross-lingual vectors map to same neighborhood regardless of source language
    - Quality loss is minimal for retrieval tasks (<5% MRR drop)
    """
    
    def __init__(
        self,
        source_dim: int = 1024,
        target_dim: int = 256,
        normalize: bool = True
    ):
        """
        Initialize MRL truncator.
        
        Args:
            source_dim: Original embedding dimension (e.g., 1024 for BGE-M3).
            target_dim: Target dimension after truncation (e.g., 256 for USEARCH).
            normalize: L2-normalize after truncation.
        """
        if target_dim > source_dim:
            raise ValueError(f'target_dim ({target_dim}) cannot exceed source_dim ({source_dim})')
        
        self._source_dim = source_dim
        self._target_dim = target_dim
        self._normalize = normalize
        
        # Validate target_dim is in MRL ladder or at least <= source_dim
        if target_dim not in MRL_DIMENSIONS and target_dim > source_dim:
            logger.warning(
                f'target_dim {target_dim} not in MRL_DIMENSIONS ladder. '
                f'Using anyway but consider using: {[d for d in MRL_DIMENSIONS if d <= source_dim]}'
    )
    
    @property
    def source_dim(self) -> int:
        """Original embedding dimension."""
        return self._source_dim
    
    @property
    def target_dim(self) -> int:
        """Target embedding dimension after truncation."""
        return self._target_dim
    
    def truncate(self, embedding: np.ndarray) -> np.ndarray:
        """
        Truncate embedding to target dimension using MRL.
        
        MRL property: First target_dim dimensions contain most semantic information.
        Simply taking the prefix preserves approximate semantic relationships.
        
        Args:
            embedding: Full-dimensional embedding array (source_dim,).
            
        Returns:
            Truncated embedding array (target_dim,).
        """
        if embedding.shape[-1] != self._source_dim:
            raise ValueError(
                f'Expected embedding dim {self._source_dim}, got {embedding.shape[-1]}'
    )
        
        # Truncate to target_dim (prefix of MRL representation)
        truncated = embedding[..., :self._target_dim]
        
        if self._normalize:
            truncated = self._l2_normalize(truncated)
        
        return truncated
    
    def truncate_batch(self, embeddings: np.ndarray) -> np.ndarray:
        """
        Truncate batch of embeddings to target dimension.
        
        Args:
            embeddings: Batch of embeddings with shape (batch, source_dim).
            
        Returns:
            Truncated embeddings with shape (batch, target_dim).
        """
        if embeddings.ndim == 1:
            return self.truncate(embeddings)
        
        if embeddings.shape[-1] != self._source_dim:
            raise ValueError(
                f'Expected embedding dim {self._source_dim}, got {embeddings.shape[-1]}'
    )
        
        # Truncate along last dimension
        truncated = embeddings[..., :self._target_dim]
        
        if self._normalize:
            truncated = self._l2_normalize_batch(truncated)
        
        return truncated
    
    @staticmethod
    def _l2_normalize(embedding: np.ndarray) -> np.ndarray:
        """L2-normalize single embedding."""
        norm = np.linalg.norm(embedding)
        if norm < 1e-10:
            return embedding
        return embedding / norm
    
    @staticmethod
    def _l2_normalize_batch(embeddings: np.ndarray) -> np.ndarray:
        """L2-normalize batch of embeddings along last axis."""
        norms = np.linalg.norm(embeddings, axis=-1, keepdims=True)
        # Avoid division by zero
        norms = np.where(norms < 1e-10, 1.0, norms)
        return embeddings / norms
    
    @staticmethod
    def find_closest_mrl_dim(target_dim: int, source_dim: int) -> int:
        """
        Find the closest MRL dimension <= target_dim.
        
        Args:
            target_dim: Desired target dimension.
            source_dim: Source embedding dimension.
            
        Returns:
            Closest MRL dimension that fits.
        """
        valid_dims = [d for d in MRL_DIMENSIONS if d <= source_dim]
        if not valid_dims:
            return source_dim
        
        return min(valid_dims, key=lambda x: abs(x - target_dim))
    
    def create_multi_level_index(
        self,
        embeddings: np.ndarray,
        dimensions: Optional[list[int]] = None
    ) -> dict[int, np.ndarray]:
        """
        Create multi-level index at different MRL dimensions.
        
        Useful for hierarchical retrieval:
        1. Fast coarse search at 8d/16d
        2. Refine with 64d/128d
        3. Final ranking at 256d
        
        Args:
            embeddings: Full embeddings (N, source_dim).
            dimensions: List of target dimensions. Defaults to [64, 128, 256].
            
        Returns:
            Dict mapping dimension -> truncated embeddings.
        """
        if dimensions is None:
            dimensions = [64, 128, self._target_dim]
        
        dimensions = [min(d, self._source_dim) for d in dimensions]
        dimensions = sorted(set(dimensions))  # Remove duplicates, sort
        
        result = {}
        for dim in dimensions:
            truncator = MRLTruncator(
                source_dim=self._source_dim,
                target_dim=dim,
                normalize=self._normalize
    )
            result[dim] = truncator.truncate_batch(embeddings)
        
        return result


def truncate_embedding(
    embedding: np.ndarray,
    target_dim: int,
    normalize: bool = True
) -> np.ndarray:
    """
    Convenience function for MRL truncation.
    
    Args:
        embedding: Full embedding vector.
        target_dim: Target dimension.
        normalize: L2-normalize result.
        
    Returns:
        Truncated embedding.
    """
    source_dim = embedding.shape[-1]
    truncator = MRLTruncator(source_dim=source_dim, target_dim=target_dim, normalize=normalize)
    return truncator.truncate(embedding)


def truncate_batch(
    embeddings: np.ndarray,
    target_dim: int,
    normalize: bool = True
) -> np.ndarray:
    """
    Convenience function for batch MRL truncation.
    
    Args:
        embeddings: Batch of embeddings (N, source_dim).
        target_dim: Target dimension.
        normalize: L2-normalize result.
        
    Returns:
        Truncated embeddings (N, target_dim).
    """
    source_dim = embeddings.shape[-1]
    truncator = MRLTruncator(source_dim=source_dim, target_dim=target_dim, normalize=normalize)
    return truncator.truncate_batch(embeddings)
