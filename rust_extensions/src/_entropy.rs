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
#[cfg(target_arch = "aarch64")]
pub(crate) unsafe fn compute_histogram_neon(data: &[u8]) -> [u32; 256] {
    use core::arch::aarch64::*;
    let mut hist = [0u32; 256];
    let n = data.len();
    let mut i = 0usize;

    // Process 16 bytes at a time via NEON.
    // Strategy: for each byte value v, build a 16-lane vector with all lanes = v,
    // compare against the data chunk, and popcount how many lanes matched.
    // vceqq + vaddvq gives 16 counts per lane in a single instruction.
    // Unrolled pairs: process 2 byte values per outer iteration (halves loop overhead).
    while i + 16 <= n {
        let bytes = vld1q_u8(data.as_ptr().add(i));

        let mut v: usize = 0;
        while v < 256 {
            let mask0 = vceqq_u8(bytes, vdupq_n_u8(v as u8));
            let mask1 = vceqq_u8(bytes, vdupq_n_u8((v + 1) as u8));
            let cnt0 = vaddvq_u8(mask0) as u32;
            let cnt1 = vaddvq_u8(mask1) as u32;
            hist[v] = hist[v].wrapping_add(cnt0);
            hist[v + 1] = hist[v + 1].wrapping_add(cnt1);
            v += 2;
        }
        i += 16;
    }

    // Tail: scalar fallback for remaining bytes.
    for &b in &data[i..] {
        hist[b as usize] += 1;
    }

    hist
}

#[cfg(not(target_arch = "aarch64"))]
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
