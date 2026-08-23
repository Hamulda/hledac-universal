# Fetch Coordinator

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/fetch-coordinator.md` |
| Source Path | `coordinators/fetch_coordinator.py` |

## Summary

Delegates fetch/crawl pipeline to coordinator. Implements stable coordinator interface (start/step/shutdown) for URL frontier selection, network fetch with security checks, and evidence creation.

## Evidence

- Implements start/step/shutdown coordinator interface
- Handles URL frontier selection and security checks
- Creates and stores evidence

## Use When

- Adding new fetch strategies
- Understanding the web fetch pipeline
- Debugging fetch failures

## Do Not Use When

- Changing the pipeline stage architecture (see pipeline orchestrator)
- Changing the CLI (see cli/parser)
