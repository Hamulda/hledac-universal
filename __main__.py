"""
Hledac Universal - Async Entry Point
====================================

Sprint 8AI: Boot Hygiene Closure
- AsyncExitStack as unified teardown backbone
- 8AG LMDB boot guard as FIRST boot step
- LIFO teardown order for existing surfaces
- Signal-safe teardown (no direct cleanup in signal handler)
- Graceful task cancellation before loop close
- CheckpointManager: N/A (AO-coupled only)

Usage:
    python -m hledac.universal [--benchmark]

No CLI arguments are required for normal operation.
Benchmark mode activates internal probe tests.

Issue A-08: Dead code removed (Sprint 8AO, _BootTelemetryDrainer, etc.)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import pathlib
import signal
import sys
import time
import traceback
from typing import TYPE_CHECKING, Any

from dotenv import load_dotenv

from hledac.universal.runtime.logging_setup import configure_logging, get_logger

# Sprint F285: Ensure local modules (utils/, runtime/, etc.) are resolvable when
# hledac is invoked via `uv run hledac` or the generated .venv/bin/hledac entry point.
_src_root = pathlib.Path(__file__).parent.resolve()
if str(_src_root) not in sys.path:
    sys.path.insert(0, str(_src_root))
del _src_root

# TYPE_CHECKING block: imports only for static analysis (ruff, mypy)
if TYPE_CHECKING:
    import argparse

# Sprint 0B: uvloop MUST be installed before any async operations
_uvloop_installed = False
try:
    import sys as _sys
    import uvloop
    if _sys.version_info >= (3, 15):
        logging.warning("[RUNTIME] Python 3.15+ detected, skipping uvloop.install()")
    else:
        import warnings as _lw
        with _lw.catch_warnings():
            _lw.filterwarnings("ignore", message=".*AbstractEventLoopPolicy.*", category=DeprecationWarning)
        uvloop.install()
        _uvloop_installed = True
        logging.info("[RUNTIME] uvloop installed successfully")
except ImportError:
    _uvloop_installed = False

logger = get_logger(__name__)


# =============================================================================
# Sprint 8AG: LMDB Boot Guard
# =============================================================================

class BootGuardError(Exception):
    """Raised when boot guard detects unsafe stale-lock state."""
    pass


def _run_boot_guard(lmdb_root: pathlib.Path | None = None) -> tuple[int, str]:
    """Run LMDB boot guard (8AG) synchronously. FIRST boot step before any runtime."""
    if lmdb_root is None:
        try:
            from hledac.universal.paths import LMDB_ROOT as _derived_root
            lmdb_root = _derived_root
        except Exception:
            return 0, "lmdb_root_not_configured"

    try:
        from hledac.universal.knowledge.lmdb_boot_guard import (
            BootGuardError as _BootGuardError,
            cleanup_stale_lmdb_lock,
        )
    except (ImportError, AttributeError) as e:
        return 0, f"boot_guard_import_failed({e})"

    try:
        removed, reason = cleanup_stale_lmdb_lock(lmdb_root)
        return removed, reason
    except _BootGuardError:
        raise
    except OSError as e:
        return 0, f"boot_guard_error({e})"


# =============================================================================
# Main entry point
# =============================================================================

def _fatal(exc: BaseException, code: int = 1) -> None:
    """Structured fatal-error handler. Logs _MAIN_FATAL with full traceback, exits."""
    logger.critical("_MAIN_FATAL [exit=%d]: %s\n%s", code, exc, traceback.format_exc())
    sys.exit(code)


def main() -> None:
    """Synchronous CLI entry point — delegates to cli.parser.dispatch_async()."""
    load_dotenv()
    configure_logging()

    try:
        import setproctitle
        setproctitle.setproctitle("kernel_worker")
    except ImportError:
        pass

    logger.info("boot_pid", pid=os.getpid())

    if os.environ.get("PYTHON_DISABLE_REMOTE_DEBUG") != "1":
        if os.environ.get("HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED") == "1":
            sys.exit(
                "HLEDAC_REQUIRE_REMOTE_DEBUG_DISABLED=1 but PYTHON_DISABLE_REMOTE_DEBUG not set — "
                "OSINT runtime requires external debugger disabled"
            )
        logger.warning("opsec_remote_debug_active")

    from hledac.universal.cli.parser import build_parser
    parser = build_parser()

    if "--help" in sys.argv or "-h" in sys.argv:
        parser.print_help()
        print()
        print("Sprint usage:")
        print("  python -m hledac.universal sprint --sprint 'query'")
        print("  python -m hledac.universal sprint --sprint 'LockBit ransomware' --duration 1800")
        print()
        print("Other commands:")
        print("  python -m hledac.universal pivot --pivot 'ransomware CVE'")
        print("  python -m hledac.universal ct --ct-pivot example.com")
        sys.exit(0)

    args = parser.parse_args()

    if getattr(args, "profile", False):
        os.environ["HLEDAC_OTEL_PROFILE"] = "1"
        if os.environ.get("HLEDAC_OTEL_EXPORTER", "") not in ("otlp", "duckdb", "logfire"):
            os.environ.setdefault("HLEDAC_OTEL_EXPORTER", "otlp")

    if getattr(args, "list_presets", False):
        try:
            from hledac.universal.utils.flag_presets import list_presets_table
            print(list_presets_table())
        except (ImportError, AttributeError) as exc:
            print(f"flag_presets unavailable: {exc!r}", file=sys.stderr)
        sys.exit(0)

    preset_name = getattr(args, "preset", None)
    if preset_name:
        try:
            from hledac.universal.utils.flag_presets import apply_preset
            applied = apply_preset(preset_name, overwrite=False)
            logger.info("flag_preset_applied", preset=preset_name, flag_count=len(applied))
        except (ValueError, RuntimeError) as exc:
            logger.error("flag_preset_failed", preset=preset_name, error=str(exc))
            sys.exit(2)

    try:
        from hledac.universal.utils.flag_registry import validate_flag_combo
        errors, warnings = validate_flag_combo()
        for w in warnings:
            logger.warning("flag_validation_warning", warning=w)
        if errors:
            for e in errors:
                logger.error("flag_conflict", error=e)
            sys.exit(2)
    except (ImportError, AttributeError) as exc:
        logger.warning("flag_validation_internal_error", error=str(exc))

    try:
        removed, reason = _run_boot_guard()
        logger.info("boot_guard_result", removed=removed, reason=reason)
    except BootGuardError as e:
        logger.error("boot_guard_unsafe_state", error=str(e))
        sys.exit(1)
    except OSError as e:
        logger.warning("boot_guard_error", error=str(e))

    try:
        from hledac.universal.cli.parser import dispatch_async
        with asyncio.Runner() as runner:
            code = runner.run(dispatch_async(args))
        sys.exit(code)
    except (NameError, AttributeError, ImportError) as e:
        _fatal(e, code=3)
    except KeyboardInterrupt:
        logger.info("interrupted_by_user")
        sys.exit(130)
    except SystemExit:
        raise
    except Exception as e:
        _fatal(e, code=1)


if __name__ == "__main__":
    main()
