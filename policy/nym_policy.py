from __future__ import annotations

import logging
from enum import Enum

import numpy as np
from hledac.universal.core.resource_governor import ResourceGovernor

from transport.transport_resolver import Transport

logger = logging.getLogger(__name__)

# Lazy transport classes for direct instantiation (when needed)
_TorTransport = None
_NymTransport = None

def _get_tor_transport():
    global _TorTransport
    if _TorTransport is None:
        from hledac.universal.transport.tor_transport import TorTransport
        _TorTransport = TorTransport
    return _TorTransport

def _get_nym_transport():
    global _NymTransport
    if _NymTransport is None:
        from hledac.universal.transport.nym_transport import NymTransport
        _NymTransport = NymTransport
    return _NymTransport


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class LinUCBArm:
    def __init__(self, dim: int = 4, alpha: float = 0.5):
        self.dim = dim
        self.alpha = alpha
        self.A_inv = np.eye(dim)
        self.b = np.zeros(dim)

    def select(self, x: np.ndarray) -> float:
        theta = self.A_inv @ self.b
        mean = x @ theta
        var = x @ self.A_inv @ x
        return mean + self.alpha * np.sqrt(max(var, 0))

    def update(self, x: np.ndarray, reward: float):
        x = x.reshape(-1, 1)
        A_inv_x = self.A_inv @ x
        denom = 1 + (x.T @ A_inv_x).item()
        self.A_inv -= (A_inv_x @ A_inv_x.T) / denom
        self.b += reward * x.flatten()


class NymPolicy:
    def __init__(self, governor: ResourceGovernor, tor_transport: TorTransport,
                 nym_transport: NymTransport, alpha: float = 0.5,
                 lambda_cost: float = 0.01, exploration_rate: float = 0.1):
        self.governor = governor
        self.tor = tor_transport
        self.nym = nym_transport
        self.alpha = alpha
        self.lambda_cost = lambda_cost
        self.exploration_rate = exploration_rate

        self.bandit_tor = LinUCBArm(dim=4, alpha=alpha)
        self.bandit_nym = LinUCBArm(dim=4, alpha=alpha)

        self._last_context: dict[str, tuple[str, np.ndarray]] = {}

        self.tor_latency = 1.0
        self.nym_latency = 2.0

    def _extract_features(self, risk_level: RiskLevel, time_budget: float,
                          sensitivity: float, transport: str) -> np.ndarray:
        risk_map = {RiskLevel.LOW: 0.1, RiskLevel.MEDIUM: 0.5, RiskLevel.HIGH: 0.8, RiskLevel.CRITICAL: 1.0}
        risk_val = risk_map[risk_level]
        time_norm = min(time_budget / 300.0, 1.0)
        sens_norm = min(sensitivity, 1.0)
        latency = self.tor_latency if transport == 'tor' else self.nym_latency
        lat_norm = min(latency / 10.0, 1.0)
        return np.array([risk_val, time_norm, sens_norm, lat_norm])

    async def select_transport(self, risk_level: RiskLevel, time_budget: float,
                               sensitivity: float, need_cover_traffic: bool = False,
                               request_id: str | None = None) -> tuple[Transport, dict]:
        if risk_level == RiskLevel.CRITICAL and time_budget > 30:
            transport = self.nym
            params = {'cover_traffic': need_cover_traffic}
            if request_id:
                x = self._extract_features(risk_level, time_budget, sensitivity, 'nym')
                self._last_context[request_id] = ('nym', x)
            return transport, params

        if np.random.random() < self.exploration_rate:
            transport = np.random.choice([self.tor, self.nym])
            transport_name = 'nym' if transport == self.nym else 'tor'
            params = {'cover_traffic': need_cover_traffic and transport == self.nym}
            if request_id:
                x = self._extract_features(risk_level, time_budget, sensitivity, transport_name)
                self._last_context[request_id] = (transport_name, x)
            return transport, params

        x_tor = self._extract_features(risk_level, time_budget, sensitivity, 'tor')
        x_nym = self._extract_features(risk_level, time_budget, sensitivity, 'nym')
        ucb_tor = self.bandit_tor.select(x_tor)
        ucb_nym = self.bandit_nym.select(x_nym)

        transport = self.nym if ucb_nym > ucb_tor else self.tor
        transport_name = 'nym' if transport == self.nym else 'tor'
        params = {'cover_traffic': need_cover_traffic and transport == self.nym}
        if request_id:
            x = x_nym if transport == self.nym else x_tor
            self._last_context[request_id] = (transport_name, x)
        return transport, params

    async def update_reward(self, request_id: str, success: bool, latency: float):
        if request_id not in self._last_context:
            return
        transport, x = self._last_context.pop(request_id)

        if transport == 'tor':
            self.tor_latency = 0.9 * self.tor_latency + 0.1 * latency
        else:
            self.nym_latency = 0.9 * self.nym_latency + 0.1 * latency

        reward = 1.0 if success else -0.5
        cost = latency / 60.0
        r = reward - self.lambda_cost * cost

        if transport == 'tor':
            self.bandit_tor.update(x, r)
        else:
            self.bandit_nym.update(x, r)
