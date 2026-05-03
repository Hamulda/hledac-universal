"""
Extrakce stavu pro MARL agenty.
Stav obsahuje globální informace (z grafu, scheduleru) a lokální informace z aktuálního vlákna.
"""

from typing import Dict, Optional

try:
    import mlx.core as mx

    MLX_AVAILABLE = True
except ImportError:
    MLX_AVAILABLE = False
    mx = None

import numpy as np


class StateExtractor:
    def __init__(self, state_dim: int = 12, gnn_predictor: Optional['GNNPredictor'] = None):
        self.state_dim = state_dim
        self.gnn_predictor = gnn_predictor

    def extract(self, thread_state: Dict, global_state: Dict):
        """
        thread_state: {
            'entity_centrality': float,
            'novelty': float,
            'depth': int,
            'contradiction': bool,
            'source_type': int  # 0=web,1=academic,2=darkweb
        }
        global_state: {
            'queue_size': int,
            'memory_pressure': float,
            'graph_entropy': float,
            'avg_reward': float,
            'num_pending_tasks': int,
            'time_since_last_finding': float,
            'resource_concurrency': float
        }
        """
        features = [
            thread_state.get('entity_centrality', 0.0),
            thread_state.get('novelty', 0.0),
            float(thread_state.get('contradiction', False)),
            thread_state.get('source_type', 0) / 3.0,
            thread_state.get('depth', 0) / 5.0,
            global_state.get('queue_size', 0) / 100.0,
            global_state.get('memory_pressure', 0.0),
            global_state.get('graph_entropy', 0.5),
            global_state.get('avg_reward', 0.0),
            global_state.get('num_pending_tasks', 0) / 20.0,
            min(global_state.get('time_since_last_finding', 0) / 3600.0, 1.0),
            global_state.get('resource_concurrency', 0.5)
        ]

        # GNN embedding (pokud k dispozici)
        if self.gnn_predictor is not None:
            try:
                graph_emb = self.gnn_predictor.get_graph_embedding()
                features.extend(graph_emb.tolist())
            except AttributeError:
                pass

        # Zarovnání na state_dim
        if len(features) < self.state_dim:
            features += [0.0] * (self.state_dim - len(features))
        else:
            features = features[:self.state_dim]

        if MLX_AVAILABLE:
            return mx.array(features)
        return np.array(features, dtype=np.float32)
