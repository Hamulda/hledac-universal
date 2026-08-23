# CLI Surface

## Metadata

| Field | Value |
| --- | --- |
| Kind | surface |
| Status | current |
| Last Verified | 2026-08-19 |
| Evidence Level | source |
| Entry Path | `surfaces/cli.md` |
| Source Path | `cli/parser.py`, `__main__.py` |

## Summary

Canonical CLI entry point using argparse. Three dispatch modes: sprint, pivot, ct.

## Commands

```bash
python -m hledac.universal --sprint "query"
python -m hledac.universal pivot --pivot "ransomware"
python -m hledac.universal ct --ct-pivot example.com
hledac --sprint "query"  # console script
```

## Evidence

- __main__.py:main() → cli.parser.main() → asyncio.run(async_main())
- Parser built via _core.cli.args.build_parser()
- Dispatch: _dispatch_*_async() for each mode
- Python 3.14+ JIT: PYTHON_JIT=1 set in parser.py

## Use When

- Running the CLI
- Adding new CLI commands

## Do Not Use When

- Programmatic use (see composition_root or duckdb_store)
