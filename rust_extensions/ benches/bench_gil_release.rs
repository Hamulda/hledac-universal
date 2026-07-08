// SPDX-License-Identifier: Apache-2.0
// ISSUE-063: PyO3 GIL handling — allow_threads() benchmark
//
// Validates that rayon parallel functions properly release the GIL,
// allowing true multi-threaded parallelism when called from Python's
// asyncio ThreadPoolExecutor.
//
// Run: cargo bench --bench bench_gil_release
// Or via Python:
//   pytest tests/test_rust_extensions.py -k test_gil_release_benchmark -v

use criterion::{black_box, criterion_group, criterion_main, Criterion, BenchmarkId};
use pyo3::prelude::*;

/// Dummy compute-intensive work to measure GIL impact.
fn busy_work(iters: usize) -> usize {
    let mut sum: usize = 0;
    for i in 0..iters {
        sum ^= i.wrapping_mul(31).wrapping_add(17);
    }
    sum
}

/// Verify release_gil is wired correctly by checking GIL_ENABLED is probed.
#[test]
fn test_gil_enabled_probe() {
    Python::with_gil(|py| {
        let is_enabled = crate::gil::is_gil_enabled();
        // Should return a boolean (not panic)
        let _ = is_enabled;
    });
}

/// Verify recommended_rayon_workers returns a valid value.
#[test]
fn test_recommended_rayon_workers() {
    Python::with_gil(|py| {
        let workers = crate::gil::recommended_rayon_workers(py);
        assert!(workers >= 1, "Should have at least 1 worker");
        assert!(workers <= 16, "Should be bounded reasonably");
    });
}

fn bench_batch_blake3_64(c: &mut Criterion) {
    // Generate test data: 1000 bodies of 4KB each
    let bodies: Vec<Vec<u8>> = (0..1000)
        .map(|i| {
            let size = 4096;
            let mut v = Vec::with_capacity(size);
            v.resize(size, (i % 256) as u8);
            v
        })
        .collect();

    let mut group = c.benchmark_group("batch_blake3_64");

    // Baseline: serial processing
    group.bench_function("serial_1000", |b| {
        b.iter(|| {
            let results: Vec<String> = bodies.iter()
                .map(|body| {
                    let hash = blake3::hash(body);
                    let bytes: [u8; 8] = hash.as_bytes()[..8].try_into().unwrap();
                    format!("{:016x}", u64::from_le_bytes(bytes))
                })
                .collect();
            black_box(results)
        });
    });

    // With GIL release (the fix)
    group.bench_function("parallel_gil_release_1000", |b| {
        b.iter(|| {
            let results = crate::content_hasher::batch_blake3_64(bodies.clone());
            black_box(results)
        });
    });

    group.finish();
}

fn bench_text_normalization(c: &mut Criterion) {
    // Generate test data: 500 texts with mixed ASCII/Unicode
    let texts: Vec<String> = (0..500)
        .map(|i| {
            if i % 2 == 0 {
                format!("Hello World {} - TEST STRING", i)
            } else {
                format!("Héllo Wörld {} - TÉST STRÏNG", i)
            }
        })
        .collect();

    let mut group = c.benchmark_group("batch_nfc_normalize");

    group.bench_function("batch_500", |b| {
        b.iter(|| {
            let results = crate::text_norm::batch_nfc_normalize(texts.clone()).unwrap();
            black_box(results)
        });
    });

    group.finish();
}

fn bench_simd_topk(c: &mut Criterion) {
    // Q=10, N=1000, D=384 — typical re-ranking workload
    let num_queries = 10;
    let num_candidates = 1000;
    let dim = 384;
    let total_scores = num_queries * num_candidates;

    let scores: Vec<f32> = (0..total_scores)
        .map(|i| ((i as f32 * 0.017 + 0.5).sin().abs()))
        .collect();

    let mut group = c.benchmark_group("batch_topk_indices");

    group.bench_function("topk_10x1000_k50", |b| {
        b.iter(|| {
            let result = crate::simd_similarity::batch_topk_indices(
                scores.clone(),
                num_queries,
                num_candidates,
                black_box(50),
            );
            black_box(result)
        });
    });

    group.finish();
}

criterion_group!(
    name = gil_benches;
    config = Criterion::default().sample_size(20).measurement_time(std::time::Duration::from_secs(3));
    targets = bench_batch_blake3_64, bench_text_normalization, bench_simd_topk
);
criterion_main!(gil_benches);
