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
import os
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


def compile_dark_query_program(train_path: Path) -> None:
    """
    Compile DarkQueryProgram using MIPROv2.

    Training data format (JSONL):
      {"ioc_brief": "...", "available_transports": "tor+stealth",
       "max_queries": 10, "dark_queries": [{"type": "onion", ...}]}
    """
    import dspy
    from dspy.teleprompt import MIPROv2

    from brain.dspy_programs import DarkQuerySignature, DarkQueryProgram, osint_metric

    logger.info(f"Loading training data from {train_path}")
    data = _load_training_data(train_path)
    logger.info(f"Loaded {len(data)} training examples")

    trainset = []
    for ex in data:
        from brain.dspy_programs import DarkQuerySignature
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
        num_trials=10,
        max_bootstrapped_demos=4,
        max_labeled_demos=8,
        minibatch_size=8,
        minibatch_full_eval_ratio=0.1,
        view_rate=5,
    )

    program = DarkQueryProgram()
    logger.info("Starting MIPROv2 compilation (this may take several minutes)...")

    compiled = teleprompter.compile(
        program,
        trainset=trainset,
        valset=None,  # use bootrap split
        require_payment=False,  # uses OpenAI/Hermes compatible LM
    )

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

    from brain.dspy_programs import HypothesisGeneratorSignature, HypothesisGeneratorProgram, osint_metric

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
        num_trials=10,
        max_bootstrapped_demos=4,
        max_labeled_demos=8,
        minibatch_size=8,
        minibatch_full_eval_ratio=0.1,
        view_rate=5,
    )

    program = HypothesisGeneratorProgram()
    logger.info("Starting MIPROv2 compilation...")

    compiled = teleprompter.compile(
        program,
        trainset=trainset,
        valset=None,
        require_payment=False,
    )

    out_path = HLEDAC_DSPY_DIR / "hypothesis_generator.json"
    state = {"program": "hypothesis_generator", "parameters": {}}
    out_path.write_text(json.dumps(state, indent=2))
    logger.info(f"Saved compiled program to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile DSPy programs for hypothesis engine")
    parser.add_argument("program", choices=["dark_query", "hypothesis_generator"],
                        help="Program to compile")
    parser.add_argument("--train", type=Path, required=True,
                        help="Training data JSONL file")
    parser.add_argument("--verbose", action="store_true")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    if not _check_dspy():
        sys.exit(1)

    if not args.train.exists():
        logger.error(f"Training file not found: {args.train}")
        sys.exit(1)

    if args.program == "dark_query":
        compile_dark_query_program(args.train)
    elif args.program == "hypothesis_generator":
        compile_hypothesis_generator_program(args.train)


if __name__ == "__main__":
    main()