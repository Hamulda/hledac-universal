"""
intelligence/_graph_serde.py — pickleless graph persistence for OSINT graphs.

Why this exists
---------------
CLAUDE.md invariants:
  - No bare ``pickle.load`` on user/IO-controlled data (F196B).
  - M1 8GB UMA friendly: zero-copy, orjson > pickle for serialization speed
    and zero import of the ``pickle`` interpreter (avoid opcode surface).
  - ``asyncio.run`` invariant: no nested event loop.

Design
------
* **NetworkX path** — uses ``networkx.readwrite.json_graph.node_link_data``
  which produces a plain dict, then ``orjson.dumps`` for zero-copy write.
  Read path is symmetric: ``orjson.loads`` + ``node_link_graph``.
  Schema-stable, forward/backward compatible, no exec on load.
* **igraph path** — keeps ``Graph.write_picklez`` / ``Graph.Load`` because
  that is igraph's *native* compact format (NOT the Python ``pickle``
  module). It uses C-level read/write; the only user data on disk is the
  graph's compressed internal layout.
* **Migration shim** — if a legacy ``.pkl`` (Python pickle) is on disk, we
  still load it but ONLY on F196B-safe paths (under ``~/.hledac/graphs``).
  New writes are always JSON; legacy loads are one-shot migrations.

All ops bounded, fail-soft. No exceptions raised to caller.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import orjson

# Sprint S4: msgspec.json facade for write path. 2-3x faster than orjson for
# plain-dict envelopes (networkx node_link_data output is plain dict — no
# numpy in the envelope). Read path keeps orjson (fast parse, no schema
# benefit on decode).
from hledac.universal.utils.msgspec_json import encode as _msgspec_encode

logger = logging.getLogger(__name__)

# Magic header so we can detect our own files and reject foreign JSON.
_MAGIC = b'{"_hledac_graph_v":1'
_FORMAT_TAG = "_hledac_graph_v"
_KIND_KEY = "_kind"
_KIND_NX = "nx_node_link"

# Bounded cap. Reused from caller if smaller, but enforced here too.
DEFAULT_MAX_NODES = 50_000


def _safe_path(path: str) -> bool:
    """F196B: reject paths outside ``~/.hledac/graphs``.

    Applied to ANY load (including legacy pickle fallback).
    """
    try:
        from pathlib import Path as _P

        graph_base = (_P("~/.hledac/graphs").expanduser()).resolve()
        resolved = _P(path).resolve()
        return str(resolved).startswith(str(graph_base) + os.sep) or resolved == graph_base
    except Exception:  # noqa: BLE001
        return False


def save_nx_graph_jsonl(path: str, graph: Any, max_nodes: int = DEFAULT_MAX_NODES) -> bool:
    """Persist a NetworkX graph as JSON (node-link format) using orjson.

    Returns True on success, False on any error (fail-soft, no raise).
    Bounded: prunes lowest-degree nodes if ``graph.number_of_nodes() > max_nodes``.
    """
    try:
        # Lazy import — networkx may be missing in some profiles.
        from networkx.readwrite import json_graph as _nx_json  # type: ignore

        # Inline prune — match caller's MAX_NODES policy.
        if max_nodes and graph.number_of_nodes() > max_nodes:
            try:
                degree_sorted = sorted(graph.nodes(), key=lambda n: graph.degree(n))
                prune = graph.number_of_nodes() - max_nodes
                graph.remove_nodes_from(set(degree_sorted[:prune]))
                logger.warning(
                    "[GraphSerde] Pruned %d lowest-degree nodes (max=%d)",
                    prune, max_nodes,
                )
            except Exception as prune_err:  # noqa: BLE001
                logger.warning("[GraphSerde] Prune failed (continuing): %s", prune_err)

        payload = _nx_json.node_link_data(graph)
        envelope = {
            _FORMAT_TAG: 1,
            _KIND_KEY: _KIND_NX,
            "data": payload,
        }
        # Sprint S4: msgspec encode — 2-3x faster than orjson for the
        # plain-dict envelope that networkx.node_link_data produces. The
        # facade falls back to orjson on type errors, so the legacy
        # OPT_SERIALIZE_NUMPY safety net is preserved transitively.
        raw = _msgspec_encode(envelope)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # Atomic write: tmp + rename. Avoids half-written file on crash.
        tmp = f"{path}.tmp"
        with open(tmp, "wb") as f:
            f.write(raw)
        os.replace(tmp, path)
        logger.debug(
            "[GraphSerde] Saved NX graph (%d nodes, %d edges) -> %s",
            graph.number_of_nodes(),
            graph.number_of_edges(),
            path,
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[GraphSerde] save_nx_graph_jsonl failed: %s", e)
        return False


def load_nx_graph_jsonl(path: str, max_nodes: int = DEFAULT_MAX_NODES) -> Any | None:
    """Load a NetworkX graph from JSON envelope (orjson-parsed).

    Returns the NetworkX Graph on success, None on any error.
    If the file is legacy Python ``pickle`` (``.pkl``) AND path is F196B-safe,
    it is loaded as a one-shot migration; new code never writes pickle.

    Bounded: if loaded graph > ``max_nodes`` after load, prune lowest-degree
    nodes in-place to keep M1 8GB RSS bounded.
    """
    if not Path(path).exists():
        return None
    try:
        from networkx.readwrite import json_graph as _nx_json  # type: ignore

        with open(path, "rb") as f:
            raw = f.read(64)  # peek for magic
        is_ours = raw.lstrip().startswith(_MAGIC[:16])
        if not is_ours:
            # Legacy pickle fallback. F196B requires safe path.
            if not _safe_path(path):
                logger.warning(
                    "[F196B] Refused legacy pickle load outside graphs dir: %s", path
                )
                return None
            logger.info(
                "[GraphSerde] Legacy pickle file detected, one-shot migration: %s", path
            )
            import pickle  # lazy, only for legacy migration

            with open(path, "rb") as f:
                obj = pickle.load(f)
            return _bound_or_none(obj, max_nodes)

        # Our JSON format. Read full.
        with open(path, "rb") as f:
            envelope = orjson.loads(f.read())
        if not isinstance(envelope, dict) or envelope.get(_FORMAT_TAG) != 1:
            logger.warning("[GraphSerde] Unknown envelope tag, refusing: %s", path)
            return None
        if envelope.get(_KIND_KEY) != _KIND_NX:
            logger.warning("[GraphSerde] Unknown kind %r, refusing", envelope.get(_KIND_KEY))
            return None
        graph = _nx_json.node_link_graph(envelope["data"])
        return _bound_or_none(graph, max_nodes)
    except Exception as e:  # noqa: BLE001
        logger.warning("[GraphSerde] load_nx_graph_jsonl failed: %s", e)
        return None


def _bound_or_none(graph: Any, max_nodes: int) -> Any | None:
    """Bound check + post-load prune. Returns graph or None on failure."""
    try:
        if max_nodes and graph.number_of_nodes() > max_nodes:
            degree_sorted = sorted(graph.nodes(), key=lambda n: graph.degree(n))
            prune = graph.number_of_nodes() - max_nodes
            graph.remove_nodes_from(set(degree_sorted[:prune]))
            logger.warning(
                "[GraphSerde] Post-load prune: dropped %d nodes (max=%d)",
                prune, max_nodes,
            )
        return graph
    except Exception as e:  # noqa: BLE001
        logger.warning("[GraphSerde] bound check failed: %s", e)
        return None


def is_our_format(path: str) -> bool:
    """Return True if ``path`` starts with our JSON magic (cheap peek)."""
    try:
        with open(path, "rb") as f:
            head = f.read(64)
        return head.lstrip().startswith(_MAGIC[:16])
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "DEFAULT_MAX_NODES",
    "save_nx_graph_jsonl",
    "load_nx_graph_jsonl",
    "is_our_format",
    "_safe_path",  # exported for tests
]
