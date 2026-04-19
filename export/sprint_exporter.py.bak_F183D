# Sprint 8VX §A: Export plane finish-up
# - ExportHandoff confirmed as primary handoff surface (wired in __main__.py:2343)
# - compat fallback documented with explicit removal conditions
# - No new framework, no new store API
"""
Sprint 8VI §A: EXPORT fáze — export_sprint() + _generate_next_sprint_seeds()
Sprint 8VJ §C: ExportHandoff | dict → typed handoff spotřeba
Sprint 8VX §A: Finish-up — removal conditions tightened, comments aligned with reality
Sprint F150I: product_value_summary — přenáší do exportu to, co runtime už ví:
  - accepted/stored reality z dedup status
  - reject breakdown (low-info / duplicate / fail-open)
  - circuit breaker state pokud je k dispozici
  - gnn_predictions signal
  - phase_durations timing truth
  - robustnější seed derivation (divný input → skip, ne pád)
Sprint F150J: Enhanced next-seed derivation driven by product_value_summary:
  - 4 seed categories: ioc_followup, query_suggestion, source_revisit, low_signal_recommendation
  - signal_quality → query direction (refine/broaden/narrow/new_approach)
  - reject_breakdown → query strategy (low_info_ratio → narrow scope)
  - cb_open_domains → source_revisit with backoff
  - depleted signal → retry_known_sources or new_approach
  - Bounded output: max 12 seeds total, sorted by priority
Sprint F150K: Next-action package — praktický follow-up balíček:
  - hypothesis_engine.suggest_next_queries() jako bounded seam (fail-soft, lazy load)
  - human-readable sprint_summary block (co found / co nevyšlo / co dělat dál)
  - priority-based next actions (max 10, deduped, signal-derived)
  - focus/expand recommendations derived from signal_quality
  - NO new persistence, NO new planner, NO new write-back path
Sprint F150L: Operator finish layer — derived seams integrated:
  - branch_value z scorecard (feed vs public branch analysis)
  - sprint_trend z store (poslední sprinty, fail-soft)
  - source_leaderboard z store (top zdroje, fail-soft)
  - eh.correlation (RunCorrelation) pokud přítomen
  - Praktický operator brief: co sprint našel, která branch nesla signál,
    co bylo slabé, jaký je nejbližší další krok, 2-5 zajímavých follow-upů
  - Rozhraní mezi feed/public branch recommendation
  - enriched next seeds z branch_value + sprint_trend
  - VŠECHNO derived only, žádný new business engine
Sprint F150P: Finish-layer truth fields — canonical surfaces from scheduler/core:
  - runtime_truth, feed_verdict, public_verdict, signal_path, hypothesis_pack
    z ExportHandoff.scorecard (compute_sprint_intelligence output)
  - run_truth_note: operator-facing sprint characterization (meaningful vs smoke)
  - branch_truth: definitive feed/public balance summary
  - best_first_move: immediate next action (single sentence)
  - why_this_run_matters: one-liner significance statement
  - No new store reads, no write-back, additive only
"""

from __future__ import annotations

import json
import logging
import pathlib
from typing import TYPE_CHECKING, Any, Union

if TYPE_CHECKING:
    from hledac.universal.types import ExportHandoff

logger = logging.getLogger(__name__)


async def export_sprint(
    store: Any,
    handoff: Union["ExportHandoff", dict, None],  # type: ignore[name-defined]
    sprint_id: str | None = None,
) -> dict:
    """
    EXPORT fáze — JSON report, seed tasky pro příští sprint.

    Voláno z _print_scorecard_report() v __main__.py EXPORT fázi.
    Nikdy nevyhodí výjimku.

    Accepts typed ExportHandoff OR raw dict (backward compat via ensure_export_handoff).

    Součásti:
      1. JSON report do ~/.hledac/reports/{sprint_id}_report.json
         Canonical path owner: paths.get_sprint_json_report_path() (post-F500B)
      2. Seed tasky pro příští sprint z top IOC graph nodes
      3. Sprint F150I: product_value_summary — decisions有用的 pro další sprinty

    PRIMARY HANDOFF SURFACE (Sprint 8VX):
      - ExportHandoff.top_nodes — kanonický zdroj pro seed generation
      - ExportHandoff.scorecard — kanonický zdroj pro JSON report

    ACCEPTED COMPAT SEAM — store-facing fallback:
      - Pokud top_nodes prázdné (windup běžel ale neplnil ExportHandoff.top_nodes),
        zkusí store.get_top_seed_nodes(n=5) — store-facing seam (post-8VX).
      - REMOVAL CONDITION: ExportHandoff.top_nodes always populated in ALL windup paths.
      - Future owner: duckdb_store.get_top_seed_nodes() — already implemented, this
        fallback is the compat bridge pending windup engine producing typed ExportHandoff.
    """
    from hledac.universal.paths import get_sprint_json_report_path
    from hledac.universal.export.COMPAT_HANDOFF import ensure_export_handoff

    # Sprint 8VJ §C: Normalize ExportHandoff | dict | None → typed ExportHandoff
    # Maintains backward compat: dict input → from_windup() extraction
    eh = ensure_export_handoff(handoff, default_sprint_id=sprint_id or "unknown")

    # Resolve sprint_id — prefer from handoff (typed path)
    _sprint_id = eh.sprint_id if eh.sprint_id != "unknown" else (sprint_id or "unknown")

    # Sprint F500B §2: Canonical JSON report path via paths.py helper
    # (post-F500B: inline report_dir composition replaced by get_sprint_json_report_path)
    report_path = get_sprint_json_report_path(_sprint_id)

    # Sprint 8VZ §C: F10 runtime boundary wiring
    # sanitize_outbound() na JSON report boundary — content opouští systém
    boundary_content = _make_serializable(eh.scorecard)
    boundary_text = json.dumps(boundary_content, indent=2, default=str)

    # Pass through early privacy gate (outbound boundary)
    # Using security_coordinator sanitize_outbound seam
    try:
        from hledac.universal.coordinators.security_coordinator import UniversalSecurityCoordinator
        sec_coordinator = UniversalSecurityCoordinator(max_concurrent=2)
        await sec_coordinator.initialize()
        gate_result = await sec_coordinator.sanitize_outbound(boundary_text, force_fallback=True)
        sanitized_scorecard_raw = gate_result.get("sanitized", boundary_text)
        # Log audit metadata (non-blocking)
        if gate_result.get("pii_count"):
            logger.info("[EXPORT] sanitize_outbound: pii_count=%s, risk=%s",
                        gate_result.get("pii_count"), gate_result.get("risk_level", "unknown"))
    except Exception as e:
        # Fail-soft: fall back to DEGRADED SANITIZED-SAFE structure, NOT unsanitized original.
        # Sprint F500M §1 CRITICAL: boundary_text is UNSANITIZED — never return it.
        # Produces a bounded degraded report structure that is safe for export.
        logger.warning("[EXPORT] sanitize_outbound failed (non-fatal): %s", e)
        degraded = {
            "_sanitize_failure": True,
            "sprint_id": _sprint_id,
            "report": "sanitization_failed_degraded_export",
        }
        sanitized_scorecard_raw = json.dumps(degraded, default=str)

    # Sprint F150I §2: Build product_value_summary from all existing surfaces
    pvs = _build_product_value_summary(store, eh, _sprint_id)

    # 1. JSON report — write via canonical path (F10 boundary applied)
    # report_path already computed via get_sprint_json_report_path() above
    try:
        # Re-parse sanitized text back to JSON for writing
        try:
            sanitized_obj = json.loads(sanitized_scorecard_raw)
        except (json.JSONDecodeError, TypeError) as parse_err:
            # Sprint F500E §1: sanitize_outbound fallback_sanitize truncates to 10KB.
            # If content exceeded 10KB, sanitized string is truncated JSON → parse fails.
            # CRITICAL: do NOT fall back to original (unsanitized) content here — that
            # would be an export boundary correctness drift. Write sanitized prefix only.
            logger.warning(
                "[EXPORT] sanitize boundary parse failed (truncated JSON, size=%d): %s. "
                "Writing sanitized prefix only, NOT falling back to unsanitized content.",
                len(sanitized_scorecard_raw), parse_err
            )
            # Write what we have from sanitized — it's bounded and sanitized
            sanitized_obj = json.loads(sanitized_scorecard_raw[:5000]) if sanitized_scorecard_raw else {}
            if sanitized_obj is None:
                sanitized_obj = {}

        # Sprint F150I §3: Attach product_value_summary to JSON report (derived output)
        if isinstance(sanitized_obj, dict):
            sanitized_obj["product_value_summary"] = pvs
        elif isinstance(sanitized_obj, list):
            # Edge case: truncated JSON is a list — wrap in dict with pvs
            sanitized_obj = {"_truncated_content": sanitized_obj, "product_value_summary": pvs}

        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(sanitized_obj, f, indent=2, default=str)
        logger.info(f"[EXPORT] JSON report → {report_path}")
    except Exception as e:
        logger.warning(f"[EXPORT] JSON write failed: {e}")
        report_path = None

    # 2. Seed tasky pro příští sprint — top_nodes z ExportHandoff (typed)
    # Post-8VZ: __main__._print_scorecard_report() sources top_nodes directly from
    # store.get_top_seed_nodes() and passes them to ExportHandoff(...) constructor.
    # Export does NOT access scheduler._ioc_graph — store-facing seam only.
    top_nodes = eh.top_nodes if eh.top_nodes else []

    # COMPAT BRIDGE: When top_nodes empty (e.g. windup path bypassed
    # _print_scorecard_report but export_sprint() still called directly),
    # fall back to store.get_top_seed_nodes() — store-facing seam.
    # Sprint 8VX §B: switched from store._ioc_graph.get_top_nodes_by_degree()
    # to store.get_top_seed_nodes() — no graph internals exposed.
    # Future owner: duckdb_store.get_top_seed_nodes()
    # REMOVAL CONDITION: ExportHandoff.top_nodes always populated in ALL windup paths
    if not top_nodes and store is not None:
        try:
            if hasattr(store, "get_top_seed_nodes"):
                top_nodes = store.get_top_seed_nodes(n=5)
        except Exception:
            pass

    # Sprint F500B §2 + F500D §3: seeds land alongside JSON report (colocation)
    # Primary: get_sprint_next_seeds_path() from paths.py (canonical)
    # Fallback: SPRINT_STORE_ROOT.parent/"reports" if report_path is None (write failed)
    # Sprint F150L: also pass branch_value + sprint_trend for enriched seeds
    branch_value = _get_branch_value(eh)
    sprint_trend = _get_sprint_trend(store, last_n=3)
    seeds_path = _generate_next_sprint_seeds(top_nodes, _sprint_id, report_path, pvs, branch_value, sprint_trend)

    # Sprint F150K: build sprint_summary for human use
    try:
        seeds_data = json.loads(seeds_path.read_text()) if seeds_path.exists() else []
        seeds_count = len(seeds_data) if isinstance(seeds_data, list) else 0
    except Exception:
        seeds_count = 0
    sprint_summary = _build_sprint_summary(pvs, seeds_count) if pvs else None

    # Sprint F150L: operator brief — derived from all available seams
    source_leaderboard = _get_source_leaderboard(store, days=7)
    # Sprint F150M: correlation from handoff + store fallback (fail-soft)
    correlation = _get_correlation_from_handoff(eh)

    # Sprint F150P: finish-layer truth fields from scheduler/core canonical surfaces
    runtime_truth = _get_runtime_truth(eh)
    feed_verdict = _get_feed_verdict(eh)
    public_verdict = _get_public_verdict(eh)
    signal_path = _get_signal_path(eh)
    hypothesis_pack = _get_hypothesis_pack(eh)
    canonical_run_summary = _get_canonical_run_summary(eh)
    sprint_verdict = _get_sprint_verdict(eh)
    # Sprint F157: synthesis_outcome_payload reader
    synthesis_outcome_payload = _get_synthesis_outcome_payload(eh)

    # Sprint F150P §2 + F157: enrich operator_brief with canonical_run_summary + sprint_verdict
    run_truth_note = _derive_run_truth_note(runtime_truth, canonical_run_summary, sprint_verdict, pvs) if pvs else ""
    branch_truth = _derive_branch_truth(feed_verdict, public_verdict, branch_value)
    best_first_move = _derive_best_first_move(runtime_truth, signal_path, canonical_run_summary, sprint_verdict, pvs, correlation) if pvs else ""
    why_this_run_matters = _derive_why_this_run_matters(runtime_truth, signal_path, hypothesis_pack, canonical_run_summary, sprint_verdict, pvs, correlation) if pvs else ""

    operator_brief = _build_operator_brief(pvs, branch_value, sprint_trend, source_leaderboard, seeds_count, correlation, runtime_truth, feed_verdict, public_verdict, signal_path, hypothesis_pack, canonical_run_summary, sprint_verdict, synthesis_outcome_payload) if pvs else None

    return {
        "report_json": str(report_path) if report_path else "",
        "seeds_json": str(seeds_path),
        "product_value_summary": pvs,
        "sprint_summary": sprint_summary,
        "operator_brief": operator_brief,
        # Sprint F150P: finish-layer fields
        "run_truth_note": run_truth_note,
        "branch_truth": branch_truth,
        "best_first_move": best_first_move,
        "why_this_run_matters": why_this_run_matters,
    }


def _generate_next_sprint_seeds(
    top_nodes: list,
    sprint_id: str,
    report_path: pathlib.Path | None,
    pvs: dict[str, Any] | None = None,
    branch_value: dict[str, Any] | None = None,
    sprint_trend: list[dict] | None = None,
) -> pathlib.Path:
    """
    Sprint F150J: Enhanced seed derivation driven by product_value_summary.

    4 seed categories derived from pvs:
      1. ioc_followup — top graph nodes (existing _type_aware_seeds logic)
      2. query_suggestion — based on signal_quality + reject_breakdown
      3. source_revisit — circuit-breaker open domains + depleted signal
      4. low_signal_recommendation — when sprint found almost nothing

    Bounded: max ~10 seeds total. No combinatorial explosion.

    Canonical next-seeds path (Sprint F500D):
      - Primary: get_sprint_next_seeds_path(sprint_id) z paths.py
      - Fallback: SPRINT_STORE_ROOT.parent/"reports" if report_path is None
    """
    from hledac.universal.paths import get_sprint_next_seeds_path, SPRINT_STORE_ROOT
    if report_path is not None:
        seeds_path = get_sprint_next_seeds_path(sprint_id)
    else:
        seeds_path = SPRINT_STORE_ROOT.parent / "reports" / f"{sprint_id}_next_seeds.json"
        seeds_path.parent.mkdir(parents=True, exist_ok=True)
    seeds: list[dict[str, Any]] = []

    try:
        # 1. IOC follow-up seeds from top_nodes (existing logic, now with reason tag)
        for node in top_nodes:
            try:
                if isinstance(node, dict):
                    ioc_value = str(node.get("value", "")) if node else ""
                    ioc_type = str(node.get("ioc_type", "unknown")) if node else "unknown"
                elif isinstance(node, (list, tuple)) and len(node) >= 2:
                    ioc_value = str(node[0]) if node[0] else ""
                    ioc_type = str(node[1]) if node[1] else "unknown"
                elif isinstance(node, (list, tuple)) and len(node) == 1:
                    ioc_value = str(node[0]) if node[0] else ""
                    ioc_type = "unknown"
                elif isinstance(node, str):
                    ioc_value = node
                    ioc_type = "unknown"
                elif isinstance(node, (int, float)):
                    ioc_value = str(node)
                    ioc_type = "unknown"
                else:
                    continue
            except Exception:
                continue

            if not ioc_value or len(ioc_value) < 3:
                continue

            node_seeds = _type_aware_seeds(ioc_value, ioc_type, reason="ioc_followup")
            seeds.extend(node_seeds)

        # 2. Sprint F150J: query_suggestion — derive next queries from sprint signal
        if pvs:
            query_seeds = _derive_query_seeds(pvs)
            seeds.extend(query_seeds)

        # 3. Sprint F150J: source_revisit — circuit breaker + depleted signal
        if pvs:
            revisit_seeds = _derive_source_revisit_seeds(pvs)
            seeds.extend(revisit_seeds)

        # 4. Sprint F150J: low_signal_recommendation — when sprint was nearly empty
        if pvs:
            low_signal_seeds = _derive_low_signal_seeds(pvs)
            seeds.extend(low_signal_seeds)

        # 5. Sprint F150K: hypothesis_engine.suggest_next_queries() seam
        if pvs:
            hyp_queries = _derive_hypothesis_queries(pvs, max_queries=2)
            seeds.extend(hyp_queries)

        # 6. Sprint F150K: focus/expand recommendations
        if pvs:
            focus_expand = _derive_focus_expand(pvs)
            seeds.extend(focus_expand)

        # 7. Sprint F150L: branch_value-driven seeds — which branch to expand
        if branch_value:
            branch_seeds = _derive_branch_seeds(branch_value)
            seeds.extend(branch_seeds)

        # 8. Sprint F150L: sprint_trend-driven seeds — what worked in recent sprints
        if sprint_trend:
            trend_seeds = _derive_trend_seeds(sprint_trend)
            seeds.extend(trend_seeds)

        # Bounded output — keep total seed count manageable
        MAX_SEEDS = 15
        if len(seeds) > MAX_SEEDS:
            # Sort by priority descending, keep top N
            seeds.sort(key=lambda s: s.get("priority", 0.5), reverse=True)
            seeds = seeds[:MAX_SEEDS]

        seeds_path.write_text(json.dumps(seeds, indent=2, default=str))
        logger.info(f"[EXPORT] {len(seeds)} enhanced seeds ({', '.join(_seed_type_counts(seeds))}) → {seeds_path}")
    except Exception as e:
        logger.warning(f"[EXPORT] Enhanced seed generation failed: {e}")
        seeds_path.write_text(json.dumps([], indent=2))

    return seeds_path


def _seed_type_counts(seeds: list[dict[str, Any]]) -> dict[str, int]:
    """Count seeds by their seed_type."""
    counts: dict[str, int] = {}
    for s in seeds:
        t = s.get("task_type", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return counts


def _derive_query_seeds(pvs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150J: query_suggestion — derive next query seeds from sprint signal.

    Reads: signal_quality, reject_breakdown, accepted, ioc_density.

    Logic:
      - high_density + accepted > 0 → suggest more of the same queries (query refinement)
      - medium_density → suggest broadening scope
      - low_density / slow_novelty → suggest different query strategy
      - depleted → no query seeds (already tried hard, switch approach)
    """
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)
    ioc_density = pvs.get("ioc_density", 0.0)
    findings_per_minute = pvs.get("findings_per_minute", 0.0)
    reject_breakdown = pvs.get("reject_breakdown") or {}

    seeds: list[dict[str, Any]] = []

    # Low-information rejection ratio — if most rejects were low-info, queries may be too broad
    total_rejected = pvs.get("total_rejected", 0)
    low_info_rejected = reject_breakdown.get("low_information", 0)
    low_info_ratio = low_info_rejected / total_rejected if total_rejected > 0 else 0.0

    if signal == "high_density":
        # Good sprint: suggest refining current query approach
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "refine",
            "priority": 0.75,
            "reason": f"signal=high_density/accepted={accepted}/ioc_density={ioc_density:.2f}",
        })
    elif signal == "medium_density":
        if low_info_ratio > 0.5:
            # Many low-info rejects → queries are too broad, suggest narrowing
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "narrow_scope",
                "priority": 0.70,
                "reason": f"low_info_ratio={low_info_ratio:.2f}/broad_queries",
            })
        else:
            # Mixed signal → suggest broadening
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "broaden",
                "priority": 0.65,
                "reason": f"signal=medium_density/ioc_density={ioc_density:.2f}",
            })
    elif signal == "slow_novelty":
        # Few findings but they exist — suggest faster queries or different sources
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "accelerate",
            "priority": 0.60,
            "reason": f"signal=slow_novelty/fpm={findings_per_minute:.2f}",
        })
    elif signal == "depleted":
        # Exhausted this query space — suggest fundamentally different queries
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "new_approach",
            "priority": 0.80,
            "reason": "signal=depleted/exhausted_query_space",
        })

    return seeds[:3]  # Hard cap: max 3 query suggestions


def _derive_source_revisit_seeds(pvs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150J: source_revisit — domains/hosts that need re-visiting.

    Reads: cb_open_domains (circuit breaker open domains), signal_quality.

    Logic:
      - cb_open_domains → retry with longer backoff
      - depleted signal → revisit domains that previously timed out
    """
    seeds: list[dict[str, Any]] = []
    cb_open: list[str] = pvs.get("cb_open_domains") or []
    signal = pvs.get("_signal_quality_classification", "unknown")

    if cb_open:
        for domain in cb_open[:3]:  # Max 3 domains from circuit breaker
            seeds.append({
                "task_type": "source_revisit",
                "value": domain,
                "priority": 0.55,
                "reason": "circuit_breaker_open",
                "backoff_seconds": 3600,  # 1h backoff recommendation
            })
    elif signal == "depleted":
        # No cb state but depleted — suggest retrying known sources with backoff
        seeds.append({
            "task_type": "source_revisit",
            "suggested_action": "retry_known_sources",
            "priority": 0.50,
            "reason": "signal=depleted/retry_after_backoff",
        })

    return seeds[:3]


def _derive_low_signal_seeds(pvs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150J: low_signal_recommendation — when sprint found almost nothing.

    Reads: accepted, total_rejected, findings_per_minute.

    Trigger: accepted <= 2 AND findings_per_minute < 0.5.

    Generates practical starting points for next sprint instead of
    continuing with same approach that yielded near-zero results.
    """
    accepted = pvs.get("accepted", 0)
    findings_per_minute = pvs.get("findings_per_minute", 0.0)
    total_rejected = pvs.get("total_rejected", 0)

    seeds: list[dict[str, Any]] = []

    if accepted <= 2 and findings_per_minute < 0.5 and total_rejected > 0:
        # Sprint was nearly empty — offer practical restart suggestions
        seeds.append({
            "task_type": "low_signal_recommendation",
            "suggested_action": "start_fresh",
            "priority": 0.70,
            "reason": f"accepted={accepted}/fpm={findings_per_minute:.2f}/near_empty_sprint",
        })
        # If dedup was effective but we still found nothing, sources may be exhausted
        if pvs.get("dedup_effective"):
            seeds.append({
                "task_type": "low_signal_recommendation",
                "suggested_action": "new_seed_sources",
                "priority": 0.65,
                "reason": "dedup_effective_but_depleted/switch_sources",
            })

    return seeds[:2]  # Hard cap: max 2 low-signal recommendations


def _type_aware_seeds(value: str, ioc_type: str, reason: str = "top_graph_node") -> list[dict[str, Any]]:
    """
    Sprint F500G §H2: Type-aware seed generation.

    Truthful mapping — generates seeds JEN kde typu odpovídá task_type.

    | ioc_type  | rdap_lookup | domain_to_ct | dht_infohash_lookup |
    |-----------|-------------|--------------|---------------------|
    | domain    | YES         | YES          | NO                  |
    | ip        | YES         | NO           | NO                  |
    | url       | YES         | NO           | NO                  |
    | infohash  | NO          | NO           | YES                 |
    | onion     | NO          | NO           | CONDITIONAL         |
    | cve       | NO          | NO           | NO                  |
    | md5/sha*  | NO          | NO           | NO                  |
    | unknown   | NO          | NO           | NO                  |

    Truthful skip: CVE, hash, unknown — žádné seed tasky, není co generovat.
    Willing to SKIP: není false-positive seed generation.
    """
    # Normalize ioc_type lowercase for matching
    t = ioc_type.lower()

    if t == "domain":
        return [
            {
                "task_type": "rdap_lookup",
                "value": value,
                "priority": 0.85,
                "reason": f"{reason}/{ioc_type}",
            },
            {
                "task_type": "domain_to_ct",
                "value": value,
                "priority": 0.80,
                "reason": f"{reason}/{ioc_type}",
            },
        ]
    elif t in ("ip", "ipv4", "ipv6"):
        return [
            {
                "task_type": "rdap_lookup",
                "value": value,
                "priority": 0.85,
                "reason": f"{reason}/{ioc_type}",
            },
        ]
    elif t == "url":
        # URL has host component — RDAP lookup makes sense
        # domain_to_ct makes NO sense (URL is not a domain)
        return [
            {
                "task_type": "rdap_lookup",
                "value": value,
                "priority": 0.80,
                "reason": f"{reason}/{ioc_type}",
            },
        ]
    elif t == "infohash":
        return [
            {
                "task_type": "dht_infohash_lookup",
                "value": value,
                "priority": 0.90,
                "reason": f"{reason}/{ioc_type}",
            },
        ]
    elif t == "onion":
        # Onion is not a DNS domain — no domain_to_ct
        # DHT lookup is marginally relevant (some Tor research uses DHT)
        # but skip entirely to be safe — no strong signal
        return []
    elif t in ("cve", "md5", "sha1", "sha256", "sha512", "sha384",
               "md6", "ripemd160", "unknown", "email", "phone",
               "ipv4_addr", "ipv6_addr", "mac_addr", "btc", "eth",
               "xmpp", "jabber"):
        # Truthful skip — these types have no meaningful follow-up seed
        # CVE: vuln ID not a network observable
        # Hashes: not domains, not infohashes, not IPs
        # Unknown: no valid seeds possible
        return []
    else:
        # Catch-all for any other type not explicitly handled:
        # generate NO seeds — better to skip than to generate falsy task
        return []


def _build_product_value_summary(
    store: Any,
    eh: "ExportHandoff",  # type: ignore[name-defined]
    sprint_id: str,
) -> dict[str, Any]:
    """
    Sprint F150I §1: product_value_summary — agreguje truth surfaces do jednoho
    rozhodovacího balíčku pro další sprinty.

    ZDROJE (existující surfaces, žádné nové):
      1. eh.scorecard — windup output (findings_per_minute, ioc_density,
         semantic_novelty, accepted_findings, peak_rss_mb, phase_timings)
      2. store.get_dedup_runtime_status() — accepted vs rejected by reason
         (Sprint 8AV extended: low-info / in-memory-dup / persistent-dup / fail-open)
      3. eh.scorecard["cb_open_domains"] — circuit breaker state
      4. eh.gnn_predictions — ML model signal (0 pokud nepoužit)
      5. eh.phase_durations — timing truth

    DEGRADED MODE: pokud store není dostupný, pole jsou None — není to chyba,
    je to expected degraded state pro standalone/test scénáře.

    JE TO DERIVED OUTPUT, NE NOVÝ TRUTH STORE:
      - Žádné nové write API
      - Žádné nové history mechanismy
      - Pouze čte z existujících surfaces a skládá je dohromady
    """
    scorecard = eh.scorecard if eh.scorecard else {}

    # 1. Základní scorecard facts
    # Sprint F150I §1: all numeric fields use isinstance guards to prevent
    # TypeError when scorecard contains MagicMock or other non-numeric values
    def _num(val, default):
        return val if isinstance(val, (int, float)) else default
    def _n(key, default):
        return _num(scorecard.get(key, default), default)
    accepted = _n("accepted_findings", 0) or _n("accepted_findings_count", 0)
    findings_per_minute = _n("findings_per_minute", 0.0)
    ioc_density = _n("ioc_density", 0.0)
    peak_rss_mb = scorecard.get("peak_rss_mb", None)
    if peak_rss_mb is not None and not isinstance(peak_rss_mb, (int, float)):
        peak_rss_mb = None
    phase_timings = scorecard.get("phase_duration_seconds", {}) or {}

    # 2. Dedup status — Sprint 8AV extended ingest outcome counters
    dedup_status: dict[str, Any] | None = None
    if store is not None:
        try:
            if hasattr(store, "get_dedup_runtime_status"):
                raw = store.get_dedup_runtime_status()
                # Sprint F150I §6: guard against MagicMock / non-dict returns
                if isinstance(raw, dict):
                    dedup_status = raw
        except Exception:
            pass

    if dedup_status:
        accepted = dedup_status.get("accepted_count", accepted)
        reject_breakdown = {
            "low_information": dedup_status.get("low_information_rejected_count", 0),
            "in_memory_duplicate": dedup_status.get("in_memory_duplicate_rejected_count", 0),
            "persistent_duplicate": dedup_status.get("persistent_duplicate_rejected_count", 0),
            "fail_open": dedup_status.get("other_rejected_count", 0),
        }
        total_rejected = sum(reject_breakdown.values())
        dedup_effective = dedup_status.get("persistent_dedup_enabled", False)
        dedup_lmdb_path = dedup_status.get("dedup_lmdb_path", "")
        hot_cache = {
            "size": dedup_status.get("hot_cache_size", 0),
            "capacity": dedup_status.get("hot_cache_capacity", 0),
        }
    else:
        reject_breakdown = None
        total_rejected = None
        dedup_effective = None
        dedup_lmdb_path = None
        hot_cache = None

    # 3. Circuit breaker state
    cb_open_domains = scorecard.get("cb_open_domains", []) or []

    # 4. GNN predictions
    gnn_predictions = eh.gnn_predictions if eh.gnn_predictions else 0

    # 5. Synthesis engine
    synthesis_engine = eh.synthesis_engine if eh.synthesis_engine else (
        scorecard.get("synthesis_engine_used", "unknown") or "unknown"
    )

    # Sprint F178C: signal_quality renamed to _signal_quality_classification
    # PRECISE SEPARATION of FACTS vs DERIVED:
    # - FACTS (raw data from scorecard/store): accepted, reject_breakdown, total_rejected,
    #   findings_per_minute, ioc_density, peak_rss_mb, phase_durations, cb_open_domains,
    #   gnn_predictions, synthesis_engine, dedup_effective, dedup_lmdb_path, hot_cache
    # - DERIVED (computed from facts): _signal_quality_classification
    #   NOTE: _prefix means "derived classification, not raw fact"
    if accepted > 0 and findings_per_minute > 0:
        if ioc_density >= 0.5:
            _signal_quality = "high_density"
        elif ioc_density >= 0.2:
            _signal_quality = "medium_density"
        else:
            _signal_quality = "low_density"
    elif accepted > 0 and findings_per_minute > 0 and ioc_density < 0.2:
        _signal_quality = "slow_novelty"
    elif accepted == 0 and dedup_status:
        _signal_quality = "depleted"
    else:
        _signal_quality = "unknown"

    summary: dict[str, Any] = {
        "sprint_id": sprint_id,
        # FACTS — raw data from scorecard/store
        "accepted": accepted,
        "reject_breakdown": reject_breakdown,
        "total_rejected": total_rejected,
        "findings_per_minute": findings_per_minute,
        "ioc_density": ioc_density,
        "peak_rss_mb": peak_rss_mb,
        "phase_durations": phase_timings if phase_timings else None,
        "cb_open_domains": cb_open_domains,
        "gnn_predictions": gnn_predictions,
        "synthesis_engine": synthesis_engine,
        "dedup_effective": dedup_effective,
        "dedup_lmdb_path": dedup_lmdb_path,
        "hot_cache": hot_cache,
        # DERIVED — computed from facts (prefix _ = classification, not raw fact)
        "_signal_quality_classification": _signal_quality,
    }

    # Remove None fields for cleaner output (keep 0 as valid)
    return {k: v for k, v in summary.items() if v is not None}


def _derive_hypothesis_queries(
    pvs: dict[str, Any],
    max_queries: int = 3,
) -> list[dict[str, Any]]:
    """
    Sprint F150K: Lazy hypothesis_engine.suggest_next_queries() seam.

    Bounded helper — only used to enrich query_followup seeds.
    Fails soft: if hypothesis_engine not available or call fails, returns [].
    Never blocks on MLX model loading.

    Args:
        pvs: product_value_summary (provides findings context)
        max_queries: hard cap on returned queries (default 3)

    Returns:
        List of query dicts with keys: query, rationale, type
    """
    try:
        from hledac.universal.brain.hypothesis_engine import HypothesisEngine
    except ImportError:
        return []

    try:
        # Lightweight instance — no inference engine, no MLX
        engine = HypothesisEngine(
            inference_engine=None,
            max_hypotheses=20,
            enable_adversarial_verification=False,
        )
    except Exception:
        return []

    # Build findings string from pvs signal
    findings: list[str] = []
    signal = pvs.get("_signal_quality_classification", "unknown")

    if signal == "high_density":
        accepted = pvs.get("accepted", 0)
        ioc_density = pvs.get("ioc_density", 0.0)
        findings.append(f"high_value_findings: {accepted} entities at density {ioc_density:.2f}")
    elif signal == "medium_density":
        findings.append(f"mixed_results: investigate_correlations")
    elif signal == "slow_novelty":
        findings.append(f"slow_but_real: verify_and_expand")
    elif signal == "depleted":
        findings.append(f"exhausted_space: new_approach_needed")

    # Dedup status context
    dedup = pvs.get("reject_breakdown")
    if dedup:
        low_info = dedup.get("low_information", 0)
        total = pvs.get("total_rejected", 1)
        if total > 0 and low_info / total > 0.5:
            findings.append(f"low_info_rejects: narrow_scope_recommended")

    try:
        queries = engine.suggest_next_queries(
            findings=findings,
            context={"known_iocs": set()},
            max_queries=max_queries,
        )
        # Convert to export format
        result = []
        for q in queries[:max_queries]:
            result.append({
                "task_type": "query_suggestion",
                "suggested_action": q.get("type", "entity_expansion"),
                "value": q.get("query", ""),
                "priority": 0.60,
                "reason": q.get("rationale", "hypothesis_engine_suggestion"),
            })
        return result
    except Exception:
        return []


def _derive_focus_expand(pvs: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150K: Focus/expand recommendations based on sprint signal.

    Derived purely from pvs signal_quality — no new data sources.
    Returns max 2 recommendations (one focus, one expand if both apply).

    Args:
        pvs: product_value_summary

    Returns:
        List of recommendation dicts with keys: task_type, suggested_action, priority, reason
    """
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)
    ioc_density = pvs.get("ioc_density", 0.0)
    dedup_effective = pvs.get("dedup_effective", False)

    recs: list[dict[str, Any]] = []

    if signal == "high_density":
        # Good results — focus on what works
        recs.append({
            "task_type": "focus_recommendation",
            "suggested_action": "focus_on_high_density",
            "priority": 0.80,
            "reason": f"signal=high_density/accepted={accepted}/ioc_density={ioc_density:.2f}",
        })
        recs.append({
            "task_type": "expand_recommendation",
            "suggested_action": "expand_sources",
            "priority": 0.70,
            "reason": "high_density_means_room_to_broaden",
        })
    elif signal == "medium_density":
        recs.append({
            "task_type": "focus_recommendation",
            "suggested_action": "narrow_scope",
            "priority": 0.75,
            "reason": "medium_density_mixed_signal_narrow_focus",
        })
    elif signal == "slow_novelty":
        recs.append({
            "task_type": "focus_recommendation",
            "suggested_action": "accelerate_existing",
            "priority": 0.65,
            "reason": "slow_novelty_means_queries_work_just_slow",
        })
        recs.append({
            "task_type": "expand_recommendation",
            "suggested_action": "new_timing_strategy",
            "priority": 0.60,
            "reason": "temporal_patterns_may_unlock_findings",
        })
    elif signal == "depleted":
        recs.append({
            "task_type": "focus_recommendation",
            "suggested_action": "abandon_current_approach",
            "priority": 0.85,
            "reason": "depleted_signal_switch_approach",
        })
        if dedup_effective:
            recs.append({
                "task_type": "expand_recommendation",
                "suggested_action": "completely_new_sources",
                "priority": 0.75,
                "reason": "dedup_effective_sources_exhausted",
            })

    return recs[:2]


def _derive_branch_seeds(branch_value: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Sprint F150L: Branch-driven seed derivation — which branch to expand next.

    Reads branch_value from scorecard:
      - feed_findings / public_findings
      - branch_verdict: feed_dominant | public_dominant | balanced
      - recommendation: expand_feed_branch | expand_public_branch | maintain_both

    Returns seeds for which branch to pursue next.
    """
    verdict = branch_value.get("branch_verdict", "")
    seeds: list[dict[str, Any]] = []

    if verdict == "feed_dominant":
        # Feed branch is winning — suggest expanding it
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "expand_feed_branch",
            "priority": 0.78,
            "reason": f"feed_dominant/{verdict}",
        })
    elif verdict == "public_dominant":
        # Public branch is winning — suggest expanding it
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "expand_public_branch",
            "priority": 0.78,
            "reason": f"public_dominant/{verdict}",
        })
    elif verdict == "balanced":
        # Both branches contribute — suggest balancing effort
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "balance_branches",
            "priority": 0.70,
            "reason": "balanced_both_branches_contribute",
        })

    # If one side had zero findings, flag it explicitly
    feed_f = branch_value.get("feed_findings", 0)
    public_f = branch_value.get("public_findings", 0)
    if feed_f == 0 and public_f > 0:
        seeds.append({
            "task_type": "source_revisit",
            "suggested_action": "activate_feed_sources",
            "priority": 0.72,
            "reason": "feed_branch_zero_findings_try_activating",
        })
    elif public_f == 0 and feed_f > 0:
        seeds.append({
            "task_type": "source_revisit",
            "suggested_action": "activate_public_sources",
            "priority": 0.72,
            "reason": "public_branch_zero_findings_try_activating",
        })

    return seeds[:3]  # Hard cap: max 3 branch seeds


def _derive_trend_seeds(sprint_trend: list[dict]) -> list[dict[str, Any]]:
    """
    Sprint F150L: Sprint-trend-driven seed derivation — what worked in recent sprints.

    Reads from sprint_trend (store.get_sprint_trend()):
      - For each recent sprint: sprint_id, new_findings, ioc_nodes, findings_per_min

    Logic:
      - High fpm sprint → accelerate same query approach
      - Zero findings sprint → pivot
      - Trend upward → continue; trend downward → adjust
    """
    seeds: list[dict[str, Any]] = []
    if not sprint_trend:
        return seeds

    # Analyze trend across recent sprints
    fpm_values = []
    for s in sprint_trend:
        fpm = s.get("findings_per_min") or s.get("findings_per_minute") or 0
        if isinstance(fpm, (int, float)):
            fpm_values.append(float(fpm))

    if len(fpm_values) >= 2:
        recent = fpm_values[0]
        older = fpm_values[-1]
        if recent > older * 1.5:
            # Trending up — continue same approach
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "accelerate_same_approach",
                "priority": 0.72,
                "reason": f"trend_up/{recent:.2f}_vs_{older:.2f}_fpm",
            })
        elif recent < older * 0.5 and recent > 0:
            # Trending down sharply — adjust
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "pivot_approach",
                "priority": 0.74,
                "reason": f"trend_down/{recent:.2f}_vs_{older:.2f}_fpm",
            })
    elif len(fpm_values) == 1:
        fpm = fpm_values[0]
        if fpm > 0.5:
            seeds.append({
                "task_type": "query_suggestion",
                "suggested_action": "same_query_continue",
                "priority": 0.68,
                "reason": f"single_sprint_fpm={fpm:.2f}",
            })

    # If all recent sprints had 0 findings — flag depleted query space
    if fpm_values and all(f == 0 for f in fpm_values):
        seeds.append({
            "task_type": "query_suggestion",
            "suggested_action": "completely_new_queries",
            "priority": 0.82,
            "reason": "all_recent_sprints_zero_findings",
        })

    return seeds[:2]  # Hard cap: max 2 trend seeds


def _get_sprint_trend(store: Any, last_n: int = 5) -> list[dict]:
    """
    Sprint F150L: Fail-soft store seam pro sprint trend.
    Používá existující duckdb_store.get_sprint_trend().
    """
    if store is None:
        return []
    try:
        if hasattr(store, "get_sprint_trend"):
            return store.get_sprint_trend(last_n=last_n) or []
    except Exception:
        pass
    return []


def _get_source_leaderboard(store: Any, days: int = 7) -> list[dict]:
    """
    Sprint F150L: Fail-soft store seam pro source leaderboard.
    Používá existující duckdb_store.get_source_leaderboard().
    """
    if store is None:
        return []
    try:
        if hasattr(store, "get_source_leaderboard"):
            return store.get_source_leaderboard(days=days) or []
    except Exception:
        pass
    return []


def _get_correlation_from_handoff(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150M + F157: correlation — handoff-first truth order.

    Truth order (priority):
      1. eh.correlation (typed RunCorrelation) → converted to dict — handoff-first
      2. eh.scorecard["correlation"] — scorecard-only builds
      3. eh.scorecard["so_what"], scorecard["dominant_cluster"] etc. — legacy scorecard keys

    RunCorrelation is typed but downstream (_build_operator_brief) expects dict.
    Convert via asdict() — msgspec Struct semantics guarantee zero-copy.

    Fail-soft: returns None when correlation unavailable (older sprints).
    NO new store reads. NO new persistence. NO new planner.
    """
    # Priority 1: typed eh.correlation (handoff-first canonical surface)
    if eh.correlation is not None:
        rc = eh.correlation
        # RunCorrelation is a plain attrs class — extract fields manually
        result: dict[str, Any] = {}
        if rc.run_id is not None:
            result["run_id"] = rc.run_id
        if rc.branch_id is not None:
            result["branch_id"] = rc.branch_id
        if rc.provider_id is not None:
            result["provider_id"] = rc.provider_id
        if rc.action_id is not None:
            result["action_id"] = rc.action_id
        return result if result else None

    # Priority 2: scorecard["correlation"] dict
    scorecard = eh.scorecard if eh.scorecard else {}
    if not scorecard:
        # F171B S3: explicit no-correlation flag for empty scorecard
        return {"_no_correlation_data": True}
    corr = scorecard.get("correlation") or scorecard.get("run_correlation")
    if corr and isinstance(corr, dict):
        return corr

    # Priority 3: legacy scorecard root fields
    so_what = scorecard.get("so_what") or ""
    dominant_cluster = scorecard.get("dominant_cluster") or scorecard.get("cluster") or ""
    campaign_hints = scorecard.get("campaign_hints") or []
    high_risk = scorecard.get("high_risk_branch") or scorecard.get("high_risk") or []
    risk_score = scorecard.get("risk_score")
    verdict = scorecard.get("verdict") or scorecard.get("risk_verdict") or ""

    # F171B §3: explicit no-correlation flag when scorecard has empty correlation dict
    # Previously returned None — _build_operator_brief then set so_what="" with no explanation.
    # Now returns explicit flag so the operator brief can surface "no correlation data" clearly.
    if so_what or dominant_cluster or campaign_hints or high_risk:
        result = {}
        if so_what:
            result["so_what"] = so_what
        if dominant_cluster:
            result["dominant_cluster"] = dominant_cluster
        if campaign_hints:
            result["campaign_hints"] = campaign_hints if isinstance(campaign_hints, list) else []
        if high_risk:
            result["high_risk_branch"] = high_risk if isinstance(high_risk, list) else []
        if risk_score is not None:
            result["risk_score"] = risk_score
        if verdict:
            result["verdict"] = verdict
        return result if result else {"_no_correlation_data": True}

    return {"_no_correlation_data": True}


def _get_runtime_truth(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P + F157: runtime_truth — handoff-first truth order.

    Truth order (priority):
      1. eh.top_level["runtime_truth"] — primary canonical surface
      2. eh.scorecard["runtime_truth"] — fallback pro scorecard-only builds
      3. eh.scorecard["run_truth"] — legacy scorecard key

    compute_sprint_intelligence() v scheduleru produkuje runtime_truth dict
    a ukládá ho do scorecard["runtime_truth"]. Windup path ho také kopíruje
    do eh.top_level["runtime_truth"] (typed ExportHandoff konstruktor).

    Fail-soft: returns None when not present (older sprints, smoke runs).
    NO new store reads. NO new planner. NO write-back.
    """
    # Priority 1: top-level canonical surface
    rt = eh.runtime_truth if eh.runtime_truth else None
    if rt and isinstance(rt, dict):
        return rt
    # Priority 2 & 3: scorecard fallback (legacy / scorecard-only builds)
    scorecard = eh.scorecard if eh.scorecard else {}
    rt = scorecard.get("runtime_truth") or scorecard.get("run_truth")
    if rt and isinstance(rt, dict):
        return rt
    return None


def _get_feed_verdict(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: feed_verdict z ExportHandoff.scorecard.

    Aggregated feed economics verdict across sprint cycles.
    Produced by compute_sprint_intelligence() → scorecard["feed_verdict"].

    Seam: scorecard["feed_verdict"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    fv = scorecard.get("feed_verdict")
    if fv and isinstance(fv, dict):
        return fv
    return None


def _get_public_verdict(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: public_verdict z ExportHandoff.scorecard.

    Aggregated public branch verdict across sprint cycles.
    Produced by compute_sprint_intelligence() → scorecard["public_verdict"].

    Seam: scorecard["public_verdict"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    pv = scorecard.get("public_verdict")
    if pv and isinstance(pv, dict):
        return pv
    return None


def _get_signal_path(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: signal_path z ExportHandoff.scorecard.

    Dominant signal path, next pivot recommendation, corroboration health.
    Produced by compute_sprint_intelligence() → scorecard["signal_path"].

    Seam: scorecard["signal_path"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    sp = scorecard.get("signal_path")
    if sp and isinstance(sp, dict):
        return sp
    return None


def _get_hypothesis_pack(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P: hypothesis_pack z ExportHandoff.scorecard.

    Operator shortlist + actionability summary z hypothesis_engine.
    Produced by compute_sprint_intelligence() → scorecard["hypothesis_pack"].

    Seam: scorecard["hypothesis_pack"]
    Fail-soft: returns None when not present.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    hp = scorecard.get("hypothesis_pack")
    if hp and isinstance(hp, dict):
        return hp
    return None


def _get_canonical_run_summary(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P §2 + F157: canonical_run_summary — handoff-first truth order.

    Truth order (priority):
      1. eh.top_level["canonical_run_summary"] — primary canonical surface
      2. eh.scorecard["canonical_run_summary"] — fallback pro scorecard-only builds

    High-level sprint characterization produced by compute_sprint_intelligence().
    Contains: sprint_id, total_cycles, accepted_findings, signal_verdict,
    feed_public_balance, key_highlight, primary_theme.

    Fail-soft: returns None when not present (older sprints).
    """
    # Priority 1: top-level canonical surface
    crs = eh.canonical_run_summary if eh.canonical_run_summary else None
    if crs and isinstance(crs, dict):
        return crs
    # Priority 2: scorecard fallback (legacy / scorecard-only builds)
    scorecard = eh.scorecard if eh.scorecard else {}
    crs = scorecard.get("canonical_run_summary")
    if crs and isinstance(crs, dict):
        return crs
    return None


def _get_sprint_verdict(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150P §2 + F157: sprint_verdict — handoff-first truth order.

    Truth order (priority):
      1. eh.top_level["sprint_verdict"] — primary canonical surface
      2. eh.scorecard["sprint_verdict"] — fallback pro scorecard-only builds

    Aggregated sprint quality verdict: success / partial / failed / degraded.
    Produced by compute_sprint_intelligence() → scorecard["sprint_verdict"].

    Fail-soft: returns None when not present.
    """
    # Priority 1: top-level canonical surface
    sv = eh.sprint_verdict if eh.sprint_verdict else None
    if sv and isinstance(sv, dict):
        return sv
    # Priority 2: scorecard fallback (legacy / scorecard-only builds)
    scorecard = eh.scorecard if eh.scorecard else {}
    sv = scorecard.get("sprint_verdict")
    if sv and isinstance(sv, dict):
        return sv
    return None


def _get_synthesis_outcome_payload(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F157: synthesis_outcome_payload — handoff-first truth order.

    Truth order (priority):
      1. eh.top_level["synthesis_outcome_payload"] — primary canonical surface
      2. eh.scorecard["synthesis_outcome_payload"] — fallback pro scorecard-only builds

    Serialized SynthesisOutcome seam from synthesis_runner.
    Fail-soft: returns None when not present (synthesis not run, or older builds).
    """
    # Priority 1: top-level canonical surface
    sop = eh.synthesis_outcome_payload if eh.synthesis_outcome_payload else None
    if sop and isinstance(sop, dict):
        return sop
    # Priority 2: scorecard fallback (legacy / scorecard-only builds)
    scorecard = eh.scorecard if eh.scorecard else {}
    sop = scorecard.get("synthesis_outcome_payload")
    if sop and isinstance(sop, dict):
        return sop
    return None


def _derive_run_truth_note(
    runtime_truth: dict[str, Any] | None,
    canonical_run_summary: dict[str, Any] | None,
    sprint_verdict: dict[str, Any] | None,
    pvs: dict[str, Any] | None,
) -> str:
    """
    Sprint F176D: run_truth_note — operator-facing sprint characterization.

    Tightened for fast operator triage: meaningful vs smoke vs degraded.

    Priority order:
      1. runtime_truth["is_meaningful"] — primary empirical signal
      2. sprint_verdict["verdict" / "sprint_status"] — synthesized verdict
      3. pvs.signal_quality — last resort fallback

    Labels are short for operator glance:
      - meaningful_run, smoke_run, slow_signal_run, mixed_run, degraded_run, unknown_run
    """
    # Priority 1: runtime_truth is PRIMARY
    if runtime_truth:
        is_meaningful = runtime_truth.get("is_meaningful")
        evidence_note = runtime_truth.get("evidence_note") or ""
        if is_meaningful is True:
            return f"meaningful_run" + (f": {evidence_note}" if evidence_note else "")
        elif is_meaningful is False:
            return f"smoke_run" + (f": {evidence_note}" if evidence_note else "")
        elif isinstance(is_meaningful, str):
            return f"{is_meaningful}" + (f": {evidence_note}" if evidence_note else "")

    # Priority 2: sprint_verdict — degraded/failed checked before general verdict
    if sprint_verdict:
        status = sprint_verdict.get("sprint_status") or sprint_verdict.get("verdict") or ""
        if status in ("degraded", "failed"):
            return f"degraded_run: {status}"
        if status and len(status) > 2:
            return f"run: {status}"

    # Priority 3: canonical_run_summary signal_verdict
    if canonical_run_summary:
        sig_verdict = canonical_run_summary.get("signal_verdict") or ""
        if sig_verdict and len(sig_verdict) > 2:
            return f"signal={sig_verdict}"

    # Priority 4: pvs-based fallback
    if pvs is None:
        return "unknown_run: insufficient data"
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)

    if signal == "high_density":
        return "meaningful_run: high density signal"
    elif signal == "medium_density":
        return "mixed_run: signal present but noisy"
    elif signal == "depleted":
        return "smoke_run: depleted, no actionable signal"
    elif signal == "slow_novelty":
        return "slow_signal_run: signal exists, low rate"
    elif signal == "unknown" and accepted > 0:
        return "findings_run: signal unclear, findings exist"
    elif signal == "unknown":
        return "unknown_run: no signal characterization"
    return "unknown_run"


def _derive_branch_truth(
    feed_verdict: dict[str, Any] | None,
    public_verdict: dict[str, Any] | None,
    branch_value: dict[str, Any] | None,
) -> str:
    """
    Sprint F150P §1: branch_truth — definitive feed/public balance summary.

    Combines feed_verdict + public_verdict + branch_value into single sentence.

    Reads:
    - feed_verdict["dominant_tag"], feed_verdict["avg_quality"]
    - public_verdict["avg_value_ratio"], public_verdict["dominant_next_action"]
    - branch_value["branch_verdict"], feed/public percentages

    Fail-soft: falls back to branch_value alone if verdicts unavailable.
    """
    parts: list[str] = []

    if feed_verdict:
        tag = feed_verdict.get("dominant_tag", "")
        quality = feed_verdict.get("avg_quality", 0)
        if tag:
            parts.append(f"feed={tag}(q={quality})")

    if public_verdict:
        vr = public_verdict.get("avg_value_ratio", 0)
        action = public_verdict.get("dominant_next_action", "")
        if vr > 0:
            parts.append(f"public=val_ratio({vr:.2f})")
        if action:
            parts.append(f"public_action={action[:20]}")

    if branch_value:
        verdict = branch_value.get("branch_verdict", "")
        feed_pct = branch_value.get("feed_pct", 0)
        pub_pct = branch_value.get("public_pct", 0)
        if verdict:
            parts.append(f"verdict={verdict}(feed={feed_pct:.0f}%/pub={pub_pct:.0f}%)")

    if not parts:
        return "branch_truth: insufficient data"
    return " | ".join(parts)


def _derive_best_first_move(
    runtime_truth: dict[str, Any] | None,
    signal_path: dict[str, Any] | None,
    canonical_run_summary: dict[str, Any] | None,
    sprint_verdict: dict[str, Any] | None,
    pvs: dict[str, Any] | None,
    correlation: dict[str, Any] | None,
) -> str:
    """
    Sprint F176D: best_first_move — immediate next action (single sentence).

    Priority order (tightened for operator speed):
    1. DEGRADED indicator FIRST — operator needs to know if sprint was bad
       - runtime_truth.is_meaningful=False → smoke signal
       - sprint_verdict.degraded → degraded state
    2. High-risk findings — critical findings override all other guidance
    3. sprint_verdict["recommended_action"] — most synthesized verdict
    4. signal_path next_pivot_recommendation
    5. canonical_run_summary["next_action"]
    6. pvs signal quality guidance
    7. correlation operator_shortlist first item

    Single sentence, max 80 chars.
    """
    # 0. DEGRADED FIRST — operator needs instant awareness of bad sprints
    if runtime_truth:
        is_meaningful = runtime_truth.get("is_meaningful")
        if is_meaningful is False:
            return "pivot: smoke run, change approach immediately"

    if sprint_verdict:
        status = sprint_verdict.get("sprint_status") or sprint_verdict.get("verdict") or ""
        if status in ("degraded", "failed"):
            return f"degraded sprint ({status}) — investigate root cause"

    # 1. High-risk first (critical findings take operational priority)
    if correlation:
        high_risk = correlation.get("high_risk_branch") or correlation.get("high_risk") or []
        if high_risk and len(high_risk) > 0:
            return "investigate high-risk branch — critical findings present"

    # 2. sprint_verdict recommended action
    if sprint_verdict:
        rec_action = sprint_verdict.get("recommended_action") or sprint_verdict.get("next_action") or ""
        if rec_action and len(rec_action) > 2:
            return f"action: {rec_action[:80]}"

    # 3. Signal path next pivot
    if signal_path:
        next_pivot = signal_path.get("next_pivot_recommendation", "")
        if next_pivot == "pivot_immediately":
            return "pivot: signal path recommends immediate pivot"

    # 4. canonical_run_summary next_action
    if canonical_run_summary:
        na = canonical_run_summary.get("next_action") or canonical_run_summary.get("recommended_action") or ""
        if na and len(na) > 2:
            return f"action: {na[:80]}"

    # 5. pvs signal guidance
    signal = pvs.get("_signal_quality_classification", "unknown") if pvs else "unknown"
    if signal == "depleted":
        return "new approach: current query space exhausted"
    elif signal == "high_density":
        return "expand: broaden successful query approach"
    elif signal == "medium_density":
        return "narrow: reduce query scope to reduce low-info noise"
    elif signal == "slow_novelty":
        return "accelerate: real signal exists, speed up sources"

    # 6. Correlation operator shortlist
    if correlation:
        shortlist = correlation.get("operator_shortlist") or []
        if shortlist and isinstance(shortlist, list) and len(shortlist) > 0:
            first = shortlist[0]
            if isinstance(first, dict):
                action = first.get("action", "")
                target = first.get("target", "")
                if action:
                    return f"{action}: {target[:40]}" if target else action[:80]

    return "assess: gather more data before committing to approach"


def _derive_why_this_run_matters(
    runtime_truth: dict[str, Any] | None,
    signal_path: dict[str, Any] | None,
    hypothesis_pack: dict[str, Any] | None,
    canonical_run_summary: dict[str, Any] | None,
    sprint_verdict: dict[str, Any] | None,
    pvs: dict[str, Any] | None,
    correlation: dict[str, Any] | None,
) -> str:
    """
    Sprint F150P §1: why_this_run_matters — one-liner significance statement.

    Tells operator why this sprint's output matters for future decisions.

    Priority order:
    - sprint_verdict["why_matters"] / sprint_verdict["significance"] — most synthesized
    - canonical_run_summary["key_highlight"] — primary theme highlight
    - correlation["so_what"] — correlation verdict
    - hypothesis_pack["what_matters_first"]
    - runtime_truth["primary_signal_source"]
    - signal_path["dominant_signal_path"]
    - pvs.signal_quality + accepted count

    F161F §A: pvs may be None — guarded at pvs-based fallback.
    """
    # F150P §2: sprint_verdict most synthesized — check first
    if sprint_verdict:
        why = sprint_verdict.get("why_matters") or sprint_verdict.get("significance") or ""
        if why and len(why) > 3:
            return why[:100]

    # canonical_run_summary key_highlight
    if canonical_run_summary:
        kh = canonical_run_summary.get("key_highlight") or ""
        if kh and len(kh) > 3:
            return kh[:100]

    # Priority: correlation so_what (most synthesized)
    if correlation:
        so_what = correlation.get("so_what") or ""
        if so_what and len(so_what) > 5:
            return so_what[:100]

    # hypothesis_pack what_matters_first
    if hypothesis_pack:
        wmf = hypothesis_pack.get("what_matters_first") or ""
        if wmf and len(wmf) > 5:
            return wmf[:100]

    # runtime_truth primary signal source
    if runtime_truth:
        pss = runtime_truth.get("primary_signal_source") or ""
        if pss and len(pss) > 3:
            return f"signal from {pss}"

    # signal_path dominant path
    if signal_path:
        dsp = signal_path.get("dominant_signal_path", "")
        if dsp:
            return f"dominant path: {dsp}"

    # pvs-based fallback — F161F §A: guard against None pvs
    if pvs is None:
        return "no actionable signal — sprint produced no useful leads"
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)
    if signal == "high_density" and accepted > 0:
        return f"{accepted} quality findings confirmed at high density"
    elif signal == "depleted":
        return "exhausted space — strategic pivot needed"
    elif signal == "slow_novelty":
        return "real but slow signal — rate vs quality tradeoff"
    elif signal == "medium_density" and accepted > 0:
        return f"{accepted} findings present, signal mixed quality"
    elif accepted > 0:
        return f"{accepted} findings found, signal quality unclear"
    return "no actionable signal — sprint produced no useful leads"


def _get_branch_value(eh: "ExportHandoff") -> dict[str, Any] | None:  # type: ignore[name-defined]
    """
    Sprint F150L: branch_value z scorecard — feed vs public branch analysis.
    Přítomno v scorecard pokud windup scheduler běžel s paralelními branchemi.
    """
    scorecard = eh.scorecard if eh.scorecard else {}
    return scorecard.get("branch_value") or None


def _build_operator_brief(
    pvs: dict[str, Any],
    branch_value: dict[str, Any] | None,
    sprint_trend: list[dict],
    source_leaderboard: list[dict],
    seeds_count: int,
    correlation: dict[str, Any] | None = None,
    runtime_truth: dict[str, Any] | None = None,
    feed_verdict: dict[str, Any] | None = None,
    public_verdict: dict[str, Any] | None = None,
    signal_path: dict[str, Any] | None = None,
    hypothesis_pack: dict[str, Any] | None = None,
    canonical_run_summary: dict[str, Any] | None = None,
    sprint_verdict: dict[str, Any] | None = None,
    synthesis_outcome_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Sprint F150L: Praktický operator brief — co sprint našel, která branch nesla signál,
    co bylo slabé místo, jaký je nejbližší další krok, 2-5 nejzajímavějších follow-upů.

    Sprint F150M: Correlation integration — correlation-derived fields surface here:
      - so_what: one-liner operator takeaway (from CorrelationResult)
      - dominant_cluster: theme with most high-severity findings
      - campaign_hints: findings suggesting same campaign
      - high_risk_branch: critical/high findings with infra hints

    DERIVED ONLY — skládá z existujících truth/derived dat:
      - pvs.signal_quality + accepted + ioc_density
      - branch_value (feed vs public)
      - sprint_trend (poslední sprinty)
      - source_leaderboard (top zdroje)
      - correlation fields (so_what, dominant_cluster, campaign_hints, high_risk_branch)

    Žádný nový business engine. Žádné nové persistence.
    """
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)
    ioc_density = pvs.get("ioc_density", 0.0)
    dedup = pvs.get("reject_breakdown")
    total_rejected = pvs.get("total_rejected", 0)

    # --- Correlation-derived operator intelligence (F150M) ---
    # Correlation fields flow: workflow_orchestrator.correlate_findings() → scorecard
    # Fail-soft: correlation may not be present in older sprints (scorecard built before F150M)
    # F171B §3: explicit "_no_correlation_data" flag from _get_correlation_from_handoff
    so_what = ""
    dominant_cluster: str | None = None
    campaign_hints: list[dict] = []
    high_risk_branch: list[dict] = []
    no_correlation_data = False
    if correlation and correlation.get("_no_correlation_data") is True:
        no_correlation_data = True
        so_what = "korelační data nedostupná — store nedostupný nebo korelace neběžela"
    elif correlation:
        so_what = correlation.get("so_what") or ""
        dominant_cluster = correlation.get("dominant_cluster") or correlation.get("cluster") or None
        # campaign_hints: list of dicts with campaign signals
        raw_hints = correlation.get("campaign_hints") or []
        if isinstance(raw_hints, list):
            campaign_hints = [h for h in raw_hints if isinstance(h, dict)]
        # high_risk_branch: list of critical/high findings with infra hints
        raw_risks = correlation.get("high_risk_branch") or correlation.get("high_risk") or []
        if isinstance(raw_risks, list):
            high_risk_branch = [r for r in raw_risks if isinstance(r, dict)]

    # --- Co sprint pravděpodobně našel ---
    if signal == "high_density":
        finding_summary = f"dobrý sprint: {accepted} kvalitních IOC při density {ioc_density:.2f}"
    elif signal == "medium_density":
        finding_summary = f"smíšený sprint: {accepted} IOC, {total_rejected} rejectů, density {ioc_density:.2f}"
    elif signal == "slow_novelty":
        fpm = pvs.get("findings_per_minute", 0.0)
        finding_summary = f"pomalý ale existující signál: {accepted} IOC při {fpm:.2f} finds/min"
    elif signal == "depleted":
        finding_summary = "sprint nic nepřinesl — vyčerpaný prostor nebo nedostupné zdroje"
    else:
        finding_summary = f"nedefinovaný stav: accepted={accepted}"

    # --- Která branch nesla nejlepší signál ---
    branch_signal = None
    if branch_value:
        verdict = branch_value.get("branch_verdict", "")
        feed_pct = branch_value.get("feed_pct", 0)
        public_pct = branch_value.get("public_pct", 0)
        if verdict == "feed_dominant":
            branch_signal = f"feed branch dominantí ({feed_pct:.0f}% nálezů) — veřejné zdroje slabé"
        elif verdict == "public_dominant":
            branch_signal = f"veřejné zdroje dominantní ({public_pct:.0f}% nálezů) — feed branch podprůměrná"
        elif verdict == "balanced":
            branch_signal = f"vyvážený přínos: feed {feed_pct:.0f}% / public {public_pct:.0f}%"
        else:
            branch_signal = None
    else:
        branch_signal = None

    # --- Co bylo slabé místo ---
    weaknesses: list[str] = []
    if signal == "depleted":
        weaknesses.append("signál vyčerpaný — continuation nepomůže")
    if total_rejected > 0 and dedup:
        low_info = dedup.get("low_information", 0)
        in_mem = dedup.get("in_memory_duplicate", 0)
        persistent = dedup.get("persistent_duplicate", 0)
        if low_info / total_rejected > 0.5:
            weaknesses.append(f"příliš široké dotazy: {low_info} low-info rejectů")
        if in_mem > 0 and in_mem / total_rejected > 0.3:
            weaknesses.append(f"příliš mnoho podobných výsledků: {in_mem} in-memory duplikátů")
        if persistent > 0 and persistent / total_rejected > 0.3:
            weaknesses.append(f"persistentní duplikáty: {persistent}")
    cb_open = pvs.get("cb_open_domains", [])
    if cb_open:
        weaknesses.append(f"circuit breaker open: {len(cb_open)} domains")
    # F171B §1: guard against misleading "clean sprint" when signal is unknown
    # (store unavailable or degraded produces unknown signal with no real evidence)
    if not weaknesses:
        if signal == "unknown" and accepted == 0 and total_rejected == 0:
            weaknesses.append("nedostatek dat — store nedostupný nebo sprint neběžel; skutečný stav neznámý")
        elif signal == "unknown":
            weaknesses.append("signál neznámý — store nedostupný; doporučuji ověřit dostupnost dat")
        else:
            weaknesses.append("žádné výrazné slabiny — sprint byl čistý")

    # --- Co dělat dál: akční next step ---
    next_step = _derive_next_step(signal, branch_value, correlation)

    # --- 2-5 nejzajímavějších follow-up bodů ---
    follow_ups = _derive_follow_ups(signal, branch_value, sprint_trend, source_leaderboard, pvs, correlation)

    # --- Rozhraní branch recommendation ---
    branch_recommendation = None
    if branch_value:
        branch_recommendation = branch_value.get("recommendation")

    # --- F150M: trust_note — operator confidence flag ---
    trust_note = _derive_trust_note(pvs, correlation, so_what, campaign_hints, high_risk_branch)

    # Sprint F150N §1: confidence_band — HIGH/MEDIUM/LOW triage na jeden pohled
    confidence_band = _derive_confidence_band(pvs, correlation, so_what, campaign_hints, high_risk_branch)

    # Sprint F150N §1: what_not_to_do — explicitní anti-patterny (inverze next-step)
    what_not_to_do = _derive_what_not_to_do(signal, branch_value, correlation, pvs)

    # Sprint F150N §2: priority_stack — rankované akce s důvody
    enriched = _enrich_follow_ups(signal, branch_value, sprint_trend, source_leaderboard, pvs, correlation)
    priority_stack = _derive_priority_stack(enriched, signal, correlation)

    # Sprint F150N §2: high_value_findings — top corroborated findings
    high_value_findings = _derive_high_value_findings(correlation, campaign_hints, high_risk_branch, dominant_cluster)

    return {
        "finding_summary": finding_summary,
        "branch_signal": branch_signal,
        "weaknesses": weaknesses,
        "next_step": next_step,
        "follow_ups": follow_ups,
        "branch_recommendation": branch_recommendation,
        "seeds_generated": seeds_count,
        # F150M: correlation-derived fields
        "so_what": so_what,
        "dominant_cluster": dominant_cluster,
        "campaign_hints": campaign_hints,
        "high_risk_branch": high_risk_branch,
        # F150M: trust note
        "trust_note": trust_note,
        # Sprint F150N: new derived operator-facing fields
        "confidence_band": confidence_band,
        "what_not_to_do": what_not_to_do,
        "priority_stack": priority_stack,
        "high_value_findings": high_value_findings,
        # Sprint F150P: finish-layer canonical truth fields
        "runtime_truth": runtime_truth,
        "feed_verdict": feed_verdict,
        "public_verdict": public_verdict,
        "signal_path": signal_path,
        "hypothesis_pack": hypothesis_pack,
        "canonical_run_summary": canonical_run_summary,
        "sprint_verdict": sprint_verdict,
        # Sprint F157: synthesis_outcome_payload
        "synthesis_outcome_payload": synthesis_outcome_payload,
        # F171B §3: data availability flags for degraded truth transparency
        "no_correlation_data": no_correlation_data,
    }


def _derive_next_step(
    signal: str,
    branch_value: dict[str, Any] | None,
    correlation: dict[str, Any] | None = None,
) -> str:
    """Derive nejbližší další krok z signal + branch_value + correlation."""
    # F150M: correlation-driven override — high risk branch takes priority
    if correlation:
        high_risk = correlation.get("high_risk_branch") or correlation.get("high_risk") or []
        if high_risk and len(high_risk) > 0:
            # High-risk findings exist — operational security priority
            return "priority: investigate high-risk branch — critical findings detected"

    # Branch-driven decision
    if branch_value:
        rec = branch_value.get("recommendation", "")
        if rec == "expand_feed_branch":
            return "rozšířit feed branch — má nejlepší signál"
        elif rec == "expand_public_branch":
            return "rozšířit veřejné zdroje — dominantní přínos"
        elif rec == "maintain_both":
            return "držet obě větve — vyvážený přínos"

    # Signal-driven fallback
    if signal == "high_density":
        return "rozšířit úspěšné dotazy o nové zdroje"
    elif signal == "medium_density":
        return "zúžit scope dotazů — příliš mnoho low-info rejectů"
    elif signal == "slow_novelty":
        return "urychlit stávající přístup (rychlejší zdroje)"
    elif signal == "depleted":
        return "úplně nový přístup — nové seed zdroje"
    return "zjistit více o stavu sprintu"


def _derive_follow_ups(
    signal: str,
    branch_value: dict[str, Any] | None,
    sprint_trend: list[dict],
    source_leaderboard: list[dict],
    pvs: dict[str, Any],
    correlation: dict[str, Any] | None = None,
) -> list[str]:
    """
    Sprint F150L: 2-5 nejzajímavějších follow-up bodů.
    Derived z branch_value + sprint_trend + source_leaderboard.
    F150M: correlation-driven follow-ups — campaign hints, high-risk branch indicators.
    """
    follow_ups: list[str] = []

    # F150M: correlation-based follow-ups (fail-soft, correlation may be absent)
    if correlation:
        # Campaign hints — findings suggesting same campaign
        campaign = correlation.get("campaign_hints") or []
        if campaign and isinstance(campaign, list) and len(campaign) > 0:
            follow_ups.append(f"detekována možná kampaň: {len(campaign)} propojených nálezů")
        # Dominant cluster follow-up
        cluster = correlation.get("dominant_cluster") or correlation.get("cluster") or ""
        if cluster and isinstance(cluster, str) and len(cluster) > 0:
            follow_ups.append(f"dominantní téma: {cluster} — rozšířit investigaci")

    # Branch-based follow-ups
    if branch_value:
        feed_f = branch_value.get("feed_findings", 0)
        public_f = branch_value.get("public_findings", 0)
        verdict = branch_value.get("branch_verdict", "")
        if verdict == "feed_dominant" and public_f == 0:
            follow_ups.append("zkusit veřejné zdroje — feed branch jede sama")
        elif verdict == "public_dominant" and feed_f == 0:
            follow_ups.append("zkusit feed zdroje — veřejné jede sama")
        elif verdict == "balanced":
            follow_ups.append("obě větve fungují — prostor pro paralelizaci")

    # Sprint trend-based follow-ups
    if sprint_trend:
        last = sprint_trend[0] if sprint_trend else None
        if last and last.get("sprint_id"):
            follow_ups.append(f"minulý sprint: {last.get('sprint_id')} — {last.get('new_findings', 0)} findings")

    # Source leaderboard follow-ups
    if source_leaderboard:
        top_source = source_leaderboard[0] if source_leaderboard else None
        if top_source:
            src = top_source.get("source_type", "?")
            hits = top_source.get("total_findings", 0)
            follow_ups.append(f"nejproduktivnější zdroj: {src} ({hits} findings)")

    # Signal-based follow-ups
    if signal == "depleted":
        follow_ups.append("změnit seed zdroje — aktuální vyčerpané")
        follow_ups.append("zkusit zcela jiný typ dotazu")
    elif signal == "low_density":
        follow_ups.append("zvýšit frekvenci dotazů — nízká density")
    elif signal == "slow_novelty":
        follow_ups.append("zvážit rychlejší zdroje místo kvalitnějších")

    # IOC density based
    ioc_density = pvs.get("ioc_density", 0.0)
    if ioc_density < 0.1 and signal != "depleted":
        follow_ups.append("IOC density velmi nízká — zvážit úpravu dedup prahů")

    return follow_ups[:5]  # Hard cap: max 5 follow-ups


def _derive_trust_note(
    pvs: dict[str, Any],
    correlation: dict[str, Any] | None,
    so_what: str,
    campaign_hints: list[dict],
    high_risk_branch: list[dict],
) -> str:
    """
    Sprint F150M §2: Operator-facing trust/confidence note.
    Derived from correlation truth + pvs signal quality.

    Tells operator: how confident should I be in this sprint's output?
    Fail-soft: returns empty string when correlation unavailable (older sprints).
    """
    if not correlation:
        return ""  # No correlation data — skip, don't fabricate confidence

    # High-risk branch presence = high stakes, operator needs explicit warning
    if high_risk_branch and len(high_risk_branch) > 0:
        risk_count = len(high_risk_branch)
        return (
            f"⚠️ {risk_count} kritických nálezů detekováno — "
            f"vysoká priorita pro další ověření"
        )

    # Campaign hints — correlated findings increase confidence
    if campaign_hints and len(campaign_hints) >= 2:
        return (
            f"kampanijní signál: {len(campaign_hints)} propojených nálezů — "
            f"zvýšená spolehlivost korelace"
        )

    # so_what present = correlation engine produced a verdict
    if so_what and len(so_what) > 5:
        return f"korelace: {so_what}"

    # Signal quality from pvs — baseline confidence
    signal = pvs.get("_signal_quality_classification", "unknown")
    if signal == "high_density":
        return "vysoká spolehlivost — hustý signál, málo šumu"
    elif signal == "depleted":
        return "nízká spolehlivost — vyčerpaný prostor, minimální data"

    return ""


def _derive_confidence_band(
    pvs: dict[str, Any],
    correlation: dict[str, Any] | None,
    so_what: str,
    campaign_hints: list[dict],
    high_risk_branch: list[dict],
) -> str:
    """
    Sprint F150N §1: confidence_band — HIGH/MEDIUM/LOW triage na jeden pohled.

    Operator vidí na první pohled, jak důvěřovat sprint outputu.
    Derived z pvs.signal_quality + correlation presence + corroboration depth.

    Fail-soft: returns "MEDIUM" as safe default when correlation unavailable.
    """
    # High-risk branch presence = HIGH (operational stakes are real)
    if high_risk_branch and len(high_risk_branch) > 0:
        return "HIGH"

    # Campaign hints with 2+ corroborations = HIGH
    if campaign_hints and len(campaign_hints) >= 2:
        return "HIGH"

    # so_what present + high_density signal = HIGH
    if so_what and len(so_what) > 5 and pvs.get("_signal_quality_classification") == "high_density":
        return "HIGH"

    # so_what present alone = MEDIUM
    if so_what and len(so_what) > 5:
        return "MEDIUM"

    # High density signal = MEDIUM (good data, no correlation)
    if pvs.get("_signal_quality_classification") == "high_density":
        return "MEDIUM"

    # Depleted = LOW (minimální data)
    if pvs.get("_signal_quality_classification") == "depleted":
        return "LOW"

    # F171B §4: unknown signal = LOW confidence — store unavailable or sprint didn't run
    # Previously fell through to MEDIUM default — misleading when we have no real data
    if pvs.get("_signal_quality_classification") == "unknown":
        return "LOW"

    # Medium/other = MEDIUM
    return "MEDIUM"


def _derive_what_not_to_do(
    signal: str,
    branch_value: dict[str, Any] | None,
    correlation: dict[str, Any] | None,
    pvs: dict[str, Any],
) -> list[str]:
    """
    Sprint F150N §1: what_not_to_do — explicitní anti-patterny.

    Inverze _derive_next_step: co rozhodně NEDĚLAT příště.
    Operator quickly sees what to avoid.

    Fail-soft: returns empty list when nothing specific to avoid.
    """
    anti: list[str] = []

    # Depleted signal — don't continue same approach
    if signal == "depleted":
        anti.append("nepokračovat ve stejných dotazech — prostor je vyčerpaný")
        dedup = pvs.get("reject_breakdown")
        if dedup and dedup.get("persistent_duplicate", 0) > 0:
            anti.append("nezkoušet stejné zdroje znovu — dedup efektivní, zdroje jsou dry")

    # Feed or public zero — don't ignore the silent branch
    if branch_value:
        feed_f = branch_value.get("feed_findings", 0)
        public_f = branch_value.get("public_findings", 0)
        if feed_f == 0 and public_f > 0:
            anti.append("neignorovat feed branch — je tichá, může nést jiný typ signálu")
        elif public_f == 0 and feed_f > 0:
            anti.append("neignorovat veřejné zdroje — jsou tiché, ale mohou mít jinou tematiku")

    # Low-info rejections dominant — don't broaden scope
    dedup = pvs.get("reject_breakdown")
    total_rejected = pvs.get("total_rejected", 0)
    if total_rejected > 0 and dedup:
        low_info = dedup.get("low_information", 0)
        if low_info / total_rejected > 0.5:
            anti.append("nezůžovat scope — problém je šíře, ne úzkost dotazů")

    # Circuit breaker open domains
    cb_open = pvs.get("cb_open_domains", [])
    if cb_open and len(cb_open) >= 3:
        anti.append(f"nezkoušet {len(cb_open)} domains s otevřeným circuit breaker — počkat na backoff")

    # High-risk branch present — don't deprioritize investigation
    if correlation:
        high_risk = correlation.get("high_risk_branch") or correlation.get("high_risk") or []
        if high_risk and len(high_risk) > 0:
            anti.append("nepřehližet high-risk branch — kritické nálezy vyžadují akci")

    # Slow novelty — don't slow down further
    if signal == "slow_novelty":
        anti.append("nezpomalhodit — už je to pomalé, fokus na rychlejší zdroje")

    return anti[:5]  # Hard cap: max 5 anti-patterns


def _enrich_follow_ups(
    signal: str,
    branch_value: dict[str, Any] | None,
    sprint_trend: list[dict],
    source_leaderboard: list[dict],
    pvs: dict[str, Any],
    correlation: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Sprint F150N §2: Enrich follow-ups with priority metadata.

    Converts flat string list from _derive_follow_ups into ranked dicts
    with: action, priority_score (0-1), rationale.

    All data from existing seams — no new store reads.
    """
    raw = _derive_follow_ups(signal, branch_value, sprint_trend, source_leaderboard, pvs, correlation)

    # Correlation-driven priority boosts
    corr_priority_boost = 0.0
    if correlation:
        high_risk = correlation.get("high_risk_branch") or correlation.get("high_risk") or []
        if high_risk and len(high_risk) > 0:
            corr_priority_boost = 0.15  # High-risk adds urgency

    # Signal-based baseline priority
    signal_priority = {
        "high_density": 0.60,
        "medium_density": 0.55,
        "slow_novelty": 0.50,
        "depleted": 0.70,  # Depleted = high urgency to change
        "unknown": 0.40,
    }.get(signal, 0.50)

    enriched: list[dict[str, Any]] = []
    for i, item in enumerate(raw):
        if isinstance(item, str):
            # Correlation-campaign items get boost
            boost = corr_priority_boost if "kampaň" in item or "dominantní téma" in item else 0.0
            enriched.append({
                "action": item,
                "priority_score": min(1.0, signal_priority + boost + (0.05 * (len(raw) - i) / len(raw))),
                "rationale": "correlation_derived" if boost > 0 else "signal_derived",
            })
        elif isinstance(item, dict):
            enriched.append(item)

    # Sort by priority_score descending
    enriched.sort(key=lambda x: x.get("priority_score", 0.5), reverse=True)
    return enriched


def _derive_priority_stack(
    enriched: list[dict[str, Any]],
    signal: str,
    correlation: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """
    Sprint F150N §2: priority_stack — ranked list of actions with reasons.

    Takes enriched follow-ups and returns top-5 as structured stack:
    [{"rank": 1, "action": "...", "why": "..."}]

    Uses correlation truth for ranking when available.
    """
    stack: list[dict[str, Any]] = []

    # High-risk branch always goes first if present
    if correlation:
        high_risk = correlation.get("high_risk_branch") or correlation.get("high_risk") or []
        if high_risk and len(high_risk) > 0:
            stack.append({
                "rank": len(stack) + 1,
                "action": "investigate high-risk branch",
                "why": f"{len(high_risk)} kritických nálezů — operační priorita",
            })

    # Correlation campaign hints
    if correlation:
        campaign = correlation.get("campaign_hints") or []
        if campaign and isinstance(campaign, list) and len(campaign) > 0:
            stack.append({
                "rank": len(stack) + 1,
                "action": f"expand campaign investigation ({len(campaign)} correlated findings)",
                "why": "propojené nálezy signalizují aktivní kampaň",
            })

    # Signal-driven actions
    for item in enriched[:5 - len(stack)]:
        action = item.get("action", "") if isinstance(item, dict) else str(item)
        if not action or any(action == s["action"] for s in stack):
            continue
        rationale = item.get("rationale", "signal_derived") if isinstance(item, dict) else "signal_derived"
        stack.append({
            "rank": len(stack) + 1,
            "action": action,
            "why": f"signal={signal} / {rationale}",
        })

    return stack[:5]  # Hard cap: max 5 stack entries


def _derive_high_value_findings(
    correlation: dict[str, Any] | None,
    campaign_hints: list[dict],
    high_risk_branch: list[dict],
    dominant_cluster: str | None,
) -> list[dict[str, Any]]:
    """
    Sprint F150N §2: high_value_findings — top corroborated findings.

    Sources:
      - correlation.campaign_hints (list of corroborated findings)
      - correlation.high_risk_branch (critical/high findings)
      - dominant_cluster theme

    Returns top-5 findings with their corroboration signal.
    Fail-soft: returns empty list when no correlation data available.
    """
    findings: list[dict[str, Any]] = []

    # High-risk findings — highest confidence (operationally verified)
    for r in (high_risk_branch or [])[:3]:
        if isinstance(r, dict):
            finding_val = r.get("value") or r.get("ioc_value") or r.get("indicator") or "?"
            finding_type = r.get("ioc_type") or r.get("type") or "unknown"
            findings.append({
                "value": str(finding_val)[:100],
                "type": str(finding_type),
                "confidence": "HIGH",
                "signal": "high_risk_branch",
                "rationale": r.get("rationale") or r.get("reason") or "critical severity + infra hints",
            })

    # Campaign hints — corroborated findings
    for h in (campaign_hints or [])[:3]:
        if isinstance(h, dict):
            findings.append({
                "value": str(h.get("value") or h.get("indicator") or "?")[:100],
                "type": str(h.get("ioc_type") or h.get("type") or "unknown"),
                "confidence": "MEDIUM-HIGH",
                "signal": "campaign_hint",
                "rationale": h.get("rationale") or h.get("campaign_id") or "corroborated by multiple sources",
            })

    # Dominant cluster — theme summary
    if dominant_cluster and isinstance(dominant_cluster, str) and len(dominant_cluster) > 2:
        findings.append({
            "value": dominant_cluster[:80],
            "type": "theme",
            "confidence": "MEDIUM",
            "signal": "dominant_cluster",
            "rationale": f"dominantní téma: {dominant_cluster}",
        })

    return findings[:5]  # Hard cap: max 5 findings


def _build_sprint_summary(pvs: dict[str, Any], seeds_count: int) -> dict[str, Any]:
    """
    Sprint F150K: Human-readable sprint_summary block.

    DERIVED ONLY — reads from pvs, no new data sources.
    NO write-back. NO new persistence.

    Structure:
      - what_found: co sprint našel (high-level)
      - what_didnt_work: co nevyšlo (reject breakdown, depleted signal)
      - what_to_do_next: co dělat dál (top priority action)
      - priority_reason: proč tohle je priorita (derived from signal)

    Args:
        pvs: product_value_summary
        seeds_count: počet vygenerovaných seeds (pro kontext)

    Returns:
        sprint_summary dict
    """
    signal = pvs.get("_signal_quality_classification", "unknown")
    accepted = pvs.get("accepted", 0)
    total_rejected = pvs.get("total_rejected", 0)
    dedup_effective = pvs.get("dedup_effective", False)
    cb_open = pvs.get("cb_open_domains", [])
    ioc_density = pvs.get("ioc_density", 0.0)
    findings_per_minute = pvs.get("findings_per_minute", 0.0)
    dedup = pvs.get("reject_breakdown")

    # --- what_found ---
    if signal == "high_density":
        what_found = f"dobrý sprint: {accepted} accept IOCs při density {ioc_density:.2f}"
    elif signal == "medium_density":
        what_found = f"smíšený sprint: {accepted} accept IOCs, ioc_density={ioc_density:.2f}"
    elif signal == "slow_novelty":
        what_found = f"pomalý aleexistující signál: {accepted} finds, {findings_per_minute:.2f} fpm"
    elif signal == "depleted":
        what_found = "sprint nic nepřinesl — vyčerpaný prostor nebo špatné zdroje"
    else:
        # F171B §2: accepted>0 with unknown signal = degraded truth, not "undefined"
        # Store unavailable or scorecard built before F150P — don't claim clarity we don't have
        if accepted > 0:
            what_found = f"částečný sprint: {accepted} IOC nalezeno, ale signál neznámý (store nedostupný)"
        else:
            what_found = "nedostatek dat — sprint neproběhl nebo store nedostupný"

    # --- what_didnt_work ---
    didnt_work: list[str] = []
    if total_rejected > 0 and dedup:
        low_info = dedup.get("low_information", 0)
        in_mem = dedup.get("in_memory_duplicate", 0)
        persistent = dedup.get("persistent_duplicate", 0)
        if low_info > 0:
            ratio = low_info / total_rejected
            didnt_work.append(f"low_info rejects: {ratio:.0%} (příliš široké dotazy)")
        if in_mem > 0:
            didnt_work.append(f"in-memory duplikáty: {in_mem} (příliš mnoho podobných výsledků)")
        if persistent > 0 and not dedup_effective:
            didnt_work.append("persistent duplikáty ale dedup nepomohl")
    if signal == "depleted":
        didnt_work.append("signál vyčerpaný — žádné nové IOCs")
    if cb_open:
        didnt_work.append(f"circuit breaker open: {len(cb_open)} domains")

    if not didnt_work:
        didnt_work.append("nic podstatného — sprint byl čistý")

    # --- what_to_do_next (top priority) ---
    if signal == "high_density":
        next_action = "rozšířit úspěšné dotazy o nové zdroje"
        priority_reason = f"{accepted} kvalitních nálezů = prostor expandovat"
    elif signal == "medium_density":
        next_action = "zúžit scope dotazů"
        priority_reason = "příliš mnoho low-info rejectů"
    elif signal == "slow_novelty":
        next_action = "urychlit stávající přístup (rychlejší zdroje)"
        priority_reason = "signál existuje ale je pomalý"
    elif signal == "depleted":
        next_action = "úplně nový přístup — nové seed zdroje"
        priority_reason = "vyčerpaný prostor — continuation nepomůže"
    else:
        next_action = "zjistit více o stavu sprintu"
        priority_reason = "nedefinovaný stav"

    return {
        "what_found": what_found,
        "what_didnt_work": didnt_work,
        "what_to_do_next": next_action,
        "priority_reason": priority_reason,
        "seeds_generated": seeds_count,
        "signal_derived": signal,
    }


def _make_serializable(obj: Any) -> Any:
    """Rekurzivně převede objekt na JSON-serializovatelný dict."""
    if isinstance(obj, (str, int, float, bool, type(None))):
        return obj
    if isinstance(obj, dict):
        return {k: _make_serializable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_make_serializable(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return _make_serializable(obj.__dict__)
    return str(obj)
