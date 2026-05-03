from __future__ import annotations

__version__ = "1.0.0"
__phase__ = "61"
__description__ = "Sprint 61 - Advanced Stealth & Post-Quantum Everything"

# Core components
try:
    from .federated_engine import (
        FederatedEngine,
        FederatedConfig,
        TrainingExample,
        FederatedRoundResult,
        create_federated_engine,
    )
    FEDERATED_AVAILABLE = True
except ImportError:
    FEDERATED_AVAILABLE = False

# Sprint 58B components
from .post_quantum import PQCProvider

# MLX-dependent — guarded so non-MLX environments can still import federated module
try:
    from .secure_aggregator import SecureAggregator
except ImportError:
    SecureAggregator = None  # type: ignore[misc,assignment]
from .sketches import CountMinSketch, MinHashSketch, SimHashSketch
from .transport_base import Transport
from .transport_inmemory import InMemoryTransport
from .transport_tor import TorTransport
from .model_store import ModelStore
from .evidence_log import FederationEvidenceLog, FederationEvidenceEvent
from .differential_privacy import DPNoise, RDPCalculator

# Sprint 61 components
from .peer_registry import PeerRegistry
from .model_store_v2 import ModelStore as ModelStoreV2
from .federated_coordinator_v2 import FederatedCoordinatorV2

__all__ = [
    'PQCProvider',
    'SecureAggregator',
    'CountMinSketch',
    'MinHashSketch',
    'SimHashSketch',
    'Transport',
    'InMemoryTransport',
    'TorTransport',
    'ModelStore',
    'FederationEvidenceLog',
    'FederationEvidenceEvent',
    'DPNoise',
    'RDPCalculator',
    'FederatedEngine',
    'FederatedConfig',
    'TrainingExample',
    'FederatedRoundResult',
    'create_federated_engine',
    # Sprint 61
    'PeerRegistry',
    'ModelStoreV2',
    'FederatedCoordinatorV2',
]
