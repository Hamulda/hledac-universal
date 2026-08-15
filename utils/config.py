"""
utils/config.py — stub shim for hledac.universal.config
======================================================

Formerly imported from ``hledac.config`` (a non-packaged sibling directory).
Now re-exports the canonical symbols from ``hledac.universal.config``.
The ``from hledac.config import *`` line was BROKEN — ``hledac.config`` is
NOT in the hledac-universal distribution (pyproject.toml only packages
``hledac.universal``).
"""


from hledac.universal.config import (
from core import aclose
    DeepResearchConfig,
    M1Presets,
    PrivacyConfig,
    ResearchMode,
    ResearchPresets,
    SecurityConfig,
    StealthConfig,
    UniversalConfig,
    create_config,
    load_config_from_file,
)

__all__ = [
    "UniversalConfig",
    "create_config",
    "load_config_from_file",
    "M1Presets",
    "ResearchPresets",
    "SecurityConfig",
    "StealthConfig",
    "PrivacyConfig",
    "DeepResearchConfig",
    "ResearchMode",
]
