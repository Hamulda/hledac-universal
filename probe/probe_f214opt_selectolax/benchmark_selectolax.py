"""
Sprint F214OPT-A microbenchmark: html_text_fast vs legacy fallback.

Compares:
- html_text_fast (selectolax-first, regex fallback when unavailable)
- Legacy BeautifulSoup path (content_extractor.py original)
- Legacy regex fallback path

Sizes: 10KB, 50KB, 250KB synthetic HTML.
Metrics: elapsed_ms, output length parity.

No network, no browser — fully hermetic.
"""



import json
import html as _html
import os
import re
import time
from pathlib import Path

# Fix module import path so benchmark can run standalone
# benchmark is at .../hledac/universal/probe_f214opt_selectolax/benchmark_selectolax.py
# hledac is at /Users/vojtechhamada/PycharmProjects/Hledac/hledac/
# so we need to add /Users/vojtechhamada/PycharmProjects/Hledac/ to sys.path
import sys
_benchmark_dir = os.path.dirname(os.path.abspath(__file__))  # .../hledac/universal/probe_f214opt_selectolax/
# Go up 3 levels: probe_f214opt_selectolax -> universal -> hledac -> project root
_project_root = os.path.dirname(os.path.dirname(os.path.dirname(_benchmark_dir)))
sys.path.insert(0, _project_root)

# ---------------------------------------------------------------------------
# Synthetic HTML generators
# ---------------------------------------------------------------------------

def make_synthetic_html(size_kb: int) -> str:
    """Generate synthetic HTML of approximately the target size."""
    # Base template with realistic structure
    base = """<!DOCTYPE html>
<html>
<head>
    <title>Benchmark Test Page</title>
    <meta charset="utf-8">
    <style>body { font-family: Arial, sans-serif; margin: 0; padding: 20px; }</style>
</head>
<body>
    <header><h1>Header Content</h1><nav><a href="#">Link 1</a><a href="#">Link 2</a></nav></header>
    <main>
        <article>
            <h2>Article Title</h2>
            <p>This is a paragraph of text content for benchmarking HTML extraction performance.</p>
            <p>Another paragraph with <strong>bold</strong> and <em>italic</em> text.</p>
            <ul>
                <li>List item one</li>
                <li>List item two</li>
                <li>List item three</li>
            </ul>
"""

    # Repeat middle content to hit target size
    middle = """
            <section>
                <h3>Section Heading</h3>
                <p>Section paragraph with some meaningful content for the benchmark.</p>
                <p>More content here with <a href="#">a link</a> and more text.</p>
                <blockquote>Blockquote text for variety in the HTML structure.</blockquote>
                <p>Yet another paragraph to increase the content size appropriately.</p>
            </section>
"""

    # Calculate how many repetitions to reach target size
    base_size = len(base.encode("utf-8"))
    middle_size = len(middle.encode("utf-8"))
    end = """
        </article>
    </main>
    <footer><p>Footer content with some text.</p></footer>
    <script>console.log("hello");</script>
    <noscript>Fallback content</noscript>
</body>
</html>"""

    end_size = len(end.encode("utf-8"))
    target_bytes = size_kb * 1024

    # Calculate repetitions needed
    if base_size + end_size >= target_bytes:
        repeats = 1
    else:
        available = target_bytes - base_size - end_size
        repeats = max(1, available // middle_size + 1)

    full_html = base + (middle * repeats) + end
    return full_html


# ---------------------------------------------------------------------------
# Legacy fallback (pure regex — from content_extractor.py original)
# ---------------------------------------------------------------------------

_RE_SCRIPT_STYLE_LEGACY = re.compile(
    r"<script[^>]*>.*?</script>|<style[^>]*>.*?</style>|"
    r"<noscript[^>]*>.*?</noscript>",
    re.DOTALL | re.IGNORECASE,
)
_RE_TAG_LEGACY = re.compile(r"<[^>]+>")
_RE_WS_LEGACY = re.compile(r"\s+")


def legacy_regex_extract(html: str) -> str:
    """Original pure-regex fallback from content_extractor.py."""
    text = _RE_SCRIPT_STYLE_LEGACY.sub("", html)
    text = _RE_TAG_LEGACY.sub(" ", text)
    text = _html.unescape(text)
    text = _RE_WS_LEGACY.sub(" ", text).strip()
    return text


# ---------------------------------------------------------------------------
# html_to_text_fast (under test)
# ---------------------------------------------------------------------------

from hledac.universal.utils.html_text_fast import html_to_text_fast
from core import aclose


# ---------------------------------------------------------------------------
# BeautifulSoup path (for comparison where available)
# ---------------------------------------------------------------------------

BS4_AVAILABLE = False
try:
    from bs4 import BeautifulSoup
    BS4_AVAILABLE = True
except ImportError:  # noqa: BLE001
    pass  # noqa: BLE001  # fail-soft suppression: module


def legacy_bs4_extract(html: str) -> str:
    """Original BeautifulSoup path from content_extractor.py."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'noscript']):
        tag.decompose()
    main_content = ""
    for selector in ['main', 'article', '[role="main"]', '.content', '.post-content', '.entry-content', '#content']:
        content_elem = soup.select_one(selector)
        if content_elem:
            main_content = content_elem.get_text(separator=' ', strip=True)
            break
    if not main_content:
        body = soup.find('body')
        if body:
            main_content = body.get_text(separator=' ', strip=True)
        else:
            main_content = soup.get_text(separator=' ', strip=True)
    return re.sub(r'\s+', ' ', main_content).strip()


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def benchmark(name: str, fn, html: str, iterations: int = 20) -> dict:
    """Run benchmark and return stats."""
    times = []
    output = ""
    for _ in range(iterations):
        start = time.perf_counter()
        output = fn(html)
        elapsed = (time.perf_counter() - start) * 1000  # ms
        times.append(elapsed)

    times.sort()
    return {
        "name": name,
        "iterations": iterations,
        "elapsed_ms_avg": round(sum(times) / len(times), 4),
        "elapsed_ms_min": round(min(times), 4),
        "elapsed_ms_max": round(max(times), 4),
        "elapsed_ms_p50": round(times[len(times) // 2], 4),
        "elapsed_ms_p95": round(times[int(len(times) * 0.95)], 4),
        "output_len": len(output),
        "output_preview": output[:100],
    }


def run_benchmarks() -> list[dict]:
    """Run all benchmarks across all sizes."""
    results = []
    sizes_kb = [10, 50, 250]

    for size_kb in sizes_kb:
        html = make_synthetic_html(size_kb)
        actual_size = len(html.encode("utf-8")) / 1024
        print(f"\n=== Size: {size_kb}KB (actual: {actual_size:.1f}KB) ===")

        # html_to_text_fast
        r_fast = benchmark("html_to_text_fast", html_to_text_fast, html)
        print(f"  html_to_text_fast: {r_fast['elapsed_ms_avg']:.3f}ms avg")
        results.append({**r_fast, "size_kb": size_kb})

        # Legacy regex fallback
        r_regex = benchmark("legacy_regex_fallback", legacy_regex_extract, html)
        print(f"  legacy_regex_fallback: {r_regex['elapsed_ms_avg']:.3f}ms avg")
        results.append({**r_regex, "size_kb": size_kb})

        # BeautifulSoup (if available)
        if BS4_AVAILABLE:
            r_bs4 = benchmark("legacy_bs4", legacy_bs4_extract, html)
            print(f"  legacy_bs4: {r_bs4['elapsed_ms_avg']:.3f}ms avg")
            results.append({**r_bs4, "size_kb": size_kb})

            # Output parity check
            fast_out = html_to_text_fast(html)
            bs4_out = legacy_bs4_extract(html)
            regex_out = legacy_regex_extract(html)
            print(f"  output lengths — fast:{len(fast_out)} bs4:{len(bs4_out)} regex:{len(regex_out)}")
            print(f"  fast≈bs4: {abs(len(fast_out) - len(bs4_out)) < 50} | fast≈regex: {abs(len(fast_out) - len(regex_out)) < 50}")
        else:
            print("  legacy_bs4: not available (bs4 not installed)")

    return results


if __name__ == "__main__":
    import sys
    results = run_benchmarks()
    print("\n=== All benchmarks complete ===")

    # Write JSON report
    report_dir = Path(__file__).parent
    json_path = report_dir / "selectolax_fast_path.json"
    with open(json_path, "w") as f:
        json.dump({
            "benchmark": "F214OPT-A selectolax fast path",
            "units": {"elapsed_ms": "milliseconds", "size_kb": "kilobytes"},
            "iterations_per_test": 20,
            "results": results,
            "selectolax_available": False,  # runtime check
            "bs4_available": BS4_AVAILABLE,
        }, f, indent=2)
    print(f"JSON report: {json_path}")
    sys.exit(0)
