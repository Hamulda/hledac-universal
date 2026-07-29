"""core/cli/args.py — Canonical ArgumentParser builder (F350M-R A-05).

Single source of truth for CLI argument definitions.
Supports both legacy flat args and modern subcommand syntax.
"""
import argparse
import os
import pathlib
from argparse import Namespace as _NS

# Acquisition profile choices
_ACQ_PROFILES: tuple[str, ...] = ("default", "nonfeed_diagnostic", "deep_osint_m1")

# Source tier choices
_TIER_CHOICES: tuple[str, ...] = ("surface", "dark", "archive", "p2p", "academic")


def build_parser() -> argparse.ArgumentParser:
    """Build the shared ArgumentParser — canonical, flat + subcommands."""
    parser = argparse.ArgumentParser(
        description="Hledac Universal OSINT Orchestrator",
        prog="hledac",
        add_help=False,
    )
    try:
        parser.suggest_on_error = True
        parser.color = True
    except AttributeError:
        pass

    _add_global_args(parser)
    _add_legacy_flat_args(parser)

    parser.add_argument("-h", "--help", action="help", help="Show this help message and exit.")

    subparsers = parser.add_subparsers(dest="_subcommand", title="commands")
    _add_sprint_subparser(subparsers)
    _add_pivot_subparser(subparsers)
    _add_ct_subparser(subparsers)
    _add_list_sources_subparser(subparsers)

    return parser


def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Arguments shared by all command forms."""
    parser.add_argument(
        "--acquisition-profile",
        type=str,
        default="default",
        choices=_ACQ_PROFILES,
        help="F216B/F251D: Acquisition runtime profile",
    )
    parser.add_argument(
        "--preset",
        type=str,
        default=None,
        choices=["minimal", "osint", "recon", "research", "full"],
        help="Phase 3: Apply a flag preset before validation.",
    )
    parser.add_argument(
        "--list-presets",
        action="store_true",
        help="Phase 3: Print preset table and exit 0.",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Issue #19: Enable M1-safe OTEL profiling via HLEDAC_OTEL_PROFILE=1.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="F221-ABORT: Override the pre-flight guard.",
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
        help="Disable aggressive mode",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Run deep probe research post-sprint",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Enable terminal dashboard during sprint",
    )


def _add_legacy_flat_args(parser: argparse.ArgumentParser) -> None:
    """Legacy flat args (backward compat: python -m hledac.universal --sprint 'query')."""
    parser.add_argument("--sprint", metavar="QUERY", help="Run sprint with given query")
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
        help="F285: Override windup lead time in seconds.",
    )
    parser.add_argument("--vault", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--rl-train",
        action="store_true",
        help="RL F257: Enable QMIX training mode.",
    )
    parser.add_argument(
        "--rl-no-train",
        action="store_true",
        help="RL F261QMIX: Force inference-only mode.",
    )
    parser.add_argument(
        "--rl-train-interval",
        type=int,
        default=None,
        help="RL F261QMIX: Override HLEDAC_RL_TRAIN_INTERVAL.",
    )
    parser.add_argument(
        "--no-communication",
        action="store_true",
        help="F26X-3: Skip CommunicationLayer injection.",
    )
    parser.add_argument(
        "--no-ghost",
        action="store_true",
        help="F260: Skip GhostLayer injection.",
    )
    parser.add_argument(
        "--no-stealth",
        action="store_true",
        help="F260: Skip StealthLayer injection.",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="F272B: Abort with exit 2 if pre-run health check fails.",
    )
    parser.add_argument(
        "--force-hermes",
        action="store_true",
        help="F273D: Force-load Hermes3 model at sprint start.",
    )


def _add_sprint_subparser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser("sprint", help="Run OSINT sprint")
    p.add_argument("--sprint", metavar="QUERY", required=True, help="Run sprint with given query")
    p.add_argument("--duration", type=float, default=1800.0, metavar="SECS")
    p.add_argument("--windup-lead", type=float, default=None)
    p.add_argument("--export-dir", default=str(pathlib.Path.home() / ".hledac" / "reports"))
    p.add_argument("--aggressive", action="store_true", default=True)
    p.add_argument("--no-aggressive", dest="aggressive", action="store_false")
    p.add_argument("--force", action="store_true")
    p.add_argument("--acquisition-profile", type=str, default="default", choices=_ACQ_PROFILES)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--preset", type=str, default=None, choices=["minimal", "osint", "recon", "research", "full"])
    p.add_argument("--list-presets", action="store_true")
    p.add_argument("--profile", action="store_true")
    p.add_argument("--ui", action="store_true")
    p.add_argument("--deep-probe", action="store_true")
    p.add_argument("--vault", action="store_true")
    p.add_argument("--no-communication", action="store_true")
    p.add_argument("--no-ghost", action="store_true")
    p.add_argument("--no-stealth", action="store_true")
    p.add_argument("--production", action="store_true")
    p.add_argument("--force-hermes", action="store_true")
    p.add_argument("--rl-train", action="store_true")
    p.add_argument("--rl-no-train", action="store_true")
    p.add_argument("--rl-train-interval", type=int, default=None)
    return p


def _add_pivot_subparser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser("pivot", help="Pivot search")
    p.add_argument("--pivot", metavar="QUERY", required=True, help="Pivot search query")
    p.add_argument("--pivot-k", type=int, default=10, help="Number of results (default: 10)")
    p.add_argument("--export-dir", default=str(pathlib.Path.home() / ".hledac" / "reports"))
    return p


def _add_ct_subparser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser("ct", help="CT (certificate transparency) pivot")
    p.add_argument("--ct-pivot", metavar="DOMAIN", required=True, help="Certificate transparency pivot domain")
    p.add_argument("--export-dir", default=str(pathlib.Path.home() / ".hledac" / "reports"))
    return p


def _add_list_sources_subparser(subparsers) -> argparse.ArgumentParser:
    p = subparsers.add_parser("list-sources", help="Print DeepSourceRegistry catalog")
    p.add_argument("--tier", type=str, default=None, choices=_TIER_CHOICES)
    return p


def resolve_rl_args(args: _NS) -> _NS:
    """F261QMIX: resolve --rl-no-train overrides and env-var propagation."""
    if getattr(args, "rl_no_train", False):
        args.rl_train = False
    if getattr(args, "rl_train_interval", None) is not None:
        os.environ["HLEDAC_RL_TRAIN_INTERVAL"] = str(args.rl_train_interval)
    return args
