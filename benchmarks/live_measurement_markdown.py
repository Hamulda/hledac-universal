"""
F227B LIVE MEASUREMENT MARKDOWN RENDERER

Extracted from benchmarks/live_sprint_measurement.py _render_md().
No runtime imports, no live execution, no network, no MLX.

Public API:
    render_live_measurement_markdown(result: LiveMeasurementResult) -> str

ABSOLUTE REPO ROOT: /Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal
"""

from __future__ import annotations

import json


def render_live_measurement_markdown(result) -> str:
    """
    Render LiveMeasurementResult as markdown.
    Delegates to _render_md() wrapper for backward compatibility.
    """
    return _render_md(result)


def _render_md(result) -> str:
    """Render measurement result as markdown."""
    verdict_badge = result.run_quality_verdict or "UNKNOWN"
    lines = [
        f"# Live Sprint Measurement: {result.measurement_id}",
        "",
        f"**Mode:** {result.mode.value}",
        f"**Status:** {result.status.value}",
        f"**Quality Verdict:** `{verdict_badge}`",
        f"**Profile:** {result.profile}",
        "",
        "## Timing",
        "",
        f"- Duration (planned): {result.planned_duration_s}s",
        f"- Actual duration: {result.actual_duration_s}s" if result.actual_duration_s else "- Actual duration: N/A",
        f"- Start: {result.start_time_iso}",
        f"- End: {result.end_time_iso}",
        "",
        "## Configuration",
        "",
        f"- Aggressive mode: {result.aggressive_mode}",
        f"- Deep probe: {result.deep_probe}",
        "",
        "## Memory Gate",
        "",
        f"- Pre-sprint state: {result.memory_state_pre}",
        f"- Post-sprint state: {result.memory_state_post}",
        f"- Swap warning: {result.swap_warning}",
        f"- Hardware constrained: {result.hardware_constrained}",
        f"- Recommended next profile: {result.recommended_next_profile or 'N/A'}",
    ]

    if result.recommended_operator_action:
        lines.extend([
            f"- Recommended operator action: {result.recommended_operator_action}",
        ])

    if result.query and result.query != "(preflight-only)":
        lines.extend([
            "",
            "## Query",
            "",
            f"`{result.query}`",
        ])

    lines.extend([
        "",
        "## UMA Memory",
        "",
        f"- Pre-sprint: {result.uma_pre_used_gib} GiB used, {result.uma_pre_swap_gib} GiB swap, state={result.uma_pre_state}",
        f"- Post-sprint: {result.uma_post_used_gib} GiB used, {result.uma_post_swap_gib} GiB swap, state={result.uma_post_state}",
    ])

    # result.mode is RunMode enum — comparing .value against string literal "live"
    if result.mode.value == "live":
        lines.extend([
            "",
            "## Sprint Results",
            "",
            f"- Findings count: {result.findings_count}",
            f"- Cycles completed: {result.cycles_completed}",
            f"- Cycles started: {result.cycles_started}",
            f"- Accepted findings: {result.accepted_findings}",
            f"- Runtime truth: {json.dumps(result.runtime_truth, default=str) if isinstance(result.runtime_truth, dict) else result.runtime_truth}",
            f"- Timing truth: {json.dumps(result.timing_truth, default=str) if isinstance(result.timing_truth, dict) else result.timing_truth}",
            f"- Checkpoint zero: {result.checkpoint_zero_category}",
            f"- Early exit class: {result.early_exit_class}",
            f"- Primary signal source: {result.primary_signal_source}",
            f"- Report: {result.report_json_path}",
        ])
    else:
        lines.extend([
            "",
            "## Sprint Results (not executed in this mode)",
        ])

    # F2130: Runtime Authority section
    ra_path = result.runtime_authority_path or "UNKNOWN"
    ra_module = result.runtime_authority_module or "N/A"
    ra_func = result.runtime_authority_function or "N/A"
    ra_canonical = result.runtime_authority_is_canonical
    if ra_path == "dry_run_no_runtime":
        ra_verdict = "DRY_RUN"
    elif ra_path == "canonical_core_run_sprint" and ra_canonical is not False:
        ra_verdict = "CONFIRMED"
    else:
        ra_verdict = "NONCANONICAL"
    lines.extend([
        "",
        "## Runtime Authority",
        "",
        f"| Field | Value |",
        f"| --- | --- |",
        f"| Runtime authority path | `{ra_path}` |",
        f"| Module | `{ra_module}` |",
        f"| Function | `{ra_func}` |",
        f"| Is canonical | {ra_canonical} |",
        f"| Verdict | **{ra_verdict}** |",
    ])
    if result.runtime_authority_evidence is not None:
        lines.extend([
            "",
            "```json",
            json.dumps(result.runtime_authority_evidence, indent=2, default=str),
            "```",
        ])

    lines.extend([
        "",
        "## Readiness Artifacts",
        "",
        f"- stabilization_seal.json: {'PRESENT' if result.stabilization_seal_present else 'MISSING'}",
        f"- hermetic_regression_manifest.json: {'PRESENT' if result.hermetic_regression_manifest_present else 'MISSING'}",
        f"- transport_authority_status_refreshed.json: {'PRESENT' if result.transport_authority_status_present else 'MISSING'}",
        f"- mlx_wired_limit_seal.json: {'PRESENT' if result.mlx_wired_limit_seal_present else 'MISSING'}",
        "",
        "## Profile Truthfulness",
        "",
        f"- Verdict: **{result.profile_verdict or 'UNKNOWN'}**",
        f"- active_runtime_expected: {result.active_runtime_expected}",
        f"- expected_windup_lead_s: {result.expected_windup_lead_s}s",
        f"- expected_active_window_s: {result.expected_active_window_s}s",
    ])

    if result.error:
        lines.extend(["", "## Error", "", f"```\n{result.error}\n```"])

    # Live KPI section (F207J)
    if result.live_kpi is not None:
        kpi = result.live_kpi
        lines.extend([
            "",
            "## Live KPI",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Total findings | {kpi.get('total_findings', 'N/A')} |",
            f"| Accepted findings | {kpi.get('accepted_findings', 'N/A')} |",
            f"| Cycles completed | {kpi.get('cycles_completed', 'N/A')} |",
            f"| Findings/min | {kpi.get('findings_per_min', 'N/A')} |",
            f"| Primary signal | {kpi.get('primary_signal_source', 'N/A')} |",
            f"| Feed dominance | {kpi.get('feed_dominance_score', 'N/A')} |",
            f"| Branch accepted counts | {json.dumps(kpi.get('branch_accepted_counts', {}))} |",
            f"| Lane execution counts | {json.dumps(kpi.get('lane_execution_counts', {}))} |",
            f"| Nonfeed attempted | {kpi.get('nonfeed_attempted_families', [])} |",
            f"| Nonfeed accepted | {kpi.get('nonfeed_accepted_findings', 'N/A')} |",
            f"| Public attempted | {kpi.get('public_fetch_attempted', 'N/A')} |",
            f"| Public accepted (pages) | {kpi.get('public_acceptance_attempted', 0)} |",
            f"| Public accepted (findings) | {kpi.get('public_acceptance_accepted', 0)} |",
            f"| Public rejected (pages) | {kpi.get('public_acceptance_rejected', 0)} |",
            f"| Top reject reason | {kpi.get('top_public_reject_reason', 'N/A')} |",
            f"| Quality verdict | {kpi.get('run_quality_verdict', 'N/A')} |",
            f"| Hardware constrained | {kpi.get('hardware_constrained', 'N/A')} |",
            f"| **Next action** | **{kpi.get('next_action', 'unknown')}** |",
        ])

        # F214C: Research Quality Score section
        _rq = kpi.get('research_quality')
        if _rq:
            _grade = _rq.get('grade', 'N/A')
            _score = _rq.get('total_quality_score', 0.0)
            _comp = _rq.get('components', {})
            _flags = _rq.get('diagnostic_flags', {})
            _comp_flag = _rq.get('research_quality_comparable', True)
            lines.extend([
                "",
                "## Research Quality Score",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Total score | {_score:.1f}/100 |",
                f"| Grade | `{_grade}` |",
                f"| Comparable | {_comp_flag} |",
                f"| Wallclock exceeded | {_flags.get('wallclock_exceeded', 'N/A')} |",
                f"| Swap GiB | {_flags.get('swap_gib', 'N/A')} |",
                f"| Swap warning | {_flags.get('swap_warning', 'N/A')} |",
                f"| Hardware constrained | {_flags.get('hardware_constrained', 'N/A')} |",
                "",
                "| Component | Score |",
                "| --- | --- |",
                f"| Findings volume | {_comp.get('findings_volume_score', 0.0):.1f} |",
                f"| Source diversity | {_comp.get('source_diversity_score', 0.0):.1f} |",
                f"| Nonfeed evidence | {_comp.get('nonfeed_evidence_score', 0.0):.1f} |",
                f"| CT evidence | {_comp.get('ct_evidence_score', 0.0):.1f} |",
                f"| Public evidence | {_comp.get('public_evidence_score', 0.0):.1f} |",
                f"| Passive evidence | {_comp.get('passive_evidence_score', 0.0):.1f} |",
                f"| Feed dominance penalty | -{_comp.get('feed_dominance_penalty', 0.0):.1f} |",
                f"| Wallclock penalty | -{_comp.get('wallclock_penalty', 0.0):.1f} |",
                f"| Memory taint penalty | -{_comp.get('memory_taint_penalty', 0.0):.1f} |",
            ])

        # Non-feed Starvation section (F207M)
        if kpi.get('nonfeed_eligible_families') or kpi.get('windup_lead_observed_s') is not None:
            suspected = kpi.get('nonfeed_starvation_suspected', False)
            starvation_rows = [
                "",
                "## Non-feed Starvation",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Nonfeed starvation suspected | {suspected} |",
                f"| Windup lead requested | {kpi.get('windup_lead_requested_s', 'N/A')}s |",
                f"| Windup lead observed | {kpi.get('windup_lead_observed_s', 'N/A')}s |",
                f"| Active window budget | {kpi.get('active_window_budget_s', 'N/A')}s |",
                f"| Nonfeed eligible families | {kpi.get('nonfeed_eligible_families', [])} |",
            ]
            if suspected:
                starvation_rows.insert(7, f"| Starvation reason | {kpi.get('nonfeed_starvation_reason', 'N/A')} |")
            lines.extend(starvation_rows)

        # F211B: Lane Execution Truth section
        _lec = kpi.get('lane_execution_counts', {})
        if _lec:
            lane_truth_rows = [
                "",
                "## Lane Execution Truth",
                "",
                "| family | attempted | terminal_state | raw_count | accepted_count | error | skipped |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for _fam, _data in sorted(_lec.items()):
                lane_truth_rows.append(
                    f"| {_fam} | {int(_data.get('attempted', False))} "
                    f"| {_data.get('terminal_state', 'N/A')} "
                    f"| {_data.get('raw_count', 0)} "
                    f"| {_data.get('accepted_count', 0)} "
                    f"| {(_data.get('error') or 'N/A')} "
                    f"| {int(_data.get('skipped', False))} |"
                )
            lines.extend(lane_truth_rows)

            _sfo_disp = kpi.get('source_family_outcomes_display', [])
            _ct_failed = any(
                _d.get('terminal_state') in ('ERROR', 'SKIPPED') and _f == 'ct'
                for _f, _d in _lec.items()
            )
            _ct_attempted = any(
                _d.get('attempted') and _f == 'ct'
                for _f, _d in _lec.items()
            )
            _ct_in_sfo = any(_e.get('family') == 'ct' for _e in _sfo_disp)
            if _ct_failed and _ct_attempted:
                lines.append(f"| **Next action** | **fix_final_terminality_reconciliation** |")
            elif _ct_in_sfo and not _ct_attempted:
                lines.append(f"| **Next action** | **fix_ct_prelude_execution** |")

        # Pre-windup Barrier section (F207Q)
        barrier_checked = kpi.get('prewindup_barrier_checked', False)
        if barrier_checked or kpi.get('prewindup_required_lanes') or kpi.get('prewindup_skipped_lanes'):
            barrier_rows = [
                "",
                "## Pre-windup Barrier",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Barrier checked | {barrier_checked} |",
                f"| Barrier satisfied | {kpi.get('prewindup_barrier_satisfied', 'N/A')} |",
                f"| Required lanes | {kpi.get('prewindup_required_lanes', [])} |",
                f"| Attempted lanes | {kpi.get('prewindup_attempted_lanes', [])} |",
            ]
            skipped = kpi.get('prewindup_skipped_lanes', {})
            if skipped:
                barrier_rows.append(f"| Skipped lanes | {json.dumps(skipped)} |")
            windup_delayed = kpi.get('windup_delayed_for_nonfeed')
            if windup_delayed is not None:
                barrier_rows.append(f"| Windup delayed for nonfeed | {windup_delayed} |")
            gap_resolved = kpi.get('nonfeed_scheduler_gap_resolved')
            if gap_resolved is not None:
                barrier_rows.append(f"| Nonfeed scheduler gap resolved | {gap_resolved} |")
            lines.extend(barrier_rows)

    # Acquisition Prelude section (F209B)
    kpi = result.live_kpi
    if kpi is not None:
        apl_checked = kpi.get('acquisition_prelude_checked')
        apl_ran = kpi.get('acquisition_prelude_ran')
        apl_reason = kpi.get('acquisition_prelude_reason', '')
        apl_required = kpi.get('acquisition_prelude_required_lanes', [])
        apl_terminal = kpi.get('acquisition_prelude_terminal_lanes', [])
        apl_missing = kpi.get('acquisition_prelude_missing_lanes', [])
        apl_skipped = kpi.get('acquisition_prelude_skipped_lanes', {})
        apl_errors = kpi.get('acquisition_prelude_errors', {})
        apl_duration = kpi.get('acquisition_prelude_duration_s')
        if apl_checked is not None or apl_ran is not None:
            prelude_rows = [
                "",
                "## Acquisition Prelude",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Prelude checked | {apl_checked if apl_checked is not None else 'N/A'} |",
                f"| Prelude ran | {apl_ran if apl_ran is not None else 'N/A'} |",
                f"| Reason | {apl_reason or 'N/A'} |",
                f"| Required lanes | {apl_required or []} |",
                f"| Terminal lanes | {apl_terminal or []} |",
                f"| Missing lanes | {apl_missing or []} |",
            ]
            if apl_duration is not None:
                prelude_rows.append(f"| Duration (s) | {apl_duration} |")
            if apl_skipped:
                prelude_rows.append(f"| Skipped lanes | {json.dumps(apl_skipped)} |")
            if apl_errors:
                prelude_rows.append(f"| Errors | {json.dumps(apl_errors)} |")
            lines.extend(prelude_rows)

    # Windup Guard Observation section (F207S)
    kpi = result.live_kpi
    if kpi is not None:
        wg_call = kpi.get('windup_guard_call_count', 0)
        wg_supplied = kpi.get('windup_guard_callback_supplied_count', 0)
        wg_exec = kpi.get('windup_guard_callback_executed_count', 0)
        wg_reason = kpi.get('windup_guard_last_reason', '')
        wg_phase = kpi.get('windup_guard_last_phase', '')
        wg_allowed = kpi.get('windup_guard_last_allowed')
        if wg_call > 0 or wg_supplied > 0 or wg_exec > 0:
            lines.extend([
                "",
                "## Windup Guard Observation",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Call count | {wg_call} |",
                f"| Callback supplied | {wg_supplied} |",
                f"| Callback executed | {wg_exec} |",
                f"| Last reason | {wg_reason or 'N/A'} |",
                f"| Last phase | {wg_phase or 'N/A'} |",
                f"| Last allowed | {wg_allowed} |",
            ])
            if wg_call == 0:
                lines.append(f"| **Next action** | **fix_scheduler_windup_callsite** |")
            elif wg_supplied == 0:
                lines.append(f"| **Next action** | **fix_callback_wiring** |")
            elif wg_exec == 0:
                lines.append(f"| **Next action** | **fix_callback_execution** |")
            elif wg_allowed is True:
                lines.append(f"| **Next action** | **no_windup_starvation** |")
            elif wg_allowed is False:
                lines.append(f"| **Next action** | **fix_barrier_semantics** |")

    # Scheduler Return Guard section (F207T)
    kpi = result.live_kpi
    if kpi is not None:
        rg_checked = kpi.get('return_guard_checked', False)
        rg_satisfied = kpi.get('return_guard_satisfied', False)
        rg_block = kpi.get('return_guard_block_reason', '')
        rg_required = kpi.get('return_guard_required_lanes', [])
        rg_attempted = kpi.get('return_guard_attempted_lanes', [])
        rg_skipped = kpi.get('return_guard_skipped_lanes', {})
        rg_errors = kpi.get('return_guard_errors', [])
        if rg_checked or rg_required or rg_block or rg_attempted or rg_skipped or rg_errors:
            lines.extend([
                "",
                "## Scheduler Return Guard",
                "",
                f"| Metric | Value |",
                f"| --- | --- |",
                f"| Return guard checked | {rg_checked} |",
                f"| Return guard satisfied | {rg_satisfied} |",
                f"| Block reason | {rg_block or 'N/A'} |",
                f"| Required lanes | {rg_required or 'N/A'} |",
                f"| Attempted lanes | {rg_attempted or 'N/A'} |",
            ])
            if rg_skipped:
                lines.append(f"| Skipped lanes | {json.dumps(rg_skipped)} |")
            if rg_errors:
                lines.append(f"| Errors | {json.dumps(rg_errors)} |")
            if not rg_checked:
                lines.append(f"| **Next action** | **fix_scheduler_return_guard_not_called** |")
            elif rg_checked and not rg_satisfied:
                lines.append(f"| **Next action** | **fix_return_guard_terminal_state** |")

    # Scheduler Exit Path section (F207V-B)
    kpi = result.live_kpi
    if kpi is not None:
        se_path = kpi.get('scheduler_exit_path', '')
        se_reason = kpi.get('scheduler_exit_reason', '')
        se_phase = kpi.get('scheduler_exit_phase', '')
        se_cycle = kpi.get('scheduler_exit_cycle', '')
        se_elapsed = kpi.get('scheduler_exit_elapsed_s', '')
        se_guard_checked = kpi.get('scheduler_exit_guard_checked', '')
        se_guard_required = kpi.get('scheduler_exit_guard_required', '')
        se_guard_satisfied = kpi.get('scheduler_exit_guard_satisfied', '')
        lines.extend([
            "",
            "## Scheduler Exit Path",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Exit path | {se_path or 'N/A'} |",
            f"| Exit reason | {se_reason or 'N/A'} |",
            f"| Exit phase | {se_phase or 'N/A'} |",
            f"| Exit cycle | {se_cycle or 'N/A'} |",
            f"| Elapsed (s) | {se_elapsed or 'N/A'} |",
            f"| Guard checked | {se_guard_checked or 'N/A'} |",
            f"| Guard required | {se_guard_required or 'N/A'} |",
            f"| Guard satisfied | {se_guard_satisfied or 'N/A'} |",
        ])
        if not se_path:
            lines.append(f"| **Next action** | **add_scheduler_exit_tracer** |")
        elif se_path and se_path != "run_complete" and not se_guard_checked:
            lines.append(f"| **Next action** | **patch_scheduler_exit_path:{se_path}** |")

    # PUBLIC Acceptance section (F207K)
    kpi = result.live_kpi
    if kpi is not None and kpi.get('public_fetch_attempted'):
        reject_reasons = kpi.get('public_acceptance_reject_reasons', {})
        rejected_urls = kpi.get('public_rejected_url_sample', ())
        url_sample_display = list(rejected_urls[:3])
        top_reason = kpi.get('top_public_reject_reason', 'N/A')

        lines.extend([
            "",
            "## PUBLIC Acceptance",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Pages attempted | {kpi.get('public_acceptance_attempted', 0)} |",
            f"| Pages accepted | {kpi.get('public_acceptance_accepted', 0)} |",
            f"| Pages rejected | {kpi.get('public_acceptance_rejected', 0)} |",
            f"| Top reject reason | {top_reason} |",
        ])
        if reject_reasons:
            lines.append("")
            lines.append("**Rejection reasons:**")
            for reason, count in sorted(reject_reasons.items(), key=lambda x: -x[1]):
                lines.append(f"- {reason}: {count}")
        if url_sample_display:
            lines.append("")
            lines.append("**Rejected URL sample (max 3):**")
            for url in url_sample_display:
                lines.append(f"- {url}")

    # Feed Balance section (F207K-C)
    kpi = result.live_kpi
    if kpi is not None and kpi.get('feed_dominance_score') is not None:
        dom_source = kpi.get('dominant_feed_source', 'N/A')
        dom_pct = kpi.get('dominant_feed_share_pct')
        dom_pct_str = f"{round(dom_pct, 1)}%" if dom_pct is not None else 'N/A'
        soft_cap = kpi.get('estimated_per_source_soft_cap', 'N/A')
        recommendation = kpi.get('feed_balance_recommendation', 'N/A')
        lines.extend([
            "",
            "## Feed Balance",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Feed dominance score | {kpi.get('feed_dominance_score')} |",
            f"| Dominant source | {dom_source} |",
            f"| Dominant share | {dom_pct_str} |",
            f"| Soft cap (est.) | {soft_cap} |",
            f"| Recommendation | {recommendation} |",
        ])

    return "\n".join(lines)