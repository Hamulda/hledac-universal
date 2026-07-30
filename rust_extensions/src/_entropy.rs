//! _entropy — Shared entropy computation primitives for OSINT quality assessment.
//!
//! Extracted from quality_gate.rs to break circular import cycle with zero_copy.rs.
//!
//! Contains:
//!   - ENTROPY_NEON_THRESHOLD constant
//!   - compute_histogram_neon (NEON-vectorized 256-bin histogram)
//!   - entropy_from_histogram (Shannon entropy from histogram)
//!
//! Used by:
//!   - quality_gate.rs (entropy computation)
//!   - zero_copy.rs (batch_entropy_zc)

/// Minimum byte length to engage NEON histogram path.
/// Below this, scalar loop overhead dominates.
pub(crate) const ENTROPY_NEON_THRESHOLD: usize = 64;

// NEON-based 256-bin histogram for aarch64.
// Safe: hist is stack-allocated [u32; 256] written in bounded loop.
// Falls back to scalar on non-NEON targets.
#[cfg(neon_available)]
#[target_feature(enable = "neon")]
pub(crate) unsafe fn compute_histogram_neon(data: &[u8]) -> [u32; 256] {
    use core::arch::aarch64::*;
    let mut hist = [0u32; 256];
    let n = data.len();
    let mut i = 0usize;

    // Process 16 bytes at a time via NEON.
    // Strategy: for each 16-byte chunk, extract each lane and increment its bin directly.
    // This is O(n) vs the naive O(256*n) approach of comparing each byte against all 256 values.
    while i + 16 <= n {
        let bytes = vld1q_u8(data.as_ptr().add(i));

        // Extract individual bytes from the NEON vector and increment their bins.
        // vgetq_lane_u8 extracts a single lane — we do 16 of these per chunk.
        hist[vgetq_lane_u8(bytes, 0) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 1) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 2) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 3) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 4) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 5) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 6) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 7) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 8) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 9) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 10) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 11) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 12) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 13) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 14) as usize] += 1;
        hist[vgetq_lane_u8(bytes, 15) as usize] += 1;

        i += 16;
    }

    // Tail: scalar fallback for remaining bytes.
    for &b in &data[i..] {
        hist[b as usize] += 1;
    }

    hist
}

#[cfg(not(neon_available))]
pub(crate) unsafe fn compute_histogram_neon(_data: &[u8]) -> [u32; 256] {
    // On non-aarch64, fall back to scalar histogram.
    let mut hist = [0u32; 256];
    for &b in _data {
        hist[b as usize] += 1;
    }
    hist
}

/// Shannon entropy computed from a pre-filled 256-bin histogram.
#[inline]
pub(crate) fn entropy_from_histogram(hist: &[u32; 256], total: usize) -> f64 {
    if total == 0 {
        return 0.0;
    }
    let n = total as f64;
    let mut entropy = 0.0_f64;
    for &count in hist.iter() {
        if count > 0 {
            let p = count as f64 / n;
            entropy -= p * p.log2();
        }
    }
    entropy
}
