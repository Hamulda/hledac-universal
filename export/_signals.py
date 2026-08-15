"""
export/_signals.py

Sprint F232C: Provider yield signal computation.
Extracted from export/sprint_exporter.py to reduce complexity hotspots.

NO network. NO model. NO new dependencies. NO HTML in output strings.
"""

from typing import Any


def _get_corrob_outcomes(scorecard: dict) -> dict:
    """Normalize lane outcomes from either src_family_outcomes or source_family_outcomes.

    Live sprint reports write ``source_family_outcomes`` as a list of dicts with
    ``family`` and lane data keys (terminal_state, accepted_count, raw_count, etc.).
    F229B helpers historically read only ``src_family_outcomes`` as a flat dict keyed
    by family name.

    This function accepts both shapes and returns a flat dict keyed by family name,
    matching what ``runtime.corroboration_score.score_from_result`` expects.

    Normalisation rules
    -------------------
    1. If ``src_family_outcomes`` is a non-empty dict, return it directly (F229B compat).
    2. Otherwise, read ``source_family_outcomes`` as a list and index by ``family``.
    3. On any failure, return ``{}`` (fail-soft).
    """
    # 1. Prefer src_family_outcomes dict (F229B shape)
    sfo = scorecard.get("src_family_outcomes")
    if isinstance(sfo, dict) and sfo:
        return sfo

    # 2. Normalise source_family_outcomes list (live shape)
    try:
        sfo_list = scorecard.get("source_family_outcomes", [])
        if not isinstance(sfo_list, list):
            return {}
        out = {}
        for entry in sfo_list:
            if isinstance(entry, dict):
                fam = entry.get("family")
                if fam and isinstance(fam, str):
                    out[fam.lower()] = entry
        return out
    except Exception:
        return {}


def _corroboration_score_value(scorecard: dict) -> float:
    """Compute corroboration score (0.0-1.0) from src_family_outcomes or source_family_outcomes.

    Returns 0.0 when no corroboration data is available (fail-soft).
    """
    outcomes = _get_corrob_outcomes(scorecard)
    if not outcomes:
        return 0.0
    try:
        from hledac.universal.runtime.corroboration_score import compute_corroboration_score

        result = compute_corroboration_score(outcomes)
        return float(result.score) if hasattr(result, "score") else float(result)
    except Exception:
        return 0.0


def _compute_provider_yield_signals(
    scorecard: dict,
    doh_provider_errors: tuple[str, ...] | None = None,
    public_provider_errors: list[dict] | None = None,
    nonfeed_missing_expected_lanes: list[str] | None = None,
) -> dict[str, Any]:
    """
    Sprint F232C: Provider yield signals from existing provider debug surfaces.

    Derives provider yield diagnostics from the union of:
      - scorecard["source_family_outcomes"]
      - doh_provider_errors (tuple of provider error strings)
      - public_provider_errors (list of {family, error, error_type} dicts)
      - nonfeed_missing_expected_lanes (list of family names)

    NO network. NO model. NO new dependencies. NO HTML in output strings.

    Returns
    -------
    dict with keys:
      provider_yield_summary : dict with keys:
        dependency_gaps    : list[str]  families with dependency_missing errors
        timeout_families   : list[str]  families with timeout errors
        low_yield_families  : list[str]  families with zero/minimal accepted results
        coverage_gaps       : list[str]  families expected but not attempted
      low_yield_families        : tuple[str, ...]
      dependency_gap_families    : tuple[str, ...]
      timeout_families          : tuple[str, ...]
      recommended_provider_actions : tuple[str, ...]
    """
    errors = doh_provider_errors or ()
    pub_errors = public_provider_errors or []
    missing = nonfeed_missing_expected_lanes or []
    sfo_list = scorecard.get("source_family_outcomes", []) if isinstance(scorecard, dict) else []
    scorecard.get("nonfeed_expected_lanes", []) or []

    # Detect feed-only: only feed family has accepted findings, no nonfeed attempted
    nonfeed_families = {"ct", "doh", "wayback", "passive_dns", "shodan", "hunter"}
    _feed_only = False
    if sfo_list:
        feed_entry = next((e for e in sfo_list if isinstance(e, dict) and e.get("family") == "feed"), None)
        nonfeed_attempted = [
            e for e in sfo_list if isinstance(e, dict) and e.get("family") in nonfeed_families and e.get("attempted")
        ]
        _feed_only = (feed_entry is not None and (feed_entry.get("accepted_count") or 0) > 0) and not nonfeed_attempted

    # 1. dependency_gap_families  --  from doh_provider_errors
    _dep_gaps: list[str] = []
    for e in errors:
        if isinstance(e, str) and "dependency_missing" in e:
            _dep_gaps.append("doh")
    # Also check public_provider_errors for dependency signals
    for pe in pub_errors:
        if isinstance(pe, dict):
            err = str(pe.get("error", "")).lower()
            if "dependency_missing" in err or "dependency" in err:
                fam = str(pe.get("family", "")).lower()
                if fam and fam not in _dep_gaps:
                    _dep_gaps.append(fam)

    # 2. timeout_families  --  from terminal_state containing "timeout" or public_provider_errors
    _timeout_fams: list[str] = []
    for pe in pub_errors:
        if isinstance(pe, dict):
            err_type = str(pe.get("error_type", "")).lower()
            if err_type == "timeout":
                fam = str(pe.get("family", ""))
                if fam and fam not in _timeout_fams:
                    _timeout_fams.append(fam)
    # Also scan source_family_outcomes terminal_state
    for entry in sfo_list:
        if isinstance(entry, dict):
            ts = str(entry.get("terminal_state", "")).lower()
            if "timeout" in ts:
                fam = str(entry.get("family", ""))
                if fam and fam not in _timeout_fams:
                    _timeout_fams.append(fam)

    # 3. low_yield_families  --  families that attempted but produced minimal/no findings
    _low_yield: list[str] = []
    for entry in sfo_list:
        if isinstance(entry, dict) and entry.get("attempted"):
            fam = entry.get("family", "")
            accepted = entry.get("accepted_count", 0)
            ts = str(entry.get("terminal_state", "")).lower()
            # not_scheduled while expected = low yield
            if ts == "not_scheduled" and fam in missing:
                if fam not in _low_yield:
                    _low_yield.append(fam)
            # zero accepted + attempted = low yield (and not already flagged as dep/timeout gap)
            elif accepted == 0 and fam not in (_dep_gaps + _timeout_fams):
                if fam not in _low_yield:
                    _low_yield.append(fam)
    # Missing expected nonfeed lanes that were never attempted
    for fam in missing:
        if fam not in _low_yield and fam not in _dep_gaps:
            _low_yield.append(fam)

    # 4. recommended_provider_actions
    _actions: list[str] = []
    outcomes = _get_corrob_outcomes(scorecard)
    try:
        from hledac.universal.runtime.corroboration_score import compute_terminal_coverage

        tc = compute_terminal_coverage(outcomes)
        terminal_score = tc.terminal_coverage_score
    except Exception:
        terminal_score = 0.0

    corrob_score = _corroboration_score_value(scorecard)

    # High terminal coverage (all families reached terminal) + low corroboration
    # -> provider quality improvement recommended
    if terminal_score >= 0.75 and corrob_score < 0.3 and not _feed_only:
        _actions.append("improve_provider_quality")

    # Feed-only with missing nonfeed lanes -> scheduling recommendation, not provider quality
    if _feed_only and missing:
        _actions.append("expand_scheduling_coverage")

    # Dependency gaps detected -> fix dependencies
    if _dep_gaps:
        _actions.append("resolve_provider_dependencies")

    # Timeouts detected -> improve provider reliability
    if _timeout_fams:
        _actions.append("improve_provider_reliability")

    return {
        "provider_yield_summary": {
            "dependency_gaps": _dep_gaps,
            "timeout_families": _timeout_fams,
            "low_yield_families": _low_yield,
            "coverage_gaps": [f for f in missing if f not in _dep_gaps and f not in _timeout_fams],
        },
        "low_yield_families": tuple(_low_yield),
        "dependency_gap_families": tuple(_dep_gaps),
        "timeout_families": tuple(_timeout_fams),
        "recommended_provider_actions": tuple(_actions),
    }
