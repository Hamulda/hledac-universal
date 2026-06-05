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
    - Community detection:    networkx greedy_modularity_communities
      (pure-Python, no scipy sparse dependency at call time).
    - Centrality:             degree_centrality (full) + betweenness_centrality
      with k-sample cap to keep O(n·k) instead of O(n²).
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

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


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


def _build_nx_graph(networkx_mod: Any, entities: list[dict[str, Any]]) -> Any:
    """Build a networkx.Graph from a list of coerced entities. Lazy import."""
    g = networkx_mod.Graph()
    for e in entities:
        key = f"{e['type']}:{e['value']}"
        g.add_node(key, etype=e["type"], value=e["value"],
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
            if types[i] == types[j] and types[i] in {"ip", "cve", "hash_sha256", "hash_md5", "domain", "onion", "i2p", "info_hash", "magnet_uri", "apt", "malware"}:
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


def _centrality_impl(networkx_mod: Any, network: Any) -> dict[str, float]:
    """Compute bounded centrality over the supplied network dict."""
    if not isinstance(network, dict):
        return {}
    nodes = network.get("entities") or network.get("nodes") or []
    edges = network.get("edges") or []
    if not nodes and not edges:
        return {}
    g = networkx_mod.Graph()
    for n in nodes:
        if isinstance(n, dict):
            label = n.get("key") or n.get("id") or n.get("value")
        else:
            label = str(n)
        if not label:
            continue
        g.add_node(str(label)[:MAX_VALUE_LEN])
    for e in edges:
        if not isinstance(e, dict):
            continue
        src = e.get("src")
        dst = e.get("dst")
        if not src or not dst:
            continue
        w = float(e.get("weight", 1.0) or 1.0)
        if g.has_edge(src, dst):
            g[src][dst]["weight"] = max(g[src][dst].get("weight", w), w)
        else:
            g.add_edge(src, dst, weight=w)
    if g.number_of_nodes() == 0:
        return {}
    n = g.number_of_nodes()
    degree = networkx_mod.degree_centrality(g)
    # Betweenness with k-sample cap to keep O(n·k) — critical for M1.
    k = min(MAX_CENTRALITY_NODES, n)
    try:
        between = networkx_mod.betweenness_centrality(g, k=k, normalized=True, seed=42)
    except TypeError:
        # Older networkx may not accept seed; fall back to deterministic-free call.
        between = networkx_mod.betweenness_centrality(g, k=k, normalized=True)
    out: dict[str, float] = {}
    for node in g.nodes():
        d = degree.get(node, 0.0)
        b = between.get(node, 0.0)
        # Combined score: weighted, biased toward degree for stability.
        score = round(0.6 * d + 0.4 * b, 6)
        out[str(node)] = float(score)
    return out


def _lazy_nx() -> Any:
    """Lazy import of networkx — keeps module-level import surface clean."""
    try:
        import networkx  # type: ignore[import-not-found]
        return networkx
    except Exception as e:
        logger.debug(f"EvidenceNetworkAnalyzer: networkx unavailable: {e}")
        return None


class EvidenceNetworkAnalyzer:
    """
    Network-based evidence analyzer — M1 8GB-safe, fail-soft, bounded.

    All public methods are async and never raise. Results are always
    bounded by the module-level MAX_* constants.
    """

    _NOT_IMPLEMENTED: bool = False
    _TODO_REF: str = "IMPLEMENTATION_ROADMAP.md T1 (implemented)"

    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        """Initialize analyzer. Args are accepted for backward compatibility."""
        self._initialized: bool = True
        self._call_count: int = 0
        self._last_args_count: int = len(_args) + len(_kwargs)
        self._last_graph_size: int = 0
        logger.debug(
            "EvidenceNetworkAnalyzer: initialized (impl, %d args)",
            self._last_args_count,
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
        try:
            coerced = self._coerce_entities(entities)
            if not coerced:
                return self._empty_result()
            nx_mod = _lazy_nx()
            if nx_mod is None:
                logger.debug("EvidenceNetworkAnalyzer: networkx missing, returning empty")
                return self._empty_result()

            g = _build_nx_graph(nx_mod, coerced)
            self._last_graph_size = g.number_of_nodes()

            # Relationships
            threshold = float(_kwargs.get("similarity_threshold", DEFAULT_SIMILARITY_THRESHOLD))
            edges = _compute_relationships(coerced, threshold)
            # Cap and attach
            for e in edges:
                src, dst = e["src"], e["dst"]
                if g.has_edge(src, dst):
                    g[src][dst]["weight"] = max(g[src][dst].get("weight", e["weight"]), e["weight"])
                else:
                    g.add_edge(src, dst, weight=e["weight"], type=e["type"])
            edges_out = edges[:MAX_EDGES]

            # Clusters — greedy modularity (pure-Python, M1-safe)
            clusters: list[list[str]] = []
            try:
                comms = list(nx_mod.algorithms.community.greedy_modularity_communities(g))
                for comm in comms[:MAX_CLUSTERS]:
                    cluster = [str(n) for n in comm][:MAX_CLUSTER_SIZE]
                    clusters.append(cluster)
            except Exception as e:
                logger.debug(f"EvidenceNetworkAnalyzer: community detection failed: {e}")

            # Centrality — bounded, k-sample betweenness
            centrality: dict[str, float] = {}
            if g.number_of_nodes() > 0:
                try:
                    centrality = _centrality_impl(
                        nx_mod,
                        {"entities": [{"key": n} for n in g.nodes()],
                         "edges": [{"src": u, "dst": v, "weight": d.get("weight", 1.0)}
                                   for u, v, d in g.edges(data=True)]},
                    )
                except Exception as e:
                    logger.debug(f"EvidenceNetworkAnalyzer: centrality failed: {e}")

            # Contradictions — only meaningful if we have ≥ 2 entities
            contradictions: list[dict[str, Any]] = []
            if len(coerced) >= 2:
                # Use top-N by graph degree to bound the pairwise budget
                ranked = sorted(
                    coerced,
                    key=lambda e: g.degree(f"{e['type']}:{e['value']}"),
                    reverse=True,
                )[: min(20, len(coerced))]
                for i in range(len(ranked)):
                    for j in range(i + 1, len(ranked)):
                        c = _detect_contradiction_impl(ranked[i], ranked[j])
                        if c is not None:
                            contradictions.append({
                                "a": f"{ranked[i]['type']}:{ranked[i]['value']}",
                                "b": f"{ranked[j]['type']}:{ranked[j]['value']}",
                                **c,
                            })
                            if len(contradictions) >= MAX_CONTRADICTIONS:
                                break
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
            nx_mod = _lazy_nx()
            if nx_mod is None:
                return []
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
            nx_mod = _lazy_nx()
            if nx_mod is None:
                return {}
            return _centrality_impl(nx_mod, network or {})
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
            if not isinstance(raw, dict):
                continue
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


__all__ = ["EvidenceNetworkAnalyzer"]
