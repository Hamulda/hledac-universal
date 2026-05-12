"""
Universal Swarm Coordinator
===========================

Integrated swarm intelligence from:
- SwarmCoordinator: Swarm state management, adaptive strategies
- SelfOrganizingCoordinator: Tree of agents, hourglass balancing

Features:
- Swarm state tracking (exploring, exploiting, converged, stagnant)
- Adaptive strategy execution
- Swarm metrics (diversity, convergence, progress, efficiency)
- Multi-agent coordination
- Fault tolerance
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

try:
    import numpy as np
    from numpy.typing import NDArray
    HAS_NUMPY = True
except ImportError:
    np = None
    NDArray = "NDArray"  # type: ignore[misc]
    HAS_NUMPY = False

from .base import UniversalCoordinator, OperationType, DecisionResponse, OperationResult

logger = logging.getLogger(__name__)


class SwarmState(Enum):
    """Swarm behavioral states."""
    EXPLORING = "exploring"
    EXPLOITING = "exploiting"
    CONVERGED = "converged"
    STAGNANT = "stagnant"
    DIVERSE = "diverse"
    COORDINATED = "coordinated"


@dataclass
class SwarmMetrics:
    """Metrics for swarm behavior analysis."""
    diversity: float = 0.0          # Population diversity
    convergence: float = 0.0        # Convergence measure
    progress: float = 0.0           # Progress rate
    efficiency: float = 0.0         # Resource efficiency
    communication: float = 0.0      # Communication intensity
    collaboration: float = 0.0      # Collaboration level
    performance: float = 0.0        # Overall performance
    fault_tolerance: float = 1.0    # Fault tolerance level


@dataclass
class AdaptiveStrategy:
    """Adaptive strategy configuration."""
    name: str
    description: str
    triggers: List[str]            # Conditions that trigger this strategy
    actions: List[str]             # Actions to execute
    parameters: Dict[str, Any]     # Strategy parameters
    priority: int = 1              # Strategy priority
    cooldown: float = 10.0         # Cooldown period in seconds


@dataclass
class SwarmAgent:
    """Individual swarm agent."""
    agent_id: str
    position: List[float] = field(default_factory=list)
    velocity: List[float] = field(default_factory=list)
    best_position: List[float] = field(default_factory=list)
    best_fitness: float = float('-inf')
    energy: float = 1.0
    exploration_rate: float = 0.5
    findings: List[Dict[str, Any]] = field(default_factory=list)
    current_task: Optional[str] = None


@dataclass
class SwarmNode:
    """
    P2P Research Swarm Node.
    
    From p2p_research_swarm.py comments:
    - WebSocket communication capability
    - Task queue with priority
    - Reputation system
    - Heartbeat monitoring
    """
    node_id: str
    endpoint: str  # WebSocket endpoint
    capabilities: List[str] = field(default_factory=list)
    reputation: float = 1.0
    last_heartbeat: float = field(default_factory=time.time)
    is_online: bool = True
    tasks_completed: int = 0
    tasks_failed: int = 0
    load: float = 0.0  # Current load 0.0-1.0
    
    def update_reputation(self, success: bool, task_complexity: float = 1.0):
        """Update node reputation based on task result."""
        if success:
            # Smooth reputation increase
            self.reputation = min(5.0, self.reputation + 0.1 * task_complexity)
            self.tasks_completed += 1
        else:
            # Reputation penalty for failure
            self.reputation = max(0.1, self.reputation - 0.2 * task_complexity)
            self.tasks_failed += 1
    
    def heartbeat(self):
        """Update last heartbeat timestamp."""
        self.last_heartbeat = time.time()
        self.is_online = True
    
    def check_health(self, timeout: float = 30.0) -> bool:
        """Check if node is still healthy based on heartbeat."""
        is_healthy = (time.time() - self.last_heartbeat) < timeout
        self.is_online = is_healthy
        return is_healthy


@dataclass
class SwarmTask:
    """
    P2P Swarm Task with priority and consensus tracking.
    
    From p2p_research_swarm.py comments:
    - Priority-based task queue
    - Consensus requirements
    - Result aggregation
    """
    task_id: str
    task_type: str
    payload: Dict[str, Any]
    priority: int = 5  # 1-10, lower is higher priority
    created_at: float = field(default_factory=time.time)
    assigned_to: Optional[str] = None
    status: str = "pending"  # pending, assigned, completed, failed
    results: List[Dict[str, Any]] = field(default_factory=list)
    consensus_threshold: float = 0.7  # Required consensus level
    
    def __lt__(self, other):
        """Enable priority queue comparison."""
        return self.priority < other.priority


@dataclass
class ConsensusProposal:
    """
    Consensus mechanism for swarm decisions.
    
    From p2p_research_swarm.py comments:
    - Distributed decision making
    - Reputation-weighted voting
    """
    proposal_id: str
    proposal_type: str
    data: Dict[str, Any]
    votes: Dict[str, bool] = field(default_factory=dict)
    vote_weights: Dict[str, float] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    
    def add_vote(self, node_id: str, vote: bool, weight: float = 1.0):
        """Add weighted vote to proposal."""
        self.votes[node_id] = vote
        self.vote_weights[node_id] = weight
    
    def get_result(self) -> Tuple[bool, float]:
        """
        Calculate consensus result.
        
        Returns:
            (accepted, confidence) tuple
        """
        if not self.votes:
            return False, 0.0
        
        total_weight = sum(self.vote_weights.values())
        if total_weight == 0:
            return False, 0.0
        
        yes_weight = sum(
            self.vote_weights[node_id] 
            for node_id, vote in self.votes.items() 
            if vote
        )
        
        acceptance_rate = yes_weight / total_weight
        confidence = min(1.0, len(self.votes) / 5.0)  # Confidence grows with votes
        
        return acceptance_rate > 0.5, confidence


class UniversalSwarmCoordinator(UniversalCoordinator):
    """
    Universal coordinator for swarm intelligence.
    
    Integrates:
    - SwarmCoordinator: State management, adaptive strategies
    - SelfOrganizingCoordinator: Tree structure, load balancing
    """

    def __init__(self, max_concurrent: int = 50):
        super().__init__(
            name="universal_swarm_coordinator",
            max_concurrent=max_concurrent,
            memory_aware=True
        )
        
        # Swarm state
        self.current_state = SwarmState.EXPLORING
        self.state_history: List[Tuple[SwarmState, float]] = []
        self.current_metrics = SwarmMetrics()
        
        # Agents
        self.agents: Dict[str, SwarmAgent] = {}
        self.max_agents = max_concurrent
        
        # Adaptive strategies
        self.strategies: List[AdaptiveStrategy] = []
        self.active_strategies: List[str] = []
        self.strategy_cooldowns: Dict[str, float] = {}
        
        # Performance tracking
        self.performance_history: deque = deque(maxlen=100)
        self.adaptation_events: List[Dict[str, Any]] = []
        
        # Coordination
        self.coordination_active = False
        self.last_coordination_time = 0.0
        
        # P2P Swarm features (from p2p_research_swarm.py comments)
        self.nodes: Dict[str, SwarmNode] = {}
        self.task_queue: List[SwarmTask] = []
        self.completed_tasks: Dict[str, SwarmTask] = {}
        self.proposals: Dict[str, ConsensusProposal] = {}
        self.max_node_load = 3  # Max tasks per node
        self.heartbeat_interval = 10.0
        
        # Initialize strategies
        self._initialize_adaptive_strategies()

    def get_supported_operations(self) -> List[OperationType]:
        return [OperationType.EXECUTION, OperationType.OPTIMIZATION]

    async def handle_request(
        self,
        operation_ref: str,
        decision: DecisionResponse
    ) -> OperationResult:
        """Handle swarm operation request."""
        start_time = time.time()
        
        try:
            operation = decision.metadata.get('swarm_operation', 'coordinate')
            
            if operation == 'coordinate':
                result = await self._execute_coordination_cycle()
            elif operation == 'adapt':
                result = await self._execute_adaptation(decision)
            else:
                result = {'success': False, 'error': f'Unknown operation: {operation}'}
            
            return OperationResult(
                operation_id=self.generate_operation_id(),
                status="completed" if result.get('success') else "failed",
                result_summary=result.get('summary', 'Swarm operation completed'),
                execution_time=time.time() - start_time,
                success=result.get('success', False),
                metadata=result
            )
        except Exception as e:
            return OperationResult(
                operation_id=self.generate_operation_id(),
                status="failed",
                result_summary=f"Swarm operation failed: {str(e)}",
                execution_time=time.time() - start_time,
                success=False,
                error_message=str(e)
            )

    def _initialize_adaptive_strategies(self):
        """Initialize built-in adaptive strategies."""
        self.strategies = [
            AdaptiveStrategy(
                name="increase_exploration",
                description="Boost exploration when diversity is low",
                triggers=["diversity_low"],
                actions=["boost_exploration", "inject_randomness"],
                parameters={"boost_factor": 1.5, "randomness_level": 0.2},
                priority=5
            ),
            AdaptiveStrategy(
                name="exploit_convergence",
                description="Exploit when convergence is good",
                triggers=["convergence_high"],
                actions=["boost_exploitation", "reduce_exploration"],
                parameters={"boost_factor": 1.3, "reduction_factor": 0.8},
                priority=4
            ),
            AdaptiveStrategy(
                name="handle_stagnation",
                description="Handle stagnation with reinitialization",
                triggers=["progress_stagnant"],
                actions=["reinitialize_particles", "increase_mutation"],
                parameters={"reinit_ratio": 0.3, "mutation_rate": 0.15},
                priority=10
            ),
            AdaptiveStrategy(
                name="enhance_communication",
                description="Enhance communication when collaboration is low",
                triggers=["communication_low", "collaboration_low"],
                actions=["boost_communication", "expand_network"],
                parameters={"boost_factor": 1.4, "expansion_factor": 0.25},
                priority=3
            ),
            AdaptiveStrategy(
                name="fault_recovery",
                description="Recover from agent failures",
                triggers=["fault_detected"],
                actions=["replace_agents", "rebalance_load"],
                parameters={"replacement_ratio": 0.2},
                priority=10
            )
        ]

    async def coordinate_swarm(self, duration_seconds: float = 60.0) -> Dict[str, Any]:
        """
        Coordinate swarm for specified duration.
        
        Args:
            duration_seconds: Coordination duration
            
        Returns:
            Coordination results
        """
        self.coordination_active = True
        start_time = time.time()
        cycles = 0
        
        logger.info(f"Starting swarm coordination for {duration_seconds}s")
        
        while self.coordination_active and (time.time() - start_time) < duration_seconds:
            try:
                # Monitor metrics
                metrics = self._monitor_swarm()
                
                # Analyze state
                self._analyze_swarm_state(metrics)
                
                # Detect adaptation needs
                triggers = self._detect_adaptation_triggers()
                
                # Execute strategies
                if triggers:
                    await self._execute_adaptive_strategies(triggers)
                
                # Perform fault tolerance checks
                self._check_fault_tolerance()
                
                cycles += 1
                await asyncio.sleep(1.0)
                
            except Exception as e:
                logger.error(f"Coordination cycle error: {e}")
        
        self.coordination_active = False
        
        return {
            'success': True,
            'cycles': cycles,
            'duration': time.time() - start_time,
            'final_state': self.current_state.value,
            'final_metrics': self._metrics_to_dict(self.current_metrics),
            'adaptations': len(self.adaptation_events)
        }

    def _monitor_swarm(self) -> SwarmMetrics:
        """Monitor all agents and collect metrics."""
        metrics = SwarmMetrics()

        if not self.agents:
            return metrics

        if not HAS_NUMPY:
            raise ImportError("numpy required for swarm metrics computation — pip install 'hledac[dev]'")

        # Calculate diversity
        positions = [a.position for a in self.agents.values() if len(a.position) > 0]
        if positions:
            positions_array = np.array(positions)
            metrics.diversity = float(np.std(positions_array, axis=0).mean())

        # Calculate convergence
        best_positions = [a.best_position for a in self.agents.values() if len(a.best_position) > 0]
        if best_positions:
            best_array = np.array(best_positions)
            metrics.convergence = float(1.0 - (np.std(best_array, axis=0).mean() / 10.0))

        # Calculate progress
        fitness_values = [a.best_fitness for a in self.agents.values()]
        if fitness_values:
            metrics.progress = float(np.mean(fitness_values))

        # Other metrics
        metrics.efficiency = len(self.agents) / self.max_agents
        metrics.communication = sum(len(a.findings) for a in self.agents.values()) / max(len(self.agents), 1)
        metrics.collaboration = sum(1 for a in self.agents.values() if a.current_task) / max(len(self.agents), 1)
        metrics.performance = (metrics.diversity + metrics.convergence + metrics.progress) / 3

        self.current_metrics = metrics
        self.performance_history.append(metrics)

        return metrics

    def _analyze_swarm_state(self, metrics: SwarmMetrics):
        """Analyze current swarm state based on metrics."""
        # State determination logic
        if metrics.progress < 0.1 and metrics.convergence > 0.8:
            new_state = SwarmState.STAGNANT
        elif metrics.convergence > 0.9:
            new_state = SwarmState.CONVERGED
        elif metrics.diversity > 0.7:
            new_state = SwarmState.DIVERSE
        elif metrics.communication > 0.6 and metrics.collaboration > 0.5:
            new_state = SwarmState.COORDINATED
        elif metrics.convergence > 0.6 and metrics.progress > 0.5:
            new_state = SwarmState.EXPLOITING
        else:
            new_state = SwarmState.EXPLORING
        
        # Update state if changed
        if new_state != self.current_state:
            logger.info(f"Swarm state changed: {self.current_state.value} -> {new_state.value}")
            self.state_history.append((new_state, time.time()))
            self.current_state = new_state

    def _detect_adaptation_triggers(self) -> List[str]:
        """Detect conditions that trigger adaptive strategies."""
        triggers = []
        metrics = self.current_metrics
        
        if metrics.diversity < 0.3:
            triggers.append("diversity_low")
        elif metrics.diversity > 0.7:
            triggers.append("diversity_high")
        
        if metrics.convergence > 0.8:
            triggers.append("convergence_high")
        elif metrics.convergence > 0.5:
            triggers.append("convergence_moderate")
        
        if metrics.progress < 0.1:
            triggers.append("progress_stagnant")
        elif metrics.progress > 0.5:
            triggers.append("progress_good")
        
        if metrics.performance < 0.3:
            triggers.append("performance_low")
        elif metrics.performance > 0.7:
            triggers.append("performance_high")
        
        if metrics.communication < 0.3:
            triggers.append("communication_low")
        
        if metrics.collaboration < 0.3:
            triggers.append("collaboration_low")
        
        # Check for faults
        failed_agents = sum(1 for a in self.agents.values() if a.energy < 0.1)
        if failed_agents > len(self.agents) * 0.2:
            triggers.append("fault_detected")
        
        return triggers

    async def _execute_adaptive_strategies(self, triggers: List[str]):
        """Execute adaptive strategies based on triggers."""
        current_time = time.time()
        
        # Find applicable strategies
        applicable = []
        for strategy in self.strategies:
            # Check if strategy is triggered
            if any(t in strategy.triggers for t in triggers):
                # Check cooldown
                last_execution = self.strategy_cooldowns.get(strategy.name, 0)
                if current_time - last_execution >= strategy.cooldown:
                    applicable.append(strategy)
        
        # Sort by priority
        applicable.sort(key=lambda s: s.priority, reverse=True)
        
        # Execute strategies
        for strategy in applicable[:3]:  # Max 3 strategies per cycle
            try:
                logger.info(f"Executing adaptive strategy: {strategy.name}")
                await self._execute_strategy_actions(strategy)
                
                # Record adaptation
                self.adaptation_events.append({
                    'timestamp': current_time,
                    'strategy': strategy.name,
                    'triggers': triggers
                })
                
                # Set cooldown
                self.strategy_cooldowns[strategy.name] = current_time
                self.active_strategies.append(strategy.name)
                
            except Exception as e:
                logger.error(f"Error executing strategy {strategy.name}: {e}")

    async def _execute_strategy_actions(self, strategy: AdaptiveStrategy):
        """Execute actions for a specific strategy."""
        params = strategy.parameters

        if not HAS_NUMPY:
            raise ImportError("numpy required for swarm strategy actions — pip install 'hledac[dev]'")

        for action in strategy.actions:
            if action == "boost_exploration":
                for agent in self.agents.values():
                    agent.exploration_rate = min(1.0, agent.exploration_rate * params.get('boost_factor', 1.5))

            elif action == "inject_randomness":
                for agent in list(self.agents.values())[:5]:
                    if len(agent.velocity) > 0:
                        agent.velocity = np.random.uniform(-1, 1, len(agent.velocity)).tolist()

            elif action == "reinitialize_particles":
                ratio = params.get('reinit_ratio', 0.3)
                num_reinit = max(1, int(len(self.agents) * ratio))
                for agent in list(self.agents.values())[:num_reinit]:
                    if len(agent.position) > 0:
                        agent.position = np.random.uniform(-5, 5, len(agent.position)).tolist()
                        agent.best_fitness = float('-inf')

            elif action == "replace_agents":
                # Reset failed agents
                for agent in self.agents.values():
                    if agent.energy < 0.1:
                        agent.energy = 1.0
                        agent.findings.clear()
                        agent.current_task = None

    def _check_fault_tolerance(self):
        """Perform fault tolerance checks."""
        failures = []
        
        # Check for low-energy agents
        failed_count = sum(1 for a in self.agents.values() if a.energy < 0.1)
        if failed_count > len(self.agents) * 0.3:
            failures.append("Too many failed agents")
        
        # Update fault tolerance metric
        if failures:
            self.current_metrics.fault_tolerance = max(0.1, self.current_metrics.fault_tolerance - 0.2)
            logger.warning(f"Fault tolerance issues: {failures}")
        else:
            self.current_metrics.fault_tolerance = min(1.0, self.current_metrics.fault_tolerance + 0.05)

    def add_agent(self, agent_id: str, position: Optional[List[float]] = None) -> SwarmAgent:
        """Add new agent to swarm."""
        if position is not None and HAS_NUMPY:
            position_arr = np.array(position)
            agent = SwarmAgent(
                agent_id=agent_id,
                position=position_arr.tolist(),
                velocity=[],
                best_position=position_arr.tolist()
            )
        else:
            agent = SwarmAgent(
                agent_id=agent_id,
                position=position if position is not None else [],
                velocity=[],
                best_position=position.copy() if position is not None else []
            )
        self.agents[agent_id] = agent
        return agent

    def remove_agent(self, agent_id: str) -> bool:
        """Remove agent from swarm."""
        if agent_id in self.agents:
            del self.agents[agent_id]
            return True
        return False

    def _metrics_to_dict(self, metrics: SwarmMetrics) -> Dict[str, float]:
        return {
            'diversity': metrics.diversity,
            'convergence': metrics.convergence,
            'progress': metrics.progress,
            'efficiency': metrics.efficiency,
            'communication': metrics.communication,
            'collaboration': metrics.collaboration,
            'performance': metrics.performance,
            'fault_tolerance': metrics.fault_tolerance
        }

    def get_swarm_status(self) -> Dict[str, Any]:
        """Get current swarm status."""
        return {
            'state': self.current_state.value,
            'agents_count': len(self.agents),
            'metrics': self._metrics_to_dict(self.current_metrics),
            'active_strategies': self.active_strategies[-5:],
            'adaptation_count': len(self.adaptation_events)
        }

    # =============================================================================
    # P2P SWARM METHODS (from p2p_research_swarm.py comments)
    # =============================================================================
    
    def register_node(self, node_id: str, endpoint: str, 
                     capabilities: List[str] = None) -> SwarmNode:
        """
        Register a new P2P node to the swarm.
        
        From p2p_research_swarm.py: "Start WebSocket server", "Node registration"
        
        Args:
            node_id: Unique node identifier
            endpoint: WebSocket endpoint URL
            capabilities: List of node capabilities
            
        Returns:
            Registered SwarmNode
        """
        node = SwarmNode(
            node_id=node_id,
            endpoint=endpoint,
            capabilities=capabilities or []
        )
        self.nodes[node_id] = node
        logger.info(f"🌐 P2P Node registered: {node_id} at {endpoint}")
        return node
    
    def unregister_node(self, node_id: str) -> bool:
        """Remove node from swarm."""
        if node_id in self.nodes:
            del self.nodes[node_id]
            logger.info(f"🌐 P2P Node unregistered: {node_id}")
            return True
        return False
    
    def submit_task(self, task_type: str, payload: Dict[str, Any],
                   priority: int = 5, consensus_threshold: float = 0.7) -> str:
        """
        Submit task to P2P swarm queue.
        
        From p2p_research_swarm.py: "Add to queue with priority"
        
        Args:
            task_type: Type of task
            payload: Task data
            priority: Task priority (1-10, lower is higher)
            consensus_threshold: Required consensus for results
            
        Returns:
            Task ID
        """
        task_id = f"task_{int(time.time())}_{len(self.task_queue)}"
        task = SwarmTask(
            task_id=task_id,
            task_type=task_type,
            payload=payload,
            priority=priority,
            consensus_threshold=consensus_threshold
        )
        
        self.task_queue.append(task)
        self.task_queue.sort()  # Sort by priority
        
        logger.info(f"📋 Task submitted: {task_id} (priority: {priority})")
        return task_id
    
    def assign_task(self, task_id: Optional[str] = None) -> Optional[Tuple[str, SwarmTask]]:
        """
        Assign task to best available node.
        
        From p2p_research_swarm.py: "Select best node based on reputation"
        
        Args:
            task_id: Specific task ID or None for next in queue
            
        Returns:
            Tuple of (node_id, task) or None
        """
        if not self.task_queue:
            return None
        
        # Get available nodes (online + low load)
        available_nodes = [
            node for node in self.nodes.values()
            if node.is_online and node.load < self.max_node_load
        ]
        
        if not available_nodes:
            return None
        
        # Select task
        if task_id:
            task = next((t for t in self.task_queue if t.task_id == task_id), None)
            if not task:
                return None
        else:
            task = self.task_queue[0]
        
        # Select best node by reputation and load
        best_node = max(
            available_nodes,
            key=lambda n: (n.reputation * 0.7 + (1 - n.load) * 0.3)
        )
        
        # Assign task
        task.assigned_to = best_node.node_id
        task.status = "assigned"
        best_node.load += 1
        
        # Remove from queue
        self.task_queue.remove(task)
        
        logger.info(f"📋 Task {task.task_id} assigned to node {best_node.node_id}")
        return best_node.node_id, task
    
    def submit_task_result(self, task_id: str, node_id: str, 
                          result: Dict[str, Any], success: bool = True):
        """
        Submit task result from node.
        
        From p2p_research_swarm.py: "Move to completed tasks"
        
        Args:
            task_id: Task identifier
            node_id: Node that completed the task
            result: Task result data
            success: Whether task succeeded
        """
        # Find task in active tasks
        task = self.completed_tasks.get(task_id)
        if not task:
            # Create placeholder if not exists
            task = SwarmTask(task_id=task_id, task_type="unknown", payload={})
            self.completed_tasks[task_id] = task
        
        # Add result
        task.results.append({
            'node_id': node_id,
            'result': result,
            'success': success,
            'timestamp': time.time()
        })
        
        # Update node
        if node_id in self.nodes:
            node = self.nodes[node_id]
            node.load = max(0, node.load - 1)
            node.update_reputation(success)
            
            if success:
                task.status = "completed"
                logger.info(f"✅ Task {task_id} completed by {node_id}")
            else:
                logger.warning(f"❌ Task {task_id} failed by {node_id}")
    
    def create_proposal(self, proposal_type: str, data: Dict[str, Any]) -> str:
        """
        Create consensus proposal.
        
        From p2p_research_swarm.py: "Check for pending consensus requests"
        
        Args:
            proposal_type: Type of proposal
            data: Proposal data
            
        Returns:
            Proposal ID
        """
        proposal_id = f"prop_{int(time.time())}_{len(self.proposals)}"
        proposal = ConsensusProposal(
            proposal_id=proposal_id,
            proposal_type=proposal_type,
            data=data
        )
        self.proposals[proposal_id] = proposal
        logger.info(f"🗳️ Proposal created: {proposal_id} ({proposal_type})")
        return proposal_id
    
    def vote_on_proposal(self, proposal_id: str, node_id: str, 
                        vote: bool) -> Tuple[bool, float]:
        """
        Cast vote on proposal.
        
        Args:
            proposal_id: Proposal to vote on
            node_id: Voting node
            vote: True for yes, False for no
            
        Returns:
            (accepted, confidence) tuple
        """
        if proposal_id not in self.proposals:
            return False, 0.0
        
        proposal = self.proposals[proposal_id]
        
        # Get node weight from reputation
        weight = 1.0
        if node_id in self.nodes:
            weight = self.nodes[node_id].reputation
        
        proposal.add_vote(node_id, vote, weight)
        
        # Calculate result
        accepted, confidence = proposal.get_result()
        
        logger.info(f"🗳️ Vote on {proposal_id}: {vote} (weight: {weight:.2f})")
        
        return accepted, confidence
    
    async def run_heartbeat_monitor(self, interval: float = None):
        """
        Run continuous heartbeat monitoring.
        
        From p2p_research_swarm.py: "Check for offline nodes"
        
        Args:
            interval: Heartbeat check interval (seconds)
        """
        interval = interval or self.heartbeat_interval
        
        while self.coordination_active:
            try:
                # Check all nodes
                for node in list(self.nodes.values()):
                    was_online = node.is_online
                    is_healthy = node.check_health(timeout=interval * 3)
                    
                    if was_online and not is_healthy:
                        logger.warning(f"💔 Node {node.node_id} went offline")
                        # Reassign its tasks
                        self._reassign_node_tasks(node.node_id)
                    elif not was_online and is_healthy:
                        logger.info(f"💚 Node {node.node_id} came back online")
                
                await asyncio.sleep(interval)
                
            except Exception as e:
                logger.error(f"Heartbeat monitor error: {e}")
                await asyncio.sleep(interval)
    
    def _reassign_node_tasks(self, node_id: str):
        """Reassign tasks from failed node back to queue."""
        for task in self.completed_tasks.values():
            if task.assigned_to == node_id and task.status == "assigned":
                task.assigned_to = None
                task.status = "pending"
                self.task_queue.append(task)
                logger.info(f"🔄 Task {task.task_id} reassigned to queue")
        
        self.task_queue.sort()
    
    def get_p2p_status(self) -> Dict[str, Any]:
        """Get P2P swarm status."""
        online_nodes = sum(1 for n in self.nodes.values() if n.is_online)
        total_load = sum(n.load for n in self.nodes.values())
        avg_reputation = (
            sum(n.reputation for n in self.nodes.values()) / len(self.nodes)
            if self.nodes else 0
        )
        
        return {
            'nodes': {
                'total': len(self.nodes),
                'online': online_nodes,
                'avg_reputation': avg_reputation,
                'total_load': total_load
            },
            'tasks': {
                'queued': len(self.task_queue),
                'completed': len(self.completed_tasks),
                'total_capacity': len(self.nodes) * self.max_node_load
            },
            'proposals': len(self.proposals)
        }

    def _get_feature_list(self) -> List[str]:
        return [
            "Swarm state management",
            "Adaptive strategy execution",
            "Swarm metrics monitoring",
            "Fault tolerance",
            "Multi-agent coordination",
            "Automatic adaptation",
            "P2P node registration",
            "Priority task queue",
            "Reputation-weighted task assignment",
            "Consensus mechanism",
            "Heartbeat monitoring",
            "Distributed task coordination"
        ]
