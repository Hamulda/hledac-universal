"""
Autonomous RL Research Loop
===========================

Iterative research with Q-learning based planning.

M1 Optimized: Async I/O, bounded memory, no heavy ML models.

P17: QTable persistence via LMDB, run_once() returns ResearchResult,
prev_reward integration in hypothesis generation.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

try:
    import orjson
    ORJSON_AVAILABLE = True
except ImportError:
    import json
    ORJSON_AVAILABLE = False

logger = logging.getLogger(__name__)


def _json_dumps(obj: Any) -> bytes:
    """Serialize object to JSON bytes."""
    if ORJSON_AVAILABLE:
        return orjson.dumps(obj)
    return json.dumps(obj).encode('utf-8')


def _json_loads(data) -> Any:
    """Deserialize JSON bytes to object."""
    if data is None:
        return None
    if ORJSON_AVAILABLE:
        try:
            return orjson.loads(data)
        except Exception:
            pass
    try:
        if isinstance(data, bytes):
            return json.loads(data.decode('utf-8'))
        elif isinstance(data, str):
            return json.loads(data)
    except Exception:
        pass
    return None


@dataclass
class ResearchResult:
    """
    P17: Result of a single RL research iteration.

    Attributes:
        findings: List of finding dicts from this iteration
        reward: RL reward score (float 0-1)
        state: Dict with current state info (cycle, findings_count, etc.)
        action: The action taken in this iteration
    """
    findings: list[dict[str, Any]] = field(default_factory=list)
    reward: float = 0.0
    state: dict[str, Any] = field(default_factory=dict)
    action: str = ""


@dataclass
class ResearchState:
    """
    State for Q-learning.

    Attributes:
        query: Current research query
        cycle: Current research cycle (0-indexed)
        findings_count: Number of findings gathered so far
        memory_budget_mb: Remaining memory budget in MB
        tot_used: Whether Tree of Thoughts was used in this cycle
    """
    query: str
    cycle: int = 0
    findings_count: int = 0
    memory_budget_mb: float = 300.0
    tot_used: bool = False


class QTable:
    """
    Simple Q-table for action selection stored in memory.

    Uses Q-learning update rule:
    Q(s,a) = Q(s,a) + alpha * (reward + gamma * max(Q(s',a')) - Q(s,a))

    P17: Supports serialization for LMDB persistence.

    Attributes:
        _table: Dict mapping (state_tuple) -> Dict(action -> q_value)
        _alpha: Learning rate (0.1 default)
        _gamma: Discount factor (0.9 default)
    """

    def __init__(self, alpha: float = 0.1, gamma: float = 0.9):
        self._table: dict[tuple, dict[str, float]] = {}
        self._alpha = alpha
        self._gamma = gamma

    def get_q(self, state: tuple, action: str) -> float:
        """
        Get Q-value for state-action pair.

        Args:
            state: State tuple
            action: Action name

        Returns:
            Q-value (default 0.0 if not seen)
        """
        return self._table.get(state, {}).get(action, 0.0)

    def get_best_action(self, state: tuple, actions: list[str]) -> str:
        """
        Get action with highest Q-value for state.
        P17: When Q-values are equal, selects deterministically by alphabetical order.

        Args:
            state: State tuple
            actions: List of available actions

        Returns:
            Action with highest Q-value (alphabetically first if tie)
        """
        q_values = [(a, self.get_q(state, a)) for a in actions]
        max_q = max(q_values, key=lambda x: x[1])
        best_actions = [a for a, q in q_values if q == max_q[1]]
        # P17: Deterministic tie-break by alphabetical order
        best_actions.sort()
        return best_actions[0]

    def update(self, state: tuple, action: str, reward: float, next_state: tuple) -> None:
        """
        Update Q-value using Q-learning rule.

        Args:
            state: Current state
            action: Action taken
            reward: Reward received
            next_state: Next state
        """
        if state not in self._table:
            self._table[state] = {}

        current_q = self._table[state].get(action, 0.0)

        # Get max Q-value for next state
        next_q_values = list(self._table.get(next_state, {}).values())
        max_next_q = max(next_q_values) if next_q_values else 0.0

        # Q-learning update
        new_q = current_q + self._alpha * (reward + self._gamma * max_next_q - current_q)
        self._table[state][action] = new_q

    def to_dict(self) -> dict[str, Any]:
        """
        P17: Serialize Q-table to dict for LMDB storage.

        Returns:
            Dict with _table (using string keys), _alpha, _gamma
        """
        # Convert tuple keys to strings for JSON serialization
        table_str = {}
        for state_key, action_dict in self._table.items():
            key_str = str(state_key)
            table_str[key_str] = action_dict
        return {
            "_table": table_str,
            "_alpha": self._alpha,
            "_gamma": self._gamma,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QTable:
        """
        P17: Deserialize Q-table from dict loaded from LMDB.

        Args:
            data: Dict from to_dict()

        Returns:
            QTable instance
        """
        qtable = cls(alpha=data.get("_alpha", 0.1), gamma=data.get("_gamma", 0.9))
        table_str = data.get("_table", {})
        # Convert string keys back to tuples
        for key_str, action_dict in table_str.items():
            # Parse the string representation back to tuple
            # Format: "('state', 1, 2, 3)" or similar
            try:
                state_key = eval(key_str)  # Safe here as we control the format
                if isinstance(state_key, tuple):
                    qtable._table[state_key] = action_dict
            except Exception:
                continue
        return qtable


class ResearchLoop:
    """
    Autonomous research loop with RL planning.

    Iterative research that:
    (a) Generates hypotheses via HypothesisEngine
    (b) Decides whether to use Tree of Thoughts (ToT)
    (c) Runs discovery/fetch
    (d) Updates graph/memory
    (e) Evaluates gain (reward = new unique findings)
    (f) Plans next steps via Q-learning

    P17: QTable persisted to LMDB via memory_manager.
          run_once() returns ResearchResult with findings, reward, and state.

    Attributes:
        hypothesis_engine: HypothesisEngine instance for generating hypotheses
        graph: Knowledge graph instance (e.g., IOCGraph)
        duckdb_store: Optional DuckDBShadowStore for persisting findings
        memory_manager: Optional MemoryManager for QTable persistence
        q_table: QTable instance for RL planning
    """

    # Available actions in the research loop
    ACTIONS = [
        "hypothesis_generation",  # Generate new hypotheses
        "tot_reasoning",          # Use Tree of Thoughts reasoning
        "discovery",              # Run discovery phase
        "fetch",                  # Fetch additional data
        "graph_update",           # Update knowledge graph
        "evaluate",               # Evaluate findings
        "done",                   # End research
    ]

    # P17: LMDB key for QTable storage
    QTABLE_LMDB_KEY = "qtable"

    def __init__(
        self,
        hypothesis_engine: Any,
        graph: Any,
        duckdb_store: Any | None = None,
        memory_manager: Any | None = None,
    ):
        """
        Initialize ResearchLoop.

        P17: Loads QTable from LMDB if memory_manager provided.

        Args:
            hypothesis_engine: HypothesisEngine or similar for hypothesis generation
            graph: Knowledge graph instance
            duckdb_store: Optional DuckDBShadowStore for persistence
            memory_manager: Optional MemoryManager for QTable persistence
        """
        self.hypothesis_engine = hypothesis_engine
        self.graph = graph
        self.duckdb_store = duckdb_store
        self.memory_manager = memory_manager
        self.q_table = QTable(alpha=0.1, gamma=0.9)

        # P17: Load QTable from LMDB if memory_manager provided
        if self.memory_manager is not None:
            self._load_qtable()

    def _load_qtable(self) -> None:
        """
        P17: Load QTable from LMDB using key 'qtable'.
        If not found, create empty QTable.
        """
        try:
            import asyncio
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            qtable_data = loop.run_until_complete(
                self.memory_manager.get("global", self.QTABLE_LMDB_KEY)
            )
            if qtable_data is not None:
                self.q_table = QTable.from_dict(qtable_data)
                logger.info("[P17] QTable loaded from LMDB")
            else:
                logger.info("[P17] No QTable found in LMDB, starting fresh")
        except Exception as e:
            logger.warning(f"[P17] Failed to load QTable from LMDB: {e}")

    async def _persist_qtable(self) -> None:
        """
        P17: Save QTable to LMDB after an update.
        """
        if self.memory_manager is None:
            return
        try:
            qtable_dict = self.q_table.to_dict()
            await self.memory_manager.put("global", self.QTABLE_LMDB_KEY, qtable_dict)
            logger.debug("[P17] QTable persisted to LMDB")
        except Exception as e:
            logger.warning(f"[P17] Failed to persist QTable to LMDB: {e}")

    def update_qtable(self, state: tuple, action: str, reward: float, next_state: tuple) -> None:
        """
        P17: Update QTable and persist to LMDB.

        Args:
            state: Current state tuple
            action: Action taken
            reward: Reward received
            next_state: Next state tuple
        """
        self.q_table.update(state, action, reward, next_state)
        # P17: Persist asynchronously
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.create_task(self._persist_qtable())

    async def research_loop(
        self,
        query: str,
        max_cycles: int = 5,
    ) -> dict[str, Any]:
        """
        Run iterative research loop.

        Args:
            query: Research query
            max_cycles: Maximum number of research cycles (default 5)

        Returns:
            Dict with keys:
            - cycles: Number of cycles completed
            - findings_total: Total findings gathered
            - findings_new: New unique findings (after dedup)
            - reward: Final reward score
            - states: List of ResearchState for each cycle
            - actions: List of actions taken each cycle
        """
        logger.info(f"Starting research loop for query: {query}")

        # Initialize state
        state = ResearchState(query=query, cycle=0, findings_count=0)
        seen_findings: set = set()

        cycles_completed = 0
        actions_taken: list[str] = []
        states_history: list[dict] = []

        for cycle in range(max_cycles):
            cycles_completed = cycle + 1
            state.cycle = cycle

            # Get available actions (exclude done until last cycle)
            available_actions = self.ACTIONS.copy()
            if cycle < max_cycles - 1 and "done" in available_actions:
                available_actions.remove("done")

            # Select action using Q-learning
            state_tuple = self._state_to_tuple(state)
            action = self.q_table.get_best_action(state_tuple, available_actions)
            actions_taken.append(action)

            logger.info(f"Cycle {cycle}: Selected action '{action}'")

            # Execute action and get reward
            reward, new_findings = await self._execute_action(
                action, state, query
            )

            # Update seen findings
            new_unique = 0
            for finding in new_findings:
                finding_key = self._fingerprint_finding(finding)
                if finding_key not in seen_findings:
                    seen_findings.add(finding_key)
                    new_unique += 1

            # Update state
            state.findings_count += len(new_findings)

            # Create next state
            next_state = ResearchState(
                query=query,
                cycle=cycle + 1,
                findings_count=state.findings_count,
                memory_budget_mb=state.memory_budget_mb,
                tot_used=(action == "tot_reasoning"),
            )

            # Update Q-table (P17: persist to LMDB)
            self.update_qtable(state_tuple, action, reward, self._state_to_tuple(next_state))

            # Record state history
            states_history.append({
                "cycle": cycle,
                "action": action,
                "reward": reward,
                "findings_this_cycle": len(new_findings),
                "new_unique": new_unique,
                "total_findings": state.findings_count,
            })

            # Update memory budget estimate
            state.memory_budget_mb -= len(new_findings) * 0.01  # ~10KB per finding

            # Check termination conditions
            if action == "done" or state.memory_budget_mb <= 0:
                logger.info(f"Research loop terminated at cycle {cycle}")
                break

            logger.debug(
                f"Cycle {cycle}: action={action}, reward={reward:.3f}, "
                f"findings={len(new_findings)}, total={state.findings_count}"
            )

        # Calculate final reward
        final_reward = self._calculate_final_reward(seen_findings, cycles_completed)

        result = {
            "cycles": cycles_completed,
            "findings_total": len(seen_findings),
            "findings_new": len(seen_findings),
            "reward": final_reward,
            "states": states_history,
            "actions": actions_taken,
        }

        logger.info(
            f"Research loop complete: {cycles_completed} cycles, "
            f"{len(seen_findings)} findings, reward={final_reward:.3f}"
        )

        return result

    async def _execute_action(
        self,
        action: str,
        state: ResearchState,
        query: str,
    ) -> tuple[float, list[dict]]:
        """
        Execute a research action and return reward + findings.

        Args:
            action: Action to execute
            state: Current research state
            query: Original research query

        Returns:
            Tuple of (reward, findings_list)
        """
        findings: list[dict] = []

        try:
            if action == "hypothesis_generation":
                # Generate hypotheses using hypothesis engine
                findings = await self._generate_hypotheses(query)

            elif action == "tot_reasoning":
                # Use Tree of Thoughts reasoning
                findings = await self._tot_reasoning(query)

            elif action == "discovery":
                # Run discovery phase
                findings = await self._run_discovery(query)

            elif action == "fetch":
                # Fetch additional data
                findings = await self._run_fetch(query)

            elif action == "graph_update":
                # Update knowledge graph
                findings = await self._update_graph(query)

            elif action == "evaluate":
                # Evaluate findings
                findings = await self._evaluate_findings(query)

            elif action == "done":
                # No action, just end
                pass

        except Exception as e:
            logger.debug(f"Action '{action}' failed: {e}")

        # Calculate reward based on new findings
        reward = len(findings) * 0.1  # Base reward per finding
        reward += 0.5 if state.tot_used else 0.0  # Bonus for using ToT
        reward -= 0.1 if len(findings) == 0 else 0.0  # Penalty for no findings

        return reward, findings

    async def _generate_hypotheses(self, query: str) -> list[dict]:
        """Generate hypotheses using HypothesisEngine.

        Calls generate_hypotheses_async() when engine has Hermes3 injected.
        Runs attempt_falsification on each candidate; only non-falsified
        hypotheses become research seeds.
        M1 memory bound: max 10 active hypotheses per iteration.
        """
        findings = []

        try:
            if self.hypothesis_engine is not None:
                ctx: dict[str, Any] = {"query": query, "source": "research_loop"}
                engine = self.hypothesis_engine

                # Use async variant if available (supports Hermes3 injection)
                if hasattr(engine, "generate_hypotheses_async"):
                    hyp_strings = await engine.generate_hypotheses_async(
                        context=ctx,
                        hermes_engine=getattr(engine, "_inference_engine", None),
                    )
                elif hasattr(engine, "generate_hypotheses"):
                    import inspect
                    if inspect.iscoroutinefunction(engine.generate_hypotheses):
                        hyp_strings = await engine.generate_hypotheses(ctx)
                    else:
                        hyp_strings = engine.generate_hypotheses(ctx)
                else:
                    hyp_strings = []

                for h_text in hyp_strings[:10]:  # M1 mem bound
                    # Attempt falsification on each candidate
                    falsified = False
                    if hasattr(engine, "attempt_falsification"):
                        try:
                            from dataclasses import dataclass, field
                            @dataclass
                            class _H:
                                hypothesis: str = ""
                                test_results: list = field(default_factory=list)
                                supporting_evidence: list = field(default_factory=list)
                                conflicting_evidence: list = field(default_factory=list)
                            tmp = _H(hypothesis=h_text)
                            result = engine.attempt_falsification(tmp)
                            falsified = getattr(result, "falsified", False)
                        except Exception:
                            pass  # fail-soft: proceed

                    status = "rejected" if falsified else "active"
                    findings.append({
                        "type": "hypothesis",
                        "content": h_text,
                        "source": "hypothesis_engine",
                        "status": status,
                    })
            else:
                # Fallback: keyword-based hypothesis generation
                keywords = query.split()[:5]
                for kw in keywords:
                    findings.append({
                        "type": "hypothesis",
                        "content": f"Explore relationships involving '{kw}'",
                        "source": "keyword_hypothesis",
                        "status": "pending",
                    })
        except Exception as e:
            logger.debug(f"Hypothesis generation failed: {e}")

        return findings

    async def _tot_reasoning(self, query: str) -> list[dict]:
        """
        Tree of Thoughts reasoning.

        Explores multiple reasoning branches and selects the best one.
        """
        findings = []

        try:
            # Simple ToT implementation: explore multiple paths
            paths = [
                f"Direct search: {query}",
                f"Expand query: {query} related",
                f"Narrow query: {query} specific",
                f"Alternative: {query} alternative",
            ]

            for path in paths:
                findings.append({
                    "type": "tot_path",
                    "content": path,
                    "source": "tot_reasoning",
                })

        except Exception as e:
            logger.debug(f"ToT reasoning failed: {e}")

        return findings

    async def _run_discovery(self, query: str) -> list[dict]:
        """Run discovery phase."""
        findings = []

        # Try to use available discovery methods
        # This is a placeholder - actual implementation would use
        # HypothesisEngine or other discovery mechanisms
        findings.append({
            "type": "discovery",
            "content": f"Discovery results for: {query}",
            "source": "discovery_phase",
        })

        return findings

    async def _run_fetch(self, query: str) -> list[dict]:
        """Fetch additional data for query."""
        findings = []

        # Placeholder for fetch implementation
        findings.append({
            "type": "fetch",
            "content": f"Fetched data for: {query}",
            "source": "fetch_phase",
        })

        return findings

    async def _update_graph(self, query: str) -> list[dict]:
        """Update knowledge graph with findings."""
        findings = []

        if self.graph is not None:
            try:
                # Add query as a node
                node_id = f"query:{hash(query)}"
                self.graph.add_node(node_id, node_type="query", query=query)
                findings.append({
                    "type": "graph_update",
                    "content": f"Updated graph with query: {query[:50]}",
                    "source": "graph_update",
                })
            except Exception as e:
                logger.debug(f"Graph update failed: {e}")

        return findings

    async def _evaluate_findings(self, query: str) -> list[dict]:
        """Evaluate and score findings."""
        findings = []

        # Placeholder for evaluation
        findings.append({
            "type": "evaluation",
            "content": f"Evaluation results for: {query}",
            "source": "evaluation_phase",
        })

        return findings

    def _state_to_tuple(self, state: ResearchState) -> tuple:
        """Convert ResearchState to hashable tuple for Q-table."""
        # Discretize continuous values for Q-table
        cycle_bucket = min(state.cycle // 2, 5)  # Bucket cycles 0-9 into 0-5
        findings_bucket = min(state.findings_count // 10, 10)  # Bucket findings
        memory_bucket = min(int(state.memory_budget_mb // 50), 6)  # Bucket memory

        return (
            state.query[:20] if len(state.query) > 20 else state.query,
            cycle_bucket,
            findings_bucket,
            memory_bucket,
            state.tot_used,
        )

    def _fingerprint_finding(self, finding: dict) -> str:
        """Create a fingerprint for deduplication."""
        content = finding.get("content", "")
        finding_type = finding.get("type", "")
        source = finding.get("source", "")

        return f"{finding_type}:{source}:{content[:100]}"

    def _calculate_final_reward(self, findings: set, cycles: int) -> float:
        """
        Calculate final reward score.

        Reward = (unique_findings * 0.1) + (cycles * 0.05) - (complexity_penalty)
        """
        base_reward = len(findings) * 0.1
        cycle_bonus = cycles * 0.05
        # Small penalty for very short research (might be incomplete)
        complexity_penalty = 0.1 if cycles < 2 else 0.0

        return base_reward + cycle_bonus - complexity_penalty

    # P17: Single-run research for CLI --loop flag
    async def run_once(
        self,
        query: str,
    ) -> ResearchResult:
        """
        P17: Run a single RL research iteration.

        This is a simplified version of research_loop() for use when the
        --loop CLI flag is set. It runs one iteration of the research
        process and returns the result as ResearchResult.

        Args:
            query: Research query

        Returns:
            ResearchResult with findings list, reward (0-1), state dict, and action
        """
        logger.info(f"[P17] ResearchLoop.run_once for query: {query}")

        # Initialize state
        state = ResearchState(query=query, cycle=0, findings_count=0)
        seen_findings: set = set()

        # Select best action using Q-learning (e-greedy would go here)
        available_actions = self.ACTIONS.copy()
        if "done" in available_actions:
            available_actions.remove("done")

        state_tuple = self._state_to_tuple(state)
        action = self.q_table.get_best_action(state_tuple, available_actions)

        logger.info(f"[P17] Selected action: '{action}'")

        # Execute action
        reward, new_findings = await self._execute_action(action, state, query)

        # Update seen findings
        new_unique = 0
        for finding in new_findings:
            finding_key = self._fingerprint_finding(finding)
            if finding_key not in seen_findings:
                seen_findings.add(finding_key)
                new_unique += 1

        # Create next state for Q-table update
        next_state = ResearchState(
            query=query,
            cycle=1,
            findings_count=len(seen_findings),
            memory_budget_mb=state.memory_budget_mb - len(new_findings) * 0.01,
            tot_used=(action == "tot_reasoning"),
        )

        # P17: Update QTable and persist to LMDB
        self.update_qtable(state_tuple, action, reward, self._state_to_tuple(next_state))

        # Calculate final reward
        final_reward = self._calculate_final_reward(seen_findings, cycles=1)

        # P17: Return ResearchResult
        result = ResearchResult(
            findings=list(seen_findings),
            reward=final_reward,
            state={
                "cycle": 0,
                "findings_count": len(seen_findings),
                "memory_budget_mb": state.memory_budget_mb,
                "tot_used": action == "tot_reasoning",
            },
            action=action,
        )

        logger.info(
            f"[P17] ResearchLoop.run_once complete: "
            f"{len(seen_findings)} findings, reward={final_reward:.3f}"
        )

        return result


# Convenience function for quick research
async def run_research(
    query: str,
    hypothesis_engine: Any = None,
    graph: Any = None,
    store: Any = None,
    max_cycles: int = 5,
) -> dict[str, Any]:
    """
    Run autonomous research loop.

    Args:
        query: Research query
        hypothesis_engine: Optional HypothesisEngine
        graph: Optional knowledge graph
        store: Optional DuckDBShadowStore
        max_cycles: Maximum research cycles

    Returns:
        Research results dict
    """
    loop = ResearchLoop(
        hypothesis_engine=hypothesis_engine,
        graph=graph,
        duckdb_store=store,
    )
    return await loop.research_loop(query, max_cycles=max_cycles)


# Export
__all__ = [
    "ResearchLoop",
    "ResearchState",
    "ResearchResult",
    "QTable",
    "run_research",
]
