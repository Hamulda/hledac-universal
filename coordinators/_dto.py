"""
Coordinator DTOs — Data Transfer Objects
=======================================





Extracted from base.py (F320-5) for better separation of concerns.
These DTOs are used across multiple coordinators.

Canonical import:
    from hledac.universal.coordinators._dto import DecisionResponse, OperationResult, CoordinatorCapabilities, OperationType
"""
from __future__ import annotations

import time
from dataclasses import field
from enum import Enum, auto
from typing import Any

import msgspec
from hledac.universal.compat.msgspec_gc_compat import Struct


class OperationType(Enum):
    """Universal operation types supported by coordinators."""
    RESEARCH = auto()
    EXECUTION = auto()
    SECURITY = auto()
    MONITORING = auto()
    SYNTHESIS = auto()
    OPTIMIZATION = auto()


class DecisionResponse(Struct):
    """Decision from orchestrator to be executed by coordinator."""
    decision_id: str
    chosen_option: str
    confidence: float
    reasoning: str
    estimated_duration: float = 0.0
    priority: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)


class OperationResult(Struct, frozen=True):
    """Result of coordinator operation execution."""
    operation_id: str
    status: str
    result_summary: str
    execution_time: float
    success: bool
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class ExecutionResult(Struct):
    """
    Intermediate result from _do_execute_decision().

    Used by the handle_request template method to construct OperationResult.
    All fields except 'success' are optional — template fills in the rest.
    """
    success: bool
    status: str = 'completed'
    result_summary: str = ''
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class CoordinatorCapabilities(Struct, frozen=True):
    """Capabilities reported by a coordinator."""
    name: str
    supported_operations: list[OperationType]
    features: list[str]
    is_available: bool
    load_factor: float
    max_concurrent: int
    current_operations: int


__all__ = [
    'OperationType',
    'DecisionResponse',
    'OperationResult',
    'ExecutionResult',
    'CoordinatorCapabilities',
]
