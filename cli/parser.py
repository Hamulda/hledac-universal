# cli/parser.py — Modular CLI parser with subcommands
"""
Modern CLI parser for Hledac Universal.

Replaces monolithic build_parser() in __main__.py with argparse subcommands.
Each command lives in cli/commands/.

Commands:
    sprint   — Run OSINT sprint (canonical path)
    pivot    — Pivot search
    ct       — CT (certificate) pivot
"""

import argparse
import pathlib
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable


# --------------------------------------------------------------------------- #
# Shared argument helpers
# --------------------------------------------------------------------------- #

def _add_sprint_common(parser: argparse.ArgumentParser) -> None:
    """Arguments common to sprint and dry-run."""
    parser.add_argument(
        "--sprint", metavar="QUERY", required=True,
        help="Run sprint with given query",
    )
    parser.add_argument(
        "--duration", type=float, default=1800.0, metavar="SECS",
        help="Sprint duration in seconds (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--windup-lead", type=float, default=None,
        help="Override windup lead time in seconds. Default: 30%% of duration (capped at 180s).",
    )
    parser.add_argument(
        "--export-dir", default=str(pathlib.Path.home() / ".hledac" / "reports"),
        help="Directory for sprint reports (default: ~/.hledac/reports)",
    )
    parser.add_argument(
        "--aggressive", action="store_true", default=True,
        help="Enable aggressive mode with 8s branch budgets (default: ON)",
    )
    parser.add_argument(
        "--no-aggressive", dest="aggressive", action="store_false",
        help="Disable aggressive mode: stable sequential branches, 30 percent windup",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="F221-ABORT: Override the pre-flight guard that aborts sprints whose "
             "active-window budget would be below MIN_ACTIVE_WINDOW_S=30s.",
    )
    parser.add_argument(
        "--acquisition-profile",
        type=str, default="default",
        choices=["default", "nonfeed_diagnostic", "deep_osint_m1"],
        help="Acquisition runtime profile",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Dry-run mode: validate config, check Hermes/UMA/sources, show timing plan.",
    )
    parser.add_argument(
        "--preset",
        type=str, default=None,
        choices=["minimal", "osint", "recon", "research", "full"],
        help="Apply a flag preset before validation.",
    )
    parser.add_argument(
        "--list-presets", action="store_true",
        help="Print preset table and exit 0.",
    )


# --------------------------------------------------------------------------- #
# Subcommand: sprint
# --------------------------------------------------------------------------- #

def _cmd_sprint(parser: argparse.ArgumentParser) -> None:
    _add_sprint_common(parser)
    parser.add_argument(
        "--ui", action="store_true",
        help="Enable terminal dashboard during sprint",
    )
    parser.add_argument(
        "--deep-probe", action="store_true",
        help="Run deep probe research post-sprint",
    )
    parser.add_argument(
        "--vault", action="store_true",
        help="Enable encrypted vault export (AES-256-ZIP)",
    )


# --------------------------------------------------------------------------- #
# Subcommand: pivot
# --------------------------------------------------------------------------- #

def _cmd_pivot(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pivot", metavar="QUERY", required=True,
        help="Pivot search query",
    )
    parser.add_argument(
        "--pivot-k", type=int, default=10,
        help="Number of pivot results (default: 10)",
    )
    parser.add_argument(
        "--export-dir", default=str(pathlib.Path.home() / ".hledac" / "reports"),
        help="Directory for reports (default: ~/.hledac/reports)",
    )


# --------------------------------------------------------------------------- #
# Subcommand: ct (certificate transparency)
# --------------------------------------------------------------------------- #

def _cmd_ct(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ct-pivot", metavar="DOMAIN", required=True,
        help="Certificate transparency pivot domain",
    )
    parser.add_argument(
        "--export-dir", default=str(pathlib.Path.home() / ".hledac" / "reports"),
        help="Directory for reports (default: ~/.hledac/reports)",
    )


# --------------------------------------------------------------------------- #
# Parser factory
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    """
    Build the root ArgumentParser with subcommands.

    Supports TWO calling conventions:
      Legacy flat (backward compat):  python -m hledac.universal --sprint 'query' ...
      Modern subcommand (new):         python -m hledac.universal sprint --sprint 'query' ...

    Architecture:
        __main__.py:main()  →  this parser  →  dispatch to core.__main__.run_sprint()
        canonical path: core.__main__.run_sprint() — NOT __main__._run_sprint_mode()

    Dead legacy symbols retained in __main__.py for regression safety:
        _run_sprint_mode, _run_async_main, run_warmup, _run_public_passive_once,
        _run_observed_default_feed_batch_once, ObservedRunReport, _UmaSampler
    """
    import argparse as _argparse

    parser = _argparse.ArgumentParser(
        description="Hledac Universal OSINT Runner",
        add_help=False,
    )

    # Python 3.14 settings
    try:
        parser.suggest_on_error = True
        parser.color = True
    except AttributeError:
        pass

    # ------------------------------------------------------------------
    # Legacy flat args (backward compat with existing tests + callers)
    # ------------------------------------------------------------------
    parser.add_argument("--sprint", metavar="QUERY", help="Run sprint with given query")
    parser.add_argument("--duration", type=float, default=1800.0, metavar="SECS")
    parser.add_argument("--windup-lead", type=float, default=None)
    parser.add_argument(
        "--export-dir", default=str(pathlib.Path.home() / ".hledac" / "reports")
    )
    parser.add_argument("--vault", action="store_true")
    parser.add_argument("--aggressive", action="store_true", default=True)
    parser.add_argument("--no-aggressive", dest="aggressive", action="store_false")
    parser.add_argument("--deep-probe", action="store_true")
    parser.add_argument("--ui", action="store_true")
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--acquisition-profile", type=str, default="default",
        choices=["default", "nonfeed_diagnostic", "deep_osint_m1"],
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--preset", type=str, default=None,
        choices=["minimal", "osint", "recon", "research", "full"],
    )
    parser.add_argument("--list-presets", action="store_true")

    # ------------------------------------------------------------------
    # Subcommands (modern syntax)
    # ------------------------------------------------------------------
    subparsers = parser.add_subparsers(dest="_subcommand", title="commands")

    p_sprint = subparsers.add_parser("sprint", help="Run OSINT sprint")
    _cmd_sprint(p_sprint)

    p_pivot = subparsers.add_parser("pivot", help="Pivot search")
    _cmd_pivot(p_pivot)

    p_ct = subparsers.add_parser("ct", help="CT (certificate transparency) pivot")
    _cmd_ct(p_ct)

    return parser


# --------------------------------------------------------------------------- #
# Dispatcher — called from __main__.py:main()
# --------------------------------------------------------------------------- #

def dispatch(args: argparse.Namespace) -> int:
    """
    Dispatch parsed args to the appropriate command handler.
    Preset/validation handled in main() before this is called.
    Returns exit code (0 = success, 1 = error, 2 = config error).

    Supports both calling conventions:
      Legacy flat:  --sprint 'query' [--duration N]  (no subcommand)
      Modern:       sprint --sprint 'query'  |  pivot --pivot ...  |  ct --ct-pivot ...
    """
    sub = getattr(args, "_subcommand", None)
    sprint_target = getattr(args, "sprint", None)

    if sub == "sprint":
        return _dispatch_sprint(args)
    elif sub == "pivot":
        return _dispatch_pivot(args)
    elif sub == "ct":
        return _dispatch_ct(args)
    elif sprint_target is not None:
        # Legacy flat syntax: --sprint 'query' without subcommand
        return _dispatch_sprint(args)
    else:
        # No subcommand and no legacy flags — show help
        parser = build_parser()
        parser.print_help()
        print("\nSprint usage:")
        print("  python -m hledac.universal sprint --sprint 'query'")
        print("  python -m hledac.universal sprint --sprint 'LockBit ransomware' --duration 1800")
        print()
        print("Legacy usage (backward compatible):")
        print("  python -m hledac.universal --sprint 'query'")
        print()
        print("Other commands:")
        print("  python -m hledac.universal pivot --pivot 'ransomware CVE' --pivot-k 10")
        print("  python -m hledac.universal ct --ct-pivot example.com")
        return 0


def _dispatch_sprint(args: argparse.Namespace) -> int:
    """Run canonical sprint via core.__main__.run_sprint()."""
    import asyncio
    import logging
    import os
    import pathlib

    from hledac.universal.core.__main__ import SprintFlags, run_sprint, dry_run_sprint

    logger = logging.getLogger(__name__)
    logger.info("[CLI] sprint: delegating to core.__main__.run_sprint()")

    target: str = getattr(args, "sprint", None) or ""
    duration: float = getattr(args, "duration", 1800.0)
    windup_lead = getattr(args, "windup_lead", None)
    aggressive: bool = getattr(args, "aggressive", True)
    ui: bool = getattr(args, "ui", False)
    deep_probe: bool = getattr(args, "deep_probe", False)
    vault: bool = getattr(args, "vault", False)
    force: bool = getattr(args, "force", False)
    profile: str | None = getattr(args, "acquisition_profile", "default")
    dry_run: bool = getattr(args, "dry_run", False)
    export_dir: str = getattr(args, "export_dir", None) or str(
        pathlib.Path.home() / ".hledac" / "reports"
    )

    if vault:
        os.environ["HLEDAC_VAULT_EXPORT"] = "1"

    try:
        if dry_run:
            asyncio.run(dry_run_sprint(query=target, duration_s=duration))
        else:
            root_flags = SprintFlags(force=force)
            asyncio.run(
                run_sprint(
                    query=target,
                    duration_s=duration,
                    export_dir=export_dir,
                    aggressive_mode=aggressive,
                    deep_probe_enabled=deep_probe,
                    ui_mode=ui,
                    windup_lead_s=windup_lead,
                    acquisition_profile=profile,
                    flags=root_flags,
                )
            )
        return 0
    except (NameError, AttributeError, ImportError):
        raise  # propagate to main() for code=3
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        logger.error("[CLI] sprint failed: %s", e, exc_info=True)
        return 1


def _dispatch_pivot(args: argparse.Namespace) -> int:
    """Run semantic pivot search."""
    import asyncio
    import logging

    from hledac.universal.core.__main__ import run_semantic_pivot

    logger = logging.getLogger(__name__)
    target: str = getattr(args, "pivot", None) or ""
    k: int = getattr(args, "pivot_k", 10)

    logger.info("[CLI] pivot: delegating to core.__main__.run_semantic_pivot()")
    try:
        asyncio.run(run_semantic_pivot(query=target, top_k=k))
        return 0
    except Exception as e:
        logger.error("[CLI] pivot failed: %s", e, exc_info=True)
        return 1


def _dispatch_ct(args: argparse.Namespace) -> int:
    """Run CT pivot."""
    import asyncio
    import logging

    from hledac.universal.core.__main__ import run_ct_pivot

    logger = logging.getLogger(__name__)
    target: str = getattr(args, "ct_pivot", None) or ""

    logger.info("[CLI] ct: delegating to core.__main__.run_ct_pivot()")
    try:
        asyncio.run(run_ct_pivot(domain=target))
        return 0
    except Exception as e:
        logger.error("[CLI] ct failed: %s", e, exc_info=True)
        return 1
