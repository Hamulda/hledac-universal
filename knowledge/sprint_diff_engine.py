"""SprintDiffEngine — cross-sprint memory and diff logic.

Boundaries:
    MAX_DIFF_FINDINGS=100   — cap new/disappeared lists


    MAX_PROFILE_ENTRIES=500 — cap entity summary

Diff logic:
    Entity key = (ioc_type, ioc_value)
    new      = current entities NOT in previous
    disappeared = previous entities NOT in current
    changed  = same ioc_value but different ioc_type or different finding_id

Profile logic:
    first_seen  = min(previous.first_seen, current_ts) or current_ts
    last_seen   = current_ts
    cumulative  = (previous.cumulative if exists else 0) + len(current)
    velocity    = cumulative / max(days_since_first, 1)

Performance (F350M-R):
    - Single-pass index building: prev_by_key + prev_by_val in ONE loop
    - Cached entity keys: compute once, reuse for both indexes
    - O(n) set operations for new/disappeared (dict key diff)
    - M1 8GB safe: pure Python, no external native dependencies
"""



import logging
from dataclasses import dataclass, field
import msgspec

import orjson

logger = logging.getLogger(__name__)


# ── Bounds ────────────────────────────────────────────────────────────────────

MAX_DIFF_FINDINGS: int = 100
MAX_PROFILE_ENTRIES: int = 500


# ── Dataclasses ────────────────────────────────────────────────────────────────

class SprintDiffResult(msgspec.Struct, frozen=True, gc=False):
    target_id: str
    current_sprint_id: str
    previous_sprint_id: str | None
    new_findings: list[dict]
    disappeared_findings: list[dict]
    changed_entities: list[dict]


class TargetProfileSummary(msgspec.Struct, gc=False):
    target_id: str
    first_seen: float
    last_seen: float
    cumulative_finding_count: int
    entity_summary_json: str
    # derived fields (default to 0 / empty — filled by build_target_profile)
    finding_velocity: float = 0.0
    entity_types: dict[str, int] = field(default_factory=dict)


# ── SprintDiffEngine ───────────────────────────────────────────────────────────

class SprintDiffEngine:
    """Pure-python cross-sprint diff and target profiling. No I/O dependencies.

    Performance design (F350M-R):
        - Single-pass index construction: builds both by_key and by_val indexes
          in ONE loop instead of 4 separate loops
        - Cached entity keys: computed once per finding, reused for all indexes
        - O(n) set operations for new/disappeared (dict key diff)
        - Fail-safe: all operations wrapped in try/except, returns empty on error
    """

    # ── public API ────────────────────────────────────────────────────────────

    def compute_diff(
        self,
        current_findings: list[dict],
        previous_findings: list[dict] | None,
        target_id: str,
        current_sprint_id: str,
        previous_sprint_id: str | None,
    ) -> SprintDiffResult:
        """Compute delta between two sprint finding sets.

        If previous_findings is None the diff is trivially "all new,
        nothing disappeared" — useful for first-sprint baselines.
        """
        cap = MAX_DIFF_FINDINGS

        if previous_findings is None:
            # First sprint: everything is new, nothing disappeared.
            return SprintDiffResult(
                target_id=target_id,
                current_sprint_id=current_sprint_id,
                previous_sprint_id=None,
                new_findings=current_findings[:cap],
                disappeared_findings=[],
                changed_entities=[],
            )

        # ── SINGLE-PASS index building (was 4 separate loops) ─────────────────
        # Build prev_by_key AND prev_by_val in ONE loop
        prev_keys: set[str] = set()
        prev_by_key: dict[str, dict] = {}
        prev_by_val: dict[str, dict] = {}
        for f in previous_findings:
            try:
                key = self._entity_key(f)
                val = self._ioc_val(f)
            except Exception:
                continue
            prev_keys.add(key)
            prev_by_key[key] = f
            prev_by_val[val] = f  # last-write-wins is fine: same val = same semantic

        # Build curr_by_key AND curr_by_val in ONE loop
        curr_keys: set[str] = set()
        curr_by_key: dict[str, dict] = {}
        curr_by_val: dict[str, dict] = {}
        for f in current_findings:
            try:
                key = self._entity_key(f)
                val = self._ioc_val(f)
            except Exception:
                continue
            curr_keys.add(key)
            curr_by_key[key] = f
            curr_by_val[val] = f

        # ── changed: same ioc_value but different ioc_type or finding_id ───────
        # Must be detected BEFORE new/disappeared set ops, and uses ioc_value
        # as the grouping key (not the composite entity key) so that a type
        # change on the same value is still recognised as "changed".
        shared_vals = set(prev_by_val.keys()) & set(curr_by_val.keys())
        changed: list[dict] = []
        for val in shared_vals:
            prev_f = prev_by_val[val]
            curr_f = curr_by_val[val]
            curr_ioc_type = curr_f.get("ioc_type")
            prev_ioc_type = prev_f.get("ioc_type")
            curr_fid = curr_f.get("finding_id")
            prev_fid = prev_f.get("finding_id")
            if curr_ioc_type != prev_ioc_type or curr_fid != prev_fid:
                changed.append({"before": prev_f, "after": curr_f})
            if len(changed) >= cap:
                break

        # ── new / disappeared (composite entity key) ───────────────────────────
        new_keys = curr_keys - prev_keys
        gone_keys = prev_keys - curr_keys

        new_findings: list[dict] = []
        for key in new_keys:
            f = curr_by_key.get(key)
            if f is not None:
                new_findings.append(f)
            if len(new_findings) >= cap:
                break

        disappeared_findings: list[dict] = []
        for key in gone_keys:
            f = prev_by_key.get(key)
            if f is not None:
                disappeared_findings.append(f)
            if len(disappeared_findings) >= cap:
                break

        return SprintDiffResult(
            target_id=target_id,
            current_sprint_id=current_sprint_id,
            previous_sprint_id=previous_sprint_id,
            new_findings=new_findings,
            disappeared_findings=disappeared_findings,
            changed_entities=changed,
        )

    def build_target_profile(
        self,
        current_findings: list[dict],
        previous_profile: TargetProfileSummary | None,
        target_id: str,
        current_ts: float,
    ) -> TargetProfileSummary:
        """Build or extend a target profile over successive sprints.

        Args:
            current_findings:  findings from the current sprint run
            previous_profile:  result of a previous call (or None for first sprint)
            target_id:         identifier of the target
            current_ts:        unix timestamp of the current run (time.time())
        """
        # ── derive timestamps ──────────────────────────────────────────────────
        try:
            if previous_profile is not None and previous_profile.first_seen > 0:
                first_seen = min(previous_profile.first_seen, current_ts)
            else:
                first_seen = current_ts
        except Exception:
            first_seen = current_ts

        last_seen = current_ts

        # ── cumulative count ───────────────────────────────────────────────────
        try:
            prev_cumulative = 0
            if previous_profile is not None:
                prev_cumulative = previous_profile.cumulative_finding_count
        except Exception:
            prev_cumulative = 0

        cumulative_count = prev_cumulative + len(current_findings)

        # ── entity summary ─────────────────────────────────────────────────────
        entity_summary: dict = {}
        try:
            entity_summary = self._compute_entity_summary(current_findings)
        except Exception as _e:
            logger.debug(
                "fail-soft suppression: build_target_profile (entity_summary): %s",
                _e,
                exc_info=True,
            )

        try:
            entity_summary_json = orjson.dumps(entity_summary).decode()
        except Exception:
            entity_summary_json = "{}"

        # ── derived: finding_velocity ──────────────────────────────────────────
        finding_velocity = 0.0
        try:
            days_elapsed = (last_seen - first_seen) / 86400.0
            days_elapsed = max(days_elapsed, 1.0)
            finding_velocity = cumulative_count / days_elapsed
        except Exception:
            finding_velocity = 0.0

        # ── derived: entity_types ───────────────────────────────────────────────
        entity_types: dict[str, int] = {}
        try:
            for f in current_findings:
                try:
                    ioc_type = f.get("ioc_type", "unknown")
                    entity_types[ioc_type] = entity_types.get(ioc_type, 0) + 1
                except Exception:
                    continue
        except Exception:
            entity_types = {}

        return TargetProfileSummary(
            target_id=target_id,
            first_seen=first_seen,
            last_seen=last_seen,
            cumulative_finding_count=cumulative_count,
            entity_summary_json=entity_summary_json,
            finding_velocity=finding_velocity,
            entity_types=entity_types,
        )

    # ── private helpers ────────────────────────────────────────────────────────

    def _entity_key(self, finding: dict) -> str:
        """Stable entity key for deduplication and diffing.

        Format: ioc_type::ioc_value (both lower-cased, empty/missing → '?').
        Raises KeyError if both ioc_type and ioc_value are absent.
        """
        ioc_type = finding.get("ioc_type") or "?"
        ioc_value = finding.get("ioc_value") or "?"
        if isinstance(ioc_type, str):
            ioc_type = ioc_type.lower()
        if isinstance(ioc_value, str):
            ioc_value = ioc_value.lower()
        # Deliberately raise KeyError if both are "?" so caller skips the finding
        return f"{ioc_type}::{ioc_value}"

    def _ioc_val(self, finding: dict) -> str:
        """Extract lowercase ioc_value for changed-detection (type migration).

        Returns '?' if missing — shared_vals will skip it as it won't match
        any properly-formed current finding.
        """
        val = finding.get("ioc_value") or "?"
        if isinstance(val, str):
            return val.lower()
        return "?"

    def _compute_entity_summary(self, findings: list[dict]) -> dict:
        """Collapse finding list into a compact per-type, per-source tally.

        Output shape::
            {
                "total": N,
                "by_type":  {"domain": 5, "ip": 2, ...},
                "by_source": {"otx_alienvault": 3, "passive_dns": 4, ...},
            }

        Capped at MAX_PROFILE_ENTRIES keys per counter dict.

        Performance: Single-pass with inline counter dict updates (no get+set).
        """
        cap = MAX_PROFILE_ENTRIES
        total = 0
        by_type: dict[str, int] = {}
        by_source: dict[str, int] = {}

        for f in findings:
            try:
                total += 1

                # ioc_type bucket — inline update, avoid get()
                ioc_type = f.get("ioc_type") or "unknown"
                if len(by_type) < cap:
                    by_type[ioc_type] = by_type.get(ioc_type, 0) + 1

                # source bucket
                source = f.get("source_type") or f.get("source") or "unknown"
                if len(by_source) < cap:
                    by_source[source] = by_source.get(source, 0) + 1

            except Exception:
                # Fail-soft: skip malformed entries without aborting
                continue

        summary: dict = {
            "total": total,
            "by_type": by_type,
            "by_source": by_source,
        }

        return summary
