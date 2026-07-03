"""
Reinforcement Learning module for Hledac OSINT Orchestrator.
"""
from __future__ import annotations


# Phase 0 alias: register `rl` as a top-level module so absolute
# `from rl.X` imports resolve regardless of how the package is launched.
# See __main__.py Phase 0 hook for original symptom; canonical fix.
import sys as _sys

_sys.modules.setdefault('rl', _sys.modules[__name__])

from rl.actions import (  # noqa: E402
    ACTION_CONTINUE,
    ACTION_DIM,
    ACTION_FETCH_MORE,
    ACTION_NAMES,
)
from rl.qmix import QMIXAgent, QMixer, QMIXJointTrainer, QNetwork  # noqa: E402
from rl.replay_buffer import MARLReplayBuffer  # noqa: E402
from rl.sprint_policy_manager import SprintPolicyManager  # noqa: E402
from rl.state_extractor import StateExtractor  # noqa: E402

__all__ = [
    "ACTION_NAMES",
    "ACTION_DIM",
    "ACTION_FETCH_MORE",
    "ACTION_CONTINUE",
    "QMIXAgent",
    "QMixer",
    "QMIXJointTrainer",
    "QNetwork",
    "MARLReplayBuffer",
    "StateExtractor",
    "SprintPolicyManager",
]
