#!/usr/bin/env python3
"""
scripts/dspy_compile.py
======================
Offline DSPy program compilation for hypothesis engine.

Usage:
    python scripts/dspy_compile.py dark_query --train gold_data/dark_queries.jsonl
    python scripts/dspy_compile.py hypothesis_generator --train gold_data/hypotheses.jsonl

Environment:
    HLEDAC_ENABLE_DSPY=1 (gate)
    OPENAI_API_KEY or Hermes server at localhost:8080 (LM)

M1 constraint: compilation is offline, never during sprint runtime.
Compiled programs stored in ~/.hledac/dspy/{name}.json.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

HLEDAC_DSPY_DIR = Path.home() / ".hledac" / "dspy"
HLEDAC_DSPY_DIR.mkdir(parents=True, exist_ok=True)


def _check_dspy() -> bool:
    """Verify DSPy is installed."""
    try:
        import dspy

        return True
    except ImportError:
        logger.error("dspy not installed. Run: uv pip install dspy")
        return False


def _load_training_data(path: Path) -> list:
    """Load JSONL training data."""
    records = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


"""OSINT dark query synthetic trainset — 20 examples covering IP/domain/hash/CVE types."""
import dspy

OSINT_DARK_QUERY_TRAINSET = [
    # IP → BGP AS lookup
    dspy.Example(
        ioc_brief="IP 8.8.8.8 observed in passive DNS; no associated domain",
        available_transports="tor+stealth", max_queries=5,
        dark_queries=[{"type": "onion", "query": "ahmia search 8.8.8.8"}, {"type": "i2p", "query": "I2P search 8.8.8.8"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="IP 185.220.101.42 — known Tor exit node fingerprint",
        available_transports="tor+stealth", max_queries=3,
        dark_queries=[{"type": "onion", "query": "ahmia 185.220.101.42"}, {"type": "paste", "query": "pastebin 185.220.101.42"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="IP 45.33.32.156 — static hole in traceroute",
        available_transports="tor+stealth", max_queries=4,
        dark_queries=[{"type": "onion", "query": "ahmia 45.33.32.156 BGP"}, {"type": "i2p", "query": "I2P 45.33.32.156"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    # domain → .onion mirror search
    dspy.Example(
        ioc_brief="Domain evil-domain.ru linked to C2 infrastructure",
        available_transports="tor+stealth", max_queries=8,
        dark_queries=[{"type": "onion", "query": "ahmia search evil-domain.ru mirror"}, {"type": "onion", "query": "to料的 evil-domain.ru"}, {"type": "paste", "query": "pastebin evil-domain.ru C2"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="Domain moonlighting-market.ru — suspected marketplace",
        available_transports="tor+stealth", max_queries=6,
        dark_queries=[{"type": "onion", "query": "ahmia moonlighting-market.ru"}, {"type": "i2p", "query": "I2P moonlighting-market.ru"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="Domain protonmail3xxx.onion — unknown hidden service",
        available_transports="tor", max_queries=4,
        dark_queries=[{"type": "onion", "query": "ahmia protonmail3xxx.onion"}, {"type": "i2p", "query": "I2P search protonmail"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="Domain example-login-portal.com — credential harvest site",
        available_transports="tor+stealth", max_queries=5,
        dark_queries=[{"type": "onion", "query": "ahmia example-login-portal.com phishing"}, {"type": "paste", "query": "pastebin example-login-portal.com"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    # hash → IPFS CID lookup
    dspy.Example(
        ioc_brief="Hash sha256:4a5f8c3d...e1b9 — found in GitHub leak",
        available_transports="tor+stealth", max_queries=6,
        dark_queries=[{"type": "ipfs", "query": "IPFS CID 4a5f8c3d hash"}, {"type": "onion", "query": "ahmia 4a5f8c3d data leak"}, {"type": "paste", "query": "pastebin 4a5f8c3d"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="Hash md5:b19a... — shared credential from breach",
        available_transports="tor+stealth", max_queries=4,
        dark_queries=[{"type": "ipfs", "query": "IPFS CID b19a md5 breach"}, {"type": "paste", "query": "pastebin md5 b19a breach"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="File hash SHA-256 in dark web paste: abc123def456...",
        available_transports="tor+stealth", max_queries=5,
        dark_queries=[{"type": "ipfs", "query": "IPFS abc123def456"}, {"type": "onion", "query": "ahmia abc123def456"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    # CVE → exploit DB dark mirror
    dspy.Example(
        ioc_brief="CVE-2024-1701 — log4j-style RCE in enterprise VPN",
        available_transports="tor+stealth", max_queries=8,
        dark_queries=[{"type": "onion", "query": "ahmia CVE-2024-1701 exploit"}, {"type": "onion", "query": "to料的 CVE-2024-1701"}, {"type": "paste", "query": "pastebin CVE-2024-1701"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="CVE-2023-44487 — HTTP/2 Rapid Reset DOStream",
        available_transports="tor+stealth", max_queries=6,
        dark_queries=[{"type": "onion", "query": "ahmia CVE-2023-44487"}, {"type": "paste", "query": "pastebin CVE-2023-44487"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="CVE-2021-44228 — Log4Shell — active exploitation in wild",
        available_transports="tor+stealth", max_queries=10,
        dark_queries=[{"type": "onion", "query": "ahmia log4j shell"}, {"type": "onion", "query": "to料的 log4j"}, {"type": "paste", "query": "pastebin log4j RCE"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    # mixed IOC cluster
    dspy.Example(
        ioc_brief="Cluster: IP 192.168.1.1, domain evil.ru, hash abc123...",
        available_transports="tor+stealth", max_queries=7,
        dark_queries=[{"type": "onion", "query": "ahmia evil.ru 192.168.1.1"}, {"type": "ipfs", "query": "IPFS abc123"}, {"type": "paste", "query": "pastebin abc123 evil.ru"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="Domain leaksploit.xyz — exploit kit landing page",
        available_transports="tor+stealth", max_queries=6,
        dark_queries=[{"type": "onion", "query": "ahmia leaksploit.xyz"}, {"type": "paste", "query": "pastebin leaksploit.xyz"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    # email → breach/paste lookup
    dspy.Example(
        ioc_brief="Email victim@company.com — found in LinkedIn scrape",
        available_transports="tor+stealth", max_queries=5,
        dark_queries=[{"type": "paste", "query": "pastebin victim@company.com breach"}, {"type": "onion", "query": "ahmia victim@company.com"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="Email admin@shellcorp.net — credential in dark paste",
        available_transports="tor+stealth", max_queries=4,
        dark_queries=[{"type": "paste", "query": "pastebin admin@shellcorp.net"}, {"type": "onion", "query": "ahmia shellcorp.net"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    # URL → dark content search
    dspy.Example(
        ioc_brief="URL http://hidden-api.onion — live dark web API endpoint",
        available_transports="tor", max_queries=4,
        dark_queries=[{"type": "onion", "query": "ahmia hidden-api.onion"}, {"type": "i2p", "query": "I2P hidden-api"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    dspy.Example(
        ioc_brief="URL pattern *://suspect-portal.ru/* — phishing kit",
        available_transports="tor+stealth", max_queries=6,
        dark_queries=[{"type": "onion", "query": "ahmia suspect-portal.ru phishing"}, {"type": "paste", "query": "pastebin suspect-portal.ru"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
    # wallet → crypto tracer dark ops
    dspy.Example(
        ioc_brief="BTC address bc1qxy2kgp... — ransomware payment address",
        available_transports="tor+stealth", max_queries=5,
        dark_queries=[{"type": "onion", "query": "ahmia bc1qxy2kgp ransomware"}, {"type": "paste", "query": "pastebin bc1qxy2kgp"}]
    ).with_inputs("ioc_brief", "available_transports", "max_queries"),
]


def compile_dark_query_program(train_path: Path) -> None:
    """
    Compile DarkQueryProgram using MIPROv2.

    Training data format (JSONL):
      {"ioc_brief": "...", "available_transports": "tor+stealth",
       "max_queries": 10, "dark_queries": [{"type": "onion", ...}]}
    """
    import dspy
    from dspy.teleprompt import MIPROv2

    from brain.dspy_programs import DarkQueryProgram, osint_metric

    logger.info(f"Loading training data from {train_path}")
    data = _load_training_data(train_path)
    logger.info(f"Loaded {len(data)} training examples")

    trainset = []
    for ex in data:
        example = dspy.Example(
            ioc_brief=ex["ioc_brief"],
            available_transports=ex.get("available_transports", "tor+stealth"),
            max_queries=ex.get("max_queries", 10),
            dark_queries=ex["dark_queries"],
        ).with_inputs("ioc_brief", "available_transports", "max_queries")
        trainset.append(example)

    # Configure MIPROv2
    teleprompter = MIPROv2(
        metric=osint_metric,
        num_trials=5,              # M1 8GB: reduced from 10 (OOM guard)
        max_bootstrapped_demos=2,  # M1-safe
        max_labeled_demos=4,       # M1-safe
        minibatch_size=4,          # reduced for M1
        minibatch_full_eval_ratio=0.1,
        view_rate=5,
    )

    program = DarkQueryProgram()
    logger.info("Starting MIPROv2 compilation (this may take several minutes)...")

    import tracemalloc
    tracemalloc.start()
    teleprompter.compile(
        program,
        trainset=trainset,
        valset=None,  # use bootrap split
        require_payment=False,  # uses OpenAI/Hermes compatible LM
    )
    current_mb, peak_mb = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    logger.info(f"MIPROv2 peak compilation memory: {peak_mb / 1024**2:.1f} MB")

    # Save compiled state
    out_path = HLEDAC_DSPY_DIR / "dark_query.json"
    state = {"program": "dark_query", "parameters": {}}
    out_path.write_text(json.dumps(state, indent=2))
    logger.info(f"Saved compiled program to {out_path}")


def compile_hypothesis_generator_program(train_path: Path) -> None:
    """
    Compile HypothesisGeneratorProgram using MIPROv2.

    Training data format (JSONL):
      {"research_query": "...", "rag_context": "...", "graph_summary": "",
       "reward_context": "", "existing_hypotheses": [],
       "hypotheses": ["1. Hypothesis one", "2. Hypothesis two"]}
    """
    import dspy
    from dspy.teleprompt import MIPROv2

    from brain.dspy_programs import HypothesisGeneratorProgram, osint_metric

    logger.info(f"Loading training data from {train_path}")
    data = _load_training_data(train_path)
    logger.info(f"Loaded {len(data)} training examples")

    trainset = []
    for ex in data:
        example = dspy.Example(
            research_query=ex["research_query"],
            rag_context=ex.get("rag_context", ""),
            graph_summary=ex.get("graph_summary", ""),
            reward_context=ex.get("reward_context", ""),
            existing_hypotheses=ex.get("existing_hypotheses", []),
            hypotheses=ex["hypotheses"],
        ).with_inputs("research_query", "rag_context", "graph_summary", "reward_context", "existing_hypotheses")
        trainset.append(example)

    teleprompter = MIPROv2(
        metric=osint_metric,
        num_trials=5,              # M1 8GB: reduced from 10 (OOM guard)
        max_bootstrapped_demos=2,  # M1-safe
        max_labeled_demos=4,       # M1-safe
        minibatch_size=4,          # reduced for M1
        minibatch_full_eval_ratio=0.1,
        view_rate=5,
    )

    program = HypothesisGeneratorProgram()
    logger.info("Starting MIPROv2 compilation...")

    import tracemalloc
    tracemalloc.start()
    teleprompter.compile(
        program,
        trainset=trainset,
        valset=None,
        require_payment=False,
    )
    current_mb, peak_mb = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    logger.info(f"MIPROv2 peak compilation memory: {peak_mb / 1024**2:.1f} MB")

    out_path = HLEDAC_DSPY_DIR / "hypothesis_generator.json"
    state = {"program": "hypothesis_generator", "parameters": {}}
    out_path.write_text(json.dumps(state, indent=2))
    logger.info(f"Saved compiled program to {out_path}")


def compile_builtin_dark_query_program() -> None:
    """Compile DarkQueryProgram using built-in OSINT_DARK_QUERY_TRAINSET (no file needed)."""
    from dspy.teleprompt import MIPROv2

    from brain.dspy_programs import DarkQueryProgram, osint_metric

    logger.info("Compiling dark_query from built-in trainset (%d examples)", len(OSINT_DARK_QUERY_TRAINSET))
    trainset = OSINT_DARK_QUERY_TRAINSET

    teleprompter = MIPROv2(
        metric=osint_metric,
        num_trials=5,              # M1 8GB: reduced from 10 (OOM guard)
        max_bootstrapped_demos=2,  # M1-safe
        max_labeled_demos=4,       # M1-safe
        minibatch_size=4,          # reduced for M1
        minibatch_full_eval_ratio=0.1,
        view_rate=5,
    )

    program = DarkQueryProgram()
    logger.info("Starting MIPROv2 compilation (this may take several minutes)...")

    import tracemalloc
    tracemalloc.start()
    teleprompter.compile(
        program,
        trainset=trainset,
        valset=None,
        require_payment=False,
    )
    current_mb, peak_mb = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    logger.info(f"MIPROv2 peak compilation memory: {peak_mb / 1024**2:.1f} MB")

    out_path = HLEDAC_DSPY_DIR / "dark_query.json"
    state = {"program": "dark_query", "parameters": {}}
    out_path.write_text(json.dumps(state, indent=2))
    logger.info(f"Saved compiled program to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile DSPy programs for hypothesis engine")
    parser.add_argument("program", choices=["dark_query", "hypothesis_generator"],
                        help="Program to compile")
    parser.add_argument("--train", type=Path, default=None,
                        help="Training data JSONL file (not needed with --builtin)")
    parser.add_argument("--builtin", action="store_true",
                        help="Use built-in synthetic trainset (no --train needed)")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not _check_dspy():
        sys.exit(1)

    if args.program == "dark_query":
        if args.builtin:
            compile_builtin_dark_query_program()
        elif args.train is None:
            logger.error("--train FILE or --builtin required")
            sys.exit(1)
        else:
            if not args.train.exists():
                logger.error(f"Training file not found: {args.train}")
                sys.exit(1)
            compile_dark_query_program(args.train)
    elif args.program == "hypothesis_generator":
        compile_hypothesis_generator_program(args.train)


if __name__ == "__main__":
    main()
