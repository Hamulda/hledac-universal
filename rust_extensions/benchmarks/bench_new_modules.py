"""Micro-benchmarks: Python fallback vs Rust implementations."""

import statistics
import time
from collections.abc import Callable
from typing import Any


def bench(name: str, fn: Callable[..., Any], iterations: int = 10_000) -> None:
    times = []
    for _ in range(iterations):
        start = time.perf_counter_ns()
        fn()
        times.append(time.perf_counter_ns() - start)
    median_ns = statistics.median(times)
    print(f"{name:50s} {median_ns:10.1f} ns/op")

# xxhash vs hashlib.md5
import hashlib  # noqa: E402

from hledac_rust_extensions import content_hash_64  # noqa: E402

test_bytes = b"hello world this is a test string for benchmarking"
bench("xxh3_hash64(b'hello world...') [Rust]", lambda: content_hash_64(test_bytes))
bench("hashlib.md5(b'hello world...').digest() [Py]", lambda: hashlib.md5(test_bytes).digest())
bench("hashlib.sha256(b'hello world...').digest() [Py]", lambda: hashlib.sha256(test_bytes).digest())

# UrlSet vs Python set
from hledac_rust_extensions import UrlSet  # noqa: E402

url = "https://example.com/test/path?param=value#anchor"
rust_set = UrlSet(capacity=100_000)
py_set = set()
for i in range(1000):
    u = f"https://example{i}.com/path"
    rust_set.add(u)
    py_set.add(u)

bench("UrlSet.contains(url) [Rust]", lambda: rust_set.contains(url))
bench("set.contains(url) [Python]", lambda: url in py_set)

# URL normalize vs Python urllib
from urllib.parse import urlparse  # noqa: E402

from hledac_rust_extensions import normalize  # noqa: E402

test_url = "https://EXAMPLE.COM:443/path?b=2&a=1"
bench("normalize(url) [Rust]", lambda: normalize(test_url))
bench("urlparse(url) [Python]", lambda: urlparse(test_url))

# IOC extraction
from hledac_rust_extensions import fast_ioc_extract  # noqa: E402

test_text = """
Contact us at admin@example.com or support@company.org.
IP addresses: 192.168.1.1, 10.0.0.1, 8.8.8.8
Domain: malicious-site.com, phishing.net
Hash: d41d8cd98f00b204e9800998ecf8427e (MD5)
CVE: CVE-2024-12345
"""
bench("fast_ioc_extract(text) [Rust]", lambda: fast_ioc_extract(test_text))

# SimHash
from hledac_rust_extensions import simhash  # noqa: E402

sample_text = "This is a sample document for near-duplicate detection testing purposes"
bench("simhash(text, ngram=3) [Rust]", lambda: simhash(sample_text, 3))

print("\n--- Batch operations ---")

# Batch URL normalize
from hledac_rust_extensions import canonicalize_batch  # noqa: E402

urls = [f"https://EXAMPLE{i}.COM:443/path?a={i}" for i in range(100)]
bench("canonicalize_batch(100 urls) [Rust]", lambda: canonicalize_batch(urls))

# Batch content hash (expects strings, returns hex)
from hledac_rust_extensions import batch_content_hash_hex  # noqa: E402

data = ["content" + str(i) for i in range(100)]
bench("batch_content_hash_hex(100 items) [Rust]", lambda: batch_content_hash_hex(data))

# Batch simhash
from hledac_rust_extensions import batch_compute_simhash  # noqa: E402
from _core import aclose

texts = [f"Document number {i} with some content for simhash testing" for i in range(100)]
bench("batch_compute_simhash(100 texts) [Rust]", lambda: batch_compute_simhash(texts))
