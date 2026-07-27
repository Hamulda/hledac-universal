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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


# --------------------------------------------------------------------------- #
# Shared argument helpers
# --------------------------------------------------------------------------- #


def _add_sprint_common(parser: argparse.ArgumentParser) -> None:
    """Arguments common to sprint and dry-run."""
    parser.add_argument(
        "--sprint",
        metavar="QUERY",
        required=True,
        help="Run sprint with given query",
    )
    parser.add_argument(
        "--duration",
        type=float,
        default=1800.0,
        metavar="SECS",
        help="Sprint duration in seconds (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--windup-lead",
        type=float,
        default=None,
        help="Override windup lead time in seconds. Default: 30%% of duration (capped at 180s).",
    )
    parser.add_argument(
        "--export-dir",
        default=str(pathlib.Path.home() / ".hledac" / "reports"),
        help="Directory for sprint reports (default: ~/.hledac/reports)",
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        default=True,
        help="Enable aggressive mode with 8s branch budgets (default: ON)",
    )
    parser.add_argument(
        "--no-aggressive",
        dest="aggressive",
        action="store_false",
        help="Disable aggressive mode: stable sequential branches, 30 percent windup",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="F221-ABORT: Override the pre-flight guard that aborts sprints whose "
        "active-window budget would be below MIN_ACTIVE_WINDOW_S=30s.",
    )
    parser.add_argument(
        "--acquisition-profile",
        type=str,
        default="default",
        choices=["default", "nonfeed_diagnostic", "deep_osint_m1"],
        help="Acquisition runtime profile",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry-run mode: validate config, check Hermes/UMA/sources, show timing plan.",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default=None,
        choices=["minimal", "osint", "recon", "research", "full"],
        help="Apply a flag preset before validation.",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="Print preset table and exit 0.",
    )


# --------------------------------------------------------------------------- #
# Subcommand: sprint
# --------------------------------------------------------------------------- #


def _cmd_sprint(parser: argparse.ArgumentParser) -> None:
    _add_sprint_common(parser)
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Enable terminal dashboard during sprint",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Run deep probe research post-sprint",
    )
    parser.add_argument(
        "--vault",
        action="store_true",
        help="Enable encrypted vault export (AES-256-ZIP)",
    )


# --------------------------------------------------------------------------- #
# Subcommand: pivot
# --------------------------------------------------------------------------- #


def _cmd_pivot(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pivot",
        metavar="QUERY",
        required=True,
        help="Pivot search query",
    )
    parser.add_argument(
        "--pivot-k",
        type=int,
        default=10,
        help="Number of pivot results (default: 10)",
    )
    parser.add_argument(
        "--export-dir",
        default=str(pathlib.Path.home() / ".hledac" / "reports"),
        help="Directory for reports (default: ~/.hledac/reports)",
    )


# --------------------------------------------------------------------------- #
# Subcommand: ct (certificate transparency)
# --------------------------------------------------------------------------- #


def _cmd_ct(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--ct-pivot",
        metavar="DOMAIN",
        required=True,
        help="Certificate transparency pivot domain",
    )
    parser.add_argument(
        "--export-dir",
        default=str(pathlib.Path.home() / ".hledac" / "reports"),
        help="Directory for reports (default: ~/.hledac/reports)",
    )


# --------------------------------------------------------------------------- #
# Parser factory
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    """
    Build the root ArgumentParser — delegates to core.cli.args.build_parser().

    Canonical parser lives in ``core.cli.args.build_parser()``.
    This function is kept for backward compatibility of existing callers.
    """
    from hledac.universal.core.cli.args import build_parser as _canonical_build_parser

    return _canonical_build_parser()


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
    """Run canonical sprint via runtime.sprint_entrypoint.run_sprint()."""
    import asyncio
    import gc
    import logging
    import os
    import pathlib

    from hledac.universal.runtime.sprint_entrypoint import (
        SprintFlags,
        _cancel_all_tasks,
        dry_run_sprint,
        run_sprint,
    )

    logger = logging.getLogger(__name__)
    logger.info("[CLI] sprint: delegating to runtime.sprint_entrypoint.run_sprint()")

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
    export_dir: str = getattr(args, "export_dir", None) or str(pathlib.Path.home() / ".hledac" / "reports")

    if vault:
        os.environ["HLEDAC_VAULT_EXPORT"] = "1"

    # F350M-R ISSUE #4 FIX: asyncio.Runner subclass with bounded drain on SIGINT.
    # Standard Runner.run() closes the loop without draining cancelled tasks,
    # so DuckDB commits / MLX evals can be abandoned.  This subclass overrides
    # the finally block to run _cancel_all_tasks() before loop.close().
    class _BoundedRunner(asyncio.Runner):
        """Runner that drains tasks with a bounded timeout before closing the loop."""

        def close(self) -> None:
            """Drain pending tasks then close the event loop."""
            if self._loop is None or self._loop.is_closed():
                return
            # Bounded drain: prevents 30+ s DuckDB/MLX/zstd ops from blocking shutdown.
            try:
                self._loop.run_until_complete(_cancel_all_tasks(timeout_s=5.0))
            except Exception:
                pass
            super().close()
            # M1 8GB: reclaim event-loop allocations
            try:
                gc.collect()
            except Exception:
                pass

    try:
        if dry_run:
            with _BoundedRunner() as runner:
                runner.run(dry_run_sprint(query=target, duration_s=duration))
        else:
            root_flags = SprintFlags(force=force)
            # F350M-R ISSUE #4: Pass shutdown_event so run_sprint can do cooperative
            # shutdown when asyncio.run() default SIGINT handler fires.
            shutdown_event = asyncio.Event()

            async def _run_with_shutdown() -> None:
                await run_sprint(
                    query=target,
                    duration_s=duration,
                    export_dir=export_dir,
                    aggressive_mode=aggressive,
                    deep_probe_enabled=deep_probe,
                    ui_mode=ui,
                    windup_lead_s=windup_lead,
                    acquisition_profile=profile,
                    flags=root_flags,
                    shutdown_event=shutdown_event,
                )

            with _BoundedRunner() as runner:
                runner.run(_run_with_shutdown())
        return 0
    except (NameError, AttributeError, ImportError):
        raise  # propagate to main() for code=3
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        logger.error("[CLI] sprint failed: %s", e, exc_info=True)
        return 1


async def _dispatch_sprint_async(args: argparse.Namespace) -> int:
    """Run canonical sprint via runtime.sprint_entrypoint.run_sprint() — fully async, no Runner nesting."""
    import asyncio
    import logging
    import os
    import pathlib

    from hledac.universal.runtime.sprint_entrypoint import (
        SprintFlags,
        dry_run_sprint,
        run_sprint,
    )

    logger = logging.getLogger(__name__)
    logger.info("[CLI] sprint: delegating to runtime.sprint_entrypoint.run_sprint()")

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
    export_dir: str = getattr(args, "export_dir", None) or str(pathlib.Path.home() / ".hledac" / "reports")

    if vault:
        os.environ["HLEDAC_VAULT_EXPORT"] = "1"

    try:
        if dry_run:
            await dry_run_sprint(query=target, duration_s=duration)
        else:
            root_flags = SprintFlags(force=force)
            shutdown_event = asyncio.Event()
            await run_sprint(
                query=target,
                duration_s=duration,
                export_dir=export_dir,
                aggressive_mode=aggressive,
                deep_probe_enabled=deep_probe,
                ui_mode=ui,
                windup_lead_s=windup_lead,
                acquisition_profile=profile,
                flags=root_flags,
                shutdown_event=shutdown_event,
            )
        return 0
    except (NameError, AttributeError, ImportError):
        raise  # propagate to main() for code=3
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        logger.error("[CLI] sprint failed: %s", e, exc_info=True)
        return 1
    finally:
        # Bounded drain is handled by _BoundedRunner.close() in the sync path
        # (_dispatch_sprint).  The async path (_dispatch_sprint_async) runs inside
        # asyncio.Runner which owns the loop — we cannot call run_until_complete()
        # on the running loop from within its own finally (deadlock), and we cannot
        # close a running loop (RuntimeError).  Runner.close() handles cleanup.
        # SIGINT exits via Runner's interrupt + sys.exit(130) from main().
        pass


def _dispatch_pivot(args: argparse.Namespace) -> int:
    """Run semantic pivot search."""
    import asyncio
    import logging

    from hledac.universal.runtime.sprint_entrypoint import run_semantic_pivot

    logger = logging.getLogger(__name__)
    target: str = getattr(args, "pivot", None) or ""
    k: int = getattr(args, "pivot_k", 10)

    logger.info("[CLI] pivot: delegating to runtime.sprint_entrypoint.run_semantic_pivot()")
    try:
        with asyncio.Runner() as runner:
            runner.run(run_semantic_pivot(query=target, top_k=k))
        return 0
    except Exception as e:
        logger.error("[CLI] pivot failed: %s", e, exc_info=True)
        return 1


def _dispatch_ct(args: argparse.Namespace) -> int:
    """Run CT pivot."""
    import asyncio
    import logging

    from hledac.universal.runtime.ct_pivot import run_ct_pivot

    logger = logging.getLogger(__name__)
    target: str = getattr(args, "ct_pivot", None) or ""

    logger.info("[CLI] ct: delegating to runtime.ct_pivot.run_ct_pivot()")
    try:
        with asyncio.Runner() as runner:
            runner.run(run_ct_pivot(domain=target))
        return 0
    except Exception as e:
        logger.error("[CLI] ct failed: %s", e, exc_info=True)
        return 1


async def _dispatch_pivot_async(args: argparse.Namespace) -> int:
    """Run semantic pivot search — fully async, no Runner nesting."""
    import logging

    from hledac.universal.runtime.sprint_entrypoint import run_semantic_pivot

    logger = logging.getLogger(__name__)
    target: str = getattr(args, "pivot", None) or ""
    k: int = getattr(args, "pivot_k", 10)

    logger.info("[CLI] pivot: delegating to runtime.sprint_entrypoint.run_semantic_pivot()")
    try:
        await run_semantic_pivot(query=target, top_k=k)
        return 0
    except Exception as e:
        logger.error("[CLI] pivot failed: %s", e, exc_info=True)
        return 1


async def _dispatch_ct_async(args: argparse.Namespace) -> int:
    """Run CT pivot — fully async, no Runner nesting."""
    import logging

    from hledac.universal.runtime.ct_pivot import run_ct_pivot

    logger = logging.getLogger(__name__)
    target: str = getattr(args, "ct_pivot", None) or ""

    logger.info("[CLI] ct: delegating to runtime.ct_pivot.run_ct_pivot()")
    try:
        await run_ct_pivot(domain=target)
        return 0
    except Exception as e:
        logger.error("[CLI] ct failed: %s", e, exc_info=True)
        return 1


# --------------------------------------------------------------------------- #
# Async dispatcher — P0-03: enables asyncio.to_thread for boot guard
# --------------------------------------------------------------------------- #
# Called from __main__.py:main() when asyncio event loop is available.
# Boot guard runs in thread pool, parallel with command startup.


async def dispatch_async(args: argparse.Namespace) -> int:
    """
    Async dispatcher — routes to async handler variants for non-blocking execution.

    P0-03: Uses asyncio.to_thread() for boot-sensitive operations instead of
    blocking the event loop. Boot guard runs in thread pool, parallel with command.
    """
    import asyncio
    import logging

    logger = logging.getLogger(__name__)
    logger.debug("[CLI] dispatch_async: entering")

    # Wire async log handler — runs inside asyncio.Runner context
    # Activated by HLEDAC_ASYNC_LOG=1 (default OFF for stability)
    try:
        from hledac.universal.runtime.observability_async_handler import (
            configure_async_logging,
        )

        await configure_async_logging()
    except Exception:
        pass

    sub = getattr(args, "_subcommand", None)
    sprint_target = getattr(args, "sprint", None)

    if sub == "sprint":
        return await _dispatch_sprint_async(args)
    elif sub == "pivot":
        return await _dispatch_pivot_async(args)
    elif sub == "ct":
        return await _dispatch_ct_async(args)
    elif sprint_target is not None:
        # Legacy flat syntax: --sprint 'query' without subcommand
        return await _dispatch_sprint_async(args)
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
