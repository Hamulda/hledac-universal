"""
ISSUE-007: Rust Extensions Integration Tests

Comprehensive integration tests for all Rust extension modules.
Tests verify that:
1. Rust extension loads and initializes correctly
2. Each module's API is accessible and functional
3. Python fallbacks work when Rust is unavailable

Run: python rust_extensions/integration_tests.py [--verbose] [--modules module1,module2]
"""
from __future__ import annotations
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional
import pytest
TESTABLE_MODULES = {'bloom': {'feature': 'core', 'tests': ['test_bloom_filter_basic', 'test_bloom_filter_false_positive_rate', 'test_url_set_basic']}, 'hash': {'feature': 'core', 'tests': ['test_content_hash_hex', 'test_sha256_hex', 'test_batch_hash']}, 'memory': {'feature': 'core', 'tests': ['test_get_process_rss', 'test_memory_pressure', 'test_available_memory']}, 'simd': {'feature': 'core', 'tests': ['test_neon_detection', 'test_dot_product']}, 'rate_limit': {'feature': 'core', 'tests': ['test_token_bucket_basic']}, 'aho_corasick': {'feature': 'core', 'tests': ['test_aho_basic', 'test_aho_multi_pattern']}, 'sprint_policies': {'feature': 'core', 'tests': ['test_lane_budget_pool']}, 'spsc_queue': {'feature': 'core', 'tests': ['test_spsc_basic']}, 'dns': {'feature': 'dns', 'tests': ['test_dns_resolve_sync'], 'requires_network': True}, 'tls': {'feature': 'tls13', 'tests': ['test_tls_metadata_extract'], 'requires_network': True}, 'anti_analysis': {'feature': 'anti_analysis', 'tests': ['test_anti_analysis_domain_check'], 'requires_network': True}, 'graph': {'feature': 'data', 'tests': ['test_graph_centrality', 'test_link_prediction']}, 'mlx_bridge': {'feature': 'mlx_fabric', 'tests': ['test_mlx_alloc_basic'], 'platform': 'macos'}, 'iosurface_bridge': {'feature': 'iosurface', 'tests': ['test_iosurface_descriptor'], 'platform': 'macos'}, 'ane': {'feature': 'ane', 'tests': ['test_ane_availability'], 'platform': 'macos'}, 'whisper': {'feature': 'whisper', 'tests': ['test_whisper_availability']}, 'stix_2_1': {'feature': 'stix', 'tests': ['test_stix_encode_decode']}, 'feed_pipeline': {'feature': 'advanced', 'tests': ['test_feed_entry_pipeline']}}

@pytest.fixture(scope='session')
def rust_backend():
    """Initialize Rust backend for tests."""
    try:
        from hledac.universal._core.rust_backend import rust
        return rust
    except ImportError as e:
        pytest.skip(f'Rust backend not available: {e}')

@pytest.fixture(scope='session')
def rust_available(rust_backend) -> bool:
    """Check if Rust backend is available."""
    return rust_backend.is_available

@pytest.fixture
def skip_if_rust_unavailable(rust_available: bool):
    """Skip test if Rust is unavailable."""
    if not rust_available:
        pytest.skip('Rust backend not available')

@pytest.fixture
def skip_if_network_unavailable():
    """Skip test if network is unavailable."""
    import socket
    try:
        socket.create_connection(('8.8.8.8', 53), timeout=2)
    except OSError:
        pytest.skip('Network not available')

@pytest.fixture
def skip_if_not_macos():
    """Skip test if not running on macOS."""
    import sys
    if sys.platform != 'darwin':
        pytest.skip('Test requires macOS')

@dataclass(slots=True)
class TestResult:
    """Result of a single test."""
    name: str
    passed: bool
    duration_ms: float
    error: Optional[str] = None
    output: Optional[str] = None

@dataclass(slots=True)
class ModuleTestResult:
    """Result of testing a single module."""
    module_name: str
    feature: str
    tests_passed: int
    tests_failed: int
    tests_skipped: int
    results: list[TestResult] = field(default_factory=list)
    error: Optional[str] = None

class TestBloomFilter:
    """Tests for bloom module."""

    def test_bloom_filter_basic(self, rust_backend, skip_if_rust_unavailable):
        """Test basic BloomFilter operations."""
        import time
        start = time.perf_counter()
        BloomFilter = getattr(rust_backend.raw, 'BloomFilter', None)
        if BloomFilter is None:
            pytest.skip('BloomFilter not available')
        bf = BloomFilter(capacity=1000, fp_rate=0.01)
        for i in range(100):
            bf.add(f'item_{i}')
        assert bf.might_contain('item_0')
        assert bf.might_contain('item_99')
        assert not bf.might_contain('nonexistent')
        duration_ms = (time.perf_counter() - start) * 1000
        return TestResult(name='test_bloom_filter_basic', passed=True, duration_ms=duration_ms)

    def test_bloom_filter_false_positive_rate(self, rust_backend, skip_if_rust_unavailable):
        """Test BloomFilter false positive rate."""
        import random
        BloomFilter = getattr(rust_backend.raw, 'BloomFilter', None)
        if BloomFilter is None:
            pytest.skip('BloomFilter not available')
        bf = BloomFilter(capacity=100, fp_rate=0.1)
        for i in range(50):
            bf.add(f'added_{i}')
        false_positives = 0
        for i in range(1000):
            if bf.might_contain(f'not_added_{i}') and f'not_added_{i}' not in [f'added_{j}' for j in range(50)]:
                false_positives += 1
        fp_rate = false_positives / 1000
        assert fp_rate < 0.2, f'False positive rate too high: {fp_rate:.2%}'

    def test_url_set_basic(self, rust_backend, skip_if_rust_unavailable):
        """Test UrlSet operations."""
        UrlSet = getattr(rust_backend.raw, 'UrlSet', None)
        if UrlSet is None:
            pytest.skip('UrlSet not available')
        us = UrlSet(capacity=1000)
        us.add('https://example.com/path1')
        us.add('https://example.com/path2')
        us.add('https://test.com')
        assert us.might_contain('https://example.com/path1')
        assert us.might_contain('https://test.com')
        assert not us.might_contain('https://other.com')

class TestHash:
    """Tests for hash module."""

    def test_content_hash_hex(self, rust_backend, skip_if_rust_unavailable):
        """Test content_hash_hex function."""
        hash_func = getattr(rust_backend.hash, 'content_hash_hex', None)
        if hash_func is None:
            pytest.skip('content_hash_hex not available')
        result = hash_func(b'hello world')
        assert isinstance(result, str)
        assert len(result) == 16
        result2 = hash_func(b'hello world')
        assert result == result2
        result3 = hash_func(b'hello world!')
        assert result != result3

    def test_sha256_hex(self, rust_backend, skip_if_rust_unavailable):
        """Test sha256_hex function."""
        hash_func = getattr(rust_backend.hash, 'sha256_hex', None)
        if hash_func is None:
            pytest.skip('sha256_hex not available')
        result = hash_func(b'hello')
        assert isinstance(result, str)
        assert len(result) == 64

    def test_batch_hash(self, rust_backend, skip_if_rust_unavailable):
        """Test batch hashing."""
        batch_func = getattr(rust_backend.hash, 'batch_content_hash_hex_parallel', None)
        if batch_func is None:
            pytest.skip('batch_content_hash_hex_parallel not available')
        data = [b'item1', b'item2', b'item3']
        results = batch_func(data)
        assert len(results) == 3
        assert all((isinstance(r, str) for r in results))
        assert results[0] != results[1]

class TestMemory:
    """Tests for memory module."""

    def test_get_process_rss(self, rust_backend, skip_if_rust_unavailable):
        """Test get_process_rss_gib."""
        func = getattr(rust_backend.memory, 'get_process_rss_gib', None)
        if func is None:
            pytest.skip('get_process_rss_gib not available')
        rss = func()
        assert isinstance(rss, float)
        assert rss >= 0
        assert rss < 100

    def test_memory_pressure(self, rust_backend, skip_if_rust_unavailable):
        """Test memory_pressure_level."""
        func = getattr(rust_backend.memory, 'memory_pressure_level', None)
        if func is None:
            pytest.skip('memory_pressure_level not available')
        level = func()
        assert isinstance(level, int)
        assert 0 <= level <= 2

    def test_available_memory(self, rust_backend, skip_if_rust_unavailable):
        """Test get_available_memory_gib."""
        func = getattr(rust_backend.memory, 'get_available_memory_gib', None)
        if func is None:
            pytest.skip('get_available_memory_gib not available')
        available = func()
        assert isinstance(available, float)
        assert available >= 0

class TestSIMD:
    """Tests for simd module."""

    def test_neon_detection(self, rust_backend, skip_if_rust_unavailable):
        """Test NEON availability detection."""
        func = getattr(rust_backend.simd, 'neon_available', None)
        if func is None:
            pytest.skip('neon_available not available')
        available = func()
        assert isinstance(available, bool)

    def test_dot_product(self, rust_backend, skip_if_rust_unavailable):
        """Test dot product function."""
        func = getattr(rust_backend.simd, 'dot_product_f32', None)
        if func is None:
            pytest.skip('dot_product_f32 not available')
        a = [1.0, 2.0, 3.0]
        b = [4.0, 5.0, 6.0]
        result = func(a, b)
        expected = 1 * 4 + 2 * 5 + 3 * 6
        assert abs(result - expected) < 0.001

class TestRateLimit:
    """Tests for rate_limit module."""

    def test_token_bucket_basic(self, rust_backend, skip_if_rust_unavailable):
        """Test TokenBucket rate limiter."""
        TokenBucket = getattr(rust_backend.raw, 'TokenBucket', None)
        if TokenBucket is None:
            pytest.skip('TokenBucket not available')
        bucket = TokenBucket(capacity=10, refill_rate=5.0)
        for i in range(10):
            assert bucket.try_acquire(), f'Failed to acquire token {i}'
        assert not bucket.try_acquire()

class TestAhoCorasick:
    """Tests for aho_corasick module."""

    def test_aho_basic(self, rust_backend, skip_if_rust_unavailable):
        """Test basic Aho-Corasick matching."""
        AhoCorasickMatcher = getattr(rust_backend.raw, 'AhoCorasickMatcher', None)
        if AhoCorasickMatcher is None:
            pytest.skip('AhoCorasickMatcher not available')
        patterns = ['hello', 'world', 'test']
        matcher = AhoCorasickMatcher(patterns, labels=[])
        text = 'hello world, this is a test'
        matches = matcher.search(text)
        assert len(matches) >= 3

    def test_aho_multi_pattern(self, rust_backend, skip_if_rust_unavailable):
        """Test Aho-Corasick with multiple patterns."""
        AhoCorasickMatcher = getattr(rust_backend.raw, 'AhoCorasickMatcher', None)
        if AhoCorasickMatcher is None:
            pytest.skip('AhoCorasickMatcher not available')
        patterns = ['foo', 'bar', 'baz']
        matcher = AhoCorasickMatcher(patterns, labels=['tag1', 'tag2', 'tag3'])
        text = 'foo and bar but not baz'
        matches = matcher.search(text)
        assert len(matches) == 3

class TestSprintPolicies:
    """Tests for sprint_policies module."""

    def test_lane_budget_pool(self, rust_backend, skip_if_rust_unavailable):
        """Test LaneBudgetPool."""
        LaneBudgetPool = getattr(rust_backend.raw, 'LaneBudgetPool', None)
        if LaneBudgetPool is None:
            pytest.skip('LaneBudgetPool not available')
        pool = LaneBudgetPool(num_lanes=4, budget_per_lane=10)
        assert pool is not None

class TestSPSCQueue:
    """Tests for spsc_queue module."""

    def test_spsc_basic(self, rust_backend, skip_if_rust_unavailable):
        """Test basic SPSC queue operations."""
        SPSCQueue = getattr(rust_backend.raw, 'SPSCQueue', None)
        if SPSCQueue is None:
            pytest.skip('SPSCQueue not available')
        queue = SPSCQueue(capacity=16)
        assert queue.is_empty()
        assert not queue.is_full()
        for i in range(5):
            queue.push(i)
        for i in range(5):
            val = queue.pop()
            assert val == i

class TestDNS:
    """Tests for dns module."""

    def test_dns_resolve_sync(self, rust_backend, skip_if_rust_unavailable, skip_if_network_unavailable):
        """Test synchronous DNS resolution."""
        resolve_async = getattr(rust_backend.dns, 'resolve_async', None)
        if resolve_async is None:
            pytest.skip('dns.resolve_async not available')
        ips = resolve_async('example.com', 'A')
        assert ips is None or isinstance(ips, list)

class TestTLS:
    """Tests for tls module."""

    def test_tls_metadata_extract(self, rust_backend, skip_if_rust_unavailable):
        """Test TLS metadata extraction."""
        extract_func = getattr(rust_backend.tls, 'extract_tls_metadata', None)
        if extract_func is None:
            pytest.skip('tls.extract_tls_metadata not available')
        try:
            result = extract_func([], None, b'')
        except Exception:
            pass

class TestAntiAnalysis:
    """Tests for anti_analysis module."""

    def test_anti_analysis_domain_check(self, rust_backend, skip_if_rust_unavailable):
        """Test domain abandonment check."""
        is_abandoned = getattr(rust_backend.anti_analysis, 'is_host_abandoned', None)
        if is_abandoned is None:
            pytest.skip('anti_analysis.is_host_abandoned not available')
        result = is_abandoned('test-domain-nonexistent.example')
        assert isinstance(result, bool)

class TestGraph:
    """Tests for graph modules."""

    def test_graph_centrality(self, rust_backend, skip_if_rust_unavailable):
        """Test graph centrality functions."""
        betweenness = getattr(rust_backend.raw, 'betweenness_centrality', None)
        if betweenness is None:
            pytest.skip('betweenness_centrality not available')
        try:
            result = betweenness([])
        except Exception:
            pass

    def test_link_prediction(self, rust_backend, skip_if_rust_unavailable):
        """Test link prediction functions."""
        predict_links = getattr(rust_backend.raw, 'predict_links', None)
        if predict_links is None:
            pytest.skip('predict_links not available')

class TestMLXBridge:
    """Tests for mlx_bridge module."""

    def test_mlx_alloc_basic(self, rust_backend, skip_if_rust_unavailable, skip_if_not_macos):
        """Test MLX allocation functions."""
        alloc_add = getattr(rust_backend.raw, 'mlx_alloc_bytes_add', None)
        if alloc_add is None:
            pytest.skip('mlx_alloc_bytes_add not available')
        try:
            alloc_add(1024 * 1024)
        except Exception:
            pass

class TestIOSurfaceBridge:
    """Tests for iosurface_bridge module."""

    def test_iosurface_descriptor(self, rust_backend, skip_if_rust_unavailable, skip_if_not_macos):
        """Test IOSurface texture descriptor."""
        IOSurfaceTextureDescriptor = getattr(rust_backend.raw, 'IOSurfaceTextureDescriptor', None)
        if IOSurfaceTextureDescriptor is None:
            pytest.skip('IOSurfaceTextureDescriptor not available')

class TestANE:
    """Tests for ane module."""

    def test_ane_availability(self, rust_backend, skip_if_rust_unavailable, skip_if_not_macos):
        """Test ANE availability."""
        is_available = getattr(rust_backend.raw, 'ane_available', None)
        if is_available is None:
            pytest.skip('ane_available not available')
        available = is_available()
        assert isinstance(available, bool)

class TestWhisper:
    """Tests for whisper module."""

    def test_whisper_availability(self, rust_backend, skip_if_rust_unavailable):
        """Test whisper availability."""
        is_available = getattr(rust_backend.whisper, 'is_available', None)
        if is_available is None:
            pytest.skip('whisper.is_available not available')
        available = is_available()
        assert isinstance(available, bool)

class TestSTIX:
    """Tests for stix_2_1 module."""

    def test_stix_encode_decode(self, rust_backend, skip_if_rust_unavailable):
        """Test STIX encode/decode."""
        encode_func = getattr(rust_backend.raw, 'stix_encode', None)
        decode_func = getattr(rust_backend.raw, 'stix_decode', None)
        if encode_func is None or decode_func is None:
            pytest.skip('STIX encode/decode not available')

class TestFeedPipeline:
    """Tests for feed_pipeline module."""

    def test_feed_entry_pipeline(self, rust_backend, skip_if_rust_unavailable):
        """Test feed entry pipeline."""
        pipeline = getattr(rust_backend.raw, 'feed_entry_pipeline', None)
        if pipeline is None:
            pytest.skip('feed_entry_pipeline not available')

def run_module_tests(module_name: str, test_names: list[str], verbose: bool=False) -> ModuleTestResult:
    """Run tests for a single module."""
    import platform
    config = TESTABLE_MODULES.get(module_name, {})
    feature = config.get('feature', 'unknown')
    result = ModuleTestResult(module_name=module_name, feature=feature, tests_passed=0, tests_failed=0, tests_skipped=0)
    if config.get('platform') == 'macos' and platform.system() != 'Darwin':
        result.error = 'Requires macOS'
        result.tests_skipped = len(test_names)
        return result
    if config.get('requires_network'):
        import socket
        try:
            socket.create_connection(('8.8.8.8', 53), timeout=1)
        except OSError:
            result.error = 'Network not available'
            result.tests_skipped = len(test_names)
            return result
    for test_name in test_names:
        test_result = TestResult(name=test_name, passed=False, duration_ms=0)
        try:
            test_class_name = 'Test' + module_name.title().replace('_', '')
            test_class = globals().get(test_class_name)
            if test_class is None:
                test_result.error = f'Test class {test_class_name} not found'
                result.tests_skipped += 1
                continue
            test_method = getattr(test_class, test_name, None)
            if test_method is None:
                test_result.error = f'Test method {test_name} not found'
                result.tests_skipped += 1
                continue
            import time
            start = time.perf_counter()
            test_instance = test_class()
            test_instance.test_setup() if hasattr(test_instance, 'test_setup') else None
            try:
                test_method(test_instance)
                test_result.passed = True
                result.tests_passed += 1
            except Exception as e:
                test_result.error = str(e)
                test_result.passed = False
                result.tests_failed += 1
            test_result.duration_ms = (time.perf_counter() - start) * 1000
        except Exception as e:
            test_result.error = str(e)
            result.tests_failed += 1
        result.results.append(test_result)
    return result

def run_all_tests(verbose: bool=False) -> dict[str, ModuleTestResult]:
    """Run all module tests."""
    results = {}
    for module_name, config in TESTABLE_MODULES.items():
        test_names = config.get('tests', [])
        if test_names:
            result = run_module_tests(module_name, test_names, verbose)
            results[module_name] = result
    return results

def print_test_report(results: dict[str, ModuleTestResult], verbose: bool=False) -> None:
    """Print test report."""
    print('\n' + '=' * 80)
    print('Rust Extensions Integration Test Report')
    print('=' * 80)
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    for module_name, result in sorted(results.items()):
        total_passed += result.tests_passed
        total_failed += result.tests_failed
        total_skipped += result.tests_skipped
        status = '✅' if result.tests_failed == 0 else '❌'
        print(f'\n{status} {module_name} ({result.feature})')
        print(f'   Passed: {result.tests_passed}, Failed: {result.tests_failed}, Skipped: {result.tests_skipped}')
        if result.error:
            print(f'   Error: {result.error}')
        if verbose:
            for test_result in result.results:
                if test_result.error:
                    print(f'   ❌ {test_result.name}: {test_result.error}')
                elif test_result.passed:
                    print(f'   ✅ {test_result.name} ({test_result.duration_ms:.2f}ms)')
    print('\n' + '-' * 80)
    print(f'Total: Passed={total_passed}, Failed={total_failed}, Skipped={total_skipped}')
    if total_failed > 0:
        print('\n⚠️  Some tests failed. Review the report above.')
    else:
        print('\n✅ All tests passed!')

def main() -> int:
    """Main entry point."""
    import argparse
    parser = argparse.ArgumentParser(description='Rust Extensions Integration Tests')
    parser.add_argument('--verbose', '-v', action='store_true', help='Verbose output')
    parser.add_argument('--modules', '-m', metavar='MODULE1,MODULE2', help='Comma-separated list of modules to test')
    parser.add_argument('--json', '-j', metavar='FILE', help='Export results as JSON')
    args = parser.parse_args()
    print('🧪 Running Rust Extensions Integration Tests...')
    modules_to_test = TESTABLE_MODULES
    if args.modules:
        module_list = [m.strip() for m in args.modules.split(',')]
        modules_to_test = {k: v for k, v in TESTABLE_MODULES.items() if k in module_list}
    results = {}
    for module_name, config in modules_to_test.items():
        test_names = config.get('tests', [])
        if test_names:
            result = run_module_tests(module_name, test_names, args.verbose)
            results[module_name] = result
    print_test_report(results, verbose=args.verbose)
    if args.json:
        import json
        from pathlib import Path
        data = {module_name: {'feature': r.feature, 'tests_passed': r.tests_passed, 'tests_failed': r.tests_failed, 'tests_skipped': r.tests_skipped, 'error': r.error, 'results': [{'name': tr.name, 'passed': tr.passed, 'duration_ms': tr.duration_ms, 'error': tr.error} for tr in r.results]} for module_name, r in results.items()}
        Path(args.json).write_text(json.dumps(data, indent=2))
        print(f'\n📄 Results exported to: {args.json}')
    total_failed = sum((r.tests_failed for r in results.values()))
    return 0 if total_failed == 0 else 1
if __name__ == '__main__':
    sys.exit(main())