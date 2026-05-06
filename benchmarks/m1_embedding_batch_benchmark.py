"""
Sprint F214OPT-F — M1 Embedding Batch Benchmark
===============================================

Measures docs/s, elapsed_ms, peak_rss_gib, swap_delta_gib, and failure/OOM flags
for embedding batch sizes [16, 24, 32, 48, 64] on M1 8GB.

Usage:
    # Dry-run (no MLX model load — synthetic data only)
    python benchmarks/m1_embedding_batch_benchmark.py --dry-run

    # Live benchmark (requires MLX model loaded)
    python benchmarks/m1_embedding_batch_benchmark.py --live

    # Custom sizes
    python benchmarks/m1_embedding_batch_benchmark.py --live --sizes 16 32 64

    # JSON output
    python benchmarks/m1_embedding_batch_benchmark.py --dry-run \
        --output-json probe_f214opt_mlx_batch/dry_run.json \
        --output-md probe_f214opt_mlx_batch/DRY_RUN.md
"""

from __future__ import annotations

import argparse
import asyncio
import gc
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Add project root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def get_rss_mb() -> float:
    """Get current RSS in MB."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024**2
    except Exception:
        return 0.0


def get_swap_used_mb() -> float:
    """Get swap used in MB."""
    try:
        import psutil
        swap = psutil.swap_memory()
        return swap.used / 1024**2
    except Exception:
        return 0.0


def synthetic_texts(n: int, seed: int = 42) -> list[str]:
    """Generate reproducible synthetic texts for hermetic benchmarking."""
    import random
    rng = random.Random(seed)
    templates = [
        "Security advisory: {topic} vulnerability in {component} allows {impact}",
        "Threat actor {actor} deploying {malware} via {vector} infrastructure",
        "OSINT indicator: {indicator} associated with {campaign} campaign",
        "Vulnerability CVE-{cve} affects {product} version {version}",
        "Malware family {family} observed using {protocol} C2 channel",
        "Attack chain: initial access via {vector}, then {lateral}, then {exfil}",
        "Infrastructure: {asn} {ip} hosting {category} content",
        "Credential dump: {count} {format} records from {source}",
        "Phishing campaign targeting {target} using {technique} delivery",
        "Ransomware strain {strain} using {encryption} with {key_mgmt}",
    ]
    topics = ["SQL injection", "XSS", "SSRF", "IDOR", "buffer overflow", "RCE", "LFI"]
    components = ["Apache", "nginx", "PostgreSQL", "Redis", "Kubernetes", "Docker"]
    impacts = ["data exfiltration", "remote code execution", "denial of service", "privilege escalation"]
    actors = ["APT29", "Lazarus", "FIN7", "Cozy Bear", "Sandworm", "Mockingbird"]
    malwares = ["CobaltStrike", "Metasploit", "Meterpreter", "AsyncRAT", "Vinacom", "R垄断"]
    vectors = ["phishing email", "watering hole", "supply chain", "zero-day", "exposed API"]
    indicators = ["192.168.1.1", "malicious.example.com", "ha32k9aencoded", "d41d8cd98f00b204"]
    campaigns = ["MIDNIGHT_FROST", "POLAR_STORM", "ARCTIC_WOLF", "SILENT_CHIMERA"]
    cves = ["2024-12345", "2024-54321", "2023-98765", "2023-11111"]
    products = ["WordPress", "Drupal", "Linux kernel", "OpenSSH", "glibc"]
    versions = ["5.1.2", "2.3.1", "4.9.2", "1.0.0", "3.14.159"]
    families = ["LockBit", "Conti", "BlackCat", "ALphasv", "DarkSide"]
    protocols = ["HTTPS", "DNS", "ICMP", " MQTT", "Tor"]
    lateral = ["PsExec", "WMI", "SMB", "SSH", "RDP"]
    exfil = ["HTTPS upload", "DNS tunnel", "ICMP exfil", "SMB copy"]
    asns = ["AS15169", "AS8075", "AS32934", "AS198571"]
    ips = ["8.8.8.8", "1.1.1.1", "140.82.112.4", "151.101.1.140"]
    categories = ["phishing", "malware", "C2", "exploit", "data"]
    counts = ["10k", "50k", "100k", "500k", "1M"]
    formats = [" bcrypt", "argon2", "PBKDF2", "SHA256", "plaintext"]
    sources = ["LinkedIn", "GitHub", "BreachForums", "dark web", "pastebin"]
    targets = ["finance", "healthcare", "government", "tech", "energy"]
    techniques = ["HTML attachment", "link", "QR code", "document", "archive"]
    strains = ["LockBit 3.0", "BlackCat", "Hive", "Revil", "Clop"]
    encryptions = ["RSA-4096", "AES-256", "ChaCha20", "hybrid", "custom"]
    key_mgmt = ["master key", "session key", "key server", "KDF"]
    vars_data = {
        "topic": topics, "component": components, "impact": impacts,
        "actor": actors, "malware": malwares, "vector": vectors,
        "indicator": indicators, "campaign": campaigns, "cve": cves,
        "product": products, "version": versions, "family": families,
        "protocol": protocols, "lateral": lateral, "exfil": exfil,
        "asn": asns, "ip": ips, "category": categories, "count": counts,
        "format": formats, "source": sources, "target": targets,
        "technique": techniques, "strain": strains, "encryption": encryptions,
        "key_mgmt": key_mgmt,
    }

    texts = []
    for _ in range(n):
        template = rng.choice(templates)
        text = template.format(**{k: rng.choice(v) for k, v in vars_data.items()})
        # Pad to realistic length (~200-400 chars)
        text = text + " | " + " ".join(rng.choice(templates).format(**{k: rng.choice(v) for k, v in vars_data.items()}) for _ in range(2))
        texts.append(text)
    return texts


async def benchmark_batch_dry_run(batch_size: int, n_items: int = 200) -> dict:
    """
    Dry-run benchmark: simulate batch timing without MLX model.

    Simulates encode delay proportional to batch_size to approximate
    real batching overhead.
    """
    texts = synthetic_texts(n_items)

    gc.collect()
    rss_before = get_rss_mb()
    swap_before = get_swap_used_mb()
    t0 = time.monotonic()

    # Simulate per-batch work (no real MLX)
    n_batches = (len(texts) + batch_size - 1) // batch_size
    for batch_idx in range(n_batches):
        start = batch_idx * batch_size
        end = min(start + batch_size, len(texts))
        batch = texts[start:end]
        # Simulate encode: small per-item delay (approx 2ms/item on M1)
        await asyncio.sleep(0.002 * len(batch))

    elapsed_ms = (time.monotonic() - t0) * 1000
    gc.collect()
    rss_after = get_rss_mb()
    swap_after = get_swap_used_mb()

    docs_per_sec = len(texts) / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

    return {
        "batch_size": batch_size,
        "docs": len(texts),
        "docs_per_sec": round(docs_per_sec, 2),
        "elapsed_ms": round(elapsed_ms, 2),
        "peak_rss_gib": round(max(rss_before, rss_after) / 1024, 3),
        "swap_delta_gib": round((swap_after - swap_before) / 1024, 3),
        "failure": False,
        "oom": False,
        "rss_before_mb": round(rss_before, 1),
        "rss_after_mb": round(rss_after, 1),
        "swap_before_mb": round(swap_before, 1),
        "swap_after_mb": round(swap_after, 1),
    }


async def benchmark_batch_live(batch_size: int, n_items: int = 200) -> dict:
    """
    Live benchmark: actually load MLX model and measure encoding.

    Returns dict with docs/s, elapsed_ms, peak RSS, swap delta, and OOM flags.
    """
    from hledac.universal.embedding_pipeline import (
        generate_embeddings_async,
        load_embedding_model,
        unload_embedding_model,
    )

    texts = synthetic_texts(n_items)

    gc.collect()
    rss_before = get_rss_mb()
    swap_before = get_swap_used_mb()
    t0 = time.monotonic()
    oom = False
    failure = False

    # Load model
    loaded = load_embedding_model()
    if not loaded:
        return {
            "batch_size": batch_size,
            "docs": len(texts),
            "docs_per_sec": 0,
            "elapsed_ms": 0,
            "peak_rss_gib": round(rss_before / 1024, 3),
            "swap_delta_gib": 0,
            "failure": True,
            "oom": False,
            "rss_before_mb": round(rss_before, 1),
            "rss_after_mb": round(rss_before, 1),
            "swap_before_mb": round(swap_before, 1),
            "swap_after_mb": round(swap_before, 1),
            "error": "model_load_failed",
        }

    try:
        embeddings = await generate_embeddings_async(texts, batch_size=batch_size)
        if embeddings is None or embeddings.shape[0] == 0:
            failure = True
    except Exception as e:
        failure = True
        error_str = str(e).lower()
        if "memory" in error_str or "oom" in error_str or "allocation" in error_str:
            oom = True
    finally:
        unload_embedding_model()
        gc.collect()

    elapsed_ms = (time.monotonic() - t0) * 1000
    rss_after = get_rss_mb()
    swap_after = get_swap_used_mb()

    docs_per_sec = len(texts) / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

    return {
        "batch_size": batch_size,
        "docs": len(texts),
        "docs_per_sec": round(docs_per_sec, 2),
        "elapsed_ms": round(elapsed_ms, 2),
        "peak_rss_gib": round(max(rss_before, rss_after) / 1024, 3),
        "swap_delta_gib": round((swap_after - swap_before) / 1024, 3),
        "failure": failure,
        "oom": oom,
        "rss_before_mb": round(rss_before, 1),
        "rss_after_mb": round(rss_after, 1),
        "swap_before_mb": round(swap_before, 1),
        "swap_after_mb": round(swap_after, 1),
    }


async def run_benchmark(sizes: list[int], dry_run: bool = False, n_items: int = 200) -> dict:
    """Run benchmark for all batch sizes and return results."""
    results = []
    for size in sizes:
        print(f"  Benchmarking batch_size={size}...", flush=True)
        if dry_run:
            result = await benchmark_batch_dry_run(size, n_items)
        else:
            result = await benchmark_batch_live(size, n_items)
        results.append(result)
        print(f"    docs/s={result['docs_per_sec']:.1f}, elapsed={result['elapsed_ms']:.0f}ms, "
              f"peak_rss={result['peak_rss_gib']:.3f}GB, oom={result['oom']}, failure={result['failure']}")
        # Brief pause between sizes
        await asyncio.sleep(1)
    return {"results": results, "sizes_tested": sizes, "dry_run": dry_run, "n_items": n_items}


def format_markdown(results: dict) -> str:
    """Format benchmark results as markdown table."""
    lines = [
        "# M1 Embedding Batch Benchmark",
        "",
        f"**Date**: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"**Mode**: {'dry-run (synthetic, no MLX model)' if results['dry_run'] else 'live (MLX model loaded)'}",
        f"**Items**: {results['n_items']}",
        "",
        "| batch_size | docs/s | elapsed_ms | peak_rss_GiB | swap_delta_GiB | OOM | failure |",
        "|------------|--------|------------|--------------|----------------|-----|---------|",
    ]
    for r in results["results"]:
        lines.append(
            f"| {r['batch_size']} | {r['docs_per_sec']:.1f} | {r['elapsed_ms']:.0f} | "
            f"{r['peak_rss_gib']:.3f} | {r['swap_delta_gib']:.4f} | {r['oom']} | {r['failure']} |"
        )
    lines.append("")
    # Best by throughput
    valid = [r for r in results["results"] if not r["failure"]]
    if valid:
        best = max(valid, key=lambda r: r["docs_per_sec"])
        lines.append(f"**Best throughput**: batch_size={best['batch_size']} at {best['docs_per_sec']:.1f} docs/s")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="M1 Embedding Batch Benchmark")
    parser.add_argument("--sizes", nargs="+", type=int, default=[16, 24, 32, 48, 64],
                        help="Batch sizes to test (default: 16 24 32 48 64)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Dry-run mode: synthetic data only, no MLX model load")
    parser.add_argument("--live", action="store_true",
                        help="Live benchmark: actually load MLX model (requires explicit flag)")
    parser.add_argument("--n-items", type=int, default=200,
                        help="Number of items to embed (default: 200)")
    parser.add_argument("--output-json", dest="output_json", metavar="PATH",
                        help="Write JSON results to file")
    parser.add_argument("--output-md", dest="output_md", metavar="PATH",
                        help="Write markdown report to file")
    args = parser.parse_args()

    if not args.live and not args.dry_run:
        print("ERROR: Must specify --dry-run or --live")
        print("  --dry-run: synthetic benchmark, no MLX model")
        print("  --live:    real MLX benchmark (will load model)")
        sys.exit(1)

    if args.live and args.dry_run:
        print("ERROR: Cannot specify both --dry-run and --live")
        sys.exit(1)

    print(f"\n=== M1 Embedding Batch Benchmark ===")
    print(f"Mode: {'DRY-RUN (synthetic)' if args.dry_run else 'LIVE (MLX model)'}")
    print(f"Sizes: {args.sizes}")
    print(f"Items: {args.n_items}")
    print()

    results = asyncio.run(run_benchmark(args.sizes, dry_run=args.dry_run, n_items=args.n_items))
    results["timestamp"] = datetime.now(timezone.utc).isoformat()
    results["python_version"] = f" {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # JSON output
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            json.dump(results, f, indent=2)
        print(f"\nJSON written to: {out_path}")

    # Markdown output
    if args.output_md:
        md = format_markdown(results)
        out_path = Path(args.output_md)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w") as f:
            f.write(md)
        print(f"Markdown written to: {out_path}")

    # Console summary
    print("\n=== Summary ===")
    valid = [r for r in results["results"] if not r["failure"]]
    if valid:
        for r in valid:
            status = "OOM" if r["oom"] else "OK"
            print(f"  batch={r['batch_size']:3d}  docs/s={r['docs_per_sec']:7.1f}  "
                  f"elapsed={r['elapsed_ms']:7.0f}ms  rss={r['peak_rss_gib']:.3f}GB  [{status}]")
    if results["dry_run"]:
        print("\n(Dry-run mode — no MLX model loaded)")


if __name__ == "__main__":
    main()