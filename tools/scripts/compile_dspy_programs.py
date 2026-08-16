#!/usr/bin/env python3
"""
scripts/compile_dspy_programs.py
=================================
Offline DSPy program compilation using local MLX LM (Hermes3) backend.

Compiles ``hypothesis_generator`` with 10 few-shot BootstrapFewShot
demonstrations and stores the compiled state in
``brain/compiled/{name}.json`` (the canonical project location, alongside
the source tree — easier to audit & diff than ``~/.hledac/dspy/``).

Usage
-----
::

    # Compile hypothesis_generator (default) with 10 few-shot examples
    python -m hledac.universal.scripts.compile_dspy_programs

    # Compile a different program
    python -m hledac.universal.scripts.compile_dspy_programs --program dark_query

    # Smoke check (no LM load, no MLX, no compiled file written)
    python -m hledac.universal.scripts.compile_dspy_programs --dry-run

    # Smoke check + write a placeholder compiled file (for CI)
    COMPILE_DSPY_WRITE_DRYRUN=1 \\
    python -m hledac.universal.scripts.compile_dspy_programs --dry-run

M1 INVARIANTS (enforced)
------------------------
* MLX is imported LAZY — ``import mlx.core as mx`` lives inside helper
  functions, never at module level.
* Metal cache limit is set in ``_init_mlx_buffers()`` to
  ``2_684_354_560`` bytes (2.5 GiB).
* ``mx.eval([])`` is invoked BEFORE ``mx.metal.clear_cache()`` in
  ``_clear_mlx_cache()`` — otherwise the clear is a no-op.
* KV cache ``kv_bits=4`` / ``max_kv_size=8192`` belong in
  ``mlx_lm.generate()``; this script does not call ``load()`` directly.
* Every system call (DSPy import, MLX import, file write, mutex acquire)
  is wrapped in ``try/except`` — fail-soft contract, returns 1 on any
  unrecoverable error.
* At most 10 few-shot examples are kept in memory (M1 RAM ceiling).

Fail-soft contract
------------------
Returns ``0`` on success, ``1`` on any unrecoverable error. The runner
never raises an unhandled exception.
"""


import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any
from _core import aclose

# Ensure repo root is on sys.path so ``hledac.universal.brain.dspy_programs``
# (and the rest of the package) is importable when the script is invoked
# directly as ``python scripts/compile_dspy_programs.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# NOTE: basicConfig removed — use utils.logging_config.configure_logging() for structured logging.
# This script uses stdlib logging since it's a standalone tool.
logger = logging.getLogger("compile_dspy_programs")

# ── Defaults ────────────────────────────────────────────────────────────────
DEFAULT_PROGRAM = "hypothesis_generator"
DEFAULT_NUM_EXAMPLES = 10
MAX_NUM_EXAMPLES = 10  # hard M1 RAM cap

# Project-relative canonical output location (alongside source).
_THIS_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = _THIS_DIR.parent / "brain" / "compiled"

# M1 invariant: 2.5 GiB Metal cache limit
METAL_CACHE_LIMIT_BYTES = 2_684_354_560

# Schema version for the compiled JSON file (forward-compat)
COMPILED_SCHEMA_VERSION = "hledac.dspy.compiled.v1"


# ── Few-shot training examples (deterministic, 10 max) ─────────────────────

def _build_hypothesis_trainset(num_examples: int = DEFAULT_NUM_EXAMPLES) -> list[dict[str, Any]]:
    """Build 10 OSINT research-query → hypothesis few-shot examples.

    Hand-curated for M1 RAM (each example < 4 KB). Covers domain
    recon, IP forensics, dark surface, leak patterns, threat-intel
    correlation, ASN / cert / graph pivots, paste / GitHub leaks, and
    S3 misconfig exposure.
    """
    examples: list[dict[str, Any]] = [
        {
            "research_query": "Investigate infrastructure behind evil-domain.ru linked to APT29 C2",
            "rag_context": "[doc1] BGP AS212238 hosting evil-domain.ru since 2024-Q1; [doc2] JARM fingerprint 27d3ed3ed3ed3ed27c43d3ed3ed3ed3ed3ed3ed3ed3ed3ed3ed3ed3ed3e matches FIN cluster",  # noqa: E501
            "graph_summary": "Graph: evil-domain.ru → AS212238 (3 edges) → 194.5.249.0/24 (5 hosts)",
            "reward_context": "Previous pivots: 0 high-confidence. Budget remaining: 8 queries.",
            "existing_hypotheses": ["Pivot to BGP peers of AS212238", "Scan 194.5.249.0/24 for additional hosts"],
            "hypotheses": "1. Search Shodan/Censys for 194.5.249.0/24 to enumerate hosts sharing the JARM fingerprint.\n2. Pivot to BGP announced prefixes of AS212238 for infrastructure expansion.\n3. Check passive DNS for sibling domains of evil-domain.ru registered in the same window.\n4. Query threat intel feeds (GreyNoise, AlienVault) for the JARM hash.\n5. Run CT log search for certs matching evil-domain.ru SAN entries.\n6. Probe AS212238 for adjacent ASNs (peers) hosting similar TTPs.\n7. Cross-reference IP-to-domain mappings from 2024-Q1 BGP events.",  # noqa: E501
        },
        {
            "research_query": "Analyze email pattern user@target-corp.com for breach correlation",
            "rag_context": "[doc1] Email found in 2 paste dumps (2025-Q3, 2025-Q4); [doc2] target-corp.com WHOIS: registered 2018-03, nameservers ns1.cloudflare.com",  # noqa: E501
            "graph_summary": "Graph: user@target-corp.com → 2 paste sites (1 edge each)",
            "reward_context": "Budget: 5 queries, mode=passive.",
            "existing_hypotheses": [],
            "hypotheses": "1. Query HaveIBeenPwned API for breach entries containing the email.\n2. Search paste sites (Pastebin, Ghostbin) for additional context around the email.\n3. Pivot to other emails in same paste dump for insider pattern.\n4. Check corporate naming convention: firstname.lastname@target-corp.com for harvesting.\n5. Cross-reference Cloudflare nameserver with other domains for cluster analysis.",  # noqa: E501
        },
        {
            "research_query": "Map Tor exit node 185.220.101.47 infrastructure to potential scanners",
            "rag_context": "[doc1] Tor exit list 2025-11-04 confirms 185.220.101.47; [doc2] GreyNoise tags: scanner, ssh_bruteforce",  # noqa: E501
            "graph_summary": "Graph: 185.220.101.47 → 12 destination ports (graph dense)",
            "reward_context": "Sprint mode: active. 6 queries remaining.",
            "existing_hypotheses": ["Check Shodan for historical banners"],
            "hypotheses": "1. Pull full Tor exit list for 185.220.101.0/24 to find sibling scanners.\n2. Query GreyNoise timeline for 185.220.101.47 to map activity windows.\n3. Cross-check AbuseIPDB for community-confirmed reports.\n4. Search Censys for cert fingerprints observed on the IP.\n5. Look up BGP origin AS for ASN-level pivots.\n6. Check if IP appears in any public scanner wordlists (e.g., projecthoneypot).",  # noqa: E501
        },
        {
            "research_query": "Investigate IPFS CID QmXxx... for hidden content distribution",
            "rag_context": "[doc1] CID first seen in 3 forum posts on dread.onion; [doc2] Pin count: 47, gateway access log shows 200 OK",  # noqa: E501
            "graph_summary": "Graph: QmXxx → 3 forum posts (3 edges) → 47 pin nodes",
            "reward_context": "Dark pivot lane active. 4 queries remaining.",
            "existing_hypotheses": ["Enumerate pin nodes"],
            "hypotheses": "1. Crawl IPFS gateways (ipfs.io, dweb.link, cloudflare-ipfs.com) for the CID content.\n2. Search the same CID on alternative indexes (Pinata, Infura) for metadata.\n3. Pivot to other CIDs referenced in the same forum threads.\n4. Check archive.org Wayback Machine for the gateway URLs.\n5. Map the 47 pin nodes to geolocate distribution.",  # noqa: E501
        },
        {
            "research_query": "Analyze certificate transparency log for subdomain takeover on *.target.io",
            "rag_context": "[doc1] CT log: 142 certs for *.target.io in 2025; [doc2] 3 certs reference dangling CNAMEs to deleted-cloud-services.io",  # noqa: E501
            "graph_summary": "Graph: *.target.io → 142 certs (3 dangling) → deleted-cloud-services.io",
            "reward_context": "Mode: research. No time pressure.",
            "existing_hypotheses": [],
            "hypotheses": "1. Register the 3 dangling CNAMEs to claim subdomains before attackers.\n2. Monitor CT logs in real-time (crt.sh RSS) for new *.target.io certs.\n3. Audit all DNS records for *.target.io for stale CNAMEs.\n4. Set up subdomain monitoring (e.g., can-i-take-over-xyz) rules.\n5. Check if any subdomains point to GitHub Pages / S3 / Heroku with claimable names.",  # noqa: E501
        },
        {
            "research_query": "Correlate ASN AS212238 with threat actor cluster activity",
            "rag_context": "[doc1] AS212238 announced 14 prefixes in 2025; [doc2] 8 prefixes overlap with known APT29 infrastructure",  # noqa: E501
            "graph_summary": "Graph: AS212238 → 14 prefixes (8 flagged) → APT29 cluster",
            "reward_context": "Tier-2 hunt. 10 queries remaining.",
            "existing_hypotheses": ["Pivot to APT29 history"],
            "hypotheses": "1. Cross-reference 8 flagged prefixes with Shodan/Censys for live services.\n2. Look up BGP history (RIPE RIS) for prefix transitions involving AS212238.\n3. Search threat intel reports (Mandiant, CrowdStrike) for AS212238 attribution.\n4. Check AbuseIPDB and Spamhaus for the 14 prefixes.\n5. Pivot to AS212238's upstream/downstream AS peers for similar patterns.\n6. Map the 6 unflagged prefixes to confirm they are benign (false negative check).",  # noqa: E501
        },
        {
            "research_query": "Investigate Pastebin paste https://pastebin.com/raw/abc123 for credential exposure",
            "rag_context": "[doc1] Paste contains 23 email:password combos; [doc2] 8 emails match corporate-domain.tld employees",  # noqa: E501
            "graph_summary": "Graph: paste → 23 cred pairs → 8 corporate emails",
            "reward_context": "Leak sentinel active. 3 queries remaining.",
            "existing_hypotheses": [],
            "hypotheses": "1. Notify affected employees through corporate security channel.\n2. Force password reset for the 8 corporate emails.\n3. Check if the paste contains additional pivotable data (API keys, internal URLs).\n4. Monitor paste sites for new dumps from the same actor (search by similar patterns).\n5. Cross-reference the 15 non-corporate emails to see if they share infrastructure with the corporate 8.",  # noqa: E501
        },
        {
            "research_query": "Map BGP announced prefixes for AS-tooling 64512 to active infrastructure",
            "rag_context": "[doc1] AS64512 announces 3 prefixes: 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24; [doc2] All 3 in documentation-reserved ranges (RFC 5737)",  # noqa: E501
            "graph_summary": "Graph: AS64512 → 3 doc prefixes (no production hosts)",
            "reward_context": "Low priority. 2 queries remaining.",
            "existing_hypotheses": [],
            "hypotheses": "1. Verify AS64512 is documentation-only via RFC 5737 range check.\n2. Check if AS64512 is being used as a decoy or honeypot AS.\n3. Search BGPStream for any anomalous announcements from AS64512.",  # noqa: E501
        },
        {
            "research_query": "Analyze GitHub repo github.com/leaked-org/internal-tool for secret exposure",
            "rag_context": "[doc1] Repo: 247 files, last commit 2025-10-15; [doc2] gitleaks scan: 14 hits (AWS keys, GitHub PATs, 2 DB strings)",  # noqa: E501
            "graph_summary": "Graph: repo → 14 secret hits → 3 cloud providers",
            "reward_context": "GitHub monitor active. 7 queries remaining.",
            "existing_hypotheses": [],
            "hypotheses": "1. Rotate the 14 detected secrets immediately and audit usage.\n2. Check git history for the secrets — were they ever pushed to main?\n3. Look for additional repos under leaked-org with similar patterns.\n4. Notify GitHub Trust & Safety for takedown.\n5. Audit IAM policies for the 3 cloud providers to check blast radius.\n6. Search GitHub Code Search for the same secret strings in other orgs.\n7. Check if any of the secrets were used to access production (CloudTrail, GitHub audit log).",  # noqa: E501
        },
        {
            "research_query": "Pivot from exposed S3 bucket corp-backups-2024 to broader AWS exposure",
            "rag_context": "[doc1] Bucket is public, contains 4.2TB of data; [doc2] Bucket policy shows IAM role 'backup-writer' from account 123456789012",  # noqa: E501
            "graph_summary": "Graph: corp-backups-2024 → 1 IAM role → 23 other buckets in account",
            "reward_context": "Asset exposure lane active. 9 queries remaining.",
            "existing_hypotheses": [],
            "hypotheses": "1. List all 23 other buckets in the account via S3 API (using public enumeration).\n2. Check if the 4.2TB contains PII (run keyword scan with grep-like patterns).\n3. Pivot to the 'backup-writer' IAM role for privilege escalation paths.\n4. Look up bucket name pattern in GrayhatWarfare / S3Hunter for similar misconfigs.\n5. Check CloudTrail (if accessible) for unauthorized access to this bucket.\n6. Enumerate account 123456789012 for other public assets (EBS snapshots, AMIs, RDS).\n7. Set up ongoing monitoring (e.g., AWS Macie) for the entire account.",  # noqa: E501
        },
    ]
    return examples[: max(1, min(num_examples, MAX_NUM_EXAMPLES))]


# ── M1 MLX buffer init / cleanup ───────────────────────────────────────────

def _init_mlx_buffers() -> None:
    """Initialize Metal cache limit per M1 invariant.

    INVARIANTS:
      * MLX import lives inside this function (not at module level).
      * ``set_cache_limit`` MUST be ``2_684_354_560`` bytes (2.5 GiB).
    """
    try:
        import mlx.core as mx  # type: ignore[import-not-found]  # INVARIANT: lazy import
        if mx.metal.is_available():
            mx.metal.set_cache_limit(METAL_CACHE_LIMIT_BYTES)
            logger.debug(
                "MLX Metal cache limit set to %.2f GiB",
                METAL_CACHE_LIMIT_BYTES / 2**30,
    )
    except Exception as e:  # fail-soft
        logger.debug("MLX buffer init skipped: %s", e)


def _clear_mlx_cache() -> None:
    """Clear Metal cache after DSPy compilation.

    INVARIANT: ``mx.eval([])`` MUST run BEFORE ``mx.metal.clear_cache()``,
    otherwise the clear is a no-op (per M1 CRITICAL INVARIANTS).
    """
    try:
        import mlx.core as mx  # type: ignore[import-not-found]  # INVARIANT: lazy import
        if mx.metal.is_available():
            mx.eval([])              # barrier BEFORE clear
            mx.metal.clear_cache()   # now the clear is real
            logger.debug("MLX Metal cache cleared (eval → clear_cache)")
    except Exception as e:  # fail-soft
        logger.debug("MLX cache clear skipped: %s", e)


# ── DSPy helpers (lazy imports, fail-soft) ─────────────────────────────────

def _check_dspy() -> bool:
    """Return True if ``dspy`` is importable (top-level)."""
    try:
        import importlib.util
        return importlib.util.find_spec("dspy") is not None
    except Exception:
        return False


def _configure_dspy_with_mlx() -> Any | None:
    """Configure DSPy with ``Hermes3DSPyLM`` (MLX backend).

    INVARIANT: MLX / Hermes3 / ANE-mutex are all imported lazily inside
    this function (never at module level). Returns the configured
    ``dspy`` module on success, ``None`` on any failure (fail-soft).
    """
    try:
        import dspy  # type: ignore[import-not-found]

        # Lazy import: avoids loading Hermes3 / MLX at module import
        from hledac.universal.brain.dspy_service import Hermes3DSPyLM  # type: ignore[import-not-found]
        try:
            from hledac.universal.brain.ane_embedder import get_ane_mlx_mutex  # type: ignore[import-not-found]
            mutex = get_ane_mlx_mutex()
            mutex.acquire_mlx(model_size_mb=2000.0)
        except Exception as e:
            # Fail-soft: ANE mutex is best-effort
            logger.debug("ANE/MLX mutex acquire skipped: %s", e)

        # Init MLX buffers BEFORE building the LM
        _init_mlx_buffers()

        lm = Hermes3DSPyLM(model_path=os.getenv("HLEDAC_LLM_MODEL"))
        dspy.configure(lm=lm)
        logger.info("DSPy configured with Hermes3DSPyLM (MLX backend)")
        return dspy
    except Exception as e:
        logger.warning("DSPy MLX configuration failed: %s", e)
        return None


def _build_program(program_name: str) -> Any | None:
    """Build an uncompiled DSPy program by name (fail-soft)."""
    try:
        from hledac.universal.brain.dspy_programs import (  # type: ignore[import-not-found]
            DarkQueryProgram,
            HypothesisGeneratorProgram,
            HypothesisRankProgram,
    )
        registry = {
            "dark_query": DarkQueryProgram,
            "hypothesis_generator": HypothesisGeneratorProgram,
            "hypothesis_ranker": HypothesisRankProgram,
        }
        cls = registry.get(program_name)
        if cls is None:
            logger.error(
                "Unknown program: %s. Known: %s",
                program_name, sorted(registry.keys()),
    )
            return None
        return cls()
    except Exception as e:
        logger.warning("Failed to build program %s: %s", program_name, e)
        return None


def _compile_with_few_shot(
    dspy_module: Any,
    program: Any,
    trainset: list[dict[str, Any]],
    program_name: str,
) -> Any | None:
    """Compile ``program`` with ``BootstrapFewShot`` (fail-soft)."""
    try:
        from dspy.teleprompt import BootstrapFewShot  # type: ignore[import-not-found]

        examples = []
        for ex in trainset:
            example = dspy_module.Example(**ex).with_inputs(
                "research_query",
                "rag_context",
                "graph_summary",
                "reward_context",
                "existing_hypotheses",
    )
            examples.append(example)
        examples = examples[:MAX_NUM_EXAMPLES]

        # Trivial metric — we only want the few-shot demonstrations baked in.
        # Accepts any DSPy metric signature (example, pred, trace) but ignores
        # all inputs: every example scores 1.0, so BootstrapFewShot keeps them
        # all as demos. Parameters are intentionally unused.
        def _trivial_metric(*args: object, **kw: object) -> float:  # type: ignore
            del args, kw
            return 1.0

        teleprompter = BootstrapFewShot(metric=_trivial_metric)

        logger.info(
            "Compiling %s with BootstrapFewShot (%d examples)...",
            program_name, len(examples),
    )
        compiled = teleprompter.compile(program=program, trainset=examples)
        n_demos = len(getattr(compiled, "demos", []) or [])
        logger.info("Compilation complete: %d demos baked in", n_demos)
        return compiled
    except Exception as e:
        logger.warning("BootstrapFewShot compilation failed: %s", e)
        return None


def _extract_demos(compiled_program: Any) -> list[dict[str, Any]]:
    """Serialize ``compiled_program.demos`` to plain dicts."""
    try:
        demos = getattr(compiled_program, "demos", []) or []
        extracted: list[dict[str, Any]] = []
        for demo in demos:
            try:
                if hasattr(demo, "toDict"):
                    extracted.append(dict(demo.toDict()))
                elif hasattr(demo, "__dict__"):
                    extracted.append({k: v for k, v in demo.__dict__.items()
                                      if not k.startswith("_")})
                else:
                    extracted.append({
                        k: getattr(demo, k, "")
                        for k in ("research_query", "rag_context", "graph_summary",
                                  "reward_context", "existing_hypotheses", "hypotheses")
                    })
            except Exception:
                continue
        return extracted
    except Exception:
        return []


def _save_compiled_state(
    output_dir: Path,
    program_name: str,
    demos: list[dict[str, Any]],
    metadata: dict[str, Any],
) -> Path | None:
    """Atomically write compiled program state to disk.

    INVARIANT: write to ``.json.tmp`` first, then ``replace()`` to
    avoid torn writes on crash.
    """
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{program_name}.json"
        state = {
            "schema": COMPILED_SCHEMA_VERSION,
            "name": program_name,
            "version": "1.0",
            "metadata": metadata,
            "demos": demos,
        }
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False))
        tmp.replace(path)  # atomic on POSIX
        logger.info("Saved compiled program: %s (%d demos, %d bytes)",
                    path, len(demos), path.stat().st_size)
        return path
    except Exception as e:
        logger.warning("Failed to save compiled program: %s", e)
        return None


# ── Main pipeline ───────────────────────────────────────────────────────────

def compile_program(
    program_name: str = DEFAULT_PROGRAM,
    num_examples: int = DEFAULT_NUM_EXAMPLES,
    output_dir: Path | None = None,
    dry_run: bool = False,
) -> int:
    """Compile a DSPy program with few-shot examples using local MLX LM.

    Returns ``0`` on success, ``1`` on any failure (fail-soft contract).
    """
    output_dir = output_dir or DEFAULT_OUTPUT_DIR
    logger.info("=== DSPy compilation pipeline ===")
    logger.info(
        "program=%s examples=%d output=%s dry_run=%s",
        program_name, num_examples, output_dir, dry_run,
    )

    if dry_run:
        return _dry_run(program_name, num_examples, output_dir)

    # 1. DSPy must be installed
    if not _check_dspy():
        logger.error("DSPy not installed. Run: uv pip install dspy-ai")
        return 1

    # 2. Configure DSPy with Hermes3DSPyLM (MLX backend)
    dspy = _configure_dspy_with_mlx()
    if dspy is None:
        logger.error("Failed to configure DSPy with MLX backend")
        return 1

    try:
        # 3. Build trainset (deterministic, bounded)
        trainset = _build_hypothesis_trainset(num_examples)
        logger.info("Built trainset: %d examples", len(trainset))

        # 4. Build the uncompiled program
        program = _build_program(program_name)
        if program is None:
            logger.error("Failed to build program: %s", program_name)
            return 1

        # 5. Compile with BootstrapFewShot (calls LM internally)
        compiled = _compile_with_few_shot(dspy, program, trainset, program_name)
        if compiled is None:
            logger.error("Compilation failed for %s", program_name)
            return 1

        # 6. Extract demos and persist
        demos = _extract_demos(compiled)
        metadata = {
            "compiler": "BootstrapFewShot",
            "num_examples": len(trainset),
            "num_demos": len(demos),
            "lm_backend": "Hermes3DSPyLM (MLX)",
            "model_id": os.getenv(
                "HLEDAC_LLM_MODEL", "hermes-3-llama-3.2-3b-4bit",
            ),
        }
        path = _save_compiled_state(output_dir, program_name, demos, metadata)
        if path is None:
            return 1
        logger.info("=== Compilation complete: %s ===", path)
        return 0
    finally:
        # INVARIANT: clear Metal cache (mx.eval([]) BEFORE clear_cache)
        _clear_mlx_cache()


def _dry_run(
    program_name: str,
    num_examples: int,
    output_dir: Path,
) -> int:
    """Smoke check: verify imports & structure without loading LM/DSPy/MLX."""
    logger.info("--- DRY RUN ---")

    has_dspy = _check_dspy()
    logger.info("DSPy installed: %s", has_dspy)

    trainset = _build_hypothesis_trainset(num_examples)
    schema = sorted(trainset[0].keys()) if trainset else []
    logger.info("Trainset: %d examples, schema=%s", len(trainset), schema)

    # Verify program module is importable (does not instantiate any class).
    # We use __import__ to avoid Pyright flagging unused symbols while still
    # failing fast if hledac.universal.brain.dspy_programs is unreachable.
    try:
        import importlib
        _mod = importlib.import_module("hledac.universal.brain.dspy_programs")  # type: ignore
        _has_programs = all(
            hasattr(_mod, name)
            for name in ("DarkQueryProgram", "HypothesisGeneratorProgram", "HypothesisRankProgram")
    )
        logger.info(
            "Program registry for '%s': resolved=%s",
            program_name,
            _has_programs,
    )
    except Exception as e:
        logger.warning("Could not import program classes: %s", e)

    if os.getenv("COMPILE_DSPY_WRITE_DRYRUN") == "1":
        metadata = {
            "compiler": "BootstrapFewShot",
            "num_examples": len(trainset),
            "num_demos": 0,
            "lm_backend": "DRY_RUN_NO_LM",
            "model_id": "DRY_RUN",
            "dry_run": True,
        }
        path = _save_compiled_state(output_dir, program_name, [], metadata)
        if path:
            logger.info("Dry-run placeholder written: %s", path)
    else:
        logger.info("Set COMPILE_DSPY_WRITE_DRYRUN=1 to write placeholder")

    logger.info("--- DRY RUN OK ---")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compile DSPy programs using local MLX LM backend.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--program",
        default=DEFAULT_PROGRAM,
        choices=["hypothesis_generator", "dark_query", "hypothesis_ranker"],
        help=f"Program to compile (default: {DEFAULT_PROGRAM})",
    )
    parser.add_argument(
        "--num-examples",
        type=int,
        default=DEFAULT_NUM_EXAMPLES,
        help=f"Few-shot examples (default: {DEFAULT_NUM_EXAMPLES}, M1 max 10)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=f"Output dir (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Verify structure; do NOT load LM/DSPy/MLX",
    )
    args = parser.parse_args()
    return compile_program(
        program_name=args.program,
        num_examples=args.num_examples,
        output_dir=args.output,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    sys.exit(main())
