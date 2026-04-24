"""
Identity Stitching Canonical Adapter — Sprint F202B
===================================================

Canonical adapter wrapping IdentityStitchingEngine for the sprint pipeline.

Responsibilities:
  1. Accept EntitySignalProfile list from entity_signal_extractor
  2. Convert to IdentityStitchingEngine IdentityProfile objects
  3. Run bounded identity stitching (MAX_COMPARISONS=2000)
  4. Produce derived identity CanonicalFinding objects
  5. Produce graph edge upserts via knowledge.graph_service

Role: deterministic sidecar, NOT the main write path.
Derived findings go through async_ingest_findings_batch() like any other finding.
Graph edges are advisory (upsert_identity_edge via graph_service).

M1 8GB CEILING:
  - MAX_PROFILES=500 profiles per sprint (from entity_signal_extractor)
  - MAX_COMPARISONS=2000 comparisons per sprint (hard cap)
  - optimize_memory() called after each stitching batch
  - All methods fail-soft: sprint continues on any error
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_COMPARISONS: int = 2000
IDENTITY_MATCH_THRESHOLD: float = 0.7
STITCH_THRESHOLD: float = 0.8

# ── Imports ──────────────────────────────────────────────────────────────────

try:
    from ..intelligence.identity_stitching import (
        IdentityProfile,
        IdentityStitchingEngine,
        UsernameEntry,
    )
    _STITCHING_AVAILABLE = True
except ImportError:
    _STITCHING_AVAILABLE = False
    IdentityProfile = None
    IdentityStitchingEngine = None
    UsernameEntry = None

try:
    from ..knowledge.duckdb_store import CanonicalFinding
except ImportError:
    CanonicalFinding = None


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass
class IdentityCandidate:
    """
    A derived identity candidate produced by the stitching engine.

    Represents a single or stitched identity with confidence and signals.
    """
    candidate_id: str
    profile_ids: List[str]             # constituent profile IDs
    primary_name: str
    emails: List[str]
    usernames: List[str]
    platforms: List[str]
    confidence: float                  # 0-1
    signals: Dict[str, float]          # individual signal scores
    evidence: List[str]                # evidence strings
    finding_ids: List[str]             # source finding IDs

    def to_dict(self) -> Dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "profile_ids": self.profile_ids,
            "primary_name": self.primary_name,
            "emails": self.emails,
            "usernames": self.usernames,
            "platforms": self.platforms,
            "confidence": self.confidence,
            "signals": self.signals,
            "evidence": self.evidence,
            "finding_ids": self.finding_ids,
        }


# ── Adapter ───────────────────────────────────────────────────────────────────

class IdentityStitchingAdapter:
    """
    Canonical adapter for IdentityStitchingEngine in the sprint pipeline.

    Wraps IdentityStitchingEngine with:
      - Bounded comparisons (MAX_COMPARISONS)
      - Fail-soft: errors never crash the sprint
      - M1 8GB memory management via optimize_memory()
      - Conversion to CanonicalFinding for async_ingest_findings_batch()
      - Graph edge upsert via knowledge.graph_service

    Usage:
        adapter = IdentityStitchingAdapter()
        candidates = adapter.extract_and_stitch(profiles)
        findings = adapter.to_derived_findings(candidates, query)
    """

    def __init__(
        self,
        match_threshold: float = IDENTITY_MATCH_THRESHOLD,
        stitch_threshold: float = STITCH_THRESHOLD,
    ):
        if not _STITCHING_AVAILABLE:
            raise ImportError(
                "identity_stitching module not available — "
                "install rapidfuzz for fuzzy string matching"
            )

        self._engine = IdentityStitchingEngine(
            similarity_threshold=match_threshold,
            enable_fuzzy=True,
        )
        self._match_threshold = match_threshold
        self._stitch_threshold = stitch_threshold
        self._stats: Dict[str, int] = {
            "profiles_added": 0,
            "candidates_found": 0,
            "comparisons_run": 0,
            "findings_produced": 0,
            "graph_edges_written": 0,
        }

    # ── Profile conversion ─────────────────────────────────────────────────────

    @staticmethod
    def _to_identity_profile(esp: Any) -> IdentityProfile:
        """Convert EntitySignalProfile to IdentityStitchingEngine IdentityProfile."""
        usernames = []
        platforms_list = list(esp.platforms) if esp.platforms else []
        platform = platforms_list[0] if platforms_list else "unknown"
        for uname in esp.usernames[:10]:  # bounded: max 10 usernames per profile
            usernames.append(UsernameEntry(
                platform=platform,
                username=uname,
            ))

        return IdentityProfile(
            id=esp.id,
            primary_name=esp.primary_name,
            emails=esp.emails[:20],  # bounded: max 20 emails
            usernames=usernames,
            confidence=esp.confidence,
            evidence=[f"esp:{fid}" for fid in esp.finding_ids[:10]],  # store ESP IDs for candidate building
        )

    # ── Stitching ──────────────────────────────────────────────────────────────

    def extract_and_stitch(
        self,
        profiles: List[Any],
    ) -> List[IdentityCandidate]:
        """
        Run identity stitching on a list of EntitySignalProfile objects.

        Bounded: MAX_COMPARISONS=2000 cap enforced.
        Fail-soft: returns empty list on any error.

        Args:
            profiles: List of EntitySignalProfile from entity_signal_extractor

        Returns:
            List of IdentityCandidate objects (stitched or single profiles)
        """
        if not profiles:
            return []

        try:
            # Add profiles to engine
            for esp in profiles[:500]:  # hard cap at MAX_PROFILES
                try:
                    ip = self._to_identity_profile(esp)
                    self._engine.add_profile(ip)
                except Exception:
                    pass

            self._stats["profiles_added"] = len(profiles)

            # Find all matches (bounded)
            start = time.monotonic()
            all_matches = self._engine.find_all_matches(min_score=self._match_threshold)
            elapsed_ms = (time.monotonic() - start) * 1000

            # Cap comparisons
            if len(all_matches) > MAX_COMPARISONS:
                logger.debug(
                    f"IdentityStitchingAdapter: capping {len(all_matches)} matches "
                    f"to MAX_COMPARISONS={MAX_COMPARISONS}"
                )
                all_matches = all_matches[:MAX_COMPARISONS]

            self._stats["comparisons_run"] = len(all_matches)
            logger.debug(
                f"IdentityStitchingAdapter: {len(all_matches)} matches "
                f"in {elapsed_ms:.1f}ms"
            )

            # Stitch identities
            stitched = []
            try:
                stitched = self._engine.stitch_identities(
                    match_threshold=self._stitch_threshold,
                    transitive_threshold=self._match_threshold,
                )
            except Exception as e:
                logger.debug(f"IdentityStitchingAdapter: stitch_identities error: {e}")

            # Build IdentityCandidate list
            candidates: List[IdentityCandidate] = []

            # Add stitched identities as high-confidence candidates
            for stitch in stitched:
                platforms = set()
                finding_ids = []
                for pid in stitch.profile_ids:
                    p = self._engine.get_profile(pid)
                    if p:
                        platforms.update(p.get_platforms())
                        # Parse esp: IDs from evidence
                        for ev in (p.evidence or []):
                            if ev.startswith("esp:"):
                                finding_ids.append(ev[4:])
                            elif ev.startswith("source:"):
                                finding_ids.append(ev[7:])

                candidates.append(IdentityCandidate(
                    candidate_id=stitch.id,
                    profile_ids=stitch.profile_ids,
                    primary_name=stitch.merged_names[0] if stitch.merged_names else stitch.id,
                    emails=stitch.merged_emails[:10],
                    usernames=[u.username for u in stitch.merged_usernames[:10]],
                    platforms=list(platforms)[:10],
                    confidence=stitch.stitch_confidence,
                    signals={"stitch_confidence": stitch.stitch_confidence},
                    evidence=stitch.match_evidence[:5],
                    finding_ids=finding_ids[:20],
                ))

            # Add unmatched profiles as low-confidence singletons
            if len(candidates) < MAX_COMPARISONS:
                matched_pids = {pid for c in candidates for pid in c.profile_ids}
                for esp in profiles:
                    if esp.id in matched_pids:
                        continue
                    if len(candidates) >= 200:  # cap singleton output
                        break
                    p = self._engine.get_profile(esp.id)
                    if p:
                        candidates.append(IdentityCandidate(
                            candidate_id=esp.id,
                            profile_ids=[esp.id],
                            primary_name=esp.primary_name,
                            emails=esp.emails[:5],
                            usernames=esp.usernames[:5],
                            platforms=list(esp.platforms)[:5],
                            confidence=esp.confidence * 0.5,  # lower confidence for singletons
                            signals={},
                            evidence=[f"single profile from {esp.finding_ids}"],
                            finding_ids=esp.finding_ids[:5],
                        ))

            self._stats["candidates_found"] = len(candidates)

            # Memory optimization
            self._engine.optimize_memory()

            return candidates

        except Exception as e:
            logger.warning(f"IdentityStitchingAdapter.extract_and_stitch error: {e}")
            return []

    # ── Graph edge upsert ──────────────────────────────────────────────────────

    def upsert_identity_edges(
        self,
        candidates: List[IdentityCandidate],
    ) -> int:
        """
        Upsert identity edges to graph_service for each candidate.

        Each candidate with multiple profile_ids produces edges between them.
        Fail-soft: returns count of edges upserted, 0 on error.

        Args:
            candidates: List of IdentityCandidate from extract_and_stitch

        Returns:
            Number of edges written to graph_service
        """
        if not candidates:
            return 0

        try:
            from ..knowledge import graph_service

            edge_count = 0
            for cand in candidates:
                if len(cand.profile_ids) < 2:
                    continue

                # Upsert edges between all constituent profiles
                primary = cand.profile_ids[0]
                for secondary in cand.profile_ids[1:]:
                    ok = graph_service.upsert_relation(
                        src=primary,
                        dst=secondary,
                        rel_type="same_identity",
                        weight=cand.confidence,
                        evidence=f"stitch:{cand.candidate_id}",
                    )
                    if ok:
                        edge_count += 1

            self._stats["graph_edges_written"] += edge_count
            return edge_count

        except Exception as e:
            logger.debug(f"IdentityStitchingAdapter.upsert_identity_edges error: {e}")
            return 0

    # ── Derived findings ───────────────────────────────────────────────────────

    def to_derived_findings(
        self,
        candidates: List[IdentityCandidate],
        query: str,
    ) -> List[Any]:
        """
        Convert IdentityCandidate list to CanonicalFinding list.

        Each candidate becomes a derived finding with source_type="identity_stitching".
        These findings go through async_ingest_findings_batch() like any finding.

        Fail-soft: returns empty list on error.

        Args:
            candidates: List of IdentityCandidate
            query: Original sprint query

        Returns:
            List of CanonicalFinding objects (empty if CanonicalFinding unavailable)
        """
        if not candidates or CanonicalFinding is None:
            return []

        findings: List[Any] = []
        try:
            for cand in candidates:
                fid = f"identity_{cand.candidate_id[:32]}_{int(time.time() * 1000) % 1000000:06d}"
                payload = {
                    "candidate_id": cand.candidate_id,
                    "profile_ids": cand.profile_ids,
                    "primary_name": cand.primary_name,
                    "emails": cand.emails,
                    "usernames": cand.usernames,
                    "platforms": cand.platforms,
                    "confidence": cand.confidence,
                    "signals": cand.signals,
                    "evidence": cand.evidence,
                    "finding_ids": cand.finding_ids[:20],  # bounded
                }

                import json
                payload_text = json.dumps(payload)

                finding = CanonicalFinding(
                    finding_id=fid,
                    query=query,
                    source_type="identity_stitching",
                    confidence=cand.confidence,
                    ts=time.time(),
                    provenance=("identity_stitching",),
                    payload_text=payload_text,
                )
                findings.append(finding)

            self._stats["findings_produced"] = len(findings)
            logger.debug(
                f"IdentityStitchingAdapter: produced {len(findings)} derived findings"
            )
            return findings

        except Exception as e:
            logger.warning(f"IdentityStitchingAdapter.to_derived_findings error: {e}")
            return []

    # ── Stats ─────────────────────────────────────────────────────────────────

    def get_stats(self) -> Dict[str, int]:
        """Return adapter statistics."""
        return self._stats.copy()

    def clear(self) -> None:
        """Clear engine state and reset stats."""
        self._engine.clear()
        self._stats = {k: 0 for k in self._stats}


# ── Factory ───────────────────────────────────────────────────────────────────

def create_identity_stitching_adapter(
    match_threshold: float = IDENTITY_MATCH_THRESHOLD,
) -> IdentityStitchingAdapter:
    """Factory to create IdentityStitchingAdapter."""
    return IdentityStitchingAdapter(match_threshold=match_threshold)


__all__ = [
    "IdentityCandidate",
    "IdentityStitchingAdapter",
    "create_identity_stitching_adapter",
    "MAX_COMPARISONS",
    "IDENTITY_MATCH_THRESHOLD",
    "STITCH_THRESHOLD",
]
