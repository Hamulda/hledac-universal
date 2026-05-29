"""
Tool Registry — Pure Registration & Discovery.

This module is the CANONICAL registration authority for tools.
All tool registration MUST go through this surface.

Boundary seams:
- register()/unregister() for registration
- get_tool(), list_tools(), has_tool() for discovery
- estimate_plan_cost() for cost planning

DO NOT:
- Add execution logic here — use tools/executor.py
- Add async patterns here — keep this synchronous for testability
- Add audit/logging here — use ToolExecLog for that
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import StrEnum
from typing import TYPE_CHECKING, Any

import msgspec
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    pass


# ============================================================================
# Enums
# ============================================================================


class RiskLevel(StrEnum):
    """Risk levels for tool execution."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# ============================================================================
# Cost Model
# ============================================================================


class CostModel(BaseModel):
    """Cost model for tool execution planning and resource management."""

    ram_mb_est: int = Field(default=100, description="Estimated RAM usage in MB")
    time_ms_est: int = Field(default=1000, description="Estimated execution time in milliseconds")
    network: bool = Field(default=False, description="Whether tool requires network access")
    network_cost: int = Field(default=0, description="Network cost tier: 0=none, 1=light, 2=heavy")
    risk_level: RiskLevel = Field(default=RiskLevel.LOW, description="Risk level for sandboxing decisions")

    def to_hermes_hint(self) -> dict[str, Any]:
        """Convert to compact hint for Hermes LLM."""
        return {
            "ram_mb": self.ram_mb_est,
            "time_ms": self.time_ms_est,
            "network": self.network,
            "network_cost": self.network_cost,
            "risk": self.risk_level.value,
        }


class CostSummary(msgspec.Struct):
    """Summary of estimated costs for a plan."""

    total_ram_mb: int = 0
    total_time_ms: int = 0
    total_network_calls: int = 0
    total_network_cost: int = 0
    high_risk_count: int = 0

    def can_fit(self, budget: BudgetLimits) -> bool:
        """Check if costs fit within budget."""
        if self.total_ram_mb > budget.max_ram_mb:
            return False
        if self.total_time_ms > budget.max_time_ms:
            return False
        if self.total_network_calls > budget.max_network_calls:
            return False
        return True


class BudgetLimits(msgspec.Struct):
    """Budget limits for execution."""

    max_ram_mb: int = 2048  # 2GB default
    max_time_ms: int = 300000  # 5 minutes default
    max_network_calls: int = 50
    max_snapshot_writes: int = 20


class SourceReputation(msgspec.Struct):
    """Source reliability scoring from own data."""

    domain: str
    path_prefix: str | None = None

    total_claims: int = 0
    corroborated_count: int = 0
    contested_count: int = 0
    drift_count: int = 0
    blocked_count: int = 0

    corroboration_rate: float = 0.0
    contested_rate: float = 0.0
    drift_rate: float = 0.0
    blocked_rate: float = 0.0
    overall_score: float = 0.5
    last_updated: str | None = None

    def compute_rates(self) -> None:
        """Compute rates from counts, handling division by zero."""
        if self.total_claims > 0:
            self.corroboration_rate = self.corroborated_count / self.total_claims
        else:
            self.corroboration_rate = 0.5

        if self.total_claims > 0:
            self.contested_rate = self.contested_count / self.total_claims
        else:
            self.contested_rate = 0.0

        if self.total_claims > 0:
            self.drift_rate = self.drift_count / self.total_claims
        else:
            self.drift_rate = 0.0

        if self.total_claims > 0:
            self.blocked_rate = self.blocked_count / self.total_claims
        else:
            self.blocked_rate = 0.0

        self.overall_score = max(0.0, min(1.0,
            0.45 * self.corroboration_rate
            - 0.25 * self.contested_rate
            - 0.15 * self.drift_rate
            - 0.15 * self.blocked_rate
        ))

    def to_dict(self) -> dict:
        """Return dict for serialization."""
        return {
            "domain": self.domain,
            "path_prefix": self.path_prefix,
            "corroboration_rate": round(self.corroboration_rate, 3),
            "contested_rate": round(self.contested_rate, 3),
            "drift_rate": round(self.drift_rate, 3),
            "blocked_rate": round(self.blocked_rate, 3),
            "overall_score": round(self.overall_score, 3),
            "total_claims": self.total_claims,
            "last_updated": self.last_updated
        }


# ============================================================================
# Rate Limits
# ============================================================================


class RateLimits(BaseModel):
    """Rate limiting configuration for tools."""

    max_calls_per_run: int = Field(default=100, description="Maximum calls per agent run")
    max_parallel: int = Field(default=1, description="Maximum parallel executions")

    def to_hermes_hint(self) -> dict[str, Any]:
        """Convert to compact hint for Hermes LLM."""
        return {
            "max_calls": self.max_calls_per_run,
            "parallel": self.max_parallel,
        }


# ============================================================================
# Tool Definition
# ============================================================================


class Tool(BaseModel):
    """
    Tool definition with schemas, cost model, and handler.

    CANONICAL EXECUTION-CONTROL SURFACE (Sprint 8VF):
    ══════════════════════════════════════════════════
    execute_with_limits() gates on required_capabilities when
    available_capabilities is explicitly provided.
    """

    model_config = {"arbitrary_types_allowed": True}

    name: str = Field(description="Unique tool identifier")
    description: str = Field(description="Description for Hermes LLM")
    args_schema: type[BaseModel] = Field(description="Pydantic model for arguments")
    returns_schema: type[BaseModel] = Field(description="Pydantic model for return value")
    cost_model: CostModel = Field(default_factory=CostModel)
    rate_limits: RateLimits = Field(default_factory=RateLimits)
    handler: Callable[..., Any] = Field(description="Tool implementation")

    required_capabilities: set[str] = Field(
        default_factory=set,
        description="Capabilities required for this tool"
    )

    def to_tool_card(self) -> dict[str, Any]:
        """Generate tool card for Hermes LLM consumption."""
        return {
            "name": self.name,
            "description": self.description,
            "args_schema": self.args_schema.model_json_schema(),
            "returns_schema": self.returns_schema.model_json_schema(),
            "cost_hints": self.cost_model.to_hermes_hint(),
            "rate_limits": self.rate_limits.to_hermes_hint(),
        }

    def validate_args(self, args: dict[str, Any]) -> BaseModel:
        """Validate arguments against schema."""
        return self.args_schema(**args)


# ============================================================================
# Tool Registry — Pure Registration & Discovery
# ============================================================================


class ToolRegistry:
    """
    Central registry for tools with validation and discovery.

    CANONICAL REGISTRATION AUTHORITY:
    ═══════════════════════════════════
    This class is the CANONICAL authority for tool registration
    and discovery. All tool registration MUST go through this surface.

    Boundary seams:
    - register()/unregister() for registration
    - get_tool(), list_tools(), has_tool() for discovery
    - estimate_plan_cost() for cost planning

    Execution has been moved to tools/executor.py for testability.
    """

    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._call_counts: dict[str, int] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    # -------------------------------------------------------------------------
    # Registration
    # -------------------------------------------------------------------------

    def register(self, tool: Tool) -> None:
        """Register a tool.

        Args:
            tool: Tool definition to register

        Raises:
            ValueError: If tool with same name already exists
        """
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' already registered")

        self._tools[tool.name] = tool
        self._call_counts[tool.name] = 0
        self._semaphores[tool.name] = asyncio.Semaphore(tool.rate_limits.max_parallel)

    def unregister(self, name: str) -> None:
        """Unregister a tool.

        Args:
            name: Tool name to unregister
        """
        self._tools.pop(name, None)
        self._call_counts.pop(name, None)
        self._semaphores.pop(name, None)

    # -------------------------------------------------------------------------
    # Discovery
    # -------------------------------------------------------------------------

    def get_tool(self, name: str) -> Tool:
        """Get tool by name.

        Args:
            name: Tool name

        Returns:
            Tool definition

        Raises:
            KeyError: If tool not found
        """
        if name not in self._tools:
            raise KeyError(f"Tool '{name}' not found")
        return self._tools[name]

    def list_tools(self) -> list[Tool]:
        """List all registered tools."""
        return list(self._tools.values())

    def has_tool(self, name: str) -> bool:
        """Check if tool is registered."""
        return name in self._tools

    def estimate_plan_cost(self, tool_names: list[str]) -> CostSummary:
        """Estimate total cost for a plan (list of tool names)."""
        summary = CostSummary()

        for name in tool_names:
            if name in self._tools:
                tool = self._tools[name]
                cost = tool.cost_model
                summary.total_ram_mb += cost.ram_mb_est
                summary.total_time_ms += cost.time_ms_est
                if cost.network:
                    summary.total_network_calls += 1
                summary.total_network_cost += cost.network_cost
                if cost.risk_level == RiskLevel.HIGH:
                    summary.high_risk_count += 1

        return summary

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def validate_args(self, tool_name: str, args: dict[str, Any]) -> bool:
        """Validate arguments for a tool."""
        tool = self.get_tool(tool_name)
        tool.validate_args(args)
        return True

    def validate_call(self, tool_name: str) -> tuple[bool, str | None]:
        """Check if tool call is allowed based on rate limits."""
        try:
            tool = self.get_tool(tool_name)
        except KeyError:
            return False, f"Tool '{tool_name}' not found"

        current = self._call_counts.get(tool_name, 0)
        if current >= tool.rate_limits.max_calls_per_run:
            return False, f"Rate limit exceeded: {current}/{tool.rate_limits.max_calls_per_run}"

        return True, None

    # -------------------------------------------------------------------------
    # Hermes Integration
    # -------------------------------------------------------------------------

    def get_tool_cards_for_hermes(self) -> list[dict[str, Any]]:
        """Get tool cards formatted for Hermes LLM."""
        return [tool.to_tool_card() for tool in self._tools.values()]

    def get_tools_by_risk(self, max_risk: RiskLevel) -> list[Tool]:
        """Get tools filtered by maximum risk level."""
        risk_order = {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}
        max_level = risk_order[max_risk]
        return [
            tool for tool in self._tools.values()
            if risk_order[tool.cost_model.risk_level] <= max_level
        ]

    def get_network_tools(self) -> list[Tool]:
        """Get all tools requiring network access."""
        return [
            tool for tool in self._tools.values()
            if tool.cost_model.network
        ]

    def get_high_memory_tools(self, threshold_mb: int = 500) -> list[Tool]:
        """Get tools with high memory requirements."""
        return [
            tool for tool in self._tools.values()
            if tool.cost_model.ram_mb_est >= threshold_mb
        ]

    # -------------------------------------------------------------------------
    # Capability Checking
    # -------------------------------------------------------------------------

    def check_capabilities(self, tool_name: str, available_caps: set[str]) -> tuple[bool, str | None]:
        """Check if required capabilities are satisfied for tool execution."""
        tool = self.get_tool(tool_name)
        required = tool.required_capabilities

        if not required:
            return True, None

        missing = required - available_caps
        if missing:
            return False, f"Tool '{tool_name}' requires capabilities {missing}, available={available_caps}"

        return True, None

    # -------------------------------------------------------------------------
    # Internal State Access (for executor)
    # -------------------------------------------------------------------------

    def _get_call_count(self, tool_name: str) -> int:
        """Get current call count for a tool."""
        return self._call_counts.get(tool_name, 0)

    def _increment_call_count(self, tool_name: str) -> None:
        """Increment call count for a tool."""
        self._call_counts[tool_name] = self._call_counts.get(tool_name, 0) + 1

    def _get_semaphore(self, tool_name: str) -> asyncio.Semaphore:
        """Get semaphore for a tool."""
        return self._semaphores[tool_name]

    def reset_counters(self) -> None:
        """Reset call counters for a new run."""
        for name in self._call_counts:
            self._call_counts[name] = 0



__all__ = [
    "ToolRegistry",
    "Tool",
    "CostModel",
    "CostSummary",
    "BudgetLimits",
    "RateLimits",
    "RiskLevel",
    "SourceReputation",
]
