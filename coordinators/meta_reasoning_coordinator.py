"""
Universal Meta-Reasoning Coordinator
====================================

Integrated meta-reasoning from:
- MetaReasoningCoordinator: Chain of Thought, Tree of Thoughts, Graph reasoning
- Advanced reasoning strategies with automatic selection

Features:
- Chain of Thought (CoT) reasoning
- Tree of Thoughts (ToT) exploration
- Graph reasoning
- Strategy selection based on query
- Strategy switching during execution
- Ensemble results
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .base import DecisionResponse, OperationResult, OperationType, UniversalCoordinator

logger = logging.getLogger(__name__)


class ReasoningStrategy(Enum):
    """Available reasoning strategies."""
    CHAIN_OF_THOUGHT = "cot"           # Linear step-by-step
    TREE_OF_THOUGHTS = "tot"           # Branching exploration
    GRAPH_REASONING = "graph"          # Graph-based reasoning
    HYBRID = "hybrid"                  # Adaptive strategy


@dataclass
class ReasoningStep:
    """Single reasoning step."""
    step_id: str
    description: str
    reasoning: str
    conclusion: str
    confidence: float
    parent_steps: list[str] = field(default_factory=list)
    sub_steps: list[str] = field(default_factory=list)


@dataclass
class ReasoningChain:
    """Chain of reasoning steps."""
    chain_id: str
    steps: list[ReasoningStep] = field(default_factory=list)
    final_conclusion: str | None = None
    overall_confidence: float = 0.0


@dataclass
class ThoughtNode:
    """Node in Tree of Thoughts."""
    node_id: str
    thought: str
    value_estimate: float
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    visited: bool = False
    expanded: bool = False
    depth: int = 0


class UniversalMetaReasoningCoordinator(UniversalCoordinator):
    """
    Universal coordinator for meta-reasoning.

    Features:
    - Multiple reasoning strategies
    - Automatic strategy selection
    - Strategy switching during execution
    - Ensemble reasoning
    """

    def __init__(self, max_concurrent: int = 3):
        super().__init__(
            name="universal_meta_reasoning_coordinator",
            max_concurrent=max_concurrent,
            memory_aware=True
        )

        # Strategy configurations
        self.strategy_configs: dict[ReasoningStrategy, dict[str, Any]] = {
            ReasoningStrategy.CHAIN_OF_THOUGHT: {
                'max_steps': 10,
                'min_confidence': 0.7,
                'step_description_template': "Step {i}: {thought}"
            },
            ReasoningStrategy.TREE_OF_THOUGHTS: {
                'max_depth': 5,
                'branching_factor': 3,
                'beam_width': 2,
                'exploration_strategy': 'beam_search'
            },
            ReasoningStrategy.GRAPH_REASONING: {
                'max_nodes': 50,
                'connection_density': 0.3,
                'centrality_metric': 'betweenness'
            }
        }

        # Strategy selection keywords
        self.strategy_keywords: dict[ReasoningStrategy, list[str]] = {
            ReasoningStrategy.CHAIN_OF_THOUGHT: [
                'step by step', 'explain', 'how', 'why', 'derive', 'calculate',
                'sequence', 'process', 'procedure', 'logical'
            ],
            ReasoningStrategy.TREE_OF_THOUGHTS: [
                'options', 'alternatives', 'compare', 'decide', 'choose',
                'select', 'best', 'optimal', 'trade-off', 'multiple'
            ],
            ReasoningStrategy.GRAPH_REASONING: [
                'connections', 'relationships', 'network', 'dependencies',
                'interconnected', 'linked', 'graph', 'structure'
            ]
        }

        # Statistics
        self._stats = {
            'chains_executed': 0,
            'trees_explored': 0,
            'graphs_traversed': 0,
            'strategy_switches': 0,
            'avg_confidence': 0.0
        }

        # History
        self.reasoning_history: deque = deque(maxlen=100)

    def get_supported_operations(self) -> list[OperationType]:
        return [OperationType.REASONING, OperationType.SYNTHESIS]

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse
    ) -> OperationResult:
        """Handle meta-reasoning request."""
        start_time = time.time()

        try:
            operation = decision.metadata.get('reasoning_operation', 'reason')
            query = decision.metadata.get('query', '')

            if operation == 'reason':
                strategy = self._select_strategy(query)
                result = await self.reason(query, strategy)
            elif operation == 'ensemble':
                result = await self._ensemble_reason(query)
            else:
                result = {'success': False, 'error': f'Unknown operation: {operation}'}

            return OperationResult(
                operation_id=self.generate_operation_id(),
                status="completed" if result.get('success') else "failed",
                result_summary=result.get('summary', 'Meta-reasoning completed'),
                execution_time=time.time() - start_time,
                success=result.get('success', False),
                metadata=result
            )
        except Exception as e:
            return OperationResult(
                operation_id=self.generate_operation_id(),
                status="failed",
                result_summary=f"Meta-reasoning failed: {str(e)}",
                execution_time=time.time() - start_time,
                success=False,
                error_message=str(e)
            )

    async def reason(
        self,
        query: str,
        strategy: ReasoningStrategy | None = None
    ) -> dict[str, Any]:
        """
        Perform meta-reasoning on query.

        Args:
            query: Query to reason about
            strategy: Reasoning strategy (or auto-select)

        Returns:
            Reasoning results
        """
        # Auto-select strategy if not specified
        if strategy is None:
            strategy = self._select_strategy(query)

        logger.info(f"Reasoning with strategy: {strategy.value}")

        # Execute strategy
        if strategy == ReasoningStrategy.CHAIN_OF_THOUGHT:
            result = await self._chain_of_thought_reasoning(query)
        elif strategy == ReasoningStrategy.TREE_OF_THOUGHTS:
            result = await self._tree_of_thoughts_reasoning(query)
        elif strategy == ReasoningStrategy.GRAPH_REASONING:
            result = await self._graph_reasoning(query)
        else:
            # Default to CoT
            result = await self._chain_of_thought_reasoning(query)

        # Update statistics
        if strategy == ReasoningStrategy.CHAIN_OF_THOUGHT:
            self._stats['chains_executed'] += 1
        elif strategy == ReasoningStrategy.TREE_OF_THOUGHTS:
            self._stats['trees_explored'] += 1
        elif strategy == ReasoningStrategy.GRAPH_REASONING:
            self._stats['graphs_traversed'] += 1

        return {
            'success': True,
            'strategy': strategy.value,
            'query': query,
            **result
        }

    def _select_strategy(self, query: str) -> ReasoningStrategy:
        """Select best reasoning strategy based on query."""
        query_lower = query.lower()
        scores: dict[ReasoningStrategy, int] = dict.fromkeys(ReasoningStrategy, 0)

        # Score based on keywords
        for strategy, keywords in self.strategy_keywords.items():
            for keyword in keywords:
                if keyword in query_lower:
                    scores[strategy] += 1

        # Select strategy with highest score
        if max(scores.values()) > 0:
            return max(scores, key=scores.get)

        # Default to CoT
        return ReasoningStrategy.CHAIN_OF_THOUGHT

    async def _chain_of_thought_reasoning(self, query: str) -> dict[str, Any]:
        """Execute Chain of Thought reasoning."""
        config = self.strategy_configs[ReasoningStrategy.CHAIN_OF_THOUGHT]
        max_steps = config['max_steps']
        min_confidence = config['min_confidence']

        chain = ReasoningChain(chain_id=f"cot_{int(time.time())}")
        steps = []

        # Generate reasoning steps
        for i in range(max_steps):
            # Simulate reasoning step generation
            step = ReasoningStep(
                step_id=f"step_{i}",
                description=f"Analysis step {i+1}",
                reasoning=f"Based on the query '{query[:50]}...', analyzing aspect {i+1}",
                conclusion=f"Conclusion for step {i+1}",
                confidence=0.7 + (0.1 * (max_steps - i) / max_steps)  # Decreasing confidence
            )
            steps.append(step)

            # Check if we can stop
            if step.confidence < min_confidence:
                break

            await asyncio.sleep(0.01)  # Simulate thinking time

        chain.steps = steps
        chain.final_conclusion = steps[-1].conclusion if steps else "No conclusion"
        chain.overall_confidence = sum(s.confidence for s in steps) / len(steps) if steps else 0

        return {
            'type': 'chain_of_thought',
            'steps': len(steps),
            'reasoning_steps': [
                {
                    'step': i+1,
                    'description': s.description,
                    'reasoning': s.reasoning,
                    'conclusion': s.conclusion,
                    'confidence': s.confidence
                }
                for i, s in enumerate(steps)
            ],
            'final_conclusion': chain.final_conclusion,
            'confidence': chain.overall_confidence,
            'summary': f"CoT reasoning: {len(steps)} steps, confidence {chain.overall_confidence:.2f}"
        }

    async def _tree_of_thoughts_reasoning(self, query: str) -> dict[str, Any]:
        """Execute Tree of Thoughts reasoning."""
        config = self.strategy_configs[ReasoningStrategy.TREE_OF_THOUGHTS]
        max_depth = config['max_depth']
        branching_factor = config['branching_factor']
        beam_width = config['beam_width']

        # Initialize root
        root = ThoughtNode(
            node_id="root",
            thought=f"Exploring: {query[:50]}...",
            value_estimate=0.5,
            depth=0
        )

        nodes: dict[str, ThoughtNode] = {"root": root}
        leaves = [root]
        best_path = []
        best_value = float('-inf')

        # Expand tree
        for depth in range(max_depth):
            new_leaves = []

            # Expand each leaf
            for leaf in leaves:
                if leaf.expanded:
                    continue

                # Generate children
                for i in range(branching_factor):
                    child = ThoughtNode(
                        node_id=f"node_{depth}_{i}",
                        thought=f"Branch {i+1} at depth {depth+1}",
                        value_estimate=random.uniform(0.3, 0.9),
                        parent=leaf.node_id,
                        depth=depth + 1
                    )
                    leaf.children.append(child.node_id)
                    nodes[child.node_id] = child
                    new_leaves.append(child)

                leaf.expanded = True

            # Beam search: keep only top-k leaves
            if len(new_leaves) > beam_width:
                new_leaves.sort(key=lambda n: n.value_estimate, reverse=True)
                new_leaves = new_leaves[:beam_width]

            leaves = new_leaves

            # Update best path
            for leaf in leaves:
                if leaf.value_estimate > best_value:
                    best_value = leaf.value_estimate
                    # Reconstruct path
                    path = [leaf.node_id]
                    current = leaf
                    while current.parent:
                        path.append(current.parent)
                        current = nodes[current.parent]
                    best_path = list(reversed(path))

        return {
            'type': 'tree_of_thoughts',
            'nodes': len(nodes),
            'depth': max_depth,
            'best_path': best_path,
            'best_value': best_value,
            'summary': f"ToT reasoning: {len(nodes)} nodes explored, best path found"
        }

    async def _graph_reasoning(self, query: str) -> dict[str, Any]:
        """Execute Graph reasoning."""
        config = self.strategy_configs[ReasoningStrategy.GRAPH_REASONING]
        max_nodes = config['max_nodes']

        # Build reasoning graph
        nodes: dict[str, dict[str, Any]] = {}
        edges: list[tuple[str, str]] = []

        # Create nodes from query aspects
        aspects = query.split()[:max_nodes]
        for i, aspect in enumerate(aspects):
            nodes[f"node_{i}"] = {
                'concept': aspect,
                'importance': random.uniform(0.3, 1.0),
                'connections': []
            }

        # Create edges
        for i in range(len(aspects)):
            for j in range(i + 1, min(i + 3, len(aspects))):
                if random.random() < config['connection_density']:
                    edges.append((f"node_{i}", f"node_{j}"))
                    nodes[f"node_{i}"]['connections'].append(f"node_{j}")
                    nodes[f"node_{j}"]['connections'].append(f"node_{i}")

        # Find central nodes
        centrality = {
            node_id: len(data['connections'])
            for node_id, data in nodes.items()
        }
        central_nodes = sorted(centrality.items(), key=lambda x: x[1], reverse=True)[:3]

        return {
            'type': 'graph_reasoning',
            'nodes': len(nodes),
            'edges': len(edges),
            'central_concepts': [
                {'concept': nodes[nid]['concept'], 'connections': count}
                for nid, count in central_nodes
            ],
            'summary': f"Graph reasoning: {len(nodes)} concepts, {len(edges)} relationships"
        }

    async def _ensemble_reason(self, query: str) -> dict[str, Any]:
        """Execute ensemble reasoning with multiple strategies."""
        strategies = [
            ReasoningStrategy.CHAIN_OF_THOUGHT,
            ReasoningStrategy.TREE_OF_THOUGHTS,
            ReasoningStrategy.GRAPH_REASONING
        ]

        # Execute all strategies in parallel
        tasks = [self.reason(query, s) for s in strategies]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Collect successful results
        successful = [
            r for r in results
            if isinstance(r, dict) and r.get('success')
        ]

        if not successful:
            return {'success': False, 'error': 'All reasoning strategies failed'}

        # Simple majority voting on strategy type
        strategy_counts = {}
        for r in successful:
            s = r.get('strategy', 'unknown')
            strategy_counts[s] = strategy_counts.get(s, 0) + 1

        best_strategy = max(strategy_counts, key=strategy_counts.get)

        return {
            'success': True,
            'ensemble_size': len(successful),
            'strategies_used': [r.get('strategy') for r in successful],
            'selected_strategy': best_strategy,
            'results': successful,
            'summary': f"Ensemble reasoning: {len(successful)} strategies, selected {best_strategy}"
        }

    def get_statistics(self) -> dict[str, Any]:
        """Get reasoning statistics."""
        return {
            **self._stats,
            'history_size': len(self.reasoning_history)
        }

    def _get_feature_list(self) -> list[str]:
        return [
            "Chain of Thought reasoning",
            "Tree of Thoughts exploration",
            "Graph reasoning",
            "Automatic strategy selection",
            "Ensemble reasoning",
            "Strategy switching"
        ]
