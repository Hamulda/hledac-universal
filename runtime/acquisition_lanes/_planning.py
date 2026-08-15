"""
Acquisition Lanes — Planning (_planning.py).

Acquisition plan building: build_acquisition_plan, _build_plan_impl,
lane query shapers, and CT/P DNS helpers.

This module is extracted from acquisition_strategy.py (5713 lines) as part of
Issue #5 god-object split. The canonical source remains acquisition_strategy.py
until full migration is complete.
"""

# Re-export from acquisition_strategy.py for now (until full extraction)
from hledac.universal.runtime.acquisition_strategy import (
from core import aclose
    build_acquisition_plan,
    build_acquisition_report,
    get_lane_plan,
    is_lane_enabled,
    lane_skip_reason,
    normalize_source_family_name,
    normalize_source_family_outcome,
    normalize_terminal_state,
    terminality_report,
    SourceFamilyOutcome,
    MandatoryLaneTerminality,
    AcquisitionStrategySnapshot,
    AcquisitionLaneOutcome,
)

__all__ = [
    'build_acquisition_plan',
    'build_acquisition_report',
    'get_lane_plan',
    'is_lane_enabled',
    'lane_skip_reason',
    'normalize_source_family_name',
    'normalize_source_family_outcome',
    'normalize_terminal_state',
    'terminality_report',
    'SourceFamilyOutcome',
    'MandatoryLaneTerminality',
    'AcquisitionStrategySnapshot',
    'AcquisitionLaneOutcome',
]
