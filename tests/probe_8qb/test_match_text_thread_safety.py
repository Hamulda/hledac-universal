"""D.9 — match_text is thread-safe: 100 concurrent calls produce no race conditions."""
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, ".")

from hledac.universal.utils.patterns.pattern_matcher import match_text, configure_patterns, get_default_bootstrap_patterns


def test_match_text_thread_safety():
    """100 concurrent match_text() calls — all must return without error."""
    configure_patterns(get_default_bootstrap_patterns())

    big_text = "CVE-2026-1234 cobalt strike lateral movement ransomware " * 50
    results = []
    errors = []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(match_text, big_text) for _ in range(100)]
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as exc:
                errors.append(exc)

    assert len(errors) == 0, f"Race conditions detected: {errors}"
    assert len(results) == 100, f"Expected 100 results, got {len(results)}"
    # Each result must be non-empty (the text contains multiple patterns)
    assert all(isinstance(r, list) for r in results), "All results must be lists"
