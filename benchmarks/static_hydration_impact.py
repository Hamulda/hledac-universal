"""
Static Hydration Impact Benchmark — F214AA.

Measures how often static hydration is sufficient vs needs JS rendering.

All fixtures are inline — no network, no browser, no external files.
M1 8GB safe: bounded fixtures, no memory blowup.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).parent.parent

# ---------------------------------------------------------------------------
# Direct import of hydration_extractor to avoid utils/__init__.py
# which has aiohttp dependency that isn't available in all environments
# ---------------------------------------------------------------------------

_spec = importlib.util.spec_from_file_location(
    "hydration_extractor",
    PROJECT_ROOT / "utils" / "hydration_extractor.py",
)
if _spec is None or _spec.loader is None:
    raise ImportError("Could not load hydration_extractor")
_hydration_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_hydration_mod)
extract_static_hydration = _hydration_mod.extract_static_hydration

# ---------------------------------------------------------------------------
# Hermetic fixtures — 8 categories, all inline
# ---------------------------------------------------------------------------

_Fixtures = [
    # 1. Next.js article page
    {
        "id": "nextjs_article",
        "source": "next_data",
        "html": (
            '<html><head><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"title":"Next.js Article: The Future of Web Development",'
            '"serverData":{"body":"Modern web development with Next.js enables server-side '
            'rendering and static generation for optimal performance and SEO. '
            'This comprehensive guide covers everything from basics to advanced patterns."}}},'
            '"pageProps":{"title":"Next.js Article: The Future of Web Development"},'
            '"title":"Next.js Article: The Future of Web Development"}}'
            '</script>'
            '<link rel="canonical" href="https://example.com/blog/nextjs-future">'
            '<meta name="description" content="Modern web development guide">'
            '</head><body></body></html>'
        ),
    },
    # 2. Nuxt page with __NUXT_DATA__
    {
        "id": "nuxt_article",
        "source": "nuxt_data",
        "html": (
            '<html><head><script>__NUXT_DATA__='
            '[{"data":{"title":"Nuxt 3 Article: Building Modern Vue Applications",'
            '"content":"Nuxt 3 represents a significant leap forward in the Vue.js ecosystem '
            'providing automatic code splitting, server-side rendering, and powerful data fetching '
            'primitives that make building universal applications straightforward."}}]'
            '</script>'
            '<link rel="alternate" type="application/rss+xml" href="/blog/rss.xml">'
            '</head><body></body></html>'
        ),
    },
    # 3. Generic SPA with __INITIAL_STATE__
    {
        "id": "generic_spa",
        "source": "initial_state",
        "html": (
            '<html><head><script>window.__INITIAL_STATE__='
            '{"props":{"page":{"title":"State Management in React Applications: A Deep Dive",'
            '"body":"Effective state management is crucial for building scalable React applications. '
            'This article explores Redux, Zustand, Jotai and other solutions with practical examples '
            'for managing complex application state across distributed component trees."}},'
            '"serverData":{"meta":{"description":"State management guide"}}'
            '}</script>'
            '</head><body></body></html>'
        ),
    },
    # 4. JSON-LD article/news
    {
        "id": "json_ld_article",
        "source": "json_ld",
        "html": (
            '<html><head>'
            '<script type="application/ld+json">'
            '{"@type":"Article","headline":"Scientific Discovery: New Species Found in Deep Ocean",'
            '"description":"Marine biologists have identified a previously unknown species living '
            'in the deepest parts of the Pacific Ocean. The discovery challenges existing theories '
            'about biological adaptation to extreme environments and extreme pressure conditions.",'
            '"datePublished":"2024-01-15","author":{"@type":"Person","name":"Dr. Jane Smith"}}'
            '</script>'
            '<link rel="canonical" href="https://news.example.com/ocean-discovery">'
            '</head><body></body></html>'
        ),
    },
    # 5. Metadata-only page (no hydration payload)
    {
        "id": "metadata_only",
        "source": "metadata",
        "html": (
            '<html><head>'
            '<title>Economics Report: Global Market Trends Analysis 2024</title>'
            '<meta name="description" content="A comprehensive analysis of global market trends '
            'covering equity markets, fixed income, commodities and alternative investments '
            'with outlook for the remainder of the fiscal year and beyond.">'
            '<meta property="og:title" content="Economics Report: Global Market Trends Analysis 2024">'
            '<meta property="og:description" content="Comprehensive analysis of global market trends '
            'covering all major asset classes with forward guidance.">'
            '<link rel="canonical" href="https://example.com/reports/markets-2024">'
            '<link rel="alternate" type="application/atom+xml" href="/reports/atom.xml">'
            '</head><body></body></html>'
        ),
    },
    # 6. Empty JS shell (no hydration payload)
    {
        "id": "empty_shell",
        "source": "none",
        "html": (
            '<html><head>'
            '<noscript>Please enable JavaScript to use this application</noscript>'
            '<script src="/app.js"></script>'
            '</head><body>'
            '<div id="root"></div>'
            '</body></html>'
        ),
    },
    # 7. Malformed hydration (Next.js payload found and parsed, but JSON is broken — fail-soft)
    {
        "id": "malformed_hydration",
        "source": "next_data",
        "html": (
            '<html><head><script id="__NEXT_DATA__" type="application/json">'
            '{"broken json with { unmatched braces and "missing quotes'
            '</script>'
            '<meta name="description" content="Page with broken hydration and meta">'
            '<title>Page With Corrupt Hydration Data</title>'
            '</head><body></body></html>'
        ),
    },
    # 8. Huge/truncated — large but bounded (should not OOM on M1)
    {
        "id": "huge_truncated",
        "source": "next_data",
        "html": (
            '<html><head><script id="__NEXT_DATA__" type="application/json">'
            '{"props":{"pageProps":{"title":"Large Document Title For Testing",'
            '"serverData":{"body":"Base body text for the huge document. '
            + "x" * 400_000 +  # 400KB of padding to approach MAX_HTML_BYTES
            '"}}},"title":"Large Document Title"'
            '}' * 3 +  # repeat to grow size
            '</script></head><body></body></html>'
        ),
    },
]

# Score bucket edges
_BUCKETS = ["0.00-0.25", "0.25-0.50", "0.50-0.75", "0.75-1.00"]

def _score_bucket(score: float) -> str:
    if score < 0.25:
        return "0.00-0.25"
    elif score < 0.50:
        return "0.25-0.50"
    elif score < 0.75:
        return "0.50-0.75"
    else:
        return "0.75-1.00"


def _leak_check(text: str) -> bool:
    """Return True if raw HTML or hydration strings leak into output."""
    for pattern in ("<html", "<script", "__NEXT_DATA__", "__NUXT__", "__INITIAL_STATE__"):
        if pattern in text:
            return True
    return False


def run_benchmark(hermetic: bool = True) -> tuple[dict, list]:
    """
    Run the static hydration impact benchmark on all fixtures.

    Returns a summary dict suitable for JSON serialization.
    """
    total = len(_Fixtures)
    attempted = 0
    sufficient_count = 0
    insufficient_count = 0
    skip_js = 0
    fallback_js = 0
    by_source: dict[str, int] = {}
    score_buckets: dict[str, int] = dict.fromkeys(_BUCKETS, 0)
    max_bytes = 0
    errors = 0

    # per-sample detail (not included in summary output, only for debugging)
    details = []

    for fixture in _Fixtures:
        fid = fixture["id"]
        html = fixture["html"]

        max_bytes = max(max_bytes, len(html.encode("utf-8")))
        attempted += 1

        try:
            result = extract_static_hydration(html)
        except Exception as e:
            errors += 1
            details.append({"id": fid, "error": str(e)})
            continue

        # Score bucket
        bucket = _score_bucket(result.hydration_score)
        score_buckets[bucket] += 1

        # Source tracking
        for s in result.sources:
            by_source[s] = by_source.get(s, 0) + 1

        if result.found:
            if result.sufficient:
                sufficient_count += 1
                skip_js += 1
            else:
                insufficient_count += 1
                fallback_js += 1
        else:
            # no hydration found = fallback to JS
            fallback_js += 1

        # Verify no raw HTML leakage in text/metadata output
        all_text = result.text + json.dumps(result.metadata)
        if _leak_check(all_text):
            errors += 1

        details.append({
            "id": fid,
            "sources": list(result.sources),
            "score": result.hydration_score,
            "sufficient": result.sufficient,
            "bucket": bucket,
            "reason": result.reason,
        })

    skip_rate = skip_js / total if total > 0 else 0.0

    summary = {
        "total_samples": total,
        "hydration_attempted": attempted,
        "hydration_sufficient": sufficient_count,
        "hydration_insufficient": insufficient_count,
        "would_skip_js": skip_js,
        "would_fallback_to_js": fallback_js,
        "skip_rate": round(skip_rate, 4),
        "by_source": by_source,
        "score_buckets": score_buckets,
        "max_sample_bytes": max_bytes,
        "benchmark_mode": "hermetic" if hermetic else "live",
        "errors": errors,
    }

    return summary, details


def main():
    parser = argparse.ArgumentParser(description="Static Hydration Impact Benchmark")
    parser.add_argument("--hermetic", action="store_true", help="Run in hermetic mode (no network)")
    parser.add_argument("--json", dest="json_path", type=str, default=None, help="Path to write JSON summary")
    args = parser.parse_args()

    summary, details = run_benchmark(hermetic=args.hermetic)

    if args.json_path:
        out = {"summary": summary, "details": details}
        with open(args.json_path, "w") as f:
            json.dump(out, f, indent=2)
        print(f"Benchmark written to {args.json_path}")

    # Console summary
    print(f"total={summary['total_samples']}  "
          f"sufficient={summary['hydration_sufficient']}  "
          f"insufficient={summary['hydration_insufficient']}  "
          f"skip_js={summary['would_skip_js']}  "
          f"fallback_js={summary['would_fallback_to_js']}  "
          f"skip_rate={summary['skip_rate']:.2%}  "
          f"errors={summary['errors']}")

    print("score_buckets:", summary["score_buckets"])
    print("by_source:", summary["by_source"])

    # Exit code: 0 if no errors, 1 if errors
    sys.exit(0 if summary["errors"] == 0 else 1)


if __name__ == "__main__":
    main()
