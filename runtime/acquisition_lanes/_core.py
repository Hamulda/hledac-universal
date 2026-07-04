"""
Acquisition Lanes — Core Structures (_core.py).

Lane planning core: AcquisitionLanePlan, AcquisitionContext, RiskLevel,
LaneSpec, LaneRule, and helper functions.

This module is extracted from acquisition_strategy.py (5713 lines) as part of
Issue #5 god-object split. The canonical source remains acquisition_strategy.py
until full migration is complete.

Lane architecture:
  FEED, PUBLIC, CT, WAYBACK, PASSIVE_DNS, BLOCKCHAIN, STEALTH, PIVOT_EXECUTOR
"""
from __future__ import annotations

import msgspec
from dataclasses import dataclass, field
from enum import StrEnum

# Re-export from acquisition_strategy.py for now (until full extraction)
from hledac.universal.runtime.acquisition_strategy import (
    AcquisitionLane,
    RiskLevel,
    AcquisitionLanePlan,
    AcquisitionContext,
    LaneSpec,
    LaneRule,
    _lc,
    _lane_rule,
    _disabled_reason,
    AcquisitionProfile,
    normalize_acquisition_profile,
)

__all__ = [
    'AcquisitionLane',
    'RiskLevel',
    'AcquisitionLanePlan',
    'AcquisitionContext',
    'LaneSpec',
    'LaneRule',
    'AcquisitionProfile',
    '_lc',
    '_lane_rule',
    '_disabled_reason',
    'normalize_acquisition_profile',
]
