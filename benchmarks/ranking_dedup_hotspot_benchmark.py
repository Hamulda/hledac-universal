#!/usr/bin/env python3
"""
Ranking & Deduplication Hotspot Benchmark

Measures O(n²) complexity hotspots in ranking duplicate removal and dedup similarity loops.
Synthetic data: ranked results with duplicate clusters by URL/title/text.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

from utils.ranking import RankedResult, ReciprocalRankFusion, RRFConfig


@dataclass
class BenchmarkResult:
    size: int
    time_seconds: float
    dedup_count: int
    output_count: int


def make_result(idx: int, url: str | None = None, title: str = "", content: str = "") -> RankedResult:
    return RankedResult(
        id=f"result_{idx}",
        title=title or f"Result {idx} Title",
        content=content or f"Content for result {idx}",
        url=url,
        source="benchmark",
        score=1.0 / idx if idx > 0 else 1.0,
    )


def make_duplicate_cluster(base_idx: int, cluster_size: int, vary_content: bool = True) -> list[RankedResult]:
    """Create a cluster of near-duplicate results."""
    results = []
    base_url = f"https://example.com/page{base_idx}"
    base_title = f"Article {base_idx} Headline"
    base_content = f"This is the content for article {base_idx} with some unique identifier {base_idx}"

    for i in range(cluster_size):
        if vary_content:
            content = f"{base_content} variant {i}"
            title = f"{base_title} - Version {i}"
        else:
            content = base_content
            title = base_title
        results.append(make_result(
            base_idx * 10 + i,
            url=base_url if i == 0 else f"{base_url}?v={i}",
            title=title,
            content=content,
        ))
    return results


def build_synthetic_dataset(size: int, dup_ratio: float = 0.3) -> list[RankedResult]:
    """Build synthetic ranked results with controlled duplicates."""
    results: list[RankedResult] = []
    dup_cluster_size = max(2, int(size * dup_ratio / 3))
    num_clusters = max(1, int(size * dup_ratio / dup_cluster_size))

    # Unique results
    unique_count = size - (num_clusters * dup_cluster_size)
    for i in range(unique_count):
        results.append(make_result(
            i,
            url=f"https://unique.example.com/item{i}",
            title=f"Unique Document {i}",
            content=f"This is unique content for document {i} with ID {i}",
        ))

    # Duplicate clusters
    for c in range(num_clusters):
        cluster = make_duplicate_cluster(1000 + c, dup_cluster_size, vary_content=True)
        results.extend(cluster)

    # Shuffle to simulate real-world ordering
    import random
    random.seed(42)
    random.shuffle(results)

    # Re-rank by score
    for i, r in enumerate(results):
        r.score = 1.0 / (i + 1)
        r.rank = i + 1

    return results


def benchmark_remove_duplicates(sizes: list[int], num_runs: int = 3) -> list[BenchmarkResult]:
    """Benchmark _remove_duplicates at multiple sizes."""
    results = []
    config = RRFConfig(deduplication=True, dedup_threshold=0.85)

    for size in sizes:
        times = []
        total_deduped = 0
        total_output = 0

        for _run in range(num_runs):
            data = build_synthetic_dataset(size, dup_ratio=0.3)
            rrf = ReciprocalRankFusion(config)

            start = time.perf_counter()
            deduped = rrf._remove_duplicates(data)
            elapsed = time.perf_counter() - start

            times.append(elapsed)
            total_deduped += len(data) - len(deduped)
            total_output += len(deduped)

        avg_time = sum(times) / len(times)
        results.append(BenchmarkResult(
            size=size,
            time_seconds=round(avg_time, 4),
            dedup_count=total_deduped // num_runs,
            output_count=total_output // num_runs,
        ))
        print(f"  size={size}: {avg_time:.4f}s, deduped={total_deduped//num_runs}")

    return results


def analyze_complexity(results: list[BenchmarkResult]) -> dict:
    """Analyze time complexity from benchmark data."""
    if len(results) < 2:
        return {"complexity": "unknown", "ratio": None}

    # Compare n=100 vs n=200 to estimate O(n²) behavior
    r50 = next((r for r in results if r.size == 50), None)
    r100 = next((r for r in results if r.size == 100), None)
    r250 = next((r for r in results if r.size == 250), None)
    r500 = next((r for r in results if r.size == 500), None)

    analysis = {"complexity": "unknown", "ratios": {}}

    if r50 and r100:
        ratio_100_50 = r100.time_seconds / r50.time_seconds if r50.time_seconds > 0 else 0
        analysis["ratios"]["100_vs_50"] = round(ratio_100_50, 2)
        # O(n²): 200²/100² = 4, O(n): 200/100 = 2
        if 3.0 <= ratio_100_50 <= 5.0:
            analysis["complexity"] = "quadratic_O(n²)"
        elif 1.5 <= ratio_100_50 <= 3.0:
            analysis["complexity"] = "between_linear_quadratic"

    if r250 and r100:
        ratio_250_100 = r250.time_seconds / r100.time_seconds if r100.time_seconds > 0 else 0
        analysis["ratios"]["250_vs_100"] = round(ratio_250_100, 2)
        # O(n²): 250²/100² = 6.25, O(n): 250/100 = 2.5
        if 4.0 <= ratio_250_100 <= 8.0:
            analysis["complexity"] = "quadratic_O(n²)"

    if r500 and r250:
        ratio_500_250 = r500.time_seconds / r250.time_seconds if r250.time_seconds > 0 else 0
        analysis["ratios"]["500_vs_250"] = round(ratio_500_250, 2)

    return analysis


def run_benchmark(args) -> dict:
    """Run full benchmark suite."""
    sizes = [50, 100, 250, 500]
    num_runs = max(1, args.runs)

    print(f"Ranking/Dedup Hotspot Benchmark (runs={num_runs})")
    print("=" * 50)

    print("\n[1/2] Benchmarking _remove_duplicates...")
    results = benchmark_remove_duplicates(sizes, num_runs)

    print("\n[2/2] Analyzing complexity...")
    complexity = analyze_complexity(results)

    benchmark_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "sizes_tested": sizes,
        "num_runs": num_runs,
        "results": [
            {
                "size": r.size,
                "time_seconds": r.time_seconds,
                "dedup_count": r.dedup_count,
                "output_count": r.output_count,
            }
            for r in results
        ],
        "complexity_analysis": complexity,
        "hotspot": "_remove_duplicates O(n²) pairwise similarity",
        "optimization": "cached normalized token sets, exact URL match early exit",
    }

    return benchmark_data


def format_markdown(data: dict) -> str:
    """Format benchmark results as markdown."""
    md = ["# Ranking & Deduplication Hotspot Benchmark\n"]
    md.append(f"**Timestamp:** {data['timestamp']}\n")
    md.append(f"**Sizes tested:** {data['sizes_tested']}\n")
    md.append(f"**Runs per size:** {data['num_runs']}\n")

    md.append("\n## Results\n")
    md.append("| Size | Time (s) | Deduped | Output |")
    md.append("|------|----------|---------|--------|")
    for r in data["results"]:
        md.append(f"| {r['size']} | {r['time_seconds']} | {r['dedup_count']} | {r['output_count']} |")

    md.append("\n## Complexity Analysis\n")
    ca = data["complexity_analysis"]
    md.append(f"- **Detected:** {ca['complexity']}")
    if ca.get("ratios"):
        for k, v in ca["ratios"].items():
            md.append(f"  - {k}: {v}x (expected O(n²): ~4x, O(n): ~2x)")

    md.append("\n## Hotspot\n")
    md.append(f"- `{data['hotspot']}`")
    md.append("\n## Optimization Applied\n")
    md.append(f"- {data['optimization']}")

    return "\n".join(md)


def main():
    parser = argparse.ArgumentParser(description="Ranking & Dedup Hotspot Benchmark")
    parser.add_argument("--output-json", default="probe_f214opt_ranking_dedup/benchmark.json",
                        help="JSON output path")
    parser.add_argument("--output-md", default="probe_f214opt_ranking_dedup/BENCHMARK.md",
                        help="Markdown output path")
    parser.add_argument("--runs", type=int, default=3, help="Number of runs per size")
    args = parser.parse_args()

    import os
    os.makedirs("probe_f214opt_ranking_dedup", exist_ok=True)

    data = run_benchmark(args)

    # Write JSON
    with open(args.output_json, "w") as f:
        json.dump(data, f, indent=2)
    print(f"\nJSON → {args.output_json}")

    # Write Markdown
    md = format_markdown(data)
    with open(args.output_md, "w") as f:
        f.write(md)
    print(f"Markdown → {args.output_md}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
