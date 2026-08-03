// crack_md5_kernel.metal — SILICON-01 Optimized MD5 Hash Cracker
// ==================================================================
// Target: Apple Silicon M1 GPU (8 cores, 32 KB threadgroup memory)
//
// Optimization strategy:
//   1. Threadgroup shared memory for wordlist chunks
//      - 1000 words × 16 bytes = 16 KB word data
//      - 1000 bytes lengths = 1 KB
//      - Total: 17 KB < 32 KB M1 limit
//   2. Cooperative loading: 256 threads load 1000 words via strided access
//   3. Fully unrolled MD5: all 64 rounds explicit (no macros in hot path)
//   4. uint4-aligned shared memory operations where safe
//   5. Chunk-based dispatch: ceil(N/1000) threadgroups of 256 threads
//
// Memory access latency hierarchy (M1 unified memory):
//   Threadgroup shared:  ~5 ns  ← we operate here
//   Device/global:      ~100 ns ← we load from here ONCE per chunk
//
// Thread mapping (per chunk of 1000 words):
//   Thread    0: loads words   0, 256, 512, 768; processes   0, 256, 512, 768
//   Thread    1: loads words   1, 257, 513, 769; processes   1, 257, 513, 769
//   ...
//   Thread  255: loads words 255, 511, 767, 999; processes 255, 511, 767, 999
//
// Author: Hledac Team — SILICON-01
// License: MIT

#include <metal_stdlib>
using namespace metal;

// ─── MD5 Algorithm Constants ────────────────────────────────────────────

// K[i] = floor(abs(sin(i + 1)) * 2^32), i = 0..63
constant uint K[64] = {
    0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee,
    0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
    0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be,
    0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
    0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa,
    0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
    0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed,
    0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
    0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c,
    0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
    0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05,
    0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
    0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039,
    0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
    0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
    0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391
};

// Pre-computed M indices per round (for quick verification)
// Round 1 (FF):  0, 1, 2, 3, 4, 5, 6, 7, 8, 9,10,11,12,13,14,15
// Round 2 (GG):  1, 6,11, 0, 5,10,15, 4, 9,14, 3, 8,13, 2, 7,12
// Round 3 (HH):  5, 8,11,14, 1, 4, 7,10,13, 0, 3, 6, 9,12,15, 2
// Round 4 (II):  0, 7,14, 5,12, 3,10, 1, 8,15, 6,13, 4,11, 2, 9

// Rotation helper (M1: single bit-field-extract + ORR instruction)
#define ROTL32(x, n) (((x) << (n)) | ((x) >> (32 - (n))))

// ─── Round Function Macros (expanded inline below for full unrolling) ───
// FF(b,c,d) = (b & c) | (~b & d)
// GG(b,c,d) = (b & d) | (c & ~d)
// HH(b,c,d) = b ^ c ^ d
// II(b,c,d) = c ^ (b | ~d)

// ── Chunk Size and Thread Configuration ─────────────────────────────────
// Tuned for M1 8-core GPU with 32 KB threadgroup memory
constant uint CHUNK_SIZE  = 1000;   // words per threadgroup chunk
constant uint THREADS     = 256;    // threads per threadgroup (8 SIMD groups × 32)
constant uint WPT         = 4;      // words per thread = ceil(1000/256)

// ─── Optimized MD5 Cracking Kernel ──────────────────────────────────────

kernel void crack_md5_kernel(
    // Global input buffers (read-only)
    device const uchar* worddata         [[buffer(0)]],
    device const uint*  offsets          [[buffer(1)]],
    device const uint*  lengths          [[buffer(2)]],
    device const uint*  target           [[buffer(3)]],   // 4 × uint32 little-endian
    // Global output buffers (atomic write)
    device atomic_uint* found_flag       [[buffer(4)]],   // 0 = searching, 1 = found
    device uint*        match_idx        [[buffer(5)]],   // global index of match
    // Constants
    constant uint&      total_candidates [[buffer(6)]],   // N = wordlist size
    // Thread identifiers
    uint tgid [[threadgroup_position_in_grid]],            // which chunk
    uint lid  [[thread_position_in_threadgroup]]           // which thread in group
) {
    // ── Threadgroup Shared Memory ───────────────────────────────────────
    // Allocated per-threadgroup by the Metal driver.
    // M1: 32 KB max → our 17 KB leaves 15 KB for register spill.
    threadgroup uchar sh_words[CHUNK_SIZE * 16];   // 16 KB: word data (16 bytes/slot)
    threadgroup uchar sh_lengths[CHUNK_SIZE];       //  1 KB: word lengths (max 55)

    // ── Determine This Chunk's Bounds ────────────────────────────────────
    uint chunk_start = tgid * CHUNK_SIZE;
    if (chunk_start >= total_candidates) return;  // OOB threadgroup
    uint chunk_size = min(CHUNK_SIZE, total_candidates - chunk_start);

    // ════════════════════════════════════════════════════════════════════
    // PHASE 1: Cooperative Load into Threadgroup Shared Memory
    // ════════════════════════════════════════════════════════════════════
    //
    // 256 threads collectively load 1000 words from global memory.
    // Each thread loads ~4 words using strided access for coalesced reads.
    // Stride pattern: thread `lid` loads indices `lid, lid+256, lid+512, lid+768`

    for (uint t = lid; t < chunk_size; t += THREADS) {
        uint global_idx = chunk_start + t;
        uint off = offsets[global_idx];
        uint len = lengths[global_idx];

        // Store length as uchar (fits: max single-block MD5 word = 55 bytes)
        sh_lengths[t] = (uchar)(len & 0xFFu);

        // Copy word bytes into 16-byte slot in shared memory
        // Use uint4-aligned writes for words >= 4 bytes (most common case)
        uint base = t << 4;  // t * 16
        uint word_len = min(len, 16u);

        if (word_len >= 4) {
            // Fast path: copy full uint32 chunks
            uint num_uints = word_len >> 2;  // word_len / 4
            threadgroup uint* dst = (threadgroup uint*)(sh_words + base);
            device const uint* src = (device const uint*)(worddata + off);
            for (uint u = 0; u < num_uints; u++) {
                dst[u] = src[u];
            }
            // Copy remaining bytes (< 4)
            for (uint b = num_uints << 2; b < word_len; b++) {
                sh_words[base + b] = worddata[off + b];
            }
        } else if (word_len > 0) {
            // Short word: byte-by-byte copy (rare, only for < 4 byte words)
            for (uint b = 0; b < word_len; b++) {
                sh_words[base + b] = worddata[off + b];
            }
        }
        // Bytes [word_len..15] in slot are left uninitialized — harmless;
        // they're masked out during Phase 2 M[16] construction.
    }

    // Synchronize: all threads must finish loading before any starts processing
    threadgroup_barrier(mem_flags::mem_threadgroup);

    // ════════════════════════════════════════════════════════════════════
    // PHASE 2: Process Assigned Words (Fully Unrolled MD5)
    // ════════════════════════════════════════════════════════════════════
    //
    // Each thread processes WPT=4 words from shared memory.
    // Word assignment: thread `lid` processes words `lid, lid+256, lid+512, lid+768`

    for (uint w = 0; w < WPT; w++) {
        uint ci = lid + w * THREADS;  // chunk-local word index
        if (ci >= chunk_size) break;

        // Early exit: another threadgroup already found the match
        if (atomic_load_explicit(found_flag, memory_order_relaxed) != 0) return;

        uint len = (uint)sh_lengths[ci];
        if (len > 55) continue;  // Multi-block: skip (CPU fallback handles)

        uint base = ci << 4;  // ci * 16

        // ── Build M[16] Message Block ────────────────────────────────────
        // M[16] = [word bytes][0x80][zeros][64-bit bit_len in LE]
        //
        // DUAL PATH:
        //   Path A (fast, len ≤ 16): switch-based precomputed padding from
        //     shared memory. Bytes 0..len-1 from shared slot, rest via
        //     switch lookup tables. Zero global memory reads.
        //   Path B (correct, len > 16): load bytes 0-15 from shared memory,
        //     then load bytes 16..len-1 directly from global memory.
        //     ~5% of words in typical wordlists — rare enough that the
        //     extra global memory access doesn't hurt throughput.
        //
        // Shared memory slot is 16 bytes per word (CHUNK_SIZE × 16).
        // For words > 16 bytes, only the first 16 bytes are in shared
        // memory; the tail must be fetched from global worddata/offsets.

        uint M0, M1, M2, M3, M4, M5, M6, M7,
             M8, M9, M10, M11, M12, M13, M14, M15;

        if (len <= 16) {
            // ── FAST PATH: len ≤ 16, all word bytes in shared memory ─────
            uint word_base = base;
            threadgroup uint* word_src = (threadgroup uint*)(sh_words + word_base);

            uint w0 = word_src[0];
            uint w1 = word_src[1];
            uint w2 = word_src[2];
            uint w3 = word_src[3];

        // Mask and assign based on actual word length
        // This correctly handles padding: bytes >= len are replaced with
        // the MD5 padding scheme (0x80 + zeros + bit length).
        switch (len) {
            case  0: M0 = 0x00000080u; M1 = 0; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 0; M15 = 0; break;
            case  1: M0 = (w0 & 0x000000FFu) | 0x00008000u; M1 = 0; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 8; M15 = 0; break;
            case  2: M0 = (w0 & 0x0000FFFFu) | 0x00800000u; M1 = 0; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 16; M15 = 0; break;
            case  3: M0 = (w0 & 0x00FFFFFFu) | 0x80000000u; M1 = 0; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 24; M15 = 0; break;
            case  4: M0 = w0; M1 = 0x00000080u; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 32; M15 = 0; break;
            case  5: M0 = w0; M1 = (w1 & 0x000000FFu) | 0x00008000u; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 40; M15 = 0; break;
            case  6: M0 = w0; M1 = (w1 & 0x0000FFFFu) | 0x00800000u; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 48; M15 = 0; break;
            case  7: M0 = w0; M1 = (w1 & 0x00FFFFFFu) | 0x80000000u; M2 = 0; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 56; M15 = 0; break;
            case  8: M0 = w0; M1 = w1; M2 = 0x00000080u; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 64; M15 = 0; break;
            case  9: M0 = w0; M1 = w1; M2 = (w2 & 0x000000FFu) | 0x00008000u; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 72; M15 = 0; break;
            case 10: M0 = w0; M1 = w1; M2 = (w2 & 0x0000FFFFu) | 0x00800000u; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 80; M15 = 0; break;
            case 11: M0 = w0; M1 = w1; M2 = (w2 & 0x00FFFFFFu) | 0x80000000u; M3 = 0; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 88; M15 = 0; break;
            case 12: M0 = w0; M1 = w1; M2 = w2; M3 = 0x00000080u; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 96; M15 = 0; break;
            case 13: M0 = w0; M1 = w1; M2 = w2; M3 = (w3 & 0x000000FFu) | 0x00008000u; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 104; M15 = 0; break;
            case 14: M0 = w0; M1 = w1; M2 = w2; M3 = (w3 & 0x0000FFFFu) | 0x00800000u; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 112; M15 = 0; break;
            case 15: M0 = w0; M1 = w1; M2 = w2; M3 = (w3 & 0x00FFFFFFu) | 0x80000000u; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 120; M15 = 0; break;
            case 16: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x00000080u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 128; M15 = 0; break;
            case 17: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x00008080u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 136; M15 = 0; break;
            case 18: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x00808080u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 144; M15 = 0; break;
            case 19: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x80808080u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 152; M15 = 0; break;
            case 20: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x00000080u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 160; M15 = 0; break;
            case 21: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x00008000u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 168; M15 = 0; break;
            case 22: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x00800000u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 176; M15 = 0; break;
            case 23: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0x80000000u; M5 = 0; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 184; M15 = 0; break;
            case 24: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x00000080u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 192; M15 = 0; break;
            case 25: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x00008080u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 200; M15 = 0; break;
            case 26: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x00808080u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 208; M15 = 0; break;
            case 27: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x80808080u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 216; M15 = 0; break;
            case 28: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x00000080u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 224; M15 = 0; break;
            case 29: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x00008000u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 232; M15 = 0; break;
            case 30: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x00800000u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 240; M15 = 0; break;
            case 31: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0x80000000u; M6 = 0; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 248; M15 = 0; break;
            case 32: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x00000080u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 256; M15 = 0; break;
            case 33: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x00008080u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 264; M15 = 0; break;
            case 34: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x00808080u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 272; M15 = 0; break;
            case 35: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x80808080u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 280; M15 = 0; break;
            case 36: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x00000080u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 288; M15 = 0; break;
            case 37: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x00008000u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 296; M15 = 0; break;
            case 38: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x00800000u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 304; M15 = 0; break;
            case 39: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0x80000000u; M7 = 0; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 312; M15 = 0; break;
            case 40: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x00000080u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 320; M15 = 0; break;
            case 41: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x00008080u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 328; M15 = 0; break;
            case 42: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x00808080u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 336; M15 = 0; break;
            case 43: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x80808080u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 344; M15 = 0; break;
            case 44: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x00000080u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 352; M15 = 0; break;
            case 45: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x00008000u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 360; M15 = 0; break;
            case 46: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x00800000u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 368; M15 = 0; break;
            case 47: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0x80000000u; M8 = 0; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 376; M15 = 0; break;
            case 48: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x00000080u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 384; M15 = 0; break;
            case 49: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x00008080u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 392; M15 = 0; break;
            case 50: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x00808080u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 400; M15 = 0; break;
            case 51: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x80808080u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 408; M15 = 0; break;
            case 52: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x00000080u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 416; M15 = 0; break;
            case 53: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x00008000u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 424; M15 = 0; break;
            case 54: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x00800000u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 432; M15 = 0; break;
            case 55: M0 = w0; M1 = w1; M2 = w2; M3 = w3; M4 = 0; M5 = 0; M6 = 0; M7 = 0; M8 = 0x80000000u; M9 = 0; M10 = 0; M11 = 0; M12 = 0; M13 = 0; M14 = 440; M15 = 0; break;
            }
        } else {
            // ── SLOW PATH: len > 16, tail bytes from global memory ────────
            // Shared memory only holds 16 bytes per word. For words with
            // 17-55 bytes, fetch bytes 16..len-1 from global worddata.
            // Bytes 0-15 still come from shared memory (cooperatively loaded).
            uint global_idx = chunk_start + ci;
            uint off = offsets[global_idx];

            // Load bytes 0-15 from shared memory
            threadgroup uint* word_src_slow = (threadgroup uint*)(sh_words + base);
            uint w0s = word_src_slow[0];
            uint w1s = word_src_slow[1];
            uint w2s = word_src_slow[2];
            uint w3s = word_src_slow[3];

            M0 = w0s; M1 = w1s; M2 = w2s; M3 = w3s;

            // Build M4..M13 from global memory using a contiguous temp array
            // (M4..M15 are separate local variables — can't assume layout)
            uint M_tail[12] = {0};  // M4..M15 workspace
            for (uint b = 16; b < len && b < 56; b++) {
                ((uchar*)M_tail)[b - 16] = worddata[off + b];
            }
            // MD5 padding: append 0x80 at byte position len
            ((uchar*)M_tail)[len - 16] = 0x80;
            // Assign to individual M variables
            M4 = M_tail[0];  M5 = M_tail[1];  M6 = M_tail[2];  M7 = M_tail[3];
            M8 = M_tail[4];  M9 = M_tail[5];  M10 = M_tail[6]; M11 = M_tail[7];
            M12 = M_tail[8]; M13 = M_tail[9];
            // M_tail[10],[11] unused — M14, M15 set below

            // Set bit length
            uint64_t bit_len_slow = (uint64_t)len * 8;
            M14 = (uint)(bit_len_slow & 0xFFFFFFFFu);
            M15 = (uint)(bit_len_slow >> 32);
        }

        // ── FULLY UNROLLED MD5 (64 Rounds) ──────────────────────────────
        // No macros, no function calls, no loops — maximum instruction-level
        // parallelism for M1 GPU's wide execution units.
        //
        // State variables: a, b, c, d (uint32)
        // Each step: a = b + ROTL32(old_a + F(b,c,d) + M[k] + K[i], s)
        // Followed by rotation (a,b,c,d) → (d,a,b,c) for next step.

        uint a = 0x67452301u;
        uint b = 0xefcdab89u;
        uint c = 0x98badcfeu;
        uint d = 0x10325476u;

        // ── Round 1: FF(b,c,d) = (b & c) | (~b & d), k sequential 0..15 ──
        // Step  0: k=0  s=7  i=0
        a = b + ROTL32(a + ((b & c) | (~b & d)) + M0  + K[0],  7);
        // Step  1: k=1  s=12 i=1
        d = a + ROTL32(d + ((a & b) | (~a & c)) + M1  + K[1],  12);
        // Step  2: k=2  s=17 i=2
        c = d + ROTL32(c + ((d & a) | (~d & b)) + M2  + K[2],  17);
        // Step  3: k=3  s=22 i=3
        b = c + ROTL32(b + ((c & d) | (~c & a)) + M3  + K[3],  22);
        // Step  4: k=4  s=7  i=4
        a = b + ROTL32(a + ((b & c) | (~b & d)) + M4  + K[4],  7);
        // Step  5: k=5  s=12 i=5
        d = a + ROTL32(d + ((a & b) | (~a & c)) + M5  + K[5],  12);
        // Step  6: k=6  s=17 i=6
        c = d + ROTL32(c + ((d & a) | (~d & b)) + M6  + K[6],  17);
        // Step  7: k=7  s=22 i=7
        b = c + ROTL32(b + ((c & d) | (~c & a)) + M7  + K[7],  22);
        // Step  8: k=8  s=7  i=8
        a = b + ROTL32(a + ((b & c) | (~b & d)) + M8  + K[8],  7);
        // Step  9: k=9  s=12 i=9
        d = a + ROTL32(d + ((a & b) | (~a & c)) + M9  + K[9],  12);
        // Step 10: k=10 s=17 i=10
        c = d + ROTL32(c + ((d & a) | (~d & b)) + M10 + K[10], 17);
        // Step 11: k=11 s=22 i=11
        b = c + ROTL32(b + ((c & d) | (~c & a)) + M11 + K[11], 22);
        // Step 12: k=12 s=7  i=12
        a = b + ROTL32(a + ((b & c) | (~b & d)) + M12 + K[12], 7);
        // Step 13: k=13 s=12 i=13
        d = a + ROTL32(d + ((a & b) | (~a & c)) + M13 + K[13], 12);
        // Step 14: k=14 s=17 i=14
        c = d + ROTL32(c + ((d & a) | (~d & b)) + M14 + K[14], 17);
        // Step 15: k=15 s=22 i=15
        b = c + ROTL32(b + ((c & d) | (~c & a)) + M15 + K[15], 22);

        // ── Round 2: GG(b,c,d) = (b & d) | (c & ~d), k: 1,6,11,0,5,10,15,4,9,14,3,8,13,2,7,12 ──
        // Step 16: k=1  s=5  i=16
        a = b + ROTL32(a + ((b & d) | (c & ~d)) + M1  + K[16], 5);
        // Step 17: k=6  s=9  i=17
        d = a + ROTL32(d + ((a & c) | (b & ~c)) + M6  + K[17], 9);
        // Step 18: k=11 s=14 i=18
        c = d + ROTL32(c + ((d & b) | (a & ~b)) + M11 + K[18], 14);
        // Step 19: k=0  s=20 i=19
        b = c + ROTL32(b + ((c & a) | (d & ~a)) + M0  + K[19], 20);
        // Step 20: k=5  s=5  i=20
        a = b + ROTL32(a + ((b & d) | (c & ~d)) + M5  + K[20], 5);
        // Step 21: k=10 s=9  i=21
        d = a + ROTL32(d + ((a & c) | (b & ~c)) + M10 + K[21], 9);
        // Step 22: k=15 s=14 i=22
        c = d + ROTL32(c + ((d & b) | (a & ~b)) + M15 + K[22], 14);
        // Step 23: k=4  s=20 i=23
        b = c + ROTL32(b + ((c & a) | (d & ~a)) + M4  + K[23], 20);
        // Step 24: k=9  s=5  i=24
        a = b + ROTL32(a + ((b & d) | (c & ~d)) + M9  + K[24], 5);
        // Step 25: k=14 s=9  i=25
        d = a + ROTL32(d + ((a & c) | (b & ~c)) + M14 + K[25], 9);
        // Step 26: k=3  s=14 i=26
        c = d + ROTL32(c + ((d & b) | (a & ~b)) + M3  + K[26], 14);
        // Step 27: k=8  s=20 i=27
        b = c + ROTL32(b + ((c & a) | (d & ~a)) + M8  + K[27], 20);
        // Step 28: k=13 s=5  i=28
        a = b + ROTL32(a + ((b & d) | (c & ~d)) + M13 + K[28], 5);
        // Step 29: k=2  s=9  i=29
        d = a + ROTL32(d + ((a & c) | (b & ~c)) + M2  + K[29], 9);
        // Step 30: k=7  s=14 i=30
        c = d + ROTL32(c + ((d & b) | (a & ~b)) + M7  + K[30], 14);
        // Step 31: k=12 s=20 i=31
        b = c + ROTL32(b + ((c & a) | (d & ~a)) + M12 + K[31], 20);

        // ── Round 3: HH(b,c,d) = b ^ c ^ d, k: 5,8,11,14,1,4,7,10,13,0,3,6,9,12,15,2 ──
        // Step 32: k=5  s=4  i=32
        a = b + ROTL32(a + (b ^ c ^ d) + M5  + K[32], 4);
        // Step 33: k=8  s=11 i=33
        d = a + ROTL32(d + (a ^ b ^ c) + M8  + K[33], 11);
        // Step 34: k=11 s=16 i=34
        c = d + ROTL32(c + (d ^ a ^ b) + M11 + K[34], 16);
        // Step 35: k=14 s=23 i=35
        b = c + ROTL32(b + (c ^ d ^ a) + M14 + K[35], 23);
        // Step 36: k=1  s=4  i=36
        a = b + ROTL32(a + (b ^ c ^ d) + M1  + K[36], 4);
        // Step 37: k=4  s=11 i=37
        d = a + ROTL32(d + (a ^ b ^ c) + M4  + K[37], 11);
        // Step 38: k=7  s=16 i=38
        c = d + ROTL32(c + (d ^ a ^ b) + M7  + K[38], 16);
        // Step 39: k=10 s=23 i=39
        b = c + ROTL32(b + (c ^ d ^ a) + M10 + K[39], 23);
        // Step 40: k=13 s=4  i=40
        a = b + ROTL32(a + (b ^ c ^ d) + M13 + K[40], 4);
        // Step 41: k=0  s=11 i=41
        d = a + ROTL32(d + (a ^ b ^ c) + M0  + K[41], 11);
        // Step 42: k=3  s=16 i=42
        c = d + ROTL32(c + (d ^ a ^ b) + M3  + K[42], 16);
        // Step 43: k=6  s=23 i=43
        b = c + ROTL32(b + (c ^ d ^ a) + M6  + K[43], 23);
        // Step 44: k=9  s=4  i=44
        a = b + ROTL32(a + (b ^ c ^ d) + M9  + K[44], 4);
        // Step 45: k=12 s=11 i=45
        d = a + ROTL32(d + (a ^ b ^ c) + M12 + K[45], 11);
        // Step 46: k=15 s=16 i=46
        c = d + ROTL32(c + (d ^ a ^ b) + M15 + K[46], 16);
        // Step 47: k=2  s=23 i=47
        b = c + ROTL32(b + (c ^ d ^ a) + M2  + K[47], 23);

        // ── Round 4: II(b,c,d) = c ^ (b | ~d), k: 0,7,14,5,12,3,10,1,8,15,6,13,4,11,2,9 ──
        // Step 48: k=0  s=6  i=48
        a = b + ROTL32(a + (c ^ (b | ~d)) + M0  + K[48], 6);
        // Step 49: k=7  s=10 i=49
        d = a + ROTL32(d + (b ^ (a | ~c)) + M7  + K[49], 10);
        // Step 50: k=14 s=15 i=50
        c = d + ROTL32(c + (a ^ (d | ~b)) + M14 + K[50], 15);
        // Step 51: k=5  s=21 i=51
        b = c + ROTL32(b + (d ^ (c | ~a)) + M5  + K[51], 21);
        // Step 52: k=12 s=6  i=52
        a = b + ROTL32(a + (c ^ (b | ~d)) + M12 + K[52], 6);
        // Step 53: k=3  s=10 i=53
        d = a + ROTL32(d + (b ^ (a | ~c)) + M3  + K[53], 10);
        // Step 54: k=10 s=15 i=54
        c = d + ROTL32(c + (a ^ (d | ~b)) + M10 + K[54], 15);
        // Step 55: k=1  s=21 i=55
        b = c + ROTL32(b + (d ^ (c | ~a)) + M1  + K[55], 21);
        // Step 56: k=8  s=6  i=56
        a = b + ROTL32(a + (c ^ (b | ~d)) + M8  + K[56], 6);
        // Step 57: k=15 s=10 i=57
        d = a + ROTL32(d + (b ^ (a | ~c)) + M15 + K[57], 10);
        // Step 58: k=6  s=15 i=58
        c = d + ROTL32(c + (a ^ (d | ~b)) + M6  + K[58], 15);
        // Step 59: k=13 s=21 i=59
        b = c + ROTL32(b + (d ^ (c | ~a)) + M13 + K[59], 21);
        // Step 60: k=4  s=6  i=60
        a = b + ROTL32(a + (c ^ (b | ~d)) + M4  + K[60], 6);
        // Step 61: k=11 s=10 i=61
        d = a + ROTL32(d + (b ^ (a | ~c)) + M11 + K[61], 10);
        // Step 62: k=2  s=15 i=62
        c = d + ROTL32(c + (a ^ (d | ~b)) + M2  + K[62], 15);
        // Step 63: k=9  s=21 i=63
        b = c + ROTL32(b + (d ^ (c | ~a)) + M9  + K[63], 21);

        // ── Final Addition ───────────────────────────────────────────────
        a += 0x67452301u;
        b += 0xefcdab89u;
        c += 0x98badcfeu;
        d += 0x10325476u;

        // ── Compare with Target (uint32 little-endian) ──────────────────
        if (a == target[0] && b == target[1] && c == target[2] && d == target[3]) {
            // Atomic claim — only the first thread to find the match wins
            uint prev = atomic_exchange_explicit(found_flag, 1u, memory_order_relaxed);
            if (prev == 0) {
                *match_idx = chunk_start + ci;  // Global index into candidates
            }
            return;
        }
    }
}

// ═══════════════════════════════════════════════════════════════════════════
// LEGACY KERNEL: crack_md5_kernel_legacy
// ═══════════════════════════════════════════════════════════════════════════
//
// Kept for reference and backward compatibility. The optimized
// crack_md5_kernel above should be preferred for all new dispatches.
// This kernel uses the original one-thread-per-candidate model with
// macro-based MD5 rounds and no threadgroup shared memory.

kernel void crack_md5_kernel_legacy(
    device const uchar* worddata   [[buffer(0)]],
    device const uint*  offsets    [[buffer(1)]],
    device const uint*  lengths    [[buffer(2)]],
    device const uint*  target     [[buffer(3)]],
    device atomic_uint* found_flag [[buffer(4)]],
    device uint*        match_idx  [[buffer(5)]],
    uint tid [[thread_position_in_grid]]
) {
    if (atomic_load_explicit(found_flag, memory_order_relaxed) != 0) {
        return;
    }

    uint offset = offsets[tid];
    uint len = lengths[tid];

    if (len > 55) return;

    uint M[16] = {0};
    for (uint i = 0; i < len; i++) {
        uint byte_val = worddata[offset + i];
        uint shift = (i & 3) * 8;
        M[i >> 2] |= byte_val << shift;
    }

    // Padding
    uint pad_byte = len;
    uint pad_shift = (pad_byte & 3) * 8;
    M[pad_byte >> 2] |= 0x80u << pad_shift;
    uint64_t bit_len = (uint64_t)len * 8;
    M[14] = (uint)(bit_len & 0xFFFFFFFF);
    M[15] = (uint)(bit_len >> 32);

    // MD5
    uint a = 0x67452301, b = 0xefcdab89, c = 0x98badcfe, d = 0x10325476;

    #define ROUND1(aa, bb, cc, dd, k, s, i) \
        aa = bb + ROTL32(aa + ((bb & cc) | (~bb & dd)) + M[k] + K[i], s)
    #define ROUND2(aa, bb, cc, dd, k, s, i) \
        aa = bb + ROTL32(aa + ((bb & dd) | (cc & ~dd)) + M[k] + K[i], s)
    #define ROUND3(aa, bb, cc, dd, k, s, i) \
        aa = bb + ROTL32(aa + (bb ^ cc ^ dd) + M[k] + K[i], s)
    #define ROUND4(aa, bb, cc, dd, k, s, i) \
        aa = bb + ROTL32(aa + (cc ^ (bb | ~dd)) + M[k] + K[i], s)

    ROUND1(a, b, c, d,  0,  7,  0); ROUND1(d, a, b, c,  1, 12,  1);
    ROUND1(c, d, a, b,  2, 17,  2); ROUND1(b, c, d, a,  3, 22,  3);
    ROUND1(a, b, c, d,  4,  7,  4); ROUND1(d, a, b, c,  5, 12,  5);
    ROUND1(c, d, a, b,  6, 17,  6); ROUND1(b, c, d, a,  7, 22,  7);
    ROUND1(a, b, c, d,  8,  7,  8); ROUND1(d, a, b, c,  9, 12,  9);
    ROUND1(c, d, a, b, 10, 17, 10); ROUND1(b, c, d, a, 11, 22, 11);
    ROUND1(a, b, c, d, 12,  7, 12); ROUND1(d, a, b, c, 13, 12, 13);
    ROUND1(c, d, a, b, 14, 17, 14); ROUND1(b, c, d, a, 15, 22, 15);

    ROUND2(a, b, c, d,  1,  5, 16); ROUND2(d, a, b, c,  6,  9, 17);
    ROUND2(c, d, a, b, 11, 14, 18); ROUND2(b, c, d, a,  0, 20, 19);
    ROUND2(a, b, c, d,  5,  5, 20); ROUND2(d, a, b, c, 10,  9, 21);
    ROUND2(c, d, a, b, 15, 14, 22); ROUND2(b, c, d, a,  4, 20, 23);
    ROUND2(a, b, c, d,  9,  5, 24); ROUND2(d, a, b, c, 14,  9, 25);
    ROUND2(c, d, a, b,  3, 14, 26); ROUND2(b, c, d, a,  8, 20, 27);
    ROUND2(a, b, c, d, 13,  5, 28); ROUND2(d, a, b, c,  2,  9, 29);
    ROUND2(c, d, a, b,  7, 14, 30); ROUND2(b, c, d, a, 12, 20, 31);

    ROUND3(a, b, c, d,  5,  4, 32); ROUND3(d, a, b, c,  8, 11, 33);
    ROUND3(c, d, a, b, 11, 16, 34); ROUND3(b, c, d, a, 14, 23, 35);
    ROUND3(a, b, c, d,  1,  4, 36); ROUND3(d, a, b, c,  4, 11, 37);
    ROUND3(c, d, a, b,  7, 16, 38); ROUND3(b, c, d, a, 10, 23, 39);
    ROUND3(a, b, c, d, 13,  4, 40); ROUND3(d, a, b, c,  0, 11, 41);
    ROUND3(c, d, a, b,  3, 16, 42); ROUND3(b, c, d, a,  6, 23, 43);
    ROUND3(a, b, c, d,  9,  4, 44); ROUND3(d, a, b, c, 12, 11, 45);
    ROUND3(c, d, a, b, 15, 16, 46); ROUND3(b, c, d, a,  2, 23, 47);

    ROUND4(a, b, c, d,  0,  6, 48); ROUND4(d, a, b, c,  7, 10, 49);
    ROUND4(c, d, a, b, 14, 15, 50); ROUND4(b, c, d, a,  5, 21, 51);
    ROUND4(a, b, c, d, 12,  6, 52); ROUND4(d, a, b, c,  3, 10, 53);
    ROUND4(c, d, a, b, 10, 15, 54); ROUND4(b, c, d, a,  1, 21, 55);
    ROUND4(a, b, c, d,  8,  6, 56); ROUND4(d, a, b, c, 15, 10, 57);
    ROUND4(c, d, a, b,  6, 15, 58); ROUND4(b, c, d, a, 13, 21, 59);
    ROUND4(a, b, c, d,  4,  6, 60); ROUND4(d, a, b, c, 11, 10, 61);
    ROUND4(c, d, a, b,  2, 15, 62); ROUND4(b, c, d, a,  9, 21, 63);

    a += 0x67452301; b += 0xefcdab89;
    c += 0x98badcfe; d += 0x10325476;

    if (a == target[0] && b == target[1] && c == target[2] && d == target[3]) {
        uint prev = atomic_exchange_explicit(found_flag, 1u, memory_order_relaxed);
        if (prev == 0) {
            *match_idx = tid;
        }
    }
}
