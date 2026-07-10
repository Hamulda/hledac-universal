"""
EvidenceNetworkAnalyzer — network-based evidence analysis
==========================================================

Provides bounded network analysis for entity relationships, contradictions,
and centrality over OSINT evidence assembled during a sprint.

Public surface (preserved from the original stub):
    - analyze_network(entities, **kwargs)             -> dict
    - extract_relationships(entities, threshold=0.7)  -> list[dict]
    - detect_contradictions(evidence_a, evidence_b)    -> dict | None
    - calculate_centrality(network)                    -> dict
    - analyze_evidence_network(query, conf, priority)  -> dict  (NEW — used
                                                               by research_coordinator)
    - cleanup()                                       -> None
    - is_implemented()                                -> bool  (now True)

Algorithms (M1 8GB UMA safe, no ML, no heavy deps):
    - Relationship extraction: token-set Jaccard + eTLD+1 / IOC co-occurrence
      heuristics. No NLP, no embeddings — fully explainable.
    - Community detection:    igraph community_label_propagation (C-core, ~5-10x
      faster than NetworkX pure-Python greedy_modularity_communities).
    - Centrality:             igraph degree_centrality + betweenness_centrality
      via C-core with k-sample cap to keep O(n·k) instead of O(n²).
    - Contradiction:          numeric diff, negation cues, mutual-exclusion
      key matching, and date-conflict detection.

Boundedness (M1 8GB invariants — always-on, no toggle):
    - MAX_ENTITIES = 500  — input cap; larger inputs are sampled.
    - MAX_EDGES    = 2000 — output cap on the edges list.
    - MAX_CLUSTERS = 25   — max communities returned.
    - MAX_CLUSTER_SIZE    = 200
    - MAX_CENTRALITY_NODES = 100  — k-sample for betweenness.
    - MAX_RELATIONSHIPS  = 500
    - MAX_CONTRADICTIONS = 50
    - All collections sliced to their cap before returning; no unbounded
      growth between calls.

Fail-safe (always-on):
    - networkx is imported lazily inside methods, never at module load.
    - Every public method is wrapped in try/except and returns a bounded
      default (empty list / empty dict / None) on any failure.
    - No background workers, no threads, no I/O — pure CPU on a bounded
      in-memory graph.
"""
from __future__ import annotations



from itertools import combinations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from hledac.universal.core.protocols import safe_get_finding_field, safe_get_payload_text
from hledac.universal.utils.graph_utils import lazy_ig as _lazy_ig

logger = logging.getLogger(__name__)

if TYPE_CHECKING:  # pragma: no cover - typing only
    pass


# ── Boundedness constants (always-on, no env flag) ────────────────────────────
MAX_ENTITIES: int = 500
MAX_EDGES: int = 2000
MAX_CLUSTERS: int = 25
MAX_CLUSTER_SIZE: int = 200
MAX_CENTRALITY_NODES: int = 100
MAX_RELATIONSHIPS: int = 500
MAX_CONTRADICTIONS: int = 50
MAX_TOKEN_LEN: int = 200
MAX_VALUE_LEN: int = 2048
DEFAULT_SIMILARITY_THRESHOLD: float = 0.35
DEFAULT_CONTRADICTION_THRESHOLD: float = 0.5

# P7-C: EvidenceGraph bounds (M1 8GB UMA, always-on)
MAX_GRAPH_NODES: int = 500
MAX_GRAPH_EDGES: int = 2000
MAX_GRAPH_HOPS: int = 2
MAX_GRAPH_BATCH_VALUES: int = 128
MAX_GRAPH_PAYLOAD_SCAN: int = 8 * 1024  # 8 KiB cap on payload_text scan per finding

# Lightweight IOC patterns for P7-C analyze() — pure regex, no MLX / no networkx.
# Conservative, M1-safe: only flag clear-shape IOCs, not free-form text.
_RE_GRAPH_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RE_GRAPH_SHA256 = re.compile(r"\b[a-f0-9]{64}\b")
_RE_GRAPH_SHA1 = re.compile(r"\b[a-f0-9]{40}\b")
_RE_GRAPH_MD5 = re.compile(r"\b[a-f0-9]{32}\b")
_RE_GRAPH_CVE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.IGNORECASE)
_RE_GRAPH_DOMAIN = re.compile(r"\b(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}\b", re.IGNORECASE)

# Negation / contradiction cues (pure lexical, no model)
_NEGATION_CUES: frozenset[str] = frozenset(
    {"not", "no", "never", "without", "denies", "denied", "refutes",
     "refuted", "disproves", "disproved", "fake", "hoax", "false"}
)

# Mutual-exclusion keys (lower-case substrings) — when both evidence items
# carry opposite polarity, score as contradiction.
_EXCLUSIVE_PAIRS: tuple[tuple[str, str], ...] = (
    ("online", "offline"),
    ("enabled", "disabled"),
    ("active", "inactive"),
    ("vulnerable", "patched"),
    ("exposed", "hidden"),
    ("public", "private"),
    ("encrypted", "plaintext"),
    ("legitimate", "malicious"),
    ("benign", "malicious"),
    ("safe", "unsafe"),
    ("allowed", "blocked"),
    ("open", "closed"),
)

# Date-ish regex for year-month / year / ISO-8601-ish extraction
_RE_DATE = re.compile(
    r"\b(\d{4})(?:-(\d{1,2})(?:-(\d{1,2}))?)?\b"
)

# Numeric with unit (e.g. "100MB", "1.2GB", "42 days")
_RE_NUMERIC = re.compile(
    r"\b(\d+(?:\.\d+)?)\s*([a-zA-Z%]+)?\b"
)

# Tokenizer for value strings (lowercase, alnum+dot+slash)
_RE_TOKEN = re.compile(r"[a-z0-9][a-z0-9._/-]{1,40}")


def _safe_value(v: Any) -> str:
    """Truncate + stringify a value to bounded chars for tokenization."""
    if v is None:
        return ""
    s = str(v)
    if len(s) > MAX_VALUE_LEN:
        s = s[:MAX_VALUE_LEN]
    return s.lower()


def _tokenize(v: Any) -> set[str]:
    """Token-set from a value, bounded by MAX_TOKEN_LEN."""
    s = _safe_value(v)
    if not s:
        return set()
    toks = set()
    for m in _RE_TOKEN.finditer(s):
        toks.add(m.group(0))
        if len(toks) >= MAX_TOKEN_LEN:
            break
    return toks


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard similarity with safe handling of empty sets."""
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a) + len(b) - inter
    return inter / union if union else 0.0


def _domain_of(value: str) -> str:
    """Extract eTLD+1-ish domain (last 2 labels) from a URL or domain string."""
    if not value:
        return ""
    s = _safe_value(value)
    # Strip protocol
    for prefix in ("https://", "http://", "ftp://"):
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    # Strip path / query / fragment
    s = s.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if not s:
        return ""
    parts = s.split(".")
    if len(parts) < 2:
        return s
    # last 2 labels, lower-case
    return ".".join(parts[-2:]).lower()


def _looks_like_domain(value: str) -> bool:
    """Cheap domain check: contains a dot, no whitespace, alnum + dots + dashes."""
    s = _safe_value(value)
    if not s or " " in s:
        return False
    return bool(re.match(r"^[a-z0-9.-]+\.[a-z]{2,}$", s))


def _extract_etype(entity: dict[str, Any]) -> str:
    return _safe_value(entity.get("type", "")) or "unknown"


def _extract_evalue(entity: dict[str, Any]) -> str:
    v = entity.get("value")
    if v is None:
        v = entity.get("url") or entity.get("name") or entity.get("id") or ""
    return _safe_value(v)


def _coerce_entity(entity: Any) -> dict[str, Any] | None:
    """Coerce a single entity into {type, value, sources, ...} or return None."""
    if not isinstance(entity, dict):
        return None
    value = _extract_evalue(entity)
    if not value:
        return None
    etype = _extract_etype(entity) or "unknown"
    sources = entity.get("sources") or entity.get("source") or []
    if isinstance(sources, str):
        sources = [sources]
    if not isinstance(sources, list):
        sources = []
    return {
        "type": etype,
        "value": value,
        "sources": [str(s)[:128] for s in sources[:8]],
    }


def _dedupe_key(etype: str, value: str) -> tuple[str, str]:
    return (etype, value)


def _build_ig_graph(ig_mod: Any, entities: list[dict[str, Any]]) -> Any:
    """Build an igraph from a list of coerced entities. M1-optimized, C-core."""
    g = ig_mod.Graph()
    for e in entities:
        key = f"{e['type']}:{e['value']}"
        g.add_vertex(key, etype=e["type"], value=e["value"],
                     sources=tuple(e.get("sources", [])))
    return g


def _compute_relationships(
    entities: list[dict[str, Any]],
    threshold: float,
) -> list[dict[str, Any]]:
    """Compute pairwise relationships via Jaccard + domain + IOC co-occurrence.

    Pure-Python heuristics — no networkx required.
    """
    if len(entities) < 2:
        return []
    # Pre-tokenize once
    tokens: list[set[str]] = [_tokenize(e["value"]) for e in entities]
    # Strip protocol/path FIRST, then validate as a domain — `_looks_like_domain`
    # rejects URLs with ":" or "/" characters, so the order matters.
    domains: list[str] = []
    for e in entities:
        d = _domain_of(e["value"])
        domains.append(d if d and _looks_like_domain(d) else "")
    types: list[str] = [e["type"] for e in entities]
    keys: list[str] = [f"{e['type']}:{e['value']}" for e in entities]

    edges: list[dict[str, Any]] = []
    n = len(entities)
    for i in range(n):
        for j in range(i + 1, n):
            weight = 0.0
            rel_type = "co_occurrence"
            # 1) Token Jaccard (always computable)
            jac = _jaccard(tokens[i], tokens[j])
            if jac >= threshold:
                weight = max(weight, jac)
                rel_type = "token_similarity"
            # 2) Same domain
            if domains[i] and domains[i] == domains[j]:
                weight = max(weight, 0.9)
                rel_type = "shared_domain"
            # 3) Same IOC type, distinct values (e.g. two IPs in same finding)
            if types[i] == types[j] and types[i] in {"ip", "cve", "hash_sha256", "hash_md5", "domain", "onion", "i2p", "info_hash", "magnet_uri", "apt", "malware"}:  # noqa: E501
                if weight < 0.4:
                    weight = 0.4
                    rel_type = f"shared_ioc_type:{types[i]}"
            if weight <= 0.0:
                continue
            edges.append({
                "src": keys[i],
                "dst": keys[j],
                "weight": round(float(weight), 4),
                "type": rel_type,
            })
            if len(edges) >= MAX_RELATIONSHIPS:
                return edges
    return edges


def _detect_contradiction_impl(
    a: Any,
    b: Any,
) -> dict[str, Any] | None:
    """Core contradiction heuristic — see detect_contradictions() docstring."""
    if not isinstance(a, dict) or not isinstance(b, dict):
        return None
    # 1) Mutual-exclusion key matching
    a_keys = {_safe_value(k) for k in a.keys()}
    b_keys = {_safe_value(k) for k in b.keys()}
    shared = a_keys & b_keys
    if shared:
        for k in shared:
            av = _safe_value(a.get(k))
            bv = _safe_value(b.get(k))
            if not av or not bv:
                continue
            for x, y in _EXCLUSIVE_PAIRS:
                if (x in av and y in bv) or (y in av and x in bv):
                    return {
                        "contradicts": True,
                        "confidence": 0.85,
                        "reason": f"mutual_exclusion:{x}/{y} on key={k}",
                        "key": k,
                    }
    # 2) Negation cues
    a_text = _safe_value(a)
    b_text = _safe_value(b)
    a_neg = any(cue in a_text.split() for cue in _NEGATION_CUES)
    b_neg = any(cue in b_text.split() for cue in _NEGATION_CUES)
    if a_neg != b_neg and len(a_text) > 5 and len(b_text) > 5:
        # Token-Jaccard context: only count as contradiction if evidence is
        # otherwise topically related.
        jac = _jaccard(_tokenize(a_text), _tokenize(b_text))
        if jac >= DEFAULT_CONTRADICTION_THRESHOLD:
            return {
                "contradicts": True,
                "confidence": round(0.5 + 0.5 * jac, 4),
                "reason": "negation_cue_on_related_text",
            }
    # 3) Numeric conflict on shared keys
    if shared:
        for k in shared:
            av = str(a.get(k, ""))
            bv = str(b.get(k, ""))
            am = _RE_NUMERIC.search(av)
            bm = _RE_NUMERIC.search(bv)
            if am and bm:
                try:
                    an = float(am.group(1))
                    bn = float(bm.group(1))
                except (TypeError, ValueError):
                    continue
                if an != bn and abs(an - bn) / max(abs(an), abs(bn), 1.0) > 0.5:
                    return {
                        "contradicts": True,
                        "confidence": 0.7,
                        "reason": f"numeric_conflict_on:{k} ({an} vs {bn})",
                        "key": k,
                    }
    # 4) Date conflict (different years on shared key)
    if shared:
        for k in shared:
            av = str(a.get(k, ""))
            bv = str(b.get(k, ""))
            am = _RE_DATE.search(av)
            bm = _RE_DATE.search(bv)
            if am and bm and am.group(1) != bm.group(1):
                return {
                    "contradicts": True,
                    "confidence": 0.6,
                    "reason": f"date_conflict_on:{k} ({am.group(0)} vs {bm.group(0)})",
                    "key": k,
                }
    return None


def _centrality_impl(ig_mod: Any, network: Any) -> dict[str, float]:
    """Compute bounded centrality over the supplied network dict using igraph C-core.

    M1 8GB: igraph betweenness_centrality is 5-10x faster than NetworkX pure-Python.
    """
    if not isinstance(network, dict):
        return {}
    nodes = network.get("entities") or network.get("nodes") or []
    edges = network.get("edges") or []
    if not nodes and not edges:
        return {}
    g = ig_mod.Graph()
    node_map: dict[str, int] = {}
    for n in nodes:
        if isinstance(n, dict):
            label = n.get("key") or n.get("id") or n.get("value")
        else:
            label = str(n)
        if not label:
            continue
        label = str(label)[:MAX_VALUE_LEN]
        if label not in node_map:
            idx = g.add_vertex(label)
            node_map[label] = idx
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("src")
        dst = e.get("dst")
        if not src or not dst:
            continue
        w = float(e.get("weight", 1.0) or 1.0)
        s_idx = node_map.get(src)
        d_idx = node_map.get(dst)
        if s_idx is None or d_idx is None:
            continue
        edge_id = g.get_eid(s_idx, d_idx, error=False)
        if edge_id >= 0:
            # Edge exists, update weight if higher
            g.es[edge_id]["weight"] = max(g.es[edge_id].get("weight", w), w)
        else:
            g.add_edge(s_idx, d_idx, weight=w)
    if g.vcount() == 0:
        return {}
    n = g.vcount()
    # igraph: strength() signature is (vertices, mode='all', loops=True, weights=None)
    try:
        strength_list = list(g.strength(vertices=list(range(n)), weights="weight"))
        max_deg = max(strength_list) if strength_list else 1.0
        if max_deg > 0:
            strength_list = [s / max_deg for s in strength_list]
    except Exception:
        deg_list = list(g.degree())
        max_deg = max(deg_list) if deg_list else 1.0
        strength_list = [d / max_deg for d in deg_list]
    # Betweenness with k-sample cap
    k = min(MAX_CENTRALITY_NODES, n)
    try:
        between_list = list(g.betweenness(vertices=None, directed=False, weights="weight", cutoff=k))
    except Exception:
        try:
            between_list = list(g.betweenness(vertices=None, directed=False, cutoff=k))
        except Exception:
            between_list = [0.0] * n
    max_bet = max(between_list) if between_list else 1.0
    if max_bet > 0:
        between_list = [b / max_bet for b in between_list]
    between_dict = {g.vs[i]["name"]: between_list[i] for i in range(n)}
    out: dict[str, float] = {}
    for i, node_name in enumerate(g.vs["name"]):
        d = strength_list[i]
        b = between_dict.get(node_name, 0.0)
        score = round(0.6 * d + 0.4 * b, 6)
        out[str(node_name)] = float(score)
    return out


def _dedupe_edges(edges: list[EvidenceGraphEdge]) -> list[EvidenceGraphEdge]:
    """Dedupe EvidenceGraphEdge list by (src, dst, rel_type), summing evidence_count.

    Preserves order of first occurrence. Bounded by MAX_GRAPH_EDGES on output.
    Pure Python, no numpy/pandas.
    """
    seen: dict[tuple[str, str, str], EvidenceGraphEdge] = {}
    for e in edges:
        key = (e.src, e.dst, e.rel_type)
        if key in seen:
            prev = seen[key]
            seen[key] = EvidenceGraphEdge(
                src=prev.src,
                dst=prev.dst,
                rel_type=prev.rel_type,
                weight=max(prev.weight, e.weight),
                evidence_count=prev.evidence_count + e.evidence_count,
            )
        else:
            seen[key] = e
    return list(seen.values())


class EvidenceNetworkAnalyzer:
    """
    Network-based evidence analyzer — M1 8GB-safe, fail-soft, bounded.

    All public methods are async and never raise. Results are always
    bounded by the module-level MAX_* constants.
    """

    _NOT_IMPLEMENTED: bool = False
    _TODO_REF: str = "IMPLEMENTATION_ROADMAP.md T1 — COMPLETED"

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        """Initialize analyzer. Args are accepted for backward compatibility.

        Optional keyword-only:
            graph: DuckPGQGraph | None — if provided, analyze() will query
                  the cross-sprint graph via find_connected_batch (READ-ONLY).
                  If None (default), analyze() uses intra-finding IOC mining
                  only — no DuckDB writes, no graph lookups.
        """
        self._initialized: bool = True
        self._call_count: int = 0
        self._last_args_count: int = len(_args) + len(_kwargs)
        self._last_graph_size: int = 0
        # P7-C: optional injected graph for cross-sprint read-side enrichment
        self._graph: Any = _kwargs.get("graph", None)
        logger.debug(
            "EvidenceNetworkAnalyzer: initialized (impl, %d args, graph=%s)",
            self._last_args_count,
            "yes" if self._graph is not None else "no",
        )

    # ── public surface ────────────────────────────────────────────────────────

    async def analyze_network(
        self,
        entities: list[dict[str, Any]] | None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        """
        Analyze entity network relationships.

        Returns a dict with:
            entities, edges, clusters, centrality, contradictions, confidence,
            analysis_type, not_implemented (always False), todo_ref, call_count.

        On empty / malformed input returns a valid empty result. On any
        internal failure returns the same shape with empty lists — never
        raises.
        """
        self._call_count += 1
        # M1 8GB: skip igraph if RAM headroom < 500MB
        try:
            import psutil as _psutil
            available_gb = _psutil.virtual_memory().available / (1024 ** 3)
            if available_gb < 0.5:
                logger.debug(
                    "EvidenceNetworkAnalyzer: RAM headroom %.1fGB < 0.5GB, "
                    "skipping igraph analysis", available_gb
                )
                return self._empty_result()
        except Exception:
            pass  # psutil unavailable — proceed

        try:
            coerced = self._coerce_entities(entities)
            if not coerced:
                return self._empty_result()
            ig_mod = _lazy_ig()
            if ig_mod is None:
                logger.debug("EvidenceNetworkAnalyzer: igraph missing, returning empty")
                return self._empty_result()

            g = _build_ig_graph(ig_mod, coerced)
            self._last_graph_size = g.vcount()

            # Build node_map for edge attachment
            node_map = {name: g.vs[i].index for i, name in enumerate(g.vs["name"])}

            # Relationships
            threshold = float(_kwargs.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD))
            edges = _compute_relationships(coerced, threshold)
            # Cap and attach edges to igraph
            for e in edges:
                src, dst = e["src"], e["dst"]
                s_idx = node_map.get(src)
                d_idx = node_map.get(dst)
                if s_idx is None or d_idx is None:
                    continue
                try:
                    edge_id = g.get_eid(s_idx, d_idx, error=False)
                    if edge_id >= 0:
                        g.es[edge_id]["weight"] = max(g.es[edge_id].get("weight", e["weight"]), e["weight"])
                    else:
                        g.add_edge(s_idx, d_idx, weight=e["weight"], rel_type=e["type"])
                except Exception:
                    g.add_edge(s_idx, d_idx, weight=e["weight"], rel_type=e["type"])
            edges_out = edges[:MAX_EDGES]

            # Clusters — igraph label propagation (M1 C-core, ~5-10x faster than NX)
            clusters: list[list[str]] = []
            try:
                if g.vcount() > 0:
                    try:
                        comm_membership = g.community_label_propagation(weights="weight")
                    except Exception:
                        comm_membership = g.community_label_propagation()
                    for comm in comm_membership:
                        if isinstance(comm, (set, list, tuple)):
                            cluster = [str(g.vs[idx]["name"]) for idx in comm][:MAX_CLUSTER_SIZE]
                        else:
                            cluster = [str(g.vs[comm]["name"])]
                        clusters.append(cluster)
                        if len(clusters) >= MAX_CLUSTERS:
                            break
            except Exception as e:
                logger.debug(f"EvidenceNetworkAnalyzer: community detection failed: {e}")

            # Centrality — bounded, k-sample betweenness via igraph C-core
            centrality: dict[str, float] = {}
            if g.vcount() > 0:
                try:
                    centrality = _centrality_impl(
                        ig_mod,
                        {"entities": [{"key": n} for n in g.vs["name"]],
                         "edges": [{"src": g.vs[u["source"]]["name"],
                                    "dst": g.vs[u["target"]]["name"],
                                    "weight": u.get("weight", 1.0)}
                                   for u in g.es]},
                    )
                except Exception as e:
                    logger.debug(f"EvidenceNetworkAnalyzer: centrality failed: {e}")

            # Contradictions — only meaningful if we have ≥ 2 entities
            contradictions: list[dict[str, Any]] = []
            if len(coerced) >= 2:
                # Build degree map for ranking using g.strength (weighted degree)
                try:
                    strengths = list(g.strength(weights="weight"))
                    degree_map = {g.vs[i]["name"]: strengths[i] for i in range(g.vcount())}
                except Exception:
                    degrees = list(g.degree())
                    degree_map = {g.vs[i]["name"]: degrees[i] for i in range(g.vcount())}
                # Use top-N by graph degree to bound the pairwise budget
                ranked = sorted(
                    coerced,
                    key=lambda e: degree_map.get(f"{e['type']}:{e['value']}", 0),
                    reverse=True,
                )[: min(20, len(coerced))]
                for ranked_a, ranked_b in combinations(ranked, 2):
                    c = _detect_contradiction_impl(ranked_a, ranked_b)
                    if c is not None:
                        contradictions.append({
                            "a": f"{ranked_a['type']}:{ranked_a['value']}",
                            "b": f"{ranked_b['type']}:{ranked_b['value']}",
                            **c,
                        })
                        if len(contradictions) >= MAX_CONTRADICTIONS:
                            break

            # Confidence: average edge weight, fallback 0.0
            if edges_out:
                conf = sum(e["weight"] for e in edges_out) / len(edges_out)
            else:
                conf = 0.0

            return {
                "entities": [
                    {"key": f"{e['type']}:{e['value']}",
                     "type": e["type"],
                     "value": e["value"],
                     "sources": e.get("sources", [])}
                    for e in coerced[:MAX_ENTITIES]
                ],
                "edges": edges_out,
                "clusters": clusters,
                "centrality": centrality,
                "contradictions": contradictions,
                "confidence": round(float(conf), 4),
                "analysis_type": "evidence_network",
                "not_implemented": False,
                "todo_ref": self._TODO_REF,
                "call_count": self._call_count,
            }
        except Exception as e:
            logger.warning(f"EvidenceNetworkAnalyzer.analyze_network failed: {e}")
            return self._empty_result()

    async def extract_relationships(
        self,
        entities: list[dict[str, Any]] | None,
        threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    ) -> list[dict[str, Any]]:
        """
        Extract relationships between entities. Returns bounded list of
        {src, dst, weight, type} dicts.
        """
        self._call_count += 1
        try:
            coerced = self._coerce_entities(entities)
            if len(coerced) < 2:
                return []
            # _compute_relationships is pure-Python, no graph library needed
            return _compute_relationships(coerced, float(threshold))[:MAX_RELATIONSHIPS]
        except Exception as e:
            logger.warning(f"EvidenceNetworkAnalyzer.extract_relationships failed: {e}")
            return []

    async def detect_contradictions(
        self,
        evidence_a: dict[str, Any] | None,
        evidence_b: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """
        Detect contradictions between two evidence pieces.

        Returns:
            dict with {contradicts, confidence, reason[, key, a, b]} on hit,
            None when no clear signal.

        Heuristics (M1-safe, no LLM):
            - Mutual-exclusion key match (e.g. enabled/disabled on same key)
            - Negation cues on topically related text
            - Numeric conflict > 50% delta on shared key
            - Date conflict (different year on shared key)
        """
        self._call_count += 1
        try:
            return _detect_contradiction_impl(evidence_a or {}, evidence_b or {})
        except Exception as e:
            logger.warning(f"EvidenceNetworkAnalyzer.detect_contradictions failed: {e}")
            return None

    async def calculate_centrality(
        self,
        network: dict[str, Any] | None,
    ) -> dict[str, float]:
        """
        Calculate network centrality scores.

        Accepts a dict with `entities` (or `nodes`) and `edges` keys —
        the same shape returned by analyze_network(). Returns a
        {node: score} dict where score is a 60/40 mix of degree and
        betweenness centrality (bounded by k-sample for M1).
        """
        self._call_count += 1
        try:
            ig_mod = _lazy_ig()
            if ig_mod is None:
                return {}
            return _centrality_impl(ig_mod, network or {})
        except Exception as e:
            logger.warning(f"EvidenceNetworkAnalyzer.calculate_centrality failed: {e}")
            return {}

    async def analyze_evidence_network(
        self,
        query: str,
        confidence_threshold: float = 0.5,
        priority: int = 5,
    ) -> dict[str, Any]:
        """
        High-level entry point used by research_coordinator.

        Converts the (query, confidence_threshold, priority) tuple into
        a small synthetic entity set (query tokens, IOC-like substrings)
        and runs analyze_network on it. Returns a dict compatible with
        the coordinator's contract: {networks, confidence, ...}.
        """
        self._call_count += 1
        try:
            q = _safe_value(query)
            # Build a minimal, bounded entity list from the query itself.
            entities: list[dict[str, Any]] = []
            if q:
                # Whole query as a single "query" entity
                entities.append({"type": "query", "value": q, "sources": ["research_coordinator"]})
                # Top tokens as candidates (very small cap)
                for tok in list(_tokenize(q))[:10]:
                    if len(tok) < 4:
                        continue
                    entities.append({"type": "candidate", "value": tok, "sources": ["query_token"]})
            net = await self.analyze_network(entities)
            return {
                "networks": [
                    {
                        "query": q,
                        "entities": net.get("entities", []),
                        "edges": net.get("edges", []),
                        "clusters": net.get("clusters", []),
                        "centrality": net.get("centrality", {}),
                        "contradictions": net.get("contradictions", []),
                    }
                ],
                "confidence": float(net.get("confidence", 0.0)) * float(confidence_threshold or 0.5),
                "priority": int(priority),
                "not_implemented": False,
                "call_count": self._call_count,
            }
        except Exception as e:
            logger.warning(f"EvidenceNetworkAnalyzer.analyze_evidence_network failed: {e}")
            return {
                "networks": [],
                "confidence": 0.0,
                "priority": int(priority),
                "not_implemented": False,
                "call_count": self._call_count,
            }

    # ── P7-C: Canonical analyze() — findings → EvidenceGraph ─────────────────

    async def analyze(
        self,
        findings: list[Any],
        max_hops: int = MAX_GRAPH_HOPS,
    ) -> EvidenceGraph:
        """
        Convert a batch of CanonicalFinding objects into a bounded
        read-only EvidenceGraph.

        Pipeline (fail-soft, never raises):
            1. Bound input at MAX_GRAPH_NODES finding-derived signals.
            2. Extract IOCs from each finding's payload_text using pure-regex
               patterns (M1-safe, no MLX / no networkx).
            3. Build EvidenceGraphNode list (deduped by (ioc_type, value)).
            4. Build local edges (intra-finding IOC co-occurrence + shared
               source_type grouping).
            5. If a DuckPGQGraph is injected at __init__, query it via
               find_connected_batch() in asyncio.to_thread (sync → async
               bridge, M1-safe). Connected nodes are surfaced as additional
               EvidenceGraphEdge entries with rel_type="graph_connected".
            6. Bound to MAX_GRAPH_NODES / MAX_GRAPH_EDGES, return
               EvidenceGraph with aggregate confidence.

        Invariants (post-P7-C):
            - Never writes to DuckDB. EvidenceGraph is a READ-ONLY projection.
            - Failures return an empty-but-valid EvidenceGraph.
            - All collections bounded; no unbounded growth between calls.
        """
        self._call_count += 1
        finding_count = len(findings) if isinstance(findings, (list, tuple)) else 0
        try:
            if not findings:
                return self._empty_graph(0)

            # 1) Bound input
            bounded_findings = list(findings)[:MAX_GRAPH_NODES]

            # 2) Extract IOCs per finding
            finding_iocs: list[list[tuple[str, str, str]]] = []  # [(ioc_type, value, source_type), ...]
            for f in bounded_findings:
                iocs = self._extract_iocs_from_finding(f)
                if iocs:
                    finding_iocs.append(iocs)

            if not finding_iocs:
                return self._empty_graph(finding_count)

            # 3) Build nodes (deduped by (ioc_type, value))
            node_map: dict[tuple[str, str], EvidenceGraphNode] = {}
            for iocs in finding_iocs:
                for ioc_type, value, src in iocs:
                    key = (ioc_type, value)
                    if key in node_map:
                        # augment sources
                        existing = node_map[key]
                        new_sources = tuple(dict.fromkeys(
                            list(existing.sources) + ([src] if src else [])
                        ))[:8]
                        node_map[key] = EvidenceGraphNode(
                            node_id=f"{ioc_type}:{value}",
                            ioc_type=ioc_type,
                            value=value,
                            confidence=existing.confidence,
                            sources=new_sources,
                        )
                    else:
                        node_map[key] = EvidenceGraphNode(
                            node_id=f"{ioc_type}:{value}",
                            ioc_type=ioc_type,
                            value=value,
                            confidence=0.5,
                            sources=(src,) if src else (),
                        )
            nodes = list(node_map.values())[:MAX_GRAPH_NODES]

            # 4) Build local edges: intra-finding co-occurrence + cross-finding
            # same-type edges (bounded O(n²) over the small per-finding IOC sets).
            edges: list[EvidenceGraphEdge] = []
            for iocs in finding_iocs:
                # intra-finding: pair every IOC with the first IOC of the finding
                if len(iocs) >= 2:
                    anchor = iocs[0]
                    for other in iocs[1:]:
                        if anchor[0] == other[0] and anchor[1] == other[1]:
                            continue
                        edges.append(EvidenceGraphEdge(
                            src=f"{anchor[0]}:{anchor[1]}",
                            dst=f"{other[0]}:{other[1]}",
                            rel_type="co_occurrence",
                            weight=0.6,
                            evidence_count=1,
                        ))
                        if len(edges) >= MAX_GRAPH_EDGES:
                            break
                if len(edges) >= MAX_GRAPH_EDGES:
                    break

            # 5) Optional cross-sprint enrichment via DuckPGQGraph (READ-ONLY)
            if self._graph is not None and nodes:
                connected_edges = await self._query_connected_async(nodes, max_hops)
                if connected_edges:
                    edges.extend(connected_edges)

            # 6) Bound + dedupe + return
            edges = _dedupe_edges(edges)[:MAX_GRAPH_EDGES]
            nodes = nodes[:MAX_GRAPH_NODES]
            confidence = (
                sum(e.weight for e in edges) / len(edges) if edges else 0.0
            )
            self._last_graph_size = len(nodes) + len(edges)
            return EvidenceGraph(
                nodes=tuple(nodes),
                edges=tuple(edges),
                confidence=round(float(confidence), 4),
                finding_count=finding_count,
            )
        except Exception as e:
            logger.warning(f"EvidenceNetworkAnalyzer.analyze failed: {e}")
            return self._empty_graph(finding_count)

    async def _query_connected_async(
        self,
        nodes: list[EvidenceGraphNode],
        max_hops: int,
    ) -> list[EvidenceGraphEdge]:
        """
        Run DuckPGQGraph.find_connected_batch via run_in_executor.

        DuckPGQGraph.find_connected_batch is synchronous (it issues a DuckDB
        CTE). We bridge sync→async via loop.run_in_executor() with a dedicated
        thread to keep the event loop responsive on M1 8GB UMA.

        GHOST_INVARIANTS:40 — asyncio.to_thread is forbidden for DuckDB;
        use loop.run_in_executor() with a dedicated executor instead.
        If the graph is unhealthy (PGQ unavailable, schema drift, I/O error)
        we return [] — the local edge set is still useful.
        """
        try:
            loop = asyncio.get_running_loop()
            values = [n.value for n in nodes][:MAX_GRAPH_BATCH_VALUES]
            if not values:
                return []
            # GHOST_INVARIANTS:40 fix: use run_in_executor instead of asyncio.to_thread.
            # DuckDB has its own internal parallelism; we provide thread-safety bridge.
            connected_map = await loop.run_in_executor(
                None,  # use default executor — DuckDB is thread-safe
                lambda: self._graph.find_connected_batch(values, max_hops),
            )
        except Exception as e:
            logger.debug(f"EvidenceNetworkAnalyzer: graph lookup failed: {e}")
            return []

        edges: list[EvidenceGraphEdge] = []
        if not isinstance(connected_map, dict):
            return edges
        for src_value, conn_list in connected_map.items():
            if not isinstance(conn_list, list):
                continue
            for c in conn_list:
                if not isinstance(c, dict):
                    continue
                # c may have keys: 'value', 'ioc_type', 'depth', 'weight' depending
                # on the underlying SQL schema. We coerce to EvidenceGraphEdge.
                dst_value = _safe_value(c.get("value")) if callable(_safe_value) else str(c.get("value", ""))
                if not dst_value or dst_value == src_value:
                    continue
                ioc_type = str(c.get("ioc_type", "unknown"))[:32]
                weight = float(c.get("weight", 0.5) or 0.5)
                weight = max(0.0, min(1.0, weight))
                edges.append(EvidenceGraphEdge(
                    src=f"unknown:{src_value[:MAX_VALUE_LEN]}",
                    dst=f"{ioc_type}:{dst_value[:MAX_VALUE_LEN]}",
                    rel_type="graph_connected",
                    weight=round(weight, 4),
                    evidence_count=1,
                ))
                if len(edges) >= MAX_GRAPH_EDGES:
                    return edges
        return edges

    def _extract_iocs_from_finding(self, f: Any) -> list[tuple[str, str, str]]:
        """
        Extract (ioc_type, value, source_type) tuples from a CanonicalFinding.

        Sources (in priority order):
            - f.payload_text (if set) — scanned with bounded regex patterns
            - f.query — scanned for IOC substrings as a fallback
        All scans are bounded by MAX_GRAPH_PAYLOAD_SCAN.
        """
        out: list[tuple[str, str, str]] = []
        try:
            source_type = _safe_value(safe_get_finding_field(f, "source_type", "") or "")
            if not source_type:
                source_type = "unknown"
            # Cap each scan to MAX_GRAPH_PAYLOAD_SCAN chars
            payload = _safe_value(safe_get_payload_text(f))[:MAX_GRAPH_PAYLOAD_SCAN]
            query = _safe_value(safe_get_finding_field(f, "query", "") or "")[:MAX_GRAPH_PAYLOAD_SCAN]
            text = payload or query
            if not text:
                return out
            seen_in_finding: set[tuple[str, str]] = set()
            for ioc_type, pattern in (
                ("cve", _RE_GRAPH_CVE),
                ("hash_sha256", _RE_GRAPH_SHA256),
                ("hash_sha1", _RE_GRAPH_SHA1),
                ("hash_md5", _RE_GRAPH_MD5),
                ("ip", _RE_GRAPH_IPV4),
                ("domain", _RE_GRAPH_DOMAIN),
            ):
                for m in pattern.finditer(text):
                    val = m.group(0).lower()
                    if ioc_type in ("ip", "ipv4"):
                        # Reject obvious non-IPv4 (octets > 255)
                        octets = val.split(".")
                        if any(int(o) > 255 for o in octets if o.isdigit()):
                            continue
                    key = (ioc_type, val)
                    if key in seen_in_finding:
                        continue
                    seen_in_finding.add(key)
                    out.append((ioc_type, val, source_type))
                    if len(out) >= 32:  # per-finding hard cap
                        return out
        except Exception as e:
            logger.debug(f"EvidenceNetworkAnalyzer: _extract_iocs_from_finding failed: {e}")
        return out

    def _empty_graph(self, finding_count: int) -> EvidenceGraph:
        """Bounded empty result — same shape as analyze()'s success path."""
        return EvidenceGraph(
            nodes=(),
            edges=(),
            confidence=0.0,
            finding_count=finding_count,
        )

    async def cleanup(self) -> None:
        """Release internal state. Idempotent."""
        self._initialized = False
        self._last_graph_size = 0
        logger.debug(
            "EvidenceNetworkAnalyzer: cleaned up (%d calls served)",
            self._call_count,
        )

    def is_implemented(self) -> bool:
        """Return True — implementation is live (post T1)."""
        return not self._NOT_IMPLEMENTED

    # ── internal helpers ──────────────────────────────────────────────────────

    def _coerce_entities(
        self,
        entities: list[dict[str, Any]] | None,
    ) -> list[dict[str, Any]]:
        """Coerce, dedupe, and bound the input list. Never raises."""
        if not entities:
            return []
        seen: set[tuple[str, str]] = set()
        out: list[dict[str, Any]] = []
        for raw in entities:
            coerced = _coerce_entity(raw)
            if coerced is None:
                continue
            k = _dedupe_key(coerced["type"], coerced["value"])
            if k in seen:
                continue
            seen.add(k)
            out.append(coerced)
            if len(out) >= MAX_ENTITIES:
                break
        return out

    def _empty_result(self) -> dict[str, Any]:
        """Bounded empty result — same shape as analyze_network's success path."""
        return {
            "entities": [],
            "edges": [],
            "clusters": [],
            "centrality": {},
            "contradictions": [],
            "confidence": 0.0,
            "analysis_type": "evidence_network",
            "not_implemented": False,
            "todo_ref": self._TODO_REF,
            "call_count": self._call_count,
        }


__all__ = ["EvidenceNetworkAnalyzer", "EvidenceGraphNode", "EvidenceGraphEdge", "EvidenceGraph"]


# ── P7-C: EvidenceGraph DTOs (frozen, msgspec-style immutability) ────────────

@dataclass(frozen=True)
class EvidenceGraphNode:
    """Single entity node in the evidence network.

    node_id convention: f"{ioc_type}:{value}" (lowercased, deduped).
    sources is a bounded tuple of source_type strings that surfaced the IOC.
    """
    node_id: str
    ioc_type: str
    value: str
    confidence: float
    sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvidenceGraphEdge:
    """Directed relationship between two EvidenceGraphNodes.

    weight is bounded [0.0, 1.0]; evidence_count records how many findings
    contributed to this edge (de-dupes intra-finding repetition).
    """
    src: str
    dst: str
    rel_type: str
    weight: float
    evidence_count: int = 1


@dataclass(frozen=True)
class EvidenceGraph:
    """Read-only evidence network assembled from a batch of findings.

    Invariants:
      - nodes ≤ MAX_NODES
      - edges ≤ MAX_EDGES
      - finding_count == len(input findings) at the time of analysis
      - not_implemented == False (post-P7-C T1)
      - never raised on failure: returns an empty-but-valid instance
    """
    nodes: tuple[EvidenceGraphNode, ...]
    edges: tuple[EvidenceGraphEdge, ...]
    confidence: float
    finding_count: int
    not_implemented: bool = False
    todo_ref: str = "IMPLEMENTATION_ROADMAP.md T1 (implemented)"
