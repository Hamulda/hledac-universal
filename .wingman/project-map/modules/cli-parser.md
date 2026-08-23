# CLI Parser

## Metadata

| Field | Value |
| --- | --- |
| Kind | module |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `modules/cli-parser.md` |
| Source Path | `cli/parser.py` |

## Summary

Canonical CLI entry point for the Hledac OSINT orchestrator. Builds the ArgumentParser and dispatches to async handlers.

## Evidence

- `__main__.py:main()` → `cli.parser.main()` → `asyncio.run(cli.parser.async_main())`
- Parser built via `_core.cli.args.build_parser()`
- Dispatch methods: `_dispatch_*_async()` for sprint, pivot, ct modes

## Use When

- Understanding CLI argument flow
- Adding new CLI commands
- Debugging CLI parsing

## Do Not Use When

- Changing orchestrator internals (see composition_root)
- Changing pipeline stages (see pipeline orchestrator)
