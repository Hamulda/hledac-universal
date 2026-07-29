"""
PrefetchOracle — ARCHIVED (2026-07-28).

Moved to: archive/prefetch_experimental/prefetch_oracle.py

Reason: ~140MB dead experimental code (bandit arms 512×131×131 float64),
no production call sites, placeholder reranker, no signal.
On resurrection: float32 arms + MLX-core arrays for ANE offload,
or replace with simple LRU (PrefetchCache already exists).
"""
from __future__ import annotations

# Stub — module archived, do not use in production
__all__: list[str] = []
