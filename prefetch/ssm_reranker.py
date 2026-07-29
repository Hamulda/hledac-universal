"""
SSMReranker — ARCHIVED (2026-07-28).

Moved to: archive/prefetch_experimental/ssm_reranker.py

Reason: placeholder reranker returning {'success': False},
no production use. Bandit arms 512×131×131 float64 ≈ 140MB resident
with no signal (numpy random embeddings — pure noise).
"""
from __future__ import annotations

# Stub — module archived, do not use in production
__all__: list[str] = []
