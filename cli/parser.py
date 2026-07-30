# cli/parser.py — Canonical CLI parser for Hledac Universal (F350M-R A-05)
"""
Entry points and dispatch for the argparse-based CLI.

Wired path:
    __main__.py:main() → cli.parser.main() → asyncio.run(cli.parser.async_main())
                                     → dispatch_async() → _dispatch_*_async()

The canonical ArgumentParser lives in ``core.cli.args.build_parser()``.
``cli/parser.py`` owns only the dispatch logic and async wrappers.
"""
from __future__ import annotations

import argparse
import asyncio
import gc
import logging
import os
import pathlib
import sys
from typing import TYPE_CHECKING

from hledac.universal.core.cli.args import build_parser, resolve_rl_args  # noqa: E402

if TYPE_CHECKING:
    pass

__all__ = ["main"]


# ── Entry points ──────────────────────────────────────────────────────────────


def main() -> int:
    """
    Synchronous CLI entry point — called from ``__main__.py``.

    Raises SystemExit on parse error so the envelope in ``__main__.py``
    can distinguish exit code 2 (config error) from exit code 1.

    M6-01: Uses asyncio.Runner() instead of asyncio.run() for Python 3.14+
    forward compatibility. asyncio.run() is deprecated in library code but
    still allowed in entry points; Runner is preferred.
    """
    try:
        with asyncio.Runner() as runner:
            return runner.run(async_main())
    except KeyboardInterrupt:
        return 130  # SIGINT


async def async_main() -> int:
    """Async CLI entry point — parses args and dispatches to async handlers."""
    parser = build_parser()
    args = parser.parse_args()
    args = resolve_rl_args(args)
    return await dispatch_async(args)


# ── Async dispatcher ───────────────────────────────────────────────────────────


async def dispatch_async(args: argparse.Namespace) -> int:
    """
    Async dispatcher — routes to async handler variants.

    P0-03: Uses asyncio.to_thread() for boot-sensitive operations.
    Boot guard runs in thread pool, parallel with command startup.
    """
    # Wire async log handler (activated by HLEDAC_ASYNC_LOG=1)
    try:
        from hledac.universal.runtime.observability_async_handler import (
            configure_async_logging,
        )
        await configure_async_logging()
    except Exception:
        pass

    sub = getattr(args, "_subcommand", None)
    sprint_target = getattr(args, "sprint", None)

    if sub == "sprint" or sprint_target is not None:
        return await _dispatch_sprint_async(args)
    elif sub == "pivot":
        return await _dispatch_pivot_async(args)
    elif sub == "ct":
        return await _dispatch_ct_async(args)
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


# ── Sprint ────────────────────────────────────────────────────────────────────


async def _dispatch_sprint_async(args: argparse.Namespace) -> int:
    """Run canonical sprint via ``runtime.sprint_entrypoint.run_sprint()``."""
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
    export_dir: str = getattr(args, "export_dir", None) or str(
        pathlib.Path.home() / ".hledac" / "reports"
    )

    if vault:
        os.environ["HLEDAC_VAULT_EXPORT"] = "1"

    # F350M-R ISSUE #4: asyncio.Runner subclass with bounded drain on SIGINT.
    # Standard Runner.run() closes the loop without draining cancelled tasks,
    # so DuckDB commits / MLX evals can be abandoned.
    class _BoundedRunner(asyncio.Runner):
        """Runner that drains tasks with a bounded timeout before closing the loop."""

        def close(self) -> None:
            """Drain pending tasks then close the event loop."""
            if self._loop is None or self._loop.is_closed():
                return
            try:
                self._loop.run_until_complete(
                    _cancel_all_tasks(timeout_s=5.0)
                )
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
        raise  # propagate to main() → code 3
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 1
    except Exception as e:
        logger.error("[CLI] sprint failed: %s", e, exc_info=True)
        return 1


async def _cancel_all_tasks(timeout_s: float) -> None:
    """Cancel all running tasks with a bounded timeout."""
    try:
        tasks = [t for t in asyncio.all_tasks() if not t.done()]
        if not tasks:
            return
        for t in tasks:
            t.cancel()
        await asyncio.wait(tasks, timeout=timeout_s)
    except Exception:
        pass


# ── Pivot ─────────────────────────────────────────────────────────────────────


async def _dispatch_pivot_async(args: argparse.Namespace) -> int:
    """Run semantic pivot search."""
    from hledac.universal.runtime.sprint_entrypoint import run_semantic_pivot

    logger = logging.getLogger(__name__)
    target: str = getattr(args, "pivot", None) or ""
    k: int = getattr(args, "pivot_k", 10)

    logger.info(
        "[CLI] pivot: delegating to runtime.sprint_entrypoint.run_semantic_pivot()"
    )
    try:
        await run_semantic_pivot(query=target, top_k=k)
        return 0
    except Exception as e:
        logger.error("[CLI] pivot failed: %s", e, exc_info=True)
        return 1


# ── CT ────────────────────────────────────────────────────────────────────────


async def _dispatch_ct_async(args: argparse.Namespace) -> int:
    """Run CT pivot."""
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
