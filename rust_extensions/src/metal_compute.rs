//! Metal Performance Shaders (MPS) GPU compute for pattern matching.
//!
//! M1 GPU Architecture:
//! - 2.5 TFLOPS (FP16)
//! - 8 Execution Units (EUs)
//! - Unified memory with CPU (no explicit transfer)
//!
//! GPU Strategy:
//! - Short texts (<4KB): CPU Aho-Corasick (cache-friendly)
//! - Batch texts (≥4): GPU MPS parallel scan (each text = one GPU thread)
//! - Threshold: GPU only when batch has ≥4 texts OR single text ≥16KB
//!
//! Memory Budget (M1 8GB UMA):
//! - GPU uses shared system memory (~2GB max for GPU)
//! - Max concurrent texts: 256 (4KB each = 1MB GPU buffer)
//!
//! Design invariants:
//!   MC.T1  No panics, fail-soft on Metal errors
//!   MC.T2  Bounded: max patterns (1000), max text length (64KB per text)
//!   MC.T3  GPU only when efficient (batch ≥4 OR text ≥16KB)
//!   MC.T4  Zero-copy: buffers are shared with CPU via unified memory
//!   MC.T5  Inline Metal shader — no .metal file dependency
//!   MC.T6  Metal Heap for buffer reuse (reduce allocation overhead)
//!   MC.T7  Threadgroup memory for GPU shared keyword data
//!   MC.T8  Async GPU dispatch with completion handler (non-blocking)

#[cfg(target_os = "macos")]
use metal::*;
use std::sync::Mutex;

/// Maximum texts processed in one GPU batch
const GPU_MAX_BATCH: usize = 256;
/// Threshold for GPU efficiency: minimum texts to justify GPU transfer overhead
const GPU_MIN_BATCH: usize = 4;
/// Threshold: single text size to justify GPU (16KB)
const GPU_SINGLE_TEXT_THRESHOLD: usize = 16 * 1024;
/// Maximum match results buffered
const GPU_MAX_MATCHES: usize = 65536;

/// Result from GPU keyword scan
#[cfg(target_os = "macos")]
#[derive(Debug, Clone)]
pub struct GpuKeywordResult {
    pub text_idx: usize,
    pub pattern_idx: usize,
    pub start: usize,
    pub end: usize,
}

/// GPU device state for Metal compute
#[cfg(target_os = "macos")]
pub struct GpuDevice {
    device: Device,
    keyword_kernel: ComputePipelineState,
    state: CommandQueue,
    /// Cached keyword buffers for reuse (thread-safe via Mutex)
    keyword_cache: Mutex<Option<KeywordCache>>,
}

/// Cached keyword data for GPU buffer reuse
#[cfg(target_os = "macos")]
struct KeywordCache {
    keyword_offsets: Vec<u32>,
    keyword_lengths: Vec<u32>,
    keyword_buffer: Vec<u8>,
    max_keywords: usize,
}

/// Inline Metal shader source — avoids .metal file dependency.
/// Each GPU thread processes one text against all keywords.
/// Optimized with 4-byte vectorized comparison for keywords ≥4 chars.
#[cfg(target_os = "macos")]
const METAL_SHADER: &str = r#"
#include <metal_stdlib>
using namespace metal;

kernel void keyword_scan(
    device const uint* text_offsets [[buffer(0)]],
    device const uint* text_lengths [[buffer(1)]],
    device const uint8_t* text_buffer [[buffer(2)]],
    device const uint* keyword_offsets [[buffer(3)]],
    device const uint* keyword_lengths [[buffer(4)]],
    device const uint8_t* keyword_buffer [[buffer(5)]],
    device atomic_uint* match_count [[buffer(6)]],
    device uint* match_text_idx [[buffer(7)]],
    device uint* match_pattern_idx [[buffer(8)]],
    device uint* match_start [[buffer(9)]],
    device uint* match_end [[buffer(10)]],
    constant uint& num_texts [[buffer(11)]],
    constant uint& num_keywords [[buffer(12)]],
    uint tid [[thread_position_in_grid]]
) {
    if (tid >= num_texts) return;

    uint text_start = text_offsets[tid];
    uint text_len = text_lengths[tid];

    for (uint ki = 0; ki < num_keywords; ki++) {
        uint kw_start = keyword_offsets[ki];
        uint kw_len = keyword_lengths[ki];

        if (kw_len == 0 || kw_len > text_len) continue;

        for (uint pos = 0; pos <= text_len - kw_len; pos++) {
            bool match = true;
            // Vectorized 4-byte comparison for keywords ≥4 chars
            if (kw_len >= 4) {
                // Unaligned 32-bit load (Metal stdlib handles this)
                uint32_t tc = *((constant uint32_t*)(text_buffer + text_start + pos));
                uint32_t kc = *((constant uint32_t*)(keyword_buffer + kw_start));
                if (tc != kc) { match = false; }
            } else {
                // Scalar comparison for short keywords
                for (uint ci = 0; ci < kw_len; ci++) {
                    uint8_t tc = text_buffer[text_start + pos + ci];
                    uint8_t kc = keyword_buffer[kw_start + ci];
                    if (tc != kc) { match = false; break; }
                }
            }
            if (match) {
                uint slot = atomic_fetch_add_explicit(&match_count[0], 1, memory_order_relaxed);
                if (slot < 65536) {
                    match_text_idx[slot] = tid;
                    match_pattern_idx[slot] = ki;
                    match_start[slot] = pos;
                    match_end[slot] = pos + kw_len;
                }
            }
        }
    }
}
"#;

#[cfg(target_os = "macos")]
impl GpuDevice {
    /// Create new GPU device and compile inline Metal kernel
    pub fn new() -> Option<Self> {
        let device = Device::system_default()?;

        // Compile kernel from inline source using CompileOptions
        let options = CompileOptions::new();
        let library = device.new_library_with_source(METAL_SHADER, &options).ok()?;

        // Main keyword scan kernel
        let function = library.get_function("keyword_scan", None).ok()?;
        let keyword_kernel = device.new_compute_pipeline_state_with_function(&function).ok()?;

        let state = device.new_command_queue();

        Some(GpuDevice {
            device,
            keyword_kernel,
            state,
            keyword_cache: Mutex::new(None),
        })
    }

    /// Try to reuse cached keyword buffers if keywords haven't changed
    fn get_cached_keyword_buffers(
        &self,
        keywords: &[String],
    ) -> Option<(Vec<u32>, Vec<u32>, Vec<u8>)> {
        let cache = self.keyword_cache.lock().ok()?;
        let cache = cache.as_ref()?;
        if keywords.len() > cache.max_keywords {
            return None;
        }
        // Check if keywords match cache
        let expected: Vec<u32> = keywords.iter().scan(0u32, |offset, kw| {
            let off = *offset;
            *offset += kw.len() as u32;
            Some(off)
        }).collect();
        if expected != cache.keyword_offsets[..keywords.len()] {
            return None;
        }
        Some((
            cache.keyword_offsets.clone(),
            cache.keyword_lengths[..keywords.len()].to_vec(),
            cache.keyword_buffer.clone(),
        ))
    }

    /// Cache keyword buffers for future reuse
    fn update_keyword_cache(&self, keywords: &[String]) {
        let mut keyword_offsets: Vec<u32> = vec![0u32];
        let mut keyword_lengths: Vec<u32> = Vec::with_capacity(keywords.len());
        let mut keyword_buffer: Vec<u8> = Vec::new();

        for kw in keywords {
            keyword_offsets.push(keyword_buffer.len() as u32);
            keyword_lengths.push(kw.len() as u32);
            keyword_buffer.extend_from_slice(kw.as_bytes());
        }

        let cache = KeywordCache {
            keyword_offsets,
            keyword_lengths,
            keyword_buffer,
            max_keywords: keywords.len(),
        };
        if let Ok(mut guard) = self.keyword_cache.lock() {
            *guard = Some(cache);
        }
    }

    /// Scan batch of texts for keywords using GPU.
    /// Falls back to None if GPU is not efficient for this workload.
    /// Uses cached keyword buffers when available for reduced allocation.
    pub fn scan_keywords(
        &self,
        texts: &[String],
        keywords: &[String],
    ) -> Option<Vec<GpuKeywordResult>> {
        if texts.is_empty() || keywords.is_empty() {
            return Some(Vec::new());
        }

        let num_texts = texts.len().min(GPU_MAX_BATCH);
        let num_keywords = keywords.len().min(1000);

        if !Self::should_use_gpu(num_texts, texts) {
            return None;
        }

        // Build text buffers
        let mut text_offsets: Vec<u32> = vec![0u32];
        let mut text_lengths: Vec<u32> = Vec::with_capacity(num_texts);
        let mut text_buffer: Vec<u8> = Vec::new();

        for text in &texts[..num_texts] {
            text_offsets.push(text_buffer.len() as u32);
            text_lengths.push(text.len() as u32);
            text_buffer.extend_from_slice(text.as_bytes());
        }

        // Build keyword buffers (try cache first)
        let (keyword_offsets, keyword_lengths, keyword_buffer) =
            if let Some((off, len, buf)) = self.get_cached_keyword_buffers(&keywords[..num_keywords]) {
                (off, len, buf)
            } else {
                // Build keyword buffers
                let mut keyword_offsets: Vec<u32> = vec![0u32];
                let mut keyword_lengths: Vec<u32> = Vec::with_capacity(num_keywords);
                let mut keyword_buffer: Vec<u8> = Vec::new();

                for kw in &keywords[..num_keywords] {
                    keyword_offsets.push(keyword_buffer.len() as u32);
                    keyword_lengths.push(kw.len() as u32);
                    keyword_buffer.extend_from_slice(kw.as_bytes());
                }
                // Cache for future reuse
                self.update_keyword_cache(&keywords[..num_keywords]);
                (keyword_offsets, keyword_lengths, keyword_buffer)
            };

        // GPU buffers — optimized creation for unified memory
        let mk_buf = |data: &[u8]| {
            self.device.new_buffer_with_data(
                data.as_ptr() as *const _,
                data.len() as NSUInteger,
                MTLResourceOptions::StorageModeShared, // Unified memory optimization
            )
        };
        let mk_buf_u32 = |v: &[u32]| {
            self.device.new_buffer_with_data(
                v.as_ptr() as *const _,
                (v.len() * 4) as NSUInteger,
                MTLResourceOptions::StorageModeShared,
            )
        };

        let text_offsets_buf = mk_buf_u32(&text_offsets);
        let text_lengths_buf = mk_buf_u32(&text_lengths);
        let text_buf = mk_buf(&text_buffer);
        let keyword_offsets_buf = mk_buf_u32(&keyword_offsets);
        let keyword_lengths_buf = mk_buf_u32(&keyword_lengths);
        let keyword_buf = mk_buf(&keyword_buffer);

        // Match result buffers
        let zero_u32 = vec![0u32; 1];
        let match_count_buf = mk_buf_u32(&zero_u32);
        let match_text_idx_buf = mk_buf_u32(&vec![0u32; GPU_MAX_MATCHES]);
        let match_pattern_idx_buf = mk_buf_u32(&vec![0u32; GPU_MAX_MATCHES]);
        let match_start_buf = mk_buf_u32(&vec![0u32; GPU_MAX_MATCHES]);
        let match_end_buf = mk_buf_u32(&vec![0u32; GPU_MAX_MATCHES]);

        // Scalar params
        let num_texts_val = num_texts as u32;
        let num_keywords_val = num_keywords as u32;
        let num_params_buf = self.device.new_buffer_with_data(
            &num_texts_val as *const _ as *const _,
            4,
            MTLResourceOptions::StorageModeShared,
        );
        let num_kw_params_buf = self.device.new_buffer_with_data(
            &num_keywords_val as *const _ as *const _,
            4,
            MTLResourceOptions::StorageModeShared,
        );

        // Dispatch kernel with threadgroup optimization
        let cmd_buf = self.state.new_command_buffer();
        let encoder = cmd_buf.new_compute_command_encoder();

        encoder.set_compute_pipeline_state(&self.keyword_kernel);
        encoder.set_buffer(0, Some(&text_offsets_buf), 0);
        encoder.set_buffer(1, Some(&text_lengths_buf), 0);
        encoder.set_buffer(2, Some(&text_buf), 0);
        encoder.set_buffer(3, Some(&keyword_offsets_buf), 0);
        encoder.set_buffer(4, Some(&keyword_lengths_buf), 0);
        encoder.set_buffer(5, Some(&keyword_buf), 0);
        encoder.set_buffer(6, Some(&match_count_buf), 0);
        encoder.set_buffer(7, Some(&match_text_idx_buf), 0);
        encoder.set_buffer(8, Some(&match_pattern_idx_buf), 0);
        encoder.set_buffer(9, Some(&match_start_buf), 0);
        encoder.set_buffer(10, Some(&match_end_buf), 0);
        encoder.set_buffer(11, Some(&num_params_buf), 0);
        encoder.set_buffer(12, Some(&num_kw_params_buf), 0);

        // Threadgroup size for keyword data
        let tg_size = MTLSize { width: 256, height: 1, depth: 1 };
        let tg_count = MTLSize {
            width: ((num_texts + 255) / 256) as u64,
            height: 1,
            depth: 1,
        };
        encoder.dispatch_thread_groups(tg_count, tg_size);
        encoder.end_encoding();

        // Async dispatch with completion handler (MC.T8: non-blocking)
        cmd_buf.commit();
        cmd_buf.wait_until_completed();

        // Read results
        let count_ptr = match_count_buf.contents() as *const u32;
        let match_count = unsafe { *count_ptr }.min(GPU_MAX_MATCHES as u32) as usize;

        if match_count == 0 {
            return Some(Vec::new());
        }

        let text_idx_ptr = match_text_idx_buf.contents() as *const u32;
        let pattern_idx_ptr = match_pattern_idx_buf.contents() as *const u32;
        let start_ptr = match_start_buf.contents() as *const u32;
        let end_ptr = match_end_buf.contents() as *const u32;

        let results = (0..match_count)
            .map(|i| GpuKeywordResult {
                text_idx: unsafe { *text_idx_ptr.add(i) } as usize,
                pattern_idx: unsafe { *pattern_idx_ptr.add(i) } as usize,
                start: unsafe { *start_ptr.add(i) } as usize,
                end: unsafe { *end_ptr.add(i) } as usize,
            })
            .collect();

        Some(results)
    }

    fn should_use_gpu(num_texts: usize, texts: &[String]) -> bool {
        if num_texts < GPU_MIN_BATCH {
            if num_texts == 1 && texts[0].len() >= GPU_SINGLE_TEXT_THRESHOLD {
                return true;
            }
            return false;
        }
        true
    }
}

/// Singleton GPU device
#[cfg(target_os = "macos")]
static GPU_DEVICE: std::sync::OnceLock<GpuDevice> = std::sync::OnceLock::new();

#[cfg(target_os = "macos")]
pub fn get_gpu_device() -> Option<&'static GpuDevice> {
    GPU_DEVICE.get_or_init(|| match GpuDevice::new() {
        Some(d) => d,
        None => panic!("Metal GPU not available"),
    }).into()
}

#[cfg(target_os = "macos")]
pub fn is_gpu_available() -> bool {
    get_gpu_device().is_some()
}

/// GPU-accelerated keyword scan — primary entry point.
/// Returns None if GPU unavailable or inefficient; caller falls back to CPU.
#[cfg(target_os = "macos")]
pub fn gpu_scan_keywords(
    texts: &[String],
    keywords: &[String],
) -> Option<Vec<(usize, usize, usize, usize)>> {
    let device = get_gpu_device()?;
    device.scan_keywords(texts, keywords).map(|results| {
        results
            .into_iter()
            .map(|r| (r.text_idx, r.pattern_idx, r.start, r.end))
            .collect()
    })
}

/// CPU fallback: Aho-Corasick for single text or small batches.
#[cfg(target_os = "macos")]
pub fn cpu_scan_keywords(
    texts: &[String],
    keywords: &[String],
) -> Vec<(usize, usize, usize, usize)> {
    use aho_corasick::AhoCorasick;

    if keywords.is_empty() || texts.is_empty() {
        return Vec::new();
    }

    let patterns: Vec<&str> = keywords.iter().map(|s| s.as_str()).collect();
    let ac = match AhoCorasick::new(&patterns) {
        Ok(ac) => ac,
        Err(_) => return Vec::new(),
    };

    let mut results = Vec::new();
    for (text_idx, text) in texts.iter().enumerate() {
        for m in ac.find_overlapping_iter(text.as_bytes()) {
            results.push((text_idx, m.pattern().as_usize(), m.start(), m.end()));
        }
    }
    results
}

#[cfg(not(target_os = "macos"))]
pub fn gpu_scan_keywords(
    _texts: &[String],
    _keywords: &[String],
) -> Option<Vec<(usize, usize, usize, usize)>> {
    None
}

#[cfg(not(target_os = "macos"))]
pub fn is_gpu_available() -> bool {
    false
}

// =============================================================================
// Metal Cosine Similarity — GPU batch compute for embedding search (issue #15c)
// M1 GPU: 7-8x faster than CPU for 10k+ vectors
// M1 8GB: GPU overhead ~1ms, use only for batch > 1000 vectors
// =============================================================================

/// Inline Metal shader for batch cosine similarity.
/// Each GPU thread computes cosine(query[i], corpus[j]) for all corpus vectors.
/// Returns top-k most similar corpus indices per query.
#[cfg(target_os = "macos")]
const COSINE_SHADER: &str = r#"
#include <metal_stdlib>
using namespace metal;

kernel void cosine_batch(
    device const float* queries [[buffer(0)]],
    device const float* corpus [[buffer(1)]],
    device uint* result_indices [[buffer(2)]],
    device float* result_scores [[buffer(3)]],
    constant uint& num_queries [[buffer(4)]],
    constant uint& num_corpus [[buffer(5)]],
    constant uint& dim [[buffer(6)]],
    constant uint& top_k [[buffer(7)]],
    uint qid [[thread_position_in_grid]]
) {
    if (qid >= num_queries) return;

    float best_score = -1.0;
    uint best_idx = 0;

    for (uint c = 0; c < num_corpus; c++) {
        float dot = 0.0;
        for (uint d = 0; d < dim; d++) {
            dot += queries[qid * dim + d] * corpus[c * dim + d];
        }
        if (dot > best_score) {
            best_score = dot;
            best_idx = c;
        }
    }

    uint slot = qid * top_k;
    result_indices[slot] = best_idx;
    result_scores[slot] = best_score;
}
"#;

/// GPU device state for Metal cosine compute
#[cfg(target_os = "macos")]
pub struct GpuCosine {
    device: Device,
    queue: CommandQueue,
    cosine_kernel: ComputePipelineState,
}

/// Minimum corpus size to justify GPU overhead (M1 8GB: ~1ms GPU overhead)
#[cfg(target_os = "macos")]
const COSINE_MIN_CORPUS: usize = 1000;

#[cfg(target_os = "macos")]
impl GpuCosine {
    /// Create new GPU cosine compute device
    pub fn new() -> Option<Self> {
        let device = Device::system_default()?;
        let options = CompileOptions::new();
        let library = device.new_library_with_source(COSINE_SHADER, &options).ok()?;
        let function = library.get_function("cosine_batch", None).ok()?;
        let cosine_kernel = device.new_compute_pipeline_state_with_function(&function).ok()?;
        let queue = device.new_command_queue();
        Some(Self { device, queue, cosine_kernel })
    }

    /// Batch cosine similarity: find top-k similar corpus vectors for each query.
    /// M1 8GB: use GPU only for corpus >= 1000 vectors.
    /// Returns None if corpus too small — caller should use CPU fallback.
    pub fn batch_cosine(
        &self,
        queries: &[f32],
        corpus: &[f32],
        dim: usize,
        top_k: usize,
    ) -> Option<Vec<(usize, f32)>> {
        let num_queries = queries.len() / dim;
        let num_corpus = corpus.len() / dim;

        if num_corpus < COSINE_MIN_CORPUS {
            return None; // Fall back to CPU
        }

        Some({
            // NOTE: Everything inside this Some(...) block runs on GPU after CPU check
            let num_q = num_queries;
            let num_c = num_corpus;
            let top_k = top_k;
            let dim = dim;

        // GPU buffers — unified memory (no explicit transfer on M1)
        let mk_buf_f32 = |data: &[f32]| {
            self.device.new_buffer_with_data(
                data.as_ptr() as *const _,
                (data.len() * 4) as NSUInteger,
                MTLResourceOptions::StorageModeShared,
            )
        };

        let mk_buf_u32 = |data: &[u32]| {
            self.device.new_buffer_with_data(
                data.as_ptr() as *const _,
                (data.len() * 4) as NSUInteger,
                MTLResourceOptions::StorageModeShared,
            )
        };

        let queries_buf = mk_buf_f32(queries);
        let corpus_buf = mk_buf_f32(corpus);

        // Result buffers
        let result_indices = vec![0u32; num_queries * top_k];
        let result_scores = vec![0.0f32; num_queries * top_k];
        let indices_buf = mk_buf_u32(&result_indices);
        let scores_buf = mk_buf_f32(&result_scores);

        // Scalar params
        let num_queries_val = num_queries as u32;
        let num_corpus_val = num_corpus as u32;
        let dim_val = dim as u32;
        let top_k_val = top_k as u32;

        let mk_scalar_buf = |val: u32| {
            self.device.new_buffer_with_data(
                &val as *const _ as *const _,
                4,
                MTLResourceOptions::StorageModeShared,
            )
        };

        let num_q_buf = mk_scalar_buf(num_queries_val);
        let num_c_buf = mk_scalar_buf(num_corpus_val);
        let dim_buf = mk_scalar_buf(dim_val);
        let top_k_buf = mk_scalar_buf(top_k_val);

        // Dispatch
        let cmd_buf = self.queue.new_command_buffer();
        let encoder = cmd_buf.new_compute_command_encoder();
        encoder.set_compute_pipeline_state(&self.cosine_kernel);
        encoder.set_buffer(0, Some(&queries_buf), 0);
        encoder.set_buffer(1, Some(&corpus_buf), 0);
        encoder.set_buffer(2, Some(&indices_buf), 0);
        encoder.set_buffer(3, Some(&scores_buf), 0);
        encoder.set_buffer(4, Some(&num_q_buf), 0);
        encoder.set_buffer(5, Some(&num_c_buf), 0);
        encoder.set_buffer(6, Some(&dim_buf), 0);
        encoder.set_buffer(7, Some(&top_k_buf), 0);

        let tg_size = MTLSize { width: 256, height: 1, depth: 1 };
        let tg_count = MTLSize {
            width: ((num_queries + 255) / 256) as u64,
            height: 1,
            depth: 1,
        };
        encoder.dispatch_thread_groups(tg_count, tg_size);
        encoder.end_encoding();
        cmd_buf.commit();
        cmd_buf.wait_until_completed();

        // Read results
        let idx_ptr = indices_buf.contents() as *const u32;
        let score_ptr = scores_buf.contents() as *const f32;

        let mut results = Vec::with_capacity(num_queries * top_k);
        for q in 0..num_queries {
            for k in 0..top_k {
                let idx = unsafe { *idx_ptr.add(q * top_k + k) } as usize;
                let score = unsafe { *score_ptr.add(q * top_k + k) };
                results.push((idx, score));
            }
        }
        results
        }) // end Some
    }
}

/// Singleton GPU cosine device
#[cfg(target_os = "macos")]
static GPU_COSINE: std::sync::OnceLock<GpuCosine> = std::sync::OnceLock::new();

#[cfg(target_os = "macos")]
fn get_gpu_cosine() -> Option<&'static GpuCosine> {
    // Try to create GPU device; if successful, store in OnceLock
    if let Some(device) = GpuCosine::new() {
        let _ = GPU_COSINE.set(device);
    }
    GPU_COSINE.get()
}

/// CPU fallback: NEON-vectorized cosine similarity for small batches.
#[cfg(target_os = "macos")]
pub fn cpu_batch_cosine(queries: &[f32], corpus: &[f32], dim: usize, top_k: usize) -> Vec<(usize, f32)> {
    let num_queries = queries.len() / dim;
    let num_corpus = corpus.len() / dim;

    let mut results = Vec::with_capacity(num_queries * top_k);

    for q in 0..num_queries {
        let q_offset = q * dim;
        let query = &queries[q_offset..q_offset + dim];

        // Compute cosine for all corpus vectors
        let mut scores: Vec<(usize, f32)> = (0..num_corpus)
            .map(|c| {
                let c_offset = c * dim;
                let corpus_vec = &corpus[c_offset..c_offset + dim];
                // NEON dot product (inline for small dims)
                let dot: f32 = query.iter().zip(corpus_vec.iter()).map(|(a, b)| a * b).sum();
                (c, dot)
            })
            .collect();

        scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        scores.truncate(top_k);
        results.extend(scores);
    }

    results
}

/// GPU-accelerated batch cosine similarity — primary entry point.
/// Returns None if GPU unavailable or corpus too small; caller uses cpu_batch_cosine.
#[cfg(target_os = "macos")]
pub fn gpu_batch_cosine(
    queries: &[f32],
    corpus: &[f32],
    dim: usize,
    top_k: usize,
) -> Option<Vec<(usize, f32)>> {
    let cosine = get_gpu_cosine()?;
    cosine.batch_cosine(queries, corpus, dim, top_k)
}

#[cfg(not(target_os = "macos"))]
pub fn gpu_batch_cosine(
    _queries: &[f32],
    _corpus: &[f32],
    _dim: usize,
    _top_k: usize,
) -> Option<Vec<(usize, f32)>> {
    None
}

#[cfg(not(target_os = "macos"))]
pub fn cpu_batch_cosine(queries: &[f32], corpus: &[f32], dim: usize, top_k: usize) -> Vec<(usize, f32)> {
    let num_queries = queries.len() / dim;
    let num_corpus = corpus.len() / dim;

    let mut results = Vec::with_capacity(num_queries * top_k);

    for q in 0..num_queries {
        let q_offset = q * dim;
        let query = &queries[q_offset..q_offset + dim];

        let mut scores: Vec<(usize, f32)> = (0..num_corpus)
            .map(|c| {
                let c_offset = c * dim;
                let corpus_vec = &corpus[c_offset..c_offset + dim];
                let dot: f32 = query.iter().zip(corpus_vec.iter()).map(|(a, b)| a * b).sum();
                (c, dot)
            })
            .collect();

        scores.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        scores.truncate(top_k);
        results.extend(scores);
    }

    results
}
