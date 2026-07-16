"""Argparse schema for hledac universal CLI.

Exported symbols:
    build_parser() -> argparse.ArgumentParser
    SPLIT_ARGS: list[str] — arguments that split CLI parsing (multi-cmd dispatch)
    RL_ENV_VARS: set[str] — env vars written by RL resolve logic
"""
import msgspec

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

# Arguments that trigger multi-command dispatch (ct_pivot / pivot / sprint / default)
SPLIT_ARGS: frozenset[str] = frozenset({"ct_pivot", "pivot", "sprint"})

# RL env vars written during --rl-args resolution
RL_ENV_VARS: frozenset[str] = frozenset({"HLEDAC_RL_TRAIN_INTERVAL"})

# Acquisition profile choices
_ACQ_PROFILES: tuple[str, ...] = ("default", "nonfeed_diagnostic", "deep_osint_m1")

# Source tier choices
_TIER_CHOICES: tuple[str, ...] = ("surface", "dark", "archive", "p2p", "academic")


def build_parser() -> argparse.ArgumentParser:
    """Build the shared ArgumentParser for all CLI commands."""
    parser = argparse.ArgumentParser(
        description="Hledac Universal OSINT Orchestrator",
        prog="hledac",
    )
    _add_global_args(parser)
    return parser


def _add_global_args(parser: argparse.ArgumentParser) -> None:
    """Arguments shared by all commands."""
    parser.add_argument(
        "--acquisition-profile",
        type=str,
        default="default",
        choices=_ACQ_PROFILES,
        help="F216B/F251D: Acquisition runtime profile",
    )


def resolve_rl_args(args: argparse.Namespace) -> argparse.Namespace:
    """F261QMIX: resolve --rl-no-train overrides and env-var propagation."""
    if getattr(args, "rl_no_train", False):
        args.rl_train = False
    if getattr(args, "rl_train_interval", None) is not None:
        os.environ["HLEDAC_RL_TRAIN_INTERVAL"] = str(args.rl_train_interval)
    return args


def configure_env_from_args(args: argparse.Namespace) -> None:
    """Side-effect: propagate CLI flags into os.environ for downstream consumers."""
    os.environ["HLEDAC_ACQUISITION_PROFILE"] = str(args.acquisition_profile)


# ── Command parsers ──────────────────────────────────────────────────────────


def sprint_parser() -> argparse.ArgumentParser:
    """Parser for the `sprint` command."""
    parser = build_parser()
    parser.description = "Run a Hledac sprint"
    parser.add_argument("--query", type=str, default="OSINT default query")
    parser.add_argument(
        "--duration",
        type=int,
        default=1800,
        help="Sprint duration in seconds (default: 1800 = 30min)",
    )
    parser.add_argument(
        "--export-dir",
        type=str,
        default=str(Path.home() / ".hledac" / "reports"),
    )
    parser.add_argument(
        "--aggressive",
        action="store_true",
        help="Sprint F195B: Enable aggressive mode with 8s branch budgets",
    )
    parser.add_argument(
        "--deep-probe",
        action="store_true",
        help="Run deep probe research post-sprint (deep web, S3 buckets, IPFS)",
    )
    parser.add_argument(
        "--deep-research",
        action="store_true",
        help="F11: Run enhanced deep research advisory post-sprint",
    )
    parser.add_argument(
        "--extreme",
        action="store_true",
        help="F11: Enable EXHAUSTIVE depth for deep research (implies --deep-research)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="F221-ABORT: Override the pre-flight guard (MIN_ACTIVE_WINDOW_S=30s). "
        "Emits [F221-FORCED] warning instead of exit 2.",
    )
    parser.add_argument(
        "--production",
        action="store_true",
        help="F272B: Abort with exit 2 if pre-run health check fails. Default OFF.",
    )
    parser.add_argument(
        "--force-hermes",
        action="store_true",
        help="F273D: Force-load Hermes3 model at sprint start.",
    )
    parser.add_argument(
        "--no-communication",
        action="store_true",
        help="F26X-3: Skip CommunicationLayer injection (default: ON)",
    )
    parser.add_argument(
        "--no-ghost",
        action="store_true",
        help="F260: Skip GhostLayer injection (default: ON)",
    )
    parser.add_argument(
        "--no-stealth",
        action="store_true",
        help="F260: Skip StealthLayer injection (default: ON)",
    )
    parser.add_argument(
        "--rl-train",
        action="store_true",
        help="RL F257: Enable QMIX training mode (updates Q-network weights every 10 sprints).",
    )
    parser.add_argument(
        "--rl-no-train",
        action="store_true",
        help="RL F261QMIX: Force inference-only mode (overrides HLEDAC_ENABLE_RL=1).",
    )
    parser.add_argument(
        "--rl-train-interval",
        type=int,
        default=None,
        help="RL F261QMIX: Override HLEDAC_RL_TRAIN_INTERVAL (default 10 sprints per QMIX step).",
    )
    return parser


def ct_pivot_parser() -> argparse.ArgumentParser:
    """Parser for the `ct-pivot` command."""
    parser = build_parser()
    parser.description = "Run CT log pivot for a domain via crt.sh"
    parser.add_argument("domain", type=str, help="Domain to pivot on")
    return parser


def pivot_parser() -> argparse.ArgumentParser:
    """Parser for the `pivot` command."""
    parser = build_parser()
    parser.description = "Sprint 8SB: semantic pivot — find similar findings via ANN search"
    parser.add_argument("query", type=str, help="Semantic search query")
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Number of results (default: 10)",
    )
    return parser


def list_sources_parser() -> argparse.ArgumentParser:
    """Parser for the `list-sources` command."""
    parser = build_parser()
    parser.description = "F270: Print DeepSourceRegistry catalog and exit"
    parser.add_argument(
        "--tier",
        type=str,
        default=None,
        choices=_TIER_CHOICES,
        help="Filter output by source tier",
    )
    return parser


def sprint_flags_from_args(args: argparse.Namespace):
    """Build a SprintFlags msgspec.Struct from parsed CLI args."""
    from hledac.universal.core.__main__ import SprintFlags

    return SprintFlags(
        force=getattr(args, "force", False),
        no_communication=getattr(args, "no_communication", False),
        no_stealth=getattr(args, "no_stealth", False),
        no_ghost=getattr(args, "no_ghost", False),
        no_coordination=getattr(args, "no_coordination", False),
        production=getattr(args, "production", False),
        hermes_force=getattr(args, "force_hermes", False),
    )
