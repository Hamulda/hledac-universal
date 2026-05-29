#!/usr/bin/env python3
"""
F214INT — InterpreterPoolExecutor Pure-Python POC Probe
========================================================

Scans utils/, tools/, intelligence/ for pure-Python CPU-heavy candidates
suitable for Python 3.14 InterpreterPoolExecutor POC.

MUST NOT USE:
- DuckDB, LMDB, LanceDB (C extensions)
- PyArrow (C++)
- MLX/CoreML (GPU)
- msgspec DTOs in hot paths
- aiohttp/network
- Global state that would break pickling

BENCHMARKS:
- Serial (baseline)
- ThreadPoolExecutor
- InterpreterPoolExecutor (Python 3.14)
- ProcessPoolExecutor (optional)

METRICS:
- Wall time
- RSS peak
- Serialization overhead
- Import overhead
- Failures due to unpickleable closures/globals
"""

from __future__ import annotations

import gc
import hashlib
import importlib
import json
import math
import os
import random
import re
import resource
import sys
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass

# -----------------------------------------------------------------------
# Candidate inventory (lazy import per candidate)
# -----------------------------------------------------------------------

@dataclass
class CandidateResult:
    name: str
    module_path: str
    func_name: str
    wall_time_ms: float
    rss_delta_kb: int
    serialization_overhead_ms: float
    import_time_ms: float
    thread_speedup: float
    interp_speedup: float
    process_speedup: float
    thread_fail: str
    interp_fail: str
    process_fail: str
    notes: str


def _get_mem_kb() -> int:
    """RSS in KB. On macOS ru_maxrss is in bytes, normalize to KB."""
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return rss // 1024  # bytes -> KB
    return rss // 1024  # assume Linux KB (already in KB)


# -----------------------------------------------------------------------
# Candidates
# -----------------------------------------------------------------------

def candidate_normalize_text(items: list[str]) -> list[str]:
    """scoring.py::normalize_text — lowercase, strip, re.sub, join."""
    results = []
    for text in items:
        text = text.lower().strip()
        text = re.sub(r'[^\w\s]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()
        results.append(text)
    return results


def candidate_rrf_fuse(items: list) -> list[str]:
    """ranking.py::rrf_fuse — Reciprocal Rank Fusion scoring."""
    from utils.ranking import rrf_fuse
    # items is list of (doc_id, score) tuples to be fused
    ranked_lists = []
    for i, chunk in enumerate(items):
        doc_id = f"doc_{i % 50}"
        score = hashlib.md5(chunk.encode()).hexdigest()[0:8]
        ranked_lists.append([(doc_id, float(int(score[:4], 16)) / 65535)])
    return rrf_fuse(ranked_lists, k=60)


def candidate_entity_confidence(items: list) -> list[float]:
    """entity_extractor.py — pattern confidence scoring."""
    from utils.entity_extractor import EntityExtractor
    extractor = EntityExtractor()
    results = []
    for pattern_type_val, value in items:
        try:
            conf = extractor._calculate_confidence(pattern_type_val, value)
            results.append(conf)
        except Exception:
            results.append(0.0)
    return results


def candidate_lang_detect(items: list[str]) -> list[str]:
    """language.py — fallback language detection."""
    from utils.language import LanguageDetector
    detector = LanguageDetector(fallback_mode=True)
    return [detector._fallback_detect(text) for text in items]


def candidate_extract_keywords(items: list[str]) -> list[list[str]]:
    """validation.py::extract_keywords — keyword extraction."""
    from utils.validation import extract_keywords
    return [extract_keywords(text, min_length=3, max_keywords=10) for text in items]


def candidate_html_extract(items: list[str]) -> list[str]:
    """content_extractor.py — HTML text extraction (no bs4 path)."""
    # Import outside the loop so it's cached
    from tools.content_extractor import extract_main_text_from_html as _extract_html
    results = []
    for html in items:
        try:
            text = _extract_html(html)
            results.append(text[:20000])
        except Exception:
            # fallback inline extraction
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<noscript[^>]*>.*?</noscript>', '', text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
            text = re.sub(r'\s+', ' ', text).strip()
            results.append(text[:20000])
    return results


def candidate_entropy(items: list[bytes]) -> list[float]:
    """Shannon entropy in bits per byte — proper math.log2 formula."""
    results = []
    for data in items:
        if not data:
            results.append(0.0)
            continue
        freq: dict[int, int] = {}
        for b in data:
            freq[b] = freq.get(b, 0) + 1
        entropy = 0.0
        n = len(data)
        for count in freq.values():
            p = count / n
            if p > 0:
                entropy -= p * math.log2(p)
        results.append(entropy / 8.0)  # normalize to [0, 1]
    return results


def candidate_aho_scan(items: list[str]) -> list[list[dict]]:
    """aho_extractor.py — Aho-Corasick scan."""
    from utils.aho_extractor import aho_scan_text, get_suspicious_keywords_automaton
    automaton = get_suspicious_keywords_automaton()
    results = []
    for text in items:
        matches = aho_scan_text(automaton, text)
        results.append(matches)
    return results


def candidate_regex_scan(items: list[str]) -> list[list[dict]]:
    """aho_extractor.py — regex substring scan (ground truth)."""
    from utils.aho_extractor import regex_scan_suspicious_keywords
    return [regex_scan_suspicious_keywords(text) for text in items]


def candidate_jaccard_sim(items: list) -> list[float]:
    """validation.py::calculate_similarity — Jaccard similarity."""
    from utils.validation import calculate_similarity
    results = []
    for text1, text2 in items:
        results.append(calculate_similarity(text1, text2))
    return results


def candidate_markdown_render(items: list) -> list[str]:
    """sprint_markdown_reporter.py — markdown report rendering."""
    from export.sprint_markdown_reporter import render_sprint_markdown
    results = []
    for item in items:
        try:
            report = item
            scorecard = {}
            sprint_id = item.get("sprint_id", "UNKNOWN") if isinstance(item, dict) else "UNKNOWN"
            rendered = render_sprint_markdown(report, scorecard, sprint_id)
            results.append(rendered[:500] if rendered else "")
        except Exception:
            results.append("")
    return results


# -----------------------------------------------------------------------
# Workload generation
# -----------------------------------------------------------------------

LOREM_TEMPLATE = (
    "This is a sample document about machine learning and artificial intelligence systems. "
    "Another article discussing the latest developments in Python programming and software engineering. "
    "A report on data analysis and statistical methods for research and development purposes. "
    "Content related to web development and cloud computing technologies and infrastructure. "
    "Documentation about API design and software architecture patterns and best practices. "
    "Machine learning models require careful tuning and validation before deployment to production. "
    "Python's ecosystem provides excellent tools for data science and numerical computing. "
    "Web scraping and information extraction are core capabilities of modern research tools. "
    "Natural language processing enables intelligent analysis of unstructured text data. "
    "Information retrieval systems help find relevant documents in large collections efficiently. "
) * 20  # ~2000 chars per template

HTML_TEMPLATE = (
    "<html><head><title>Test Document</title></head><body>"
    "<script>console.log('evil');</script>"
    "<style>.hidden{display:none;}</style>"
    "<div class='content'><h1>Heading</h1><p>Paragraph text with <strong>bold</strong> and "
    "<em>italic</em> content. More text here to make it realistic.</p>"
    "<ul><li>Item one</li><li>Item two</li><li>Item three</li></ul>"
    "<a href='https://example.com'>Link</a><img src='img.png' alt='image'/>"
    "<noscript>NoScript content</noscript>"
    "</div></body></html>"
) * 50  # ~3500 chars per HTML template


def gen_text_items(n: int, seed: int = 42) -> list[str]:
    """Generate n synthetic text items for benchmark."""
    rng = random.Random(seed)
    items = []
    for i in range(n):
        # Rotate and add noise to create varied text
        base = LOREM_TEMPLATE
        noise_pos = rng.randint(0, len(base) - 200)
        noise_len = rng.randint(100, 500)
        noisy = base[:noise_pos] + base[noise_pos:noise_pos+noise_len] + f" item_{i} unique_{rng.randint(0, 999999)}"
        items.append(noisy)
    return items


def gen_bytes_items(n: int, seed: int = 42) -> list[bytes]:
    """Generate n synthetic bytes items for entropy benchmark."""
    random.Random(seed)
    items = []
    for i in range(n):
        size = 50000 + (i % 100000)  # 50KB-150KB per item
        # Create pseudo-random but compressible data
        pattern = bytes(range(256)) * (size // 256 + 1)
        items.append(pattern[:size])
    return items


def gen_rrf_items(n: int, seed: int = 42) -> list[str]:
    """Generate n text items for RRF benchmark (converted to ranked lists inside candidate)."""
    return gen_text_items(n, seed)


def gen_entity_items(n: int, seed: int = 42) -> list[tuple]:
    """Generate (pattern_type, value) pairs for entity confidence benchmark."""
    random.Random(seed)
    patterns = [
        ("email", "user@example.com"),
        ("email", "admin@company.org"),
        ("domain", "example.com"),
        ("domain", "test.example.org"),
        ("url", "https://example.com/page"),
        ("url", "http://test.org/api"),
        ("ipv4", "192.168.1.1"),
        ("ipv4", "10.0.0.255"),
    ]
    items = []
    for i in range(n):
        pattern, value = patterns[i % len(patterns)]
        # Add variation
        value = value.replace("example", f"user{i}").replace("test", f"site{i}")
        items.append((pattern, value))
    return items


def gen_html_items(n: int, seed: int = 42) -> list[str]:
    """Generate n HTML items for content extraction benchmark."""
    rng = random.Random(seed)
    items = []
    for i in range(n):
        # Vary the HTML template with random noise
        noise = f"<div>noise_{rng.randint(0, 99999)}</div>"
        items.append(HTML_TEMPLATE + noise + f"<!-- comment_{i} -->")
    return items


def gen_jaccard_items(n: int, seed: int = 42) -> list[tuple]:
    """Generate (text1, text2) pairs for Jaccard similarity benchmark."""
    random.Random(seed)
    texts = gen_text_items(n * 2, seed)
    items = []
    for i in range(0, n * 2 - 1, 2):
        # 80% similar, 20% different
        if i % 5 < 4:
            items.append((texts[i], texts[i] + " slight modification"))
        else:
            items.append((texts[i], texts[i + 1]))
    return items


def gen_markdown_items(n: int, seed: int = 42) -> list[dict]:
    """Generate mock sprint result dicts for markdown rendering."""
    rng = random.Random(seed)
    items = []
    for i in range(n):
        item = {
            "sprint_id": f"F214X_{i:04d}",
            "query": f"test_query_{i}",
            "total_findings": rng.randint(10, 500),
            "accepted_findings": rng.randint(5, 400),
            "rejected_findings": rng.randint(1, 100),
            "duration_seconds": rng.randint(60, 3600),
            "sources_touched": rng.randint(1, 20),
            "findings": [
                {
                    "finding_id": f"F{i:06d}_{j}",
                    "source": rng.choice(["ct", "dns", "scraped", " passive"]),
                    "type": rng.choice(["domain", "ip", "url", "cert"]),
                    "confidence": rng.uniform(0.3, 0.99),
                    " IOC": f"192.168.1.{j}",
                }
                for j in range(rng.randint(5, 20))
            ],
        }
        items.append(item)
    return items


# -----------------------------------------------------------------------
# Benchmark runner
# -----------------------------------------------------------------------

def benchmark_candidate(
    name: str,
    func: Callable,
    workload: list,
    n_iter: int = 3,
    workers: int = 4,
) -> CandidateResult:
    """Run serial + ThreadPool + InterpreterPool + ProcessPool benchmark."""
    result = CandidateResult(
        name=name,
        module_path="",
        func_name=name,
        wall_time_ms=0.0,
        rss_delta_kb=0,
        serialization_overhead_ms=0.0,
        import_time_ms=0.0,
        thread_speedup=0.0,
        interp_speedup=0.0,
        process_speedup=0.0,
        thread_fail="",
        interp_fail="",
        process_fail="",
        notes="",
    )

    gc.collect()
    rss_before = _get_mem_kb()

    # Warm-up with meaningful size
    try:
        warmup_size = min(100, len(workload))
        func(workload[:warmup_size])
    except Exception as e:
        result.notes = f"Warm-up failed: {e}"
        return result

    # Serial benchmark
    times = []
    for _ in range(n_iter):
        gc.collect()
        t0 = time.perf_counter()
        func(workload)
        t1 = time.perf_counter()
        times.append((t1 - t0) * 1000)
    serial_ms = min(times)

    rss_after = _get_mem_kb()
    result.wall_time_ms = serial_ms
    result.rss_delta_kb = rss_after - rss_before

    # ThreadPool benchmark
    try:
        times_thread = []
        for _ in range(n_iter):
            gc.collect()
            t0 = time.perf_counter()
            with ThreadPoolExecutor(max_workers=workers) as exc:
                chunk_size = max(1, len(workload) // workers)
                futures = [exc.submit(func, workload[i*chunk_size:(i+1)*chunk_size])
                           for i in range(workers)]
                [f.result() for f in futures]
            t1 = time.perf_counter()
            times_thread.append((t1 - t0) * 1000)
        thread_ms = min(times_thread)
        result.thread_speedup = serial_ms / thread_ms if thread_ms > 0 else 0.0
    except Exception as e:
        result.thread_fail = str(e)[:120]

    # InterpreterPool benchmark (Python 3.14+)
    try:
        from concurrent.futures import InterpreterPoolExecutor
        times_interp = []
        for _ in range(n_iter):
            gc.collect()
            t0 = time.perf_counter()
            with InterpreterPoolExecutor(max_workers=workers) as exc:
                chunk_size = max(1, len(workload) // workers)
                futures = [exc.submit(func, workload[i*chunk_size:(i+1)*chunk_size])
                           for i in range(workers)]
                [f.result() for f in futures]
            t1 = time.perf_counter()
            times_interp.append((t1 - t0) * 1000)
        interp_ms = min(times_interp)
        result.interp_speedup = serial_ms / interp_ms if interp_ms > 0 else 0.0
    except ImportError:
        result.interp_fail = "InterpreterPoolExecutor not available (Python < 3.14)"
    except Exception as e:
        result.interp_fail = str(e)[:120]

    # ProcessPool benchmark
    try:
        times_proc = []
        for _ in range(n_iter):
            gc.collect()
            t0 = time.perf_counter()
            with ProcessPoolExecutor(max_workers=workers) as exc:
                chunk_size = max(1, len(workload) // workers)
                futures = [exc.submit(func, workload[i*chunk_size:(i+1)*chunk_size])
                           for i in range(workers)]
                [f.result() for f in futures]
            t1 = time.perf_counter()
            times_proc.append((t1 - t0) * 1000)
        proc_ms = min(times_proc)
        result.process_speedup = serial_ms / proc_ms if proc_ms > 0 else 0.0

        # Serialization overhead estimate
        if proc_ms > 0 and serial_ms > 0:
            # overhead = total_proc_time - serial_time_serialized
            # If serial time for 1/4 chunk is X, then proc_time - X is overhead
            chunk_serial_ms = serial_ms / workers
            result.serialization_overhead_ms = max(0, proc_ms - chunk_serial_ms)
    except Exception as e:
        result.process_fail = str(e)[:120]

    return result


# -----------------------------------------------------------------------
# Import overhead measurement
# -----------------------------------------------------------------------

def measure_import(module_path: str) -> tuple[float, float]:
    """Measure import time and return (import_time_ms, module_size_kb)."""
    gc.collect()
    t0 = time.perf_counter()
    try:
        importlib.import_module(module_path)
        t1 = time.perf_counter()
        import_ms = (t1 - t0) * 1000
        size_kb = 0
        return import_ms, size_kb
    except Exception:
        return -1.0, 0.0


# -----------------------------------------------------------------------
# Main probe
# -----------------------------------------------------------------------

def run_probe() -> list[CandidateResult]:
    """Run all candidate benchmarks."""
    print("F214INT — InterpreterPoolExecutor Pure-Python POC Probe")
    print("=" * 60)

    # Generate workloads
    text_items = gen_text_items(10000, seed=42)  # larger items for measurable CPU time
    bytes_items = gen_bytes_items(200, seed=42)  # 50KB-150KB per item, 200 items = ~20GB theoretical
    rrf_items = gen_rrf_items(500, seed=42)  # 50 docs per fusion
    entity_items = gen_entity_items(10000, seed=42)  # more items since each is tiny
    html_items = gen_html_items(5000, seed=42)  # larger HTML
    jaccard_items = gen_jaccard_items(5000, seed=42)  # more pairs
    markdown_items = gen_markdown_items(500, seed=42)  # larger markdown workload

    candidates = [
        # (name, func, workload, notes)
        ("normalize_text (scoring.py)", candidate_normalize_text, text_items,
         "lowercase, strip, re.sub punctuation, normalize whitespace"),

        ("rrf_fuse (ranking.py)", candidate_rrf_fuse, rrf_items,
         "Reciprocal Rank Fusion, 50 docs per list"),

        ("entity_confidence (entity_extractor.py)", candidate_entity_confidence,
         entity_items,
         "Pattern confidence scoring across 8 pattern types"),

        ("lang_fallback_detect (language.py)", candidate_lang_detect, text_items,
         "Pure Python fallback language detection"),

        ("extract_keywords (validation.py)", candidate_extract_keywords, text_items,
         "Keyword extraction with stopword filtering"),

        ("html_text_extract (content_extractor.py)", candidate_html_extract, html_items,
         "HTML text extraction without bs4"),

        ("shannon_entropy (bytes)", candidate_entropy, bytes_items,
         "Shannon entropy in bits per byte, proper log2 formula"),

        ("aho_scan (aho_extractor.py)", candidate_aho_scan, text_items,
         "Aho-Corasick multi-pattern scan"),

        ("regex_scan (aho_extractor.py)", candidate_regex_scan, text_items,
         "Ground truth substring scan for comparison"),

        ("jaccard_similarity (validation.py)", candidate_jaccard_sim,
         jaccard_items,
         "Jaccard text similarity"),

        ("markdown_render (sprint_markdown_reporter.py)", candidate_markdown_render,
         markdown_items,
         "Markdown report rendering from sprint results dict"),
    ]

    results: list[CandidateResult] = []
    for name, func, workload, notes in candidates:
        print(f"\n--- {name} ---")
        print(f"    Workload: {len(workload)} items, ~{sys.getsizeof(workload)//1024}KB")
        result = benchmark_candidate(name, func, workload, n_iter=3)
        result.notes = notes

        print(f"    Serial:     {result.wall_time_ms:.2f}ms")
        print(f"    RSS delta:  {result.rss_delta_kb}KB")
        if result.thread_fail:
            print(f"    ThreadPool: FAIL ({result.thread_fail[:60]})")
        else:
            print(f"    ThreadPool: {result.thread_speedup:.2f}x speedup")
        if result.interp_fail:
            print(f"    InterpPool: FAIL ({result.interp_fail[:60]})")
        else:
            print(f"    InterpPool: {result.interp_speedup:.2f}x speedup")
        if result.process_fail:
            print(f"    ProcPool:   FAIL ({result.process_fail[:60]})")
        else:
            print(f"    ProcPool:   {result.process_speedup:.2f}x speedup")
            print(f"    Serial overhead: {result.serialization_overhead_ms:.2f}ms")

        results.append(result)

    return results


def main():
    results = run_probe()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"{'Candidate':<45} {'Serial(ms)':<12} {'Thread':<8} {'Interp':<8} {'Process':<8}")
    print("-" * 81)
    for r in results:
        thread_str = f"{r.thread_speedup:.2f}x" if not r.thread_fail else "FAIL"
        interp_str = f"{r.interp_speedup:.2f}x" if not r.interp_fail else "FAIL"
        proc_str = f"{r.process_speedup:.2f}x" if not r.process_fail else "FAIL"
        print(f"{r.name:<45} {r.wall_time_ms:<12.2f} {thread_str:<8} {interp_str:<8} {proc_str:<8}")

    # Save results as JSON for report generation
    output_path = os.environ.get('F214INT_OUTPUT', '/tmp/f214int_results.json')
    results_data = [
        {
            'name': r.name,
            'module_path': r.module_path,
            'func_name': r.func_name,
            'wall_time_ms': r.wall_time_ms,
            'rss_delta_kb': r.rss_delta_kb,
            'serialization_overhead_ms': r.serialization_overhead_ms,
            'import_time_ms': r.import_time_ms,
            'thread_speedup': r.thread_speedup,
            'interp_speedup': r.interp_speedup,
            'process_speedup': r.process_speedup,
            'thread_fail': r.thread_fail,
            'interp_fail': r.interp_fail,
            'process_fail': r.process_fail,
            'notes': r.notes,
        }
        for r in results
    ]
    with open(output_path, 'w') as f:
        json.dump(results_data, f, indent=2)
    print(f"\nResults saved to {output_path}")


if __name__ == "__main__":
    main()
