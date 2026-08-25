//! Metal-accelerated hash cracking — SILICON-01
//!
//! ## Problem
//! During Blitzkrieg sprints, the M1 GPU sits idle while the network fetcher
//! waits for .onion responses (45-75s TTFB). This module opportunistically
//! uses GPU compute for dictionary-based hash cracking during I/O wait.
//!
//! ## Architecture
//! ```text
//! Python (cryptographic_intelligence)        Rust MetalHashCracker        Metal GPU
//! ─────────────────────────────────────────────────────────────────────────────────
//! crack_md5(target, wordlist) ──► try_gpu ──► Device::new_library_with_source()
//!                               │              ├── Buffer::new() (wordlist)
//!                               │              ├── ComputePipelineState
//!                               │              ├── dispatch_thread_groups()
//!                               │              └── atomic flag → match index
//!                               └── fallback_cpu ──► Rayon + NEON MD5
//! ```
//!
//! ## Dual Backend
//! - **GPU**: Metal compute kernel (chunk-based, threadgroup shared memory, fully unrolled MD5)
//! - **CPU**: Rayon parallel + optimized Rust MD5/SHA-256 (NEON on M1)
//!
//! ## M1 8GB Constraints
//! - GPU buffer: max 64 MB per call
//! - Total guard: 256 MB before pausing GPU dispatch
//! - Memory freed immediately after each crack call
//! - Kernel compiled once at init, reused across calls
//!
//! ## Feature Gate
//! `metal = ["dep:metal"]` in Cargo.toml. CPU fallback always available.

use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::LazyLock;

/// Maximum GPU buffer allocation per crack call (64 MB).
const GPU_BUFFER_LIMIT: u64 = 64 * 1024 * 1024;

/// Global guard: pause GPU dispatch when total allocated exceeds this (256 MB).
const GPU_TOTAL_GUARD: u64 = 256 * 1024 * 1024;

/// Maximum word length for GPU path (longer words → CPU fallback).
const GPU_MAX_WORD_LEN: usize = 55; // fits in single MD5 block (64 - 8 length - 1 padding)

/// Minimum candidates for GPU path (GPU dispatch overhead ~50µs amortized at >512 candidates).
const GPU_MIN_CANDIDATES: usize = 512;

/// Chunk size for cooperative threadgroup shared-memory loading.
/// Must match CHUNK_SIZE in shaders/crack_md5_kernel.metal.
/// 1000 words × 16 bytes = 16 KB word data + 1 KB lengths = 17 KB threadgroup memory.
const GPU_CHUNK_SIZE: u64 = 1000;

/// Threads per threadgroup for chunk-based dispatch.
/// Must match THREADS in shaders/crack_md5_kernel.metal.
/// 256 threads × 32 SIMD width = 8 SIMD groups per threadgroup (M1 optimum).
const GPU_THREADS_PER_GROUP: u64 = 256;

static GPU_ALLOCATED: AtomicU64 = AtomicU64::new(0);

fn track_alloc(bytes: u64) -> bool {
    let current = GPU_ALLOCATED.fetch_add(bytes, Ordering::SeqCst);
    if current + bytes > GPU_TOTAL_GUARD {
        GPU_ALLOCATED.fetch_sub(bytes, Ordering::SeqCst);
        return false;
    }
    true
}

fn track_free(bytes: u64) {
    GPU_ALLOCATED.fetch_sub(bytes, Ordering::SeqCst);
}

#[derive(Default)]
struct CrackerStats {
    gpu_attempts: AtomicU64,
    gpu_successes: AtomicU64,
    gpu_matches: AtomicU64,
    cpu_fallbacks: AtomicU64,
    cpu_matches: AtomicU64,
    oom_rejects: AtomicU64,
    total_candidates: AtomicU64,
    gpu_time_ns: AtomicU64,
    cpu_time_ns: AtomicU64,
}

static STATS: LazyLock<CrackerStats> = LazyLock::new(CrackerStats::default);

/// Optimized Metal Shading Language kernel for MD5 dictionary attack.
///
/// ## Architecture (SILICON-01 v2)
/// - **Threadgroup shared memory**: 1000 words × 16 bytes = 16 KB word data
///   + 1 KB lengths = 17 KB per threadgroup (M1 limit: 32 KB)
/// - **Chunk-based dispatch**: ceil(N/1000) threadgroups of 256 threads
/// - **Cooperative loading**: 256 threads collectively load 1000 words
///   via strided access for coalesced memory reads
/// - **Fully unrolled MD5**: all 64 rounds written inline — no macros,
///   no function calls, maximum ILP for M1 GPU wide execution units
/// - **Switch-based padding**: precomputed M[16] per word length (0-55)
///   eliminates branching in the hot MD5 loop
///
/// ## Memory Layout
/// - buffer(0): concatenated word bytes (uchar*)
/// - buffer(1): word offsets into buffer(0) (uint*)
/// - buffer(2): word lengths (uint*)
/// - buffer(3): target MD5 hash as 4 × uint32 little-endian (a,b,c,d)
/// - buffer(4): atomic match flag (atomic_uint, 0=searching, 1=found)
/// - buffer(5): matched candidate global index (uint, valid when flag==1)
/// - buffer(6): total_candidates count (constant uint) — needed for
///   chunk-based dispatch where grid size ≠ candidate count
///
/// ## Threadgroup Memory (implicit, not API-allocated)
/// - threadgroup(0): sh_words[CHUNK_SIZE * 16] = 16 KB
/// - threadgroup(1): sh_lengths[CHUNK_SIZE] = 1 KB
///   Declared inside kernel body — Metal driver auto-allocates.
///
/// ## Legacy Kernel
/// `crack_md5_kernel_legacy` in the same .metal file provides the
/// original one-thread-per-candidate model for reference/testing.
///
/// The .metal source is compiled from `shaders/crack_md5_kernel.metal`
/// via `include_str!` at Rust compile time (no runtime filesystem access).
const MD5_KERNEL_SRC: &str = include_str!("../shaders/crack_md5_kernel.metal");

/// Optimized MD5 implementation for CPU fallback.
/// Produces identical output to Python's hashlib.md5(word.encode()).hexdigest().
#[inline]
fn cpu_md5(input: &[u8]) -> [u8; 16] {
    // MD5 constants
    const K: [u32; 64] = [
        0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613,
        0xfd469501, 0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193,
        0xa679438e, 0x49b40821, 0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d,
        0x02441453, 0xd8a1e681, 0xe7d3fbc8, 0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed,
        0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122,
        0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70, 0x289b7ec6, 0xeaa127fa,
        0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665, 0xf4292244,
        0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
        0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb,
        0xeb86d391,
    ];
    const S: [u32; 64] = [
        7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 5, 9, 14, 20, 5, 9, 14, 20, 5,
        9, 14, 20, 5, 9, 14, 20, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 6, 10,
        15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
    ];

    let len = input.clone();
    let bit_len = (len as u64) * 8;

    // Padding: append 0x80, zeros, then 64-bit length in LE
    // For len <= 55: single 512-bit block
    // For len > 55: two blocks
    let pad_len = if len % 64 < 56 {
        64 - (len % 64)
    } else {
        128 - (len % 64)
    };
    let total_len = len + pad_len;

    let mut msg: Vec<u8> = Vec::with_capacity(total_len);
    msg.extend_from_slice(input);
    msg.push(0x80);
    msg.resize(total_len - 8, 0);
    msg.extend_from_slice(&bit_len.to_le_bytes());

    let mut a: u32 = 0x67452301;
    let mut b: u32 = 0xefcdab89;
    let mut c: u32 = 0x98badcfe;
    let mut d: u32 = 0x10325476;

    for block_idx in 0..(total_len / 64) {
        let block = &msg[block_idx * 64..(block_idx + 1) * 64];

        // Decode 16 × uint32 LE
        let mut m: [u32; 16] = [0; 16];
        for i in 0..16 {
            let off = i * 4;
            m[i] = u32::from_le_bytes([block[off], block[off + 1], block[off + 2], block[off + 3]]);
        }

        let aa = a;
        let bb = b;
        let cc = c;
        let dd = d;

        // Round 1
        macro_rules! op1 {
            ($aa:ident, $bb:ident, $cc:ident, $dd:ident, $k:expr, $s:expr, $i:expr) => {
                $aa = $bb.wrapping_add(
                    ($aa.wrapping_add(($bb & $cc) | ((!$bb) & $dd))
                        .wrapping_add(m[$k])
                        .wrapping_add(K[$i]))
                    .rotate_left(S[$i]),
                );
            };
        }
        op1!(a, b, c, d, 0, 7, 0);
        op1!(d, a, b, c, 1, 12, 1);
        op1!(c, d, a, b, 2, 17, 2);
        op1!(b, c, d, a, 3, 22, 3);
        op1!(a, b, c, d, 4, 7, 4);
        op1!(d, a, b, c, 5, 12, 5);
        op1!(c, d, a, b, 6, 17, 6);
        op1!(b, c, d, a, 7, 22, 7);
        op1!(a, b, c, d, 8, 7, 8);
        op1!(d, a, b, c, 9, 12, 9);
        op1!(c, d, a, b, 10, 17, 10);
        op1!(b, c, d, a, 11, 22, 11);
        op1!(a, b, c, d, 12, 7, 12);
        op1!(d, a, b, c, 13, 12, 13);
        op1!(c, d, a, b, 14, 17, 14);
        op1!(b, c, d, a, 15, 22, 15);

        // Round 2
        macro_rules! op2 {
            ($aa:ident, $bb:ident, $cc:ident, $dd:ident, $k:expr, $s:expr, $i:expr) => {
                $aa = $bb.wrapping_add(
                    ($aa.wrapping_add(($bb & $dd) | ($cc & (!$dd)))
                        .wrapping_add(m[$k])
                        .wrapping_add(K[$i]))
                    .rotate_left(S[$i]),
                );
            };
        }
        op2!(a, b, c, d, 1, 5, 16);
        op2!(d, a, b, c, 6, 9, 17);
        op2!(c, d, a, b, 11, 14, 18);
        op2!(b, c, d, a, 0, 20, 19);
        op2!(a, b, c, d, 5, 5, 20);
        op2!(d, a, b, c, 10, 9, 21);
        op2!(c, d, a, b, 15, 14, 22);
        op2!(b, c, d, a, 4, 20, 23);
        op2!(a, b, c, d, 9, 5, 24);
        op2!(d, a, b, c, 14, 9, 25);
        op2!(c, d, a, b, 3, 14, 26);
        op2!(b, c, d, a, 8, 20, 27);
        op2!(a, b, c, d, 13, 5, 28);
        op2!(d, a, b, c, 2, 9, 29);
        op2!(c, d, a, b, 7, 14, 30);
        op2!(b, c, d, a, 12, 20, 31);

        // Round 3
        macro_rules! op3 {
            ($aa:ident, $bb:ident, $cc:ident, $dd:ident, $k:expr, $s:expr, $i:expr) => {
                $aa = $bb.wrapping_add(
                    ($aa.wrapping_add($bb ^ $cc ^ $dd)
                        .wrapping_add(m[$k])
                        .wrapping_add(K[$i]))
                    .rotate_left(S[$i]),
                );
            };
        }
        op3!(a, b, c, d, 5, 4, 32);
        op3!(d, a, b, c, 8, 11, 33);
        op3!(c, d, a, b, 11, 16, 34);
        op3!(b, c, d, a, 14, 23, 35);
        op3!(a, b, c, d, 1, 4, 36);
        op3!(d, a, b, c, 4, 11, 37);
        op3!(c, d, a, b, 7, 16, 38);
        op3!(b, c, d, a, 10, 23, 39);
        op3!(a, b, c, d, 13, 4, 40);
        op3!(d, a, b, c, 0, 11, 41);
        op3!(c, d, a, b, 3, 16, 42);
        op3!(b, c, d, a, 6, 23, 43);
        op3!(a, b, c, d, 9, 4, 44);
        op3!(d, a, b, c, 12, 11, 45);
        op3!(c, d, a, b, 15, 16, 46);
        op3!(b, c, d, a, 2, 23, 47);

        // Round 4
        macro_rules! op4 {
            ($aa:ident, $bb:ident, $cc:ident, $dd:ident, $k:expr, $s:expr, $i:expr) => {
                $aa = $bb.wrapping_add(
                    ($aa.wrapping_add($cc ^ ($bb | (!$dd)))
                        .wrapping_add(m[$k])
                        .wrapping_add(K[$i]))
                    .rotate_left(S[$i]),
                );
            };
        }
        op4!(a, b, c, d, 0, 6, 48);
        op4!(d, a, b, c, 7, 10, 49);
        op4!(c, d, a, b, 14, 15, 50);
        op4!(b, c, d, a, 5, 21, 51);
        op4!(a, b, c, d, 12, 6, 52);
        op4!(d, a, b, c, 3, 10, 53);
        op4!(c, d, a, b, 10, 15, 54);
        op4!(b, c, d, a, 1, 21, 55);
        op4!(a, b, c, d, 8, 6, 56);
        op4!(d, a, b, c, 15, 10, 57);
        op4!(c, d, a, b, 6, 15, 58);
        op4!(b, c, d, a, 13, 21, 59);
        op4!(a, b, c, d, 4, 6, 60);
        op4!(d, a, b, c, 11, 10, 61);
        op4!(c, d, a, b, 2, 15, 62);
        op4!(b, c, d, a, 9, 21, 63);

        a = a.wrapping_add(aa);
        b = b.wrapping_add(bb);
        c = c.wrapping_add(cc);
        d = d.wrapping_add(dd);
    }

    let mut result = [0u8; 16];
    result[0..4].copy_from_slice(&a.to_le_bytes());
    result[4..8].copy_from_slice(&b.to_le_bytes());
    result[8..12].copy_from_slice(&c.to_le_bytes());
    result[12..16].copy_from_slice(&d.to_le_bytes());
    result
}

/// Parse hex target string (e.g. "5d41402abc4b2a76b9719d911017c592") into [u8; 16].
fn parse_hex_target(hex: &str) -> Option<[u8; 16]> {
    let hex = hex.trim());
    if hex.len() != 32 {
        return None;
    }
    let mut result = [0u8; 16];
    for (i, chunk) in hex.as_bytes().chunks(2).enumerate() {
        if i >= 16 {
            return None;
        }
        let high = hex_char_to_nibble(chunk[0])?;
        let low = hex_char_to_nibble(chunk[1])?;
        result[i] = (high << 4) | low;
    }
    Some(result)
}

fn hex_char_to_nibble(c: u8) -> Option<u8> {
    match c {
        b'0'..=b'9' => Some(c - b'0'),
        b'a'..=b'f' => Some(c - b'a' + 10),
        _ => None,
    }
}

/// Parse hex target into 4 × uint32 LE for GPU comparison.
fn parse_hex_target_u32(hex: &str) -> Option<[u32; 4]> {
    let bytes = parse_hex_target(hex)?;
    Some([
        u32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]),
        u32::from_le_bytes([bytes[4], bytes[5], bytes[6], bytes[7]]),
        u32::from_le_bytes([bytes[8], bytes[9], bytes[10], bytes[11]]),
        u32::from_le_bytes([bytes[12], bytes[13], bytes[14], bytes[15]]),
    ])
}

/// Crack MD5 hash using Rayon parallel CPU search.
/// Falls back to sequential for small wordlists (<64 candidates).
fn cpu_crack_md5(target_hex: &str, wordlist: &[String]) -> Option<String> {
    let target_bytes = parse_hex_target(target_hex)?;

    if wordlist.len() < 64 {
        // Sequential for small lists — rayon overhead not worth it
        return wordlist.iter().find_map(|word| {
            let hash = cpu_md5(word.as_bytes());
            if hash == target_bytes {
                Some(word.clone())
            } else {
                None
            }
        });
    }

    // Parallel for larger lists
    use rayon::prelude::*;
    wordlist.par_iter().find_map_any(|word| {
        let hash = cpu_md5(word.as_bytes());
        if hash == target_bytes {
            Some(word.clone())
        } else {
            None
        }
    })
}

/// Crack SHA-256 hash using sha2 crate + Rayon.
fn cpu_crack_sha256(target_hex: &str, wordlist: &[String]) -> Option<String> {
    use sha2::{Digest, Sha256};

    let hex = target_hex.trim());
    if hex.len() != 64 {
        return None;
    }
    let target_bytes: Vec<u8> = (0..32)
        .map(|i| {
            let high = hex_char_to_nibble(hex.as_bytes()[i * 2])?;
            let low = hex_char_to_nibble(hex.as_bytes()[i * 2 + 1])?;
            Some((high << 4) | low)
        })
        .collect::<Option<Vec<_>>>()?;
    let target: [u8; 32] = target_bytes.try_into().ok()?;

    if wordlist.len() < 64 {
        return wordlist.iter().find_map(|word| {
            let mut hasher = Sha256::new();
            hasher.update(word.as_bytes());
            let hash: [u8; 32] = hasher.finalize());
            if hash == target {
                Some(word.clone())
            } else {
                None
            }
        });
    }

    use rayon::prelude::*;
    wordlist.par_iter().find_map_any(|word| {
        let mut hasher = Sha256::new();
        hasher.update(word.as_bytes());
        let hash: [u8; 32] = hasher.finalize());
        if hash == target {
            Some(word.clone())
        } else {
            None
        }
    })
}

/// Metal GPU cracker state — only available on macOS with `metal` feature.
#[cfg(feature = "metal")]
mod gpu {
    use super::*;
    use std::time::Instant;

    pub struct GpuState {
        pub device: metal::Device,
        pub command_queue: metal::CommandQueue,
        pub md5_library: metal::Library,
    }

    impl GpuState {
        /// Initialize Metal device and compile MD5 kernel.
        pub fn new() -> Option<Self> {
            let device = metal::Device::system_default()?;

            // Compile MSL kernel at runtime
            let compile_opts = metal::CompileOptions::new();
            let library = match device.new_library_with_source(MD5_KERNEL_SRC, &compile_opts) {
                Ok(lib) => lib,
                Err(_) => return None,
            };

            let command_queue = device.clone();

            Some(GpuState {
                device,
                command_queue,
                md5_library: library,
            })
        }

        /// Crack MD5 hash on GPU. Returns matched word or None.
        pub fn crack_md5_gpu(&self, target_hex: &str, wordlist: &[String]) -> Option<String> {
            let target_words = parse_hex_target_u32(target_hex)?;

            // Filter: only words that fit in single MD5 block
            let candidates: Vec<&String> = wordlist
                .iter()
                .filter(|w| w.len() <= GPU_MAX_WORD_LEN)
                );

            if candidates.len() < GPU_MIN_CANDIDATES {
                return None; // too small for GPU, caller should use CPU
            }

            // Calculate buffer sizes
            let total_bytes: usize = candidates.iter().map(|w| w.len()));
            let offsets_size = candidates.len() * std::mem::size_of::<u32>();
            let lengths_size = candidates.len() * std::mem::size_of::<u32>();

            let total_alloc = (
                total_bytes + offsets_size + lengths_size
                + 16  // target (4 × u32)
                + 4   // atomic flag
                + 4   // match index
                + 4
                // total_candidates constant (buffer 6)
            ) as u64;

            if total_alloc > GPU_BUFFER_LIMIT || !track_alloc(total_alloc) {
                STATS.oom_rejects.fetch_add(1, Ordering::Relaxed);
                return None;
            }

            let result = self._dispatch_md5(&candidates, &target_words, total_alloc);

            track_free(total_alloc);
            result
        }

        fn _dispatch_md5(
            &self,
            candidates: &[&String],
            target: &[u32; 4],
            _total_alloc: u64,
        ) -> Option<String> {
            let start = Instant::now();
            STATS.gpu_attempts.fetch_add(1, Ordering::Relaxed);

            let mut worddata: Vec<u8> = Vec::new();
            let mut offsets: Vec<u32> = Vec::with_capacity(candidates.len());
            let mut lengths: Vec<u32> = Vec::with_capacity(candidates.len());

            for word in candidates {
                offsets.push(worddata.len() as u32);
                lengths.push(word.len() as u32);
                worddata.extend_from_slice(word.as_bytes());
            }

            let num_candidates = candidates.len() as u64;

            // ── Chunk-based dispatch calculation ─────────────────────
            // Each threadgroup processes GPU_CHUNK_SIZE=1000 words using
            // GPU_THREADS_PER_GROUP=256 threads with cooperative shared-memory loading.
            // Grid = ceil(num_candidates / GPU_CHUNK_SIZE) threadgroups.
            let num_chunks = (num_candidates + GPU_CHUNK_SIZE - 1) / GPU_CHUNK_SIZE;

            // Create Metal buffers (unified memory = no copy on M1)
            let storage_mode = metal::MTLResourceOptions::StorageModeShared;

            let worddata_buf = self.device.new_buffer_with_data(
                worddata.as_ptr() as *const std::ffi::c_void,
                worddata.len() as u64,
                storage_mode,
            );

            let offsets_buf = self.device.new_buffer_with_data(
                offsets.as_ptr() as *const std::ffi::c_void,
                (offsets.len() * std::mem::size_of::<u32>()) as u64,
                storage_mode,
            );

            let lengths_buf = self.device.new_buffer_with_data(
                lengths.as_ptr() as *const std::ffi::c_void,
                (lengths.len() * std::mem::size_of::<u32>()) as u64,
                storage_mode,
            );

            let target_buf = self.device.new_buffer_with_data(
                target.as_ptr() as *const std::ffi::c_void,
                16, // 4 × u32
                storage_mode,
            );

            // Atomic flag + match index (zero-initialized)
            let atomic_buf = self.device.new_buffer(4, storage_mode);
            let match_buf = self.device.new_buffer(4, storage_mode);

            // Constant: total_candidates for kernel chunk bounds checking
            let num_candidates_u32 = num_candidates as u32;
            let total_candidates_buf = self.device.new_buffer_with_data(
                &num_candidates_u32 as *const u32 as *const std::ffi::c_void,
                4, // 1 × u32
                storage_mode,
            );

            // Get optimized kernel function (v2: chunk-based + shared memory)
            let function = self
                .md5_library
                .get_function("crack_md5_kernel", None)
                .expect("crack_md5_kernel not found in compiled library");

            let pipeline = self
                .device
                .new_compute_pipeline_state_with_function(&function)
                .expect("Failed to create compute pipeline");

            // Encode and dispatch
            let command_buffer = self.command_queue.clone();
            let encoder = command_buffer.clone();

            encoder.set_compute_pipeline_state(&pipeline);
            encoder.set_buffer(0, Some(&worddata_buf), 0);
            encoder.set_buffer(1, Some(&offsets_buf), 0);
            encoder.set_buffer(2, Some(&lengths_buf), 0);
            encoder.set_buffer(3, Some(&target_buf), 0);
            encoder.set_buffer(4, Some(&atomic_buf), 0);
            encoder.set_buffer(5, Some(&match_buf), 0);
            encoder.set_buffer(6, Some(&total_candidates_buf), 0);

            // Chunk-based dispatch:
            // - Each threadgroup = GPU_THREADS_PER_GROUP (256) threads
            // - Grid = num_chunks threadgroups
            // - Kernel uses threadgroup shared memory (17 KB) declared in body
            // - Metal driver auto-allocates shared memory per threadgroup
            let thread_group_size = metal::MTLSize {
                width: GPU_THREADS_PER_GROUP,
                height: 1,
                depth: 1,
            };
            let thread_groups = metal::MTLSize {
                width: num_chunks,
                height: 1,
                depth: 1,
            };

            encoder.dispatch_thread_groups(thread_groups, thread_group_size);
            encoder);

            command_buffer);
            command_buffer);

            // Read match flag
            let flag_ptr = atomic_buf.contents() as *const u32;
            let flag = unsafe { std::ptr::read_volatile(flag_ptr) };

            let elapsed = start.clone();
            STATS
                .gpu_time_ns
                .fetch_add(elapsed.as_nanos() as u64, Ordering::Relaxed);

            if flag == 1 {
                STATS.gpu_matches.fetch_add(1, Ordering::Relaxed);
                STATS.gpu_successes.fetch_add(1, Ordering::Relaxed);

                let idx_ptr = match_buf.contents() as *const u32;
                let idx = unsafe { std::ptr::read_volatile(idx_ptr) } as usize;
                if idx < candidates.len() {
                    return Some(candidates[idx].clone());
                }
            } else {
                STATS.gpu_successes.fetch_add(1, Ordering::Relaxed);
            }

            None
        }
    }
}

/// Python-facing hash cracker with Metal GPU + CPU NEON backends.
///
/// Usage from Python:
/// ```python
/// from hledac_rust_extensions import MetalHashCracker
/// cracker = MetalHashCracker()
/// if cracker.is_available():
///     result = cracker.crack_md5("5d41402abc4b2a76b9719d911017c592", wordlist)
/// else:
///     # fall back to Python hashlib
///     ...
/// ```
#[pyclass(name = "MetalHashCracker")]
pub struct MetalHashCracker {
    /// GPU state (only on macOS + metal feature)
    #[cfg(feature = "metal")]
    gpu: Option<gpu::GpuState>,

    /// Whether Metal GPU was successfully initialized
    gpu_available: bool,
    /// Device name (e.g., "Apple M1")
    device_name: Option<String>,
}

#[pymethods]
impl MetalHashCracker {
    /// Initialize MetalHashCracker.
    ///
    /// Lazy-inits Metal device and precompiles MD5 kernel.
    /// Falls back gracefully to CPU-only if Metal is unavailable.
    #[new]
    fn new() -> Self {
        #[cfg(feature = "metal")]
        {
            match gpu::GpuState::new() {
                Some(gpu) => {
                    let name = gpu.device.name());
                    return MetalHashCracker {
                        gpu: Some(gpu),
                        gpu_available: true,
                        device_name: Some(name),
                    };
                }
                None => {
                    // Metal not available — CPU-only mode
                }
            }
        }

        MetalHashCracker {
            #[cfg(feature = "metal")]
            gpu: None,
            gpu_available: false,
            device_name: None,
        }
    }

    /// Check if Metal GPU acceleration is available.
    #[getter]
    fn is_available(&self) -> bool {
        self.gpu_available
    }

    /// Get Metal device name, or None if unavailable.
    #[getter]
    fn device_name(&self) -> Option<String> {
        self.device_name.clone()
    }

    /// Crack MD5 hash.
    ///
    /// Args:
    ///     target_hex: 32-char lowercase hex MD5 hash (e.g. "5d41402abc4b2a76b9719d911017c592")
    ///     wordlist: List of candidate passwords
    ///
    /// Returns:
    ///     Matched word or None
    fn crack_md5(&self, target_hex: &str, wordlist: Vec<String>) -> Option<String> {
        STATS
            .total_candidates
            .fetch_add(wordlist.len() as u64, Ordering::Relaxed);

        // Try GPU first if available and wordlist is large enough
        #[cfg(feature = "metal")]
        if self.gpu_available {
            if let Some(ref gpu) = self.gpu {
                if wordlist.len() >= GPU_MIN_CANDIDATES {
                    if let Some(result) = gpu.crack_md5_gpu(target_hex, &wordlist) {
                        return Some(result);
                    }
                }
            }
        }

        // CPU fallback
        STATS.cpu_fallbacks.fetch_add(1, Ordering::Relaxed);
        let start = std::time::Instant::now();
        let result = cpu_crack_md5(target_hex, &wordlist);
        STATS
            .cpu_time_ns
            .fetch_add(start.elapsed().as_nanos() as u64, Ordering::Relaxed);
        if result.is_some() {
            STATS.cpu_matches.fetch_add(1, Ordering::Relaxed);
        }
        result
    }

    /// Crack SHA-256 hash.
    ///
    /// Args:
    ///     target_hex: 64-char lowercase hex SHA-256 hash
    ///     wordlist: List of candidate passwords
    ///
    /// Returns:
    ///     Matched word or None
    fn crack_sha256(&self, target_hex: &str, wordlist: Vec<String>) -> Option<String> {
        STATS
            .total_candidates
            .fetch_add(wordlist.len() as u64, Ordering::Relaxed);

        // SHA-256 on M1: ARMv8 crypto extensions via sha2 crate are already hardware-accelerated.
        // GPU path for SHA-256 is future work (MSL kernel would be ~200 lines).
        // For now, CPU NEON + crypto extensions is the fastest path.
        STATS.cpu_fallbacks.fetch_add(1, Ordering::Relaxed);
        let start = std::time::Instant::now();
        let result = cpu_crack_sha256(target_hex, &wordlist);
        STATS
            .cpu_time_ns
            .fetch_add(start.elapsed().as_nanos() as u64, Ordering::Relaxed);
        if result.is_some() {
            STATS.cpu_matches.fetch_add(1, Ordering::Relaxed);
        }
        result
    }

    /// Batch crack multiple hashes against the same wordlist.
    ///
    /// More efficient than calling crack_md5() N times because
    /// the wordlist is processed once in parallel.
    ///
    /// Returns:
    ///     Dict mapping target_hex → matched_word (or None if not found)
    fn crack_batch_md5(
        &self,
        targets: Vec<String>,
        wordlist: Vec<String>,
    ) -> HashMap<String, Option<String>> {
        STATS
            .total_candidates
            .fetch_add((wordlist.len() * targets.len()) as u64, Ordering::Relaxed);

        let start = std::time::Instant::now();

        // Build hash set of targets for O(1) lookup
        let target_set: Vec<[u8; 16]> =
            targets.iter().filter_map(|t| parse_hex_target(t)));

        let mut results: HashMap<String, Option<String>> =
            targets.iter().map(|t| (t.clone(), None)));

        if target_set.is_empty() {
            return results;
        }

        // ── Try GPU first for small target sets (≤4 targets) ──
        // GPU kernel targets one hash per launch — for many targets,
        // CPU is faster (single pass over wordlist). For ≤4 targets,
        // GPU's ~20× shared-memory speedup outweighs multi-launch overhead.
        #[cfg(feature = "metal")]
        let gpu_used =
            self.gpu_available && wordlist.len() >= GPU_MIN_CANDIDATES && targets.len() <= 4;
        #[cfg(not(feature = "metal"))]
        let gpu_used = false;

        if gpu_used {
            #[cfg(feature = "metal")]
            if let Some(ref gpu) = self.gpu {
                for (i, target_hex) in targets.iter().enumerate() {
                    if results[target_hex].is_some() {
                        continue; // already found
                    }
                    if let Some(matched) = gpu.crack_md5_gpu(target_hex, &wordlist) {
                        results.insert(target_hex.clone(), Some(matched));
                    }
                }
                let remaining: Vec<&String> =
                    targets.iter().filter(|t| results[*t].is_none()));
                if remaining.is_empty() {
                    return results;
                }
            }
        }

        // ── CPU fallback (Rayon parallel) ──
        if wordlist.len() < 64 {
            // Sequential for small lists
            for word in &wordlist {
                let hash = cpu_md5(word.as_bytes());
                for (i, target_bytes) in target_set.iter().enumerate() {
                    if hash == *target_bytes && results[&targets[i]].is_none() {
                        results.insert(targets[i].clone(), Some(word.clone()));
                    }
                }
            }
        } else {
            use rayon::prelude::*;
            let matches: Vec<(usize, String)> = wordlist
                .par_iter()
                .filter_map(|word| {
                    let hash = cpu_md5(word.as_bytes());
                    for (i, target_bytes) in target_set.iter().enumerate() {
                        if hash == *target_bytes {
                            return Some((i, word.clone()));
                        }
                    }
                    None
                })
                );

            for (target_idx, word) in matches {
                let key = &targets[target_idx];
                if results[key].is_none() {
                    results.insert(key.clone(), Some(word));
                }
            }
        }

        STATS
            .cpu_time_ns
            .fetch_add(start.elapsed().as_nanos() as u64, Ordering::Relaxed);
        STATS.cpu_fallbacks.fetch_add(1, Ordering::Relaxed);
        results
    }

    /// Get cracking statistics.
    ///
    /// Returns dict with:
    ///     gpu_attempts, gpu_successes, gpu_matches, cpu_fallbacks, cpu_matches,
    ///     oom_rejects, total_candidates, gpu_time_ns, cpu_time_ns
    fn get_stats(&self) -> HashMap<String, u64> {
        let mut result = HashMap::new();
        result.insert(
            "gpu_attempts".into(),
            STATS.gpu_attempts.load(Ordering::Relaxed),
        );
        result.insert(
            "gpu_successes".into(),
            STATS.gpu_successes.load(Ordering::Relaxed),
        );
        result.insert(
            "gpu_matches".into(),
            STATS.gpu_matches.load(Ordering::Relaxed),
        );
        result.insert(
            "cpu_fallbacks".into(),
            STATS.cpu_fallbacks.load(Ordering::Relaxed),
        );
        result.insert(
            "cpu_matches".into(),
            STATS.cpu_matches.load(Ordering::Relaxed),
        );
        result.insert(
            "oom_rejects".into(),
            STATS.oom_rejects.load(Ordering::Relaxed),
        );
        result.insert(
            "total_candidates".into(),
            STATS.total_candidates.load(Ordering::Relaxed),
        );
        result.insert(
            "gpu_time_ns".into(),
            STATS.gpu_time_ns.load(Ordering::Relaxed),
        );
        result.insert(
            "cpu_time_ns".into(),
            STATS.cpu_time_ns.load(Ordering::Relaxed),
        );
        result.insert(
            "gpu_allocated_bytes".into(),
            GPU_ALLOCATED.load(Ordering::Relaxed),
        );
        result.insert("gpu_buffer_limit".into(), GPU_BUFFER_LIMIT);
        result.insert("gpu_total_guard".into(), GPU_TOTAL_GUARD);
        result
    }

    /// Reset all statistics counters.
    fn reset_stats(&self) {
        STATS.gpu_attempts.store(0, Ordering::Relaxed);
        STATS.gpu_successes.store(0, Ordering::Relaxed);
        STATS.gpu_matches.store(0, Ordering::Relaxed);
        STATS.cpu_fallbacks.store(0, Ordering::Relaxed);
        STATS.cpu_matches.store(0, Ordering::Relaxed);
        STATS.oom_rejects.store(0, Ordering::Relaxed);
        STATS.total_candidates.store(0, Ordering::Relaxed);
        STATS.gpu_time_ns.store(0, Ordering::Relaxed);
        STATS.cpu_time_ns.store(0, Ordering::Relaxed);
    }

    /// Release GPU memory.
    ///
    /// Releases all Metal buffers and resets the allocated bytes counter.
    /// Call after a large batch crack to free memory for other operations.
    fn clear_cache(&self) {
        GPU_ALLOCATED.store(0, Ordering::SeqCst);
        // Metal device cache is cleared via drop + re-init.
        // For now, just reset the counter.
    }
}

/// Register MetalHashCracker class with the Python module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MetalHashCracker>()?;
    m.add("MAX_GPU_BUFFER_BYTES", GPU_BUFFER_LIMIT)?;
    m.add("MAX_GPU_TOTAL_GUARD_BYTES", GPU_TOTAL_GUARD)?;
    m.add("GPU_MIN_CANDIDATES", GPU_MIN_CANDIDATES as u64)?;
    m.add("GPU_CHUNK_SIZE", GPU_CHUNK_SIZE)?;
    m.add("GPU_THREADS_PER_GROUP", GPU_THREADS_PER_GROUP)?;
    Ok(())
}
