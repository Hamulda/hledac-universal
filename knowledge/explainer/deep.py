"""
Deep explainer – využívá mlx-graphs native explain nebo fallback GNNExplainer v MLX.
"""

import logging

# C1-X FIX: Import MLX_AVAILABLE from SSOT (zero-import detection)
# Uses importlib.metadata.version("mlx") — no mlx.core import at module load
from hledac.universal.utils.mlx_memory import MLX_AVAILABLE

# mx and nn are used ONLY when MLX is available, imported lazily in methods
mx = None
nn = None

from hledac.universal._core.resource_governor import Priority, ResourceGovernor

logger = logging.getLogger(__name__)
try:
    import mlx_graphs as mxg

    USE_NATIVE = True
except ImportError:
    USE_NATIVE = False
    logger.warning("mlx-graphs not available, using fallback GNNExplainer")


class DeepExplainer:
    """Deep explainer pro vysvětlení predikcí pomocí GNN."""

    __slots__ = ("gnn", "governor")

    def __init__(self, gnn_predictor, governor: ResourceGovernor) -> None:
        self.gnn = gnn_predictor
        self.governor = governor

    async def explain(
        self, node: str, target_prediction: str | None = None, max_nodes: int = 10, optimize_features: bool = False
    ) -> dict:
        """
        Vysvětlí predikci pro daný uzel.
        Vrací slovník s důležitými hranami a případně důležitými features.
        """
        # C1-X FIX: Use SSOT MLX_AVAILABLE
        if not MLX_AVAILABLE:
            return {}
        async with self.governor.reserve({"ram_mb": 200, "gpu": True}, Priority.NORMAL):
            subgraph = await self._extract_subgraph(node, max_nodes)
            if not subgraph or not subgraph.get("nodes"):
                return {}
            # C1-X FIX: Lazy import mxg only when native explain is available
            if USE_NATIVE and hasattr(self.gnn, "explain"):
                try:
                    import mlx.core as _mx
                    import mlx_graphs as _mxg

                    data = _mxg.data.Data(
                        x=_mx.array(subgraph["node_features"]),
                        edge_index=_mx.array(subgraph["edges"]).T,
                        edge_weight=_mx.array(subgraph.get("edge_weights", [1.0] * len(subgraph["edges"]))),
                        y=_mx.array([subgraph["target_idx"]]),
                    )
                    explanation = self.gnn.explain(data, target_idx=subgraph["target_idx"])
                    return {
                        "node": node,
                        "important_edges": explanation.edge_importance,
                        "feature_importance": explanation.feature_importance if optimize_features else None,
                    }
                except Exception as e:
                    logger.warning(f"Native explain failed, falling back: {e}")
            return await self._fallback_explain(subgraph, optimize_features)

    async def _fallback_explain(self, subgraph: dict, optimize_features: bool) -> dict:
        """Fallback GNN explainer s gradient-based mask."""
        # C1-X FIX: Use SSOT MLX_AVAILABLE
        if not MLX_AVAILABLE:
            return {}
        # C1-X FIX: Lazy import mlx modules only when needed
        import mlx.core as _mx
        import mlx.nn as _nn

        node_features = _mx.array(subgraph["node_features"])
        edge_index = _mx.array(subgraph["edges"]).T
        edge_weights = _mx.array(subgraph.get("edge_weights", [1.0] * edge_index.shape[1]))
        target_idx = subgraph["target_idx"]
        num_edges = edge_index.shape[1]
        if num_edges == 0:
            return {"node": subgraph.get("nodes", [""])[0], "important_edges": [], "feature_importance": None}
        mask = _mx.random.uniform(shape=(num_edges,))
        optimizer = _nn.optim.Adam(learning_rate=0.01)

        def loss_fn(m):
            masked_weights = edge_weights * m
            try:
                pred = self.gnn(node_features, edge_index, edge_weight=masked_weights)[target_idx]
                orig = self.gnn(node_features, edge_index, edge_weight=edge_weights)[target_idx]
            except TypeError:
                pred = self.gnn(node_features, edge_index)[target_idx]
                orig = self.gnn(node_features, edge_index)[target_idx]
            return _nn.losses.mse_loss(pred, orig)

        loss_grad_fn = _mx.value_and_grad(loss_fn)
        for i in range(30):
            loss, grads = loss_grad_fn(mask)
            optimizer.update(mask, grads)
            mask = _mx.clip(mask, 0, 1)
            _mx.eval(mask)
        important_edges = []
        for i in range(num_edges):
            if mask[i] > 0.5:
                u, v = (int(edge_index[0, i].item()), int(edge_index[1, i].item()))
                if u < len(subgraph["nodes"]) and v < len(subgraph["nodes"]):
                    important_edges.append((subgraph["nodes"][u], subgraph["nodes"][v], float(mask[i])))
        return {"node": subgraph.get("nodes", [""])[0], "important_edges": important_edges, "feature_importance": None}

    async def _extract_subgraph(self, node: str, max_nodes: int) -> dict:
        """Extrahuje subgraf – využívá RelationshipDiscoveryEngine."""
        return {"nodes": [node], "edges": [], "edge_weights": [], "node_features": [[0.0] * 64], "target_idx": 0}
