# Pivot Lane Planning

## Metadata

| Field | Value |
| --- | --- |
| Kind | flow |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `flows/pivot-lane-planning.md` |

## Summary

Determines which nonfeed lanes run per pivot seed type (F220B). Pure, no network.

## Seed → Lane Mapping

| Seed Type | Lanes |
|---|---|
| domain | DOH + CT + WAYBACK + PASSIVE_DNS |
| url | WAYBACK + PUBLIC |
| ip | BGP + PASSIVE_DNS + DOH reverse |
| hash | no-op |
| entity | PUBLIC |

## Evidence

- PivotLanePlanner in pipeline/pivot_lane_planner.py
- Returns plan only, no network calls
- Used by pipeline orchestrator to decide which lanes to run

## Use When

- Understanding pivot expansion logic
- Adding new seed types or lanes

## Do Not Use When

- Understanding specific lane implementation (see respective lane)
