# Sprint F230F: Post-F230 Integration Guard Report

## F230A — Single Launch Gate with Swap Tiers
- OneButtonVerdict.DO_NOT_RUN_MEMORY_HARD_BLOCK present
- Swap tiers: <=2.0 GiB → RUN_NOW, >2.0-4.0 → RESTART_THEN_RUN, >4.0 → DO_NOT_RUN_MEMORY_HARD_BLOCK
- nonfeed_diagnostic180 benchmark wired
- capability_synthesis in expected_assertions
- next_sprint_seeds_generated in expected_assertions

## F230B — PUBLIC Bootstrap First
- PipelineRunResult has public_bootstrap_candidates_count, public_bootstrap_fetch_attempted, public_bootstrap_fetch_success, public_bootstrap_accepted_findings
- _PublicStage.BOOTSTRAP_ZERO_SUCCESS and BOOTSTRAP_ACCEPTED present
- _compute_public_stage: bootstrap accepted > 0 → BOOTSTRAP_ACCEPTED (not DISCOVERY_TIMEOUT)
- Discovery timeout cannot erase bootstrap telemetry

## F230C — CT Provider Truth with STALE_CACHE_USED
- CTLossStage.STALE_CACHE_USED present (line 613 of sprint_scheduler.py)
- cache_used derivation checked BEFORE raw=0 check
- raw>0 + accepted=0 + bridge → ALL_REJECTED_BY_BRIDGE (not no_loss)
- ct_cache_used, ct_cache_stale, ct_cache_age_s on SprintSchedulerResult

## F230D — Nonfeed Budget Policy
- FeedDominanceBudget.cap_feeding accepts acquisition_profile
- nonfeed_diagnostic profile activates cap at domain_recon threshold (20)
- cap relaxes when nonfeed_unresolved=False (nonfeed terminal)
- SprintSchedulerResult has nonfeed_budget_active, *_expected_lanes, *_terminal_lanes, *_unresolved_lanes, feed_suppressed_by_nonfeed_budget, feed_suppression_count, feed_suppression_reason

## Cross-Cutting Integration
- F230A gate + F230B bootstrap + F230C CT cache + F230D nonfeed budget coexist without conflict
- Synthetic nonfeed_diagnostic180 artifact would NOT be FAIL_TERMINALITY_UNSATISFIED, no_loss CT, or generic DISCOVERY_TIMEOUT
- Nonfeed budget state verified post-F230D ready for one clean live run

## Verdict
F230A-D proven as one coherent nonfeed capability upgrade. Ready for one clean live run of nonfeed_diagnostic180.
