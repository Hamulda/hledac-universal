"""
Prefetch package — active modules only.

Archived (2026-07-28): prefetch_oracle, ssm_reranker, prefetch_oracle_integration
→ moved to archive/prefetch_experimental/
"""
from __future__ import annotations

from .budget_tracker import BudgetTracker
from .prefetch_cache import PrefetchCache
from .temporal_predictor import TemporalIOCPredictor
from _core import aclose

__all__ = ['PrefetchCache', 'BudgetTracker', 'TemporalIOCPredictor']
