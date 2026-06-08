"""
Hypothesis Engine — Pack DTOs (C4 Tier-4)
==========================================

Sprint F262OBS-Tier4: Pure-DTO classes extracted from
:mod:`brain.hypothesis_engine` — the 714 LOC pack definitions
that carry the "what next?" advice for the sprint scheduler.

Scope
-----
- :class:`SourceHint` — source recommendation with quality score (5 LOC)
- :class:`HypothesisPack` — bounded hypothesis/query pack from findings
  (706 LOC, 17 methods: 3 ``cached_property`` + 1 ``property`` + 13 plain
  methods)

Why Plain ``@dataclass`` (not ``frozen=True``)
-----------------------------------------------
``HypothesisPack`` fields are mutable ``list[dict]`` /
``list[Any]`` containers populated incrementally by builders
(``build_hypothesis_pack()``, ``_model_assisted_hypothesis_pack()``).
``@dataclass(frozen=True)`` would forbid ``self.hypotheses.append(...)``
in those builders. Plain ``@dataclass`` is the only correct choice.

Why No Engine Coupling
----------------------
HypothesisPack has zero runtime dependency on ``HypothesisEngine``:

- No ``self.hypothesis_engine`` reference
- No MLX/Metal/cache_limit access
- No inline imports of engine-side helpers
- All 17 methods are pure functions of ``self`` and their arguments

This makes the extraction safe: HypothesisPack can live in its own
module and be re-imported by any caller.

GHOST_INVARIANTS:
- Field names, defaults, and ordering preserved byte-for-byte.
- All 17 method bodies preserved verbatim — no refactor, no rename.
- Backward compat shim lives in ``brain/research_hypothesis_engine.py``:
  ``from brain.hypothesis.packs import SourceHint, HypothesisPack``
- New code should prefer the forward import:
  ``from brain.hypothesis.packs import SourceHint, HypothesisPack``
"""
from __future__ import annotations

import functools
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SourceHint:
    """Source recommendation with quality score."""
    source: str
    quality: float  # 0-1
    hint_type: str = "general"  # trusted_source, quoted_source, general


@dataclass
class HypothesisPack:
    """
    Bounded hypothesis/query pack from findings.

    Returned by build_hypothesis_pack() - practical OSINT guidance
    without requiring heavy model.

    Field roles (STRICT separation - each field has one job):
    - hypotheses: Concrete follow-up claims to verify (what might be true).
      NOT search queries. NOT IOCs. Pure "X might be connected to Y" claims.
    - suggested_queries: Ranked search queries to execute (how to investigate).
      Structured with query/rationale/type/priority/pivot_type.
      These are the actual search strings for the scheduler.
    - ioc_follow_ups: Structured IOC pivot trails (actionable IOC chains).
      Each has pivot/from/to/query/rationale/priority.
      These are domain-specific pivot paths, not general queries.
    - source_hints: Where to look next (quality-ranked sources).
      Each has source/quality/hint_type - not queries or IOCs.
    - provenance: "heuristic" or "model-assisted" (never mixed).

    Priority order for ranking: IOC pivots > entity-pair > relationship > broad entity
    """
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    suggested_queries: list[dict[str, Any]] = field(default_factory=list)  # Has priority, pivot_type
    ioc_follow_ups: list[dict[str, Any]] = field(default_factory=list)    # Has priority, to field
    source_hints: list[Any] = field(default_factory=list)
    provenance: str = "heuristic"  # "heuristic" or "model-assisted"

    # -------------------------------------------------------------------------
    # Sprint F187E: Derived fields — scheduler-facing shape (lazy computed)
    # -------------------------------------------------------------------------

    @functools.cached_property
    def signal_quality(self) -> str:
        """Classify signal as strong/mixed/weak for scheduler filtering.

        Derived from pack content — no model required.
        """
        strong_indicators = (
            len(self.hypotheses) >= 3 and
            len(self.suggested_queries) >= 2 and
            len(self.ioc_follow_ups) >= 1
        )
        weak_indicators = (
            not self.hypotheses and
            not self.suggested_queries and
            not self.ioc_follow_ups
        )
        if strong_indicators:
            return "strong"
        elif weak_indicators:
            return "weak"
        return "mixed"

    @functools.cached_property
    def confidence_note(self) -> str:
        """Human-readable confidence explanation for operator."""
        total = len(self.hypotheses) + len(self.suggested_queries) + len(self.ioc_follow_ups)
        if total >= 8:
            abundance = "rich pack"
        elif total >= 4:
            abundance = "moderate pack"
        elif total >= 1:
            abundance = "thin pack"
        else:
            abundance = "empty pack"
        return f"{abundance} | provenance={self.provenance} | {len(self.source_hints)} source hints"

    @functools.cached_property
    def what_matters_first(self) -> str:
        """Single primary action/takeaway for operator."""
        if self.ioc_follow_ups:
            top_ioc = self.ioc_follow_ups[0]
            return f"Pivot on IOC: {top_ioc.get('from', '?')} → {top_ioc.get('to', '?')}"
        if self.suggested_queries:
            top_q = self.suggested_queries[0]
            return f"Investigate: {top_q.get('query', '')[:80]}"
        if self.hypotheses:
            top_h = self.hypotheses[0]
            return f"Verify: {top_h.get('hypothesis', '')[:80]}"
        return "No immediate action — empty hypothesis pack"

    # -------------------------------------------------------------------------
    # Sprint F187E: operator_shortlist — scheduler-consumable shape
    # Returns items with: action=query, target=rationale[:80], rationale=pivot_type
    # -------------------------------------------------------------------------

    @property
    def operator_shortlist(self) -> list[dict[str, Any]]:
        """Bounded operator shortlist (max 3) in scheduler-consumable shape.

        Returns items: {action: query, target: rationale[:80], rationale: pivot_type}
        """
        raw = self.actionable_shortlist(max_items=3)
        return [
            {
                "action": item.get("query", ""),
                "target": item.get("rationale", "")[:80],
                "rationale": item.get("pivot_type", ""),
            }
            for item in raw
        ]

    def is_empty(self) -> bool:
        """Check if pack has any actionable content."""
        return (
            not self.hypotheses
            and not self.suggested_queries
            and not self.ioc_follow_ups
        )

    def summary(self) -> str:
        """One-line summary of pack contents."""
        parts = []
        if self.hypotheses:
            parts.append(f"{len(self.hypotheses)} hypotheses")
        if self.suggested_queries:
            types = {}
            for q in self.suggested_queries:
                t = q.get("type", "unknown")
                types[t] = types.get(t, 0) + 1
            type_str = ", ".join(f"{v} {k}" for k, v in list(types.items())[:3])
            parts.append(f"{len(self.suggested_queries)} queries ({type_str})")
        if self.ioc_follow_ups:
            parts.append(f"{len(self.ioc_follow_ups)} IOC pivots")
        if self.source_hints:
            parts.append(f"{len(self.source_hints)} sources")
        return ", ".join(parts) or "empty"

    def top_queries(self, n: int = 3) -> list[dict[str, Any]]:
        """Get top N queries by priority for scheduler."""
        return sorted(self.suggested_queries, key=lambda x: x.get("priority", 0.5), reverse=True)[:n]

    def pivot_trail(self, ioc: str) -> list[dict[str, Any]]:
        """Get all pivots starting from a specific IOC."""
        return [p for p in self.ioc_follow_ups if p.get("from") == ioc]

    # -------------------------------------------------------------------------
    # Sprint F150H.1: next_best_actions - actionable shortlist from pack
    # -------------------------------------------------------------------------

    def next_best_actions(self, max_actions: int = 4) -> list[dict[str, Any]]:
        """
        Return a small, ranked shortlist of next actions.

        Prioritizes: IOC pivots > entity-pair > high-priority queries > sources.
        Returns max_actions items, never blocks, never loads models.

        Each action has: action_type, query, rationale, priority, pivot_type.
        """
        actions: list[dict[str, Any]] = []

        seen_queries: set[str] = set()

        # Pre-populate dedup set from all sources (IOC + queries + source hints)
        # so source_hints never collide with existing queries
        for pivot in self.ioc_follow_ups:
            q = pivot.get("query", "")
            if q:
                seen_queries.add(q)
        for q in self.suggested_queries:
            q_str = q.get("query", "")
            if q_str:
                seen_queries.add(q_str)

        # 1. IOC pivots (highest priority - actionable domain-specific paths)
        for pivot in sorted(self.ioc_follow_ups, key=lambda x: x.get("priority", 0.5), reverse=True)[:2]:
            q = pivot.get("query", "")
            if q and q not in seen_queries:
                actions.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "from_ioc": pivot.get("from", ""),
                    "to_field": pivot.get("to", ""),
                    "rationale": pivot.get("rationale", ""),
                    "priority": pivot.get("priority", 0.8),
                    "pivot_type": "ioc",
                })
                seen_queries.add(q)

        # 2. Top ranked queries (high priority, not already covered)
        for q in sorted(self.suggested_queries, key=lambda x: x.get("priority", 0.5), reverse=True):
            if q["query"] not in seen_queries and len(actions) < max_actions:
                actions.append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                    "pivot_type": q.get("pivot_type", "general"),
                })
                seen_queries.add(q["query"])

        # 3. Source hints (only if we still have room)
        for hint in self.source_hints[:2]:
            if len(actions) >= max_actions:
                break
            actions.append({
                "action_type": "source_check",
                "query": f'"{hint.source}" latest',
                "rationale": f"Source: {hint.source} (quality: {hint.quality:.2f})",
                "priority": hint.quality * 0.6,
                "pivot_type": "source",
            })

        return actions[:max_actions]

    # -------------------------------------------------------------------------
    # Sprint F150H.2: why_best_first - explain the best_first_path choice
    # -------------------------------------------------------------------------

    def why_best_first(self) -> dict[str, Any] | None:
        """
        Explain why best_first_path chose its action.

        Returns a dict with:
        - chosen_action: the best_first_path result
        - reason: human-readable explanation of priority ordering
        - alternatives: what else was available and why it ranked lower
        - pivot_type_rank: where this pivot_type sits in the priority order

        Returns None if pack is empty.
        """
        if self.is_empty():
            return None

        pivot_type_rank = {
            "ioc": 0,
            "ioc_lookup": 0,
            "entity_pair": 1,
            "relationship": 2,
            "ioc_entity": 3,
            "entity": 4,
            "entity_expansion": 5,
            "source": 6,
            "organization": 7,
            "temporal": 8,
            "general": 9,
        }

        chosen = self.best_first_path()
        if not chosen:
            return None

        pt = chosen.get("pivot_type", "general")
        rank = pivot_type_rank.get(pt, 9)
        alternatives: list[dict[str, Any]] = []

        # Collect what ranked lower
        for pivot in self.ioc_follow_ups:
            if pivot.get("query") != chosen.get("query"):
                alternatives.append({
                    "action_type": "ioc_pivot",
                    "query": pivot.get("query", ""),
                    "pivot_type": "ioc",
                    "priority": pivot.get("priority", 0.5),
                    "rank": 0,
                })

        for q in self.suggested_queries:
            if q.get("query") != chosen.get("query"):
                q_pt = q.get("pivot_type", "general")
                alternatives.append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "pivot_type": q_pt,
                    "priority": q.get("priority", 0.5),
                    "rank": pivot_type_rank.get(q_pt, 9),
                })

        # Sort alternatives by priority desc, then rank asc
        alternatives.sort(key=lambda x: (-x.get("priority", 0.5), x.get("rank", 9)))
        alternatives = alternatives[:3]

        if pt == "ioc":
            reason = "IOC pivot selected as highest-priority actionable domain path"
        elif pt == "entity_pair":
            reason = "Entity-pair query selected as most specific relationship probe"
        elif pt == "relationship":
            reason = "Relationship query selected for direct connection verification"
        elif pt == "source":
            reason = "Source check selected as lowest-risk verification path"
        else:
            reason = f"{pt} query selected by priority {chosen.get('priority', 0.5):.2f}"

        return {
            "chosen_action": chosen,
            "reason": reason,
            "pivot_type": pt,
            "pivot_type_rank": rank,
            "alternatives": alternatives,
            "total_ioc_pivots": len(self.ioc_follow_ups),
            "total_queries": len(self.suggested_queries),
            "total_sources": len(self.source_hints),
        }

    # -------------------------------------------------------------------------
    # Sprint F150H.2: discarded_as_redundant - what was dropped and why
    # -------------------------------------------------------------------------

    def discarded_as_redundant(self, max_items: int = 5) -> list[dict[str, Any]]:
        """
        Return items from the pack that were dropped by actionable_shortlist dedup.

        Useful for understanding what was intentionally left out.
        Each item has: action_type, query, reason_discarded, pivot_type, priority.

        Reason codes:
        - 'query_deduped': same query string already in shortlist
        - 'below_priority_threshold': priority below 0.5 and shortlist already full
        - 'low_pivot_type_priority': general/organization pivot types deprioritized
        """
        shortlist_queries: set[str] = {
            a.get("query", "") for a in self.actionable_shortlist(max_items=999)
        }
        discarded: list[dict[str, Any]] = []

        # Check IOC follow-ups
        for pivot in self.ioc_follow_ups:
            q = pivot.get("query", "")
            if q in shortlist_queries:
                discarded.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "reason_discarded": "query_deduped",
                    "pivot_type": "ioc",
                    "priority": pivot.get("priority", 0.5),
                    "from_ioc": pivot.get("from", ""),
                })
            elif pivot.get("priority", 0.5) < 0.5:
                discarded.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "reason_discarded": "below_priority_threshold",
                    "pivot_type": "ioc",
                    "priority": pivot.get("priority", 0.5),
                    "from_ioc": pivot.get("from", ""),
                })

        # Check queries
        for q in self.suggested_queries:
            q_str = q.get("query", "")
            if q_str in shortlist_queries:
                discarded.append({
                    "action_type": "query",
                    "query": q_str,
                    "reason_discarded": "query_deduped",
                    "pivot_type": q.get("pivot_type", "general"),
                    "priority": q.get("priority", 0.5),
                })

        return discarded[:max_items]

    # -------------------------------------------------------------------------
    # Sprint F150H.2: action_confidence - score an action from this pack
    # -------------------------------------------------------------------------

    def action_confidence(self, action: dict[str, Any]) -> float:
        """
        Score an action's confidence (0-1) based on pack context.

        Factors:
        - Base priority from the action itself (40%)
        - Whether it's an IOC pivot (bonus +0.15)
        - Source quality if source hint (from source_hints quality field)
        - Provenance: heuristic vs model-assisted (10% boost for model-assisted)

        Fail-soft: returns 0.5 for malformed actions.
        """
        if not action or not isinstance(action, dict):
            return 0.5

        base_priority = action.get("priority", 0.5)
        pivot_type = action.get("pivot_type", "general")

        # Pivot type bonus
        pt_bonus = 0.0
        if pivot_type in ("ioc", "ioc_lookup"):
            pt_bonus = 0.15
        elif pivot_type in ("entity_pair", "relationship"):
            pt_bonus = 0.10
        elif pivot_type == "source":
            # Source hints already encode quality in priority
            pt_bonus = 0.0

        # Source quality lookup (for source_check actions)
        source_bonus = 0.0
        if action.get("action_type") == "source_check":
            source_name = action.get("query", "").strip('"')
            for hint in self.source_hints:
                if hasattr(hint, "source") and hint.source == source_name:
                    source_bonus = (hint.quality - 0.5) * 0.2
                    break

        # Provenance bonus
        provenance_bonus = 0.10 if self.provenance == "model-assisted" else 0.0

        confidence = base_priority * 0.4 + min(base_priority + pt_bonus, 1.0) * 0.4 + source_bonus + provenance_bonus
        return max(0.0, min(1.0, confidence))

    # -------------------------------------------------------------------------
    # Sprint F150H.2: track_recommendation + best_track - track-level guidance
    # -------------------------------------------------------------------------

    def track_recommendation(self) -> dict[str, Any]:
        """
        Recommend which investigation track to pursue next.

        Returns dict with:
        - recommended_track: name of the highest-value track
        - track_scores: dict of track -> score (0-1)
        - reasoning: why this track was recommended
        - next_action: first action from that track's shortlist
        """
        tracks = self.investigation_tracks()
        if not tracks:
            return {"recommended_track": None, "track_scores": {}, "reasoning": "empty pack", "next_action": None}

        # Score each track
        track_scores: dict[str, float] = {}
        for track_name, items in tracks.items():
            if not items:
                track_scores[track_name] = 0.0
                continue

            # Score based on: item count, avg priority, IOC presence
            avg_priority = sum(i.get("priority", 0.5) for i in items) / len(items)
            ioc_count = sum(1 for i in items if i.get("action_type") == "ioc_pivot")
            high_conf_count = sum(1 for i in items if i.get("priority", 0.5) >= 0.7)

            # Weighted score
            score = (avg_priority * 0.4) + (min(ioc_count / 3, 1.0) * 0.3) + (high_conf_count / len(items) * 0.3)
            track_scores[track_name] = max(0.0, min(1.0, score))

        recommended = max(track_scores, key=lambda k: track_scores.get(k, 0.0))
        reasoning_map = {
            "ioc_pivots": "IOC pivot track has highest actionable domain value",
            "entity_tracking": "Entity tracking track has strong specific targets",
            "relationship_verification": "Relationship verification has direct connection probes",
            "source_investigation": "Source investigation is lowest-risk verification path",
            "cluster_analysis": "Cluster analysis offers broad correlation overview",
        }

        # Get next action from recommended track
        next_action = None
        if recommended in tracks and tracks[recommended]:
            first_item = tracks[recommended][0]
            next_action = {
                "action_type": first_item.get("action_type", "query"),
                "query": first_item.get("query", ""),
                "rationale": f"Track: {recommended}",
                "priority": first_item.get("priority", 0.5),
            }

        return {
            "recommended_track": recommended,
            "track_scores": track_scores,
            "reasoning": reasoning_map.get(recommended, f"{recommended} selected by score"),
            "next_action": next_action,
        }

    def best_track(self) -> str | None:
        """Return the name of the highest-scoring track. Shortcut for track_recommendation."""
        tracks = self.investigation_tracks()
        if not tracks:
            return None
        return max(tracks, key=lambda t: sum(i.get("priority", 0.5) for i in tracks[t]) / max(1, len(tracks[t])))

    # -------------------------------------------------------------------------
    # Sprint F150H.1: investigation_tracks - multi-pronged paths
    # -------------------------------------------------------------------------

    def investigation_tracks(self) -> dict[str, list[dict[str, Any]]]:
        """
        Group pack contents into distinct investigation tracks.

        Returns dict with keys:
        - 'ioc_pivots': all IOC follow-ups grouped
        - 'entity_tracking': entity-based hypotheses + queries
        - 'relationship_verification': relationship hypotheses + queries
        - 'source_investigation': source hints + source queries
        - 'cluster_analysis': cross-entity/cross-IOC hypotheses

        Each track is a list of structured items with action_type + details.
        """
        tracks: dict[str, list[dict[str, Any]]] = {
            "ioc_pivots": [],
            "entity_tracking": [],
            "relationship_verification": [],
            "source_investigation": [],
            "cluster_analysis": [],
        }

        # IOC pivots track
        for pivot in self.ioc_follow_ups:
            tracks["ioc_pivots"].append({
                "action_type": "ioc_pivot",
                "from_ioc": pivot.get("from", ""),
                "pivot": pivot.get("pivot", ""),
                "to_field": pivot.get("to", ""),
                "query": pivot.get("query", ""),
                "priority": pivot.get("priority", 0.5),
            })

        # Entity tracking track
        for h in self.hypotheses:
            if h.get("type") in ("entity_tracking", "ioc_attribution"):
                tracks["entity_tracking"].append({
                    "action_type": "hypothesis",
                    "statement": h.get("hypothesis", ""),
                    "confidence": h.get("confidence", "0.5"),
                    "type": h.get("type", ""),
                })
        for q in self.suggested_queries:
            if q.get("pivot_type") in ("entity", "entity_expansion"):
                tracks["entity_tracking"].append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                })

        # Relationship verification track
        for h in self.hypotheses:
            if h.get("type") in ("relationship_tracking", "cluster_correlation"):
                tracks["relationship_verification"].append({
                    "action_type": "hypothesis",
                    "statement": h.get("hypothesis", ""),
                    "confidence": h.get("confidence", "0.5"),
                    "type": h.get("type", ""),
                })
        for q in self.suggested_queries:
            if q.get("pivot_type") in ("relationship", "entity_pair"):
                tracks["relationship_verification"].append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                })

        # Source investigation track
        for hint in self.source_hints:
            tracks["source_investigation"].append({
                "action_type": "source_hint",
                "source": hint.source if hasattr(hint, "source") else str(hint),
                "quality": hint.quality if hasattr(hint, "quality") else 0.5,
                "hint_type": hint.hint_type if hasattr(hint, "hint_type") else "general",
            })
        for q in self.suggested_queries:
            if q.get("pivot_type") == "source":
                tracks["source_investigation"].append({
                    "action_type": "query",
                    "query": q.get("query", ""),
                    "rationale": q.get("rationale", ""),
                    "priority": q.get("priority", 0.5),
                })

        # Cluster analysis track
        for h in self.hypotheses:
            if h.get("type") == "cluster_correlation":
                tracks["cluster_analysis"].append({
                    "action_type": "hypothesis",
                    "statement": h.get("hypothesis", ""),
                    "confidence": h.get("confidence", "0.5"),
                })

        # Remove empty tracks
        return {k: v for k, v in tracks.items() if v}

    # -------------------------------------------------------------------------
    # Sprint F150H.1: best_first_path - single optimal path through pack
    # -------------------------------------------------------------------------

    def best_first_path(self) -> dict[str, Any] | None:
        """
        Return the single best first action from the pack.

        IOC pivot if available, else top priority query, else None.
        Never returns empty - always prefers actionable IOC over noisy broad query.

        Returns:
            Dict with action_type, query, rationale, priority, pivot_type
            or None if pack is empty.
        """
        if self.is_empty():
            return None

        # First choice: highest priority IOC pivot
        if self.ioc_follow_ups:
            best_ioc = max(self.ioc_follow_ups, key=lambda x: x.get("priority", 0.5))
            return {
                "action_type": "ioc_pivot",
                "query": best_ioc.get("query", ""),
                "from_ioc": best_ioc.get("from", ""),
                "to_field": best_ioc.get("to", ""),
                "rationale": best_ioc.get("rationale", "IOC pivot"),
                "priority": best_ioc.get("priority", 0.9),
                "pivot_type": "ioc",
            }

        # Second choice: highest priority query (but prefer entity-pair or specific over broad)
        if self.suggested_queries:
            sorted_qs = sorted(
                self.suggested_queries,
                key=lambda x: (x.get("priority", 0.5), x.get("pivot_type", "") == "entity_expansion"),
                reverse=True,
            )
            # Prefer specific pivot types over general entity expansion
            for q in sorted_qs:
                pt = q.get("pivot_type", "")
                if pt in ("entity_pair", "relationship", "ioc_entity", "ioc_lookup"):
                    return {
                        "action_type": "query",
                        "query": q.get("query", ""),
                        "rationale": q.get("rationale", ""),
                        "priority": q.get("priority", 0.5),
                        "pivot_type": pt,
                    }
            # Fall back to highest priority query
            top = sorted_qs[0]
            return {
                "action_type": "query",
                "query": top.get("query", ""),
                "rationale": top.get("rationale", ""),
                "priority": top.get("priority", 0.5),
                "pivot_type": top.get("pivot_type", "general"),
            }

        return None

    # -------------------------------------------------------------------------
    # Sprint F150H.1: actionable_shortlist - compact sprint-ready output
    # -------------------------------------------------------------------------

    def actionable_shortlist(self, max_items: int = 5) -> list[dict[str, Any]]:
        """
        Return a compact, sprint-ready shortlist.
        """
        shortlist: list[dict[str, Any]] = []
        seen_queries: set[str] = set()

        # 1. IOC pivots first (highest priority)
        for pivot in sorted(self.ioc_follow_ups, key=lambda x: x.get("priority", 0.5), reverse=True):
            q = pivot.get("query", "")
            if q and q not in seen_queries:
                shortlist.append({
                    "action_type": "ioc_pivot",
                    "query": q,
                    "from_ioc": pivot.get("from", ""),
                    "to_field": pivot.get("to", ""),
                    "rationale": pivot.get("rationale", ""),
                    "priority": pivot.get("priority", 0.5),
                    "pivot_type": "ioc",
                })
                seen_queries.add(q)
                if len(shortlist) >= max_items:
                    return shortlist

        # 2. Suggested queries
        for q in sorted(self.suggested_queries, key=lambda x: x.get("priority", 0.5), reverse=True):
            if q["query"] in seen_queries:
                continue
            shortlist.append({
                "action_type": "query",
                "query": q.get("query", ""),
                "rationale": q.get("rationale", ""),
                "priority": q.get("priority", 0.5),
                "pivot_type": q.get("pivot_type", "general"),
            })
            seen_queries.add(q["query"])
            if len(shortlist) >= max_items:
                return shortlist

        # 3. Source hints
        for hint in self.source_hints:
            if len(shortlist) >= max_items:
                return shortlist
            if hasattr(hint, "source"):
                shortlist.append({
                    "action_type": "source_hint",
                    "source": hint.source,
                    "quality": hint.quality,
                    "hint_type": getattr(hint, "hint_type", "general"),
                })

        return shortlist
