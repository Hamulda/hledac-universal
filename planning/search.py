"""
Anytime beam search s budget‑aware a heuristikou value / cost.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from hledac.universal.core.resource_governor import Priority, ResourceGovernor

logger = logging.getLogger(__name__)


class SearchNode:
    def __init__(self, state: dict[str, Any], parent: SearchNode | None = None,
                 action: dict | None = None, cost: float = 0.0, value: float = 0.0,
                 ram_cost: float = 0.0, net_cost: float = 0.0):
        self.state = state
        self.parent = parent
        self.action = action
        self.cost = cost
        self.ram_cost = ram_cost
        self.net_cost = net_cost
        self.value = value
        self.score = 0.0  # bude nastaveno

    def __lt__(self, other):
        return self.score > other.score  # max-heap


def anytime_beam_search(initial_state: dict[str, Any],
                        goal_check: Callable[[dict], bool],
                        expand: Callable[[dict], list[tuple[dict | None, dict, float, float, float, float]]],
                        heuristic: Callable[[dict], tuple[float, float, float]],
                        governor: ResourceGovernor,
                        time_budget: float,
                        ram_budget_mb: float,
                        net_budget_mb: float,
                        beam_width: int = 10) -> list[dict] | None:
    """
    Anytime beam search maximalizující value / cost.
    expand vrací: (action, new_state, time_cost, ram_cost, net_cost, value)
    heuristika vrací (remaining_value, remaining_time, remaining_ram)
    """
    start_time = time.time()
    beam = [SearchNode(initial_state)]
    best_plan = None
    best_value = -float('inf')

    while beam and (time.time() - start_time) < time_budget:
        # Vybereme top-beam_width podle score
        beam.sort(key=lambda n: n.score, reverse=True)
        beam = beam[:beam_width]

        next_beam = []
        for node in beam:
            if goal_check(node.state):
                if node.value > best_value:
                    best_value = node.value
                    best_plan = _reconstruct_plan(node)
                continue

            for action, succ_state, t_cost, r_cost, n_cost, val in expand(node.state):
                new_node = SearchNode(succ_state, node, action,
                                      node.cost + t_cost,
                                      node.value + val,
                                      node.ram_cost + r_cost,
                                      node.net_cost + n_cost)

                # Kontrola budgetů
                if new_node.cost > time_budget:
                    continue
                if new_node.ram_cost > ram_budget_mb:
                    continue
                if new_node.net_cost > net_budget_mb:
                    continue

                # Kontrola zdrojů (synchronní)
                if not governor.can_afford_sync({'ram_mb': r_cost}, Priority.NORMAL):
                    continue

                # Heuristika
                h_val, h_time, h_ram = heuristic(succ_state)
                expected_value = new_node.value + h_val
                expected_time = new_node.cost + h_time
                new_node.ram_cost + h_ram

                # Score = value / (time + epsilon)
                if expected_time > 0:
                    new_node.score = expected_value / expected_time
                else:
                    new_node.score = expected_value

                next_beam.append(new_node)

        beam = next_beam

    return best_plan


def _reconstruct_plan(node: SearchNode) -> list[dict]:
    plan = []
    while node.parent:
        if node.action is not None:
            plan.append(node.action)
        node = node.parent
    return list(reversed(plan))
