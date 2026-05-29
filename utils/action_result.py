# hledac/universal/utils/action_result.py
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ActionResult:
    """Unified result from any research action."""
    success: bool = False
    findings: list[Any] = field(default_factory=list)   # ResearchFinding objekty
    sources: list[Any] = field(default_factory=list)    # ResearchSource objekty
    hypotheses: list[Any] = field(default_factory=list) # Hypothesis objekty
    contradictions: list[Any] = field(default_factory=list) # Contradiction objekty
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
