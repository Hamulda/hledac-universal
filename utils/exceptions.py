"""
Ghost Exceptions - Minimal exception hierarchy for Sprint 6A
=========================================================

Provides typed exceptions for async hygiene compliance.
No feature flags, always-on.
"""

from __future__ import annotations


class GhostBaseException(Exception):
    """Base exception for ghost-related errors."""
    pass


class TransportException(GhostBaseException):
    """Transport/HTTP layer errors."""
    pass


class TimeoutException(GhostBaseException):
    """Timeout errors (async timeout, operation timeout)."""
    pass


class ParseException(GhostBaseException):
    """Parsing/serialization errors."""
    pass


class CheckpointCorruptException(GhostBaseException):
    """Raised when a checkpoint file is corrupted or unreadable."""
    pass


class SprintTimeoutException(GhostBaseException):
    """Raised when a sprint exceeds its allocated time budget."""
    pass


class BootstrapError(GhostBaseException):
    """Raised during early-stage initialization failures."""
    pass


class TeardownError(GhostBaseException):
    """Raised during graceful shutdown / teardown phase."""
    pass


class RuntimeInitError(GhostBaseException):
    """Raised when runtime initialization (event loop, factory) fails."""
    pass


class SignalHandlingError(GhostBaseException):
    """Raised when signal handler setup or invocation fails."""
    pass


class MemoryPressureError(RuntimeError):
    """Raised when system memory pressure exceeds safe thresholds for model loading."""
    pass


__all__ = [
    "GhostBaseException",
    "TransportException",
    "TimeoutException",
    "ParseException",
    "CheckpointCorruptException",
    "SprintTimeoutException",
    "BootstrapError",
    "TeardownError",
    "RuntimeInitError",
    "SignalHandlingError",
    "MemoryPressureError",
]
