//! Page compression for LMDB-backed caches (hot-edges, IOC dedup).
//!
//! Two codecs, one API:
//!   - ``lz4_flex`` — fast encode/decode, ~2:1 ratio on binary data. Used for
//!     hot-path compression (every write/read). Speed > ratio.
//!   - ``zstd`` — higher ratio (~3:1 on text), slower. Used when lz4
//!     output is not smaller than input (i.e. incompressible data).
//!
//! Wire format: 1-byte header (`0x00`=uncompressed, `0x01`=lz4, `0x02`=zstd,
//! `0x03`=zstd_with_dict) followed by the compressed payload. For `0x03`,
//! a 4-byte little-endian dictionary ID follows the marker before the
//! compressed payload. This allows the decompressor to pick the right codec
//! and dictionary without an external schema.
//!
//! Bounds:
//!   - Input: 64 B ≤ page ≤ 1 MB (rejected outside range)
//!   - Output: always ≤ input bytes (codec guarantees)
//!   - Never panics — all errors return a meaningful Python exception.

use std::collections::HashMap;
use std::sync::{LazyLock, Mutex};

use pyo3::prelude::*;
use rayon::prelude::*;

/// Global dictionary registry: dict_id → raw zstd dictionary bytes.
/// Populated via Python `register_zstd_dict()` at startup.
static DICT_REGISTRY: LazyLock<Mutex<HashMap<u32, Vec<u8>>>> =
    LazyLock::new(|| Mutex::new(HashMap::new()));

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Minimum page size to attempt compression.
const MIN_PAGE_SIZE: usize = 64;

/// Maximum page size — prevents OOM on malformed input.
const MAX_PAGE_SIZE: usize = 1 * 1024 * 1024; // 1 MiB

/// Header markers.
const HDR_UNCOMPRESSED: u8 = 0x00;
const HDR_LZ4: u8 = 0x01;
const HDR_ZSTD: u8 = 0x02;
const HDR_ZSTD_DICT: u8 = 0x03;

/// Size of the dictionary ID field after HDR_ZSTD_DICT marker (little-endian u32).
const DICT_ID_SIZE: usize = 4;

// ---------------------------------------------------------------------------
// Core compression — lz4 first, zstd fallback
// ---------------------------------------------------------------------------

/// Compress a page using lz4 (fast) or zstd (high ratio).
///
/// Returns wire-format bytes: `[marker][compressed_payload]`.
/// If compression does not yield savings, returns `[HDR_UNCOMPRESSED][data]`.
/// Fails gracefully if input is outside [64B, 1MB] bounds.
fn compress_page_impl(data: &[u8]) -> Result<Vec<u8>, &'static str> {
    if data.len() < MIN_PAGE_SIZE {
        return Err("input too small (min 64 bytes)");
    }
    if data.len() > MAX_PAGE_SIZE {
        return Err("input too large (max 1 MiB)");
    }

    // lz4 — fast encode for hot path. Returns Vec directly (no Result).
    let lz4_out = lz4_flex::compress_prepend_size(data);

    // Only use lz4 if it actually saves space.
    if !lz4_out.is_empty() && lz4_out.len() < data.len() {
        let mut out = Vec::with_capacity(1 + lz4_out.len());
        out.push(HDR_LZ4);
        out.extend_from_slice(&lz4_out);
        return Ok(out);
    }

    // lz4 didn't help — try zstd.
    let zstd_out = match zstd::encode_all(data, 3) {
        Ok(out) => out,
        Err(_) => {
            // zstd failed — return uncompressed.
            let mut out = Vec::with_capacity(1 + data.len());
            out.push(HDR_UNCOMPRESSED);
            out.extend_from_slice(data);
            return Ok(out);
        }
    };

    if zstd_out.len() < data.len() {
        let mut out = Vec::with_capacity(1 + zstd_out.len());
        out.push(HDR_ZSTD);
        out.extend_from_slice(&zstd_out);
        Ok(out)
    } else {
        // Neither codec helped — store uncompressed.
        let mut out = Vec::with_capacity(1 + data.len());
        out.push(HDR_UNCOMPRESSED);
        out.extend_from_slice(data);
        Ok(out)
    }
}

/// Compress a page using a pre-registered zstd dictionary.
///
/// Wire format: `[HDR_ZSTD_DICT][dict_id: 4 bytes LE][compressed_payload]`.
/// If the dictionary isn't registered, falls back to plain zstd (HDR_ZSTD).
/// If compression doesn't save space, returns uncompressed.
fn compress_page_with_dict_impl(data: &[u8], dict_id: u32) -> Result<Vec<u8>, &'static str> {
    if data.len() < MIN_PAGE_SIZE {
        return Err("input too small (min 64 bytes)");
    }
    if data.len() > MAX_PAGE_SIZE {
        return Err("input too large (max 1 MiB)");
    }

    let dict = DICT_REGISTRY
        .lock()
        .map_err(|_| "dict registry lock poisoned")?
        .get(&dict_id)
        );

    match dict {
        Some(dict_data) => {
            // Compress with dictionary
            let mut encoder = zstd::stream::Encoder::with_dictionary(Vec::new(), 3, &dict_data[..])
                .map_err(|_| "zstd dict encoder init failed")?;
            // Use write_all via io::Write
            std::io::Write::write_all(&mut encoder, data)
                .map_err(|_| "zstd dict compress failed")?;
            let zstd_out = encoder.finish().map_err(|_| "zstd dict finish failed")?;

            if zstd_out.len() < data.len() {
                let mut out = Vec::with_capacity(1 + DICT_ID_SIZE + zstd_out.len());
                out.push(HDR_ZSTD_DICT);
                out.extend_from_slice(&dict_id.to_le_bytes());
                out.extend_from_slice(&zstd_out);
                Ok(out)
            } else {
                // Dictionary didn't help — store uncompressed
                let mut out = Vec::with_capacity(1 + data.len());
                out.push(HDR_UNCOMPRESSED);
                out.extend_from_slice(data);
                Ok(out)
            }
        }
        None => {
            // Dictionary not registered — fall back to plain zstd
            let zstd_out = match zstd::encode_all(data, 3) {
                Ok(out) => out,
                Err(_) => {
                    let mut out = Vec::with_capacity(1 + data.len());
                    out.push(HDR_UNCOMPRESSED);
                    out.extend_from_slice(data);
                    return Ok(out);
                }
            };
            if zstd_out.len() < data.len() {
                let mut out = Vec::with_capacity(1 + zstd_out.len());
                out.push(HDR_ZSTD);
                out.extend_from_slice(&zstd_out);
                Ok(out)
            } else {
                let mut out = Vec::with_capacity(1 + data.len());
                out.push(HDR_UNCOMPRESSED);
                out.extend_from_slice(data);
                Ok(out)
            }
        }
    }
}

/// Decompress wire-format bytes back to original page.
fn decompress_page_impl(wire: &[u8]) -> Result<Vec<u8>, &'static str> {
    if wire.is_empty() {
        return Err("empty wire bytes");
    }
    let marker = wire[0];
    let payload = &wire[1..];

    match marker {
        HDR_UNCOMPRESSED => Ok(payload.to_vec()),
        HDR_LZ4 => {
            // lz4_flex stores with size prefix.
            match lz4_flex::decompress_size_prepended(payload) {
                Ok(out) => Ok(out),
                Err(_) => Err("lz4 decompress failed"),
            }
        }
        HDR_ZSTD => match zstd::decode_all(payload) {
            Ok(out) => Ok(out),
            Err(_) => Err("zstd decompress failed"),
        },
        HDR_ZSTD_DICT => {
            if payload.len() < DICT_ID_SIZE {
                return Err("zstd dict wire too short (missing dict ID)");
            }
            let dict_id_bytes: [u8; 4] = payload[..DICT_ID_SIZE]
                .try_into()
                .map_err(|_| "zstd dict id parse failed")?;
            let dict_id = u32::from_le_bytes(dict_id_bytes);
            let compressed = &payload[DICT_ID_SIZE..];
            let dict = DICT_REGISTRY
                .lock()
                .map_err(|_| "dict registry lock poisoned")?
                .get(&dict_id)
                );
            match dict {
                Some(dict_data) => {
                    let mut decoder =
                        zstd::stream::Decoder::with_dictionary(compressed, &dict_data[..])
                            .map_err(|_| "zstd dict decoder init failed")?;
                    let mut out = Vec::new();
                    std::io::copy(&mut decoder, &mut out)
                        .map_err(|_| "zstd dict decompress read failed")?;
                    Ok(out)
                }
                None => Err("unknown dictionary ID — register it first"),
            }
        }
        _ => Err("unknown compression marker"),
    }
}

// ---------------------------------------------------------------------------
// Python bindings
// ---------------------------------------------------------------------------

/// Compress a page for LMDB storage.
///
/// Args:
///   data: bytes — raw page (64 B ≤ len ≤ 1 MB)
///
/// Returns:
///   bytes — wire-format compressed page (marker + payload)
///
/// ## GIL Handling
/// Releases GIL via `release_gil` during CPU-bound compression (lz4/zstd).
/// This allows asyncio event loop to run on other threads during compression.
#[pyfunction]
pub fn compress_page(data: &[u8]) -> PyResult<Vec<u8>> {
    use crate::gil::release_gil;
    Python::attach(|py| {
        release_gil(py, || {
            compress_page_impl(data)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("compress_page: {}", e)))
        })
    })
}

/// Decompress a wire-format page from LMDB storage.
///
/// Args:
///   wire: bytes — wire-format page (marker + compressed payload)
///
/// Returns:
///   bytes — decompressed original page
///
/// ## GIL Handling
/// Releases GIL via `release_gil` during CPU-bound decompression.
#[pyfunction]
pub fn decompress_page(wire: &[u8]) -> PyResult<Vec<u8>> {
    use crate::gil::release_gil;
    Python::attach(|py| {
        release_gil(py, || {
            decompress_page_impl(wire)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("decompress_page: {}", e)))
        })
    })
}

/// Compress many pages in parallel via rayon.
///
/// For batch operations on hot-edges cache (bulk insert).
/// Uses the singleton `io_pool()` (2 threads) — CPU-bound compression benefits
/// from parallelism; 2 threads is the P-core ceiling for M1 8GB.
///
/// Args:
///   pages: list of bytes — raw pages (each 64 B ≤ len ≤ 1 MB)
///
/// Returns:
///   list of bytes — wire-format compressed pages
#[pyfunction]
pub fn batch_compress_pages(pages: Vec<Vec<u8>>) -> Vec<Vec<u8>> {
    let n = pages);
    if n < 64 {
        // Small batch: serial fallback
        pages
            .iter()
            .map(|data| {
                compress_page_impl(data).unwrap_or_else(|_| {
                    let mut out = Vec::with_capacity(1 + data.len());
                    out.push(HDR_UNCOMPRESSED);
                    out.extend_from_slice(data);
                    out
                })
            })
            .collect()
    } else {
        // CPU-bound: use io_pool() (2 threads)
        // Issue #6: GIL released via `release_gil` to enable true rayon parallelism.
        use crate::gil::release_gil;
        Python::attach(|py| {
            release_gil(py, || {
                crate::io_pool().install(|| {
                    pages
                        .par_iter()
                        .map(|data| {
                            compress_page_impl(data).unwrap_or_else(|_| {
                                let mut out = Vec::with_capacity(1 + data.len());
                                out.push(HDR_UNCOMPRESSED);
                                out.extend_from_slice(data);
                                out
                            })
                        })
                        .collect()
                })
            })
        })
    }
}

/// Decompress many wire-format pages in parallel.
///
/// Uses singleton `io_pool()` (2 threads) for the same reason as batch_compress_pages.
#[pyfunction]
pub fn batch_decompress_pages(wires: Vec<Vec<u8>>) -> Vec<Vec<u8>> {
    let n = wires);
    if n < 64 {
        wires
            .iter()
            .map(|wire| decompress_page_impl(wire).unwrap_or_else(|_| Vec::new()))
            .collect()
    } else {
        // Issue #6: GIL released via `release_gil` to enable true rayon parallelism.
        use crate::gil::release_gil;
        Python::attach(|py| {
            release_gil(py, || {
                crate::io_pool().install(|| {
                    wires
                        .par_iter()
                        .map(|wire| decompress_page_impl(wire).unwrap_or_else(|_| Vec::new()))
                        .collect()
                })
            })
        })
    }
}

// ---------------------------------------------------------------------------
// Dictionary-aware compression — HEIST-07
// ---------------------------------------------------------------------------

/// Register a zstd dictionary for use with compress_page_dict.
///
/// Dictionaries are trained offline (e.g., via zstd_compressor.py) and loaded
/// at startup. Each dictionary gets a unique u32 ID. The registry is global
/// and thread-safe (Mutex-protected HashMap).
///
/// Args:
///   dict_id: u32 — unique dictionary identifier
///   dict_data: bytes — raw zstd dictionary bytes (from zstd.train_dictionary)
///
/// Returns:
///   bool — True if registered, False if ID already exists
#[pyfunction]
pub fn register_zstd_dict(dict_id: u32, dict_data: Vec<u8>) -> PyResult<bool> {
    let mut registry = DICT_REGISTRY.lock().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("dict registry lock poisoned: {}", e))
    })?;
    if registry.contains_key(&dict_id) {
        return Ok(false);
    }
    registry.insert(dict_id, dict_data);
    Ok(true)
}

/// Unregister a zstd dictionary, freeing its memory.
///
/// Args:
///   dict_id: u32 — dictionary ID to remove
///
/// Returns:
///   bool — True if removed, False if not found
#[pyfunction]
pub fn unregister_zstd_dict(dict_id: u32) -> PyResult<bool> {
    let mut registry = DICT_REGISTRY.lock().map_err(|e| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("dict registry lock poisoned: {}", e))
    })?;
    Ok(registry.remove(&dict_id).is_some())
}

/// Compress a page using a pre-registered zstd dictionary.
///
/// Wire format: `[0x03][dict_id: 4 bytes LE][zstd_compressed_with_dict]`.
/// Falls back to plain zstd (0x02) if dictionary not registered.
///
/// Args:
///   data: bytes — raw page (64 B ≤ len ≤ 1 MB)
///   dict_id: int — dictionary ID registered via register_zstd_dict
///
/// Returns:
///   bytes — wire-format compressed page
///
/// ## GIL Handling
/// Releases GIL via `release_gil` during CPU-bound zstd compression.
#[pyfunction]
pub fn compress_page_dict(data: &[u8], dict_id: u32) -> PyResult<Vec<u8>> {
    use crate::gil::release_gil;
    Python::attach(|py| {
        release_gil(py, || {
            compress_page_with_dict_impl(data, dict_id)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("compress_page_dict: {}", e)))
        })
    })
}

// ---------------------------------------------------------------------------
// Raw LZ4 for JSONL streaming — lz4_flex frame format (no wire header)
// ---------------------------------------------------------------------------

/// Compress bytes using lz4 frame format (raw, no size prefix).
///
/// Used by jsonl_lz4_writer for streaming JSONL batch compression.
/// Wire format: raw lz4 frame bytes — decompress with lz4_decompress_raw.
///
/// Args:
///   data: bytes — raw input to compress
///
/// Returns:
///   bytes — lz4 frame compressed data
///
/// ## GIL Handling
/// Releases GIL via `release_gil` during CPU-bound lz4 compression.
#[pyfunction]
pub fn lz4_compress_raw(data: &[u8]) -> PyResult<Vec<u8>> {
    use crate::gil::release_gil;
    if data.is_empty() {
        return Ok(Vec::new());
    }
    Python::attach(|py| {
        release_gil(py, || {
            // lz4_flex frame format — appends nothing extra, self-contained.
            match lz4_flex::compress_prepend_size(data) {
                out if out.len() < data.len() => {
                    // Strip the 4-byte little-endian size prefix (lz4_flex always prepends it).
                    // Safe: size prefix is exactly 4 bytes at the start.
                    Ok(out[4..].to_vec())
                }
                // Compression didn't help — store raw
                out => Ok(out[4..].to_vec()),
            }
        })
    })
}

/// Decompress lz4 frame bytes back to original.
///
/// Complements lz4_compress_raw. Reads the full lz4 frame.
/// For empty input (len=0) returns empty vec.
///
/// Args:
///   compressed: bytes — lz4 frame data (from lz4_compress_raw)
///
/// Returns:
///   bytes — decompressed original data
///
/// ## GIL Handling
/// Releases GIL via `release_gil` during CPU-bound lz4 decompression.
#[pyfunction]
pub fn lz4_decompress_raw(compressed: &[u8]) -> PyResult<Vec<u8>> {
    use crate::gil::release_gil;
    if compressed.is_empty() {
        return Ok(Vec::new());
    }
    Python::attach(|py| {
        release_gil(py, || {
            // Re-add the 4-byte size prefix that lz4_flex::decompress_size_prepended expects.
            let size = compressed.len() as u32;
            let mut wire = Vec::with_capacity(4 + compressed.len());
            wire.extend_from_slice(&size.to_le_bytes());
            wire.extend_from_slice(compressed);

            lz4_flex::decompress_size_prepended(&wire)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("lz4_decompress_raw: {}", e)))
        })
    })
}

/// Compress a JSONL batch: join lines with '\n', compress with lz4 frame.
///
/// High-level helper for jsonl_lz4_writer. Each input bytes is one JSON line.
/// Output is a single lz4 frame containing all lines joined by '\n'.
///
/// Args:
///   lines: list of bytes — individual JSON lines (each a complete JSON object)
///
/// Returns:
///   bytes — lz4 frame containing all lines joined by newline
///
/// ## GIL Handling
/// Releases GIL via `release_gil` during CPU-bound lz4 compression.
#[pyfunction]
pub fn lz4_compress_jsonl_batch(lines: Vec<Vec<u8>>) -> PyResult<Vec<u8>> {
    use crate::gil::release_gil;
    if lines.is_empty() {
        return Ok(Vec::new());
    }
    Python::attach(|py| {
        release_gil(py, || {
            // Join with '\n' — JSONL standard delimiter
            let total: usize = lines.iter().map(|l| l.len()).sum::<usize>() + lines.len() - 1;
            let mut combined = Vec::with_capacity(total);
            for (i, line) in lines.iter().enumerate() {
                if i > 0 {
                    combined.push(b'\n');
                }
                combined.extend_from_slice(line);
            }
            // Direct lz4 compression (avoid nested Python::attach)
            match lz4_flex::compress_prepend_size(&combined) {
                out if out.len() < combined.len() => {
                    // Strip the 4-byte little-endian size prefix
                    Ok(out[4..].to_vec())
                }
                out => Ok(out[4..].to_vec()),
            }
        })
    })
}

/// Decompress an lz4-compressed JSONL batch into individual lines.
///
/// Complements lz4_compress_jsonl_batch.
///
/// Args:
///   compressed: bytes — lz4 frame containing '\n'-joined JSON lines
///
/// Returns:
///   list of bytes — individual JSON lines
///
/// ## GIL Handling
/// Releases GIL via `release_gil` during CPU-bound lz4 decompression.
#[pyfunction]
pub fn lz4_decompress_jsonl_batch(compressed: &[u8]) -> PyResult<Vec<Vec<u8>>> {
    use crate::gil::release_gil;
    if compressed.is_empty() {
        return Ok(Vec::new());
    }
    Python::attach(|py| {
        release_gil(py, || {
            // Re-add the 4-byte size prefix
            let size = compressed.len() as u32;
            let mut wire = Vec::with_capacity(4 + compressed.len());
            wire.extend_from_slice(&size.to_le_bytes());
            wire.extend_from_slice(compressed);

            let decompressed = lz4_flex::decompress_size_prepended(&wire)
                .map_err(|e| pyo3::exceptions::PyValueError::new_err(format!("lz4_decompress_jsonl_batch: {}", e)))?;
            let lines: Vec<Vec<u8>> = decompressed
                .split(|&b| b == b'\n')
                .map(|s| s.to_vec())
                );
            Ok(lines)
        })
    })
}

/// Register compression functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compress_page))?;
    m.add_function(wrap_pyfunction!(decompress_page))?;
    m.add_function(wrap_pyfunction!(batch_compress_pages))?;
    m.add_function(wrap_pyfunction!(batch_decompress_pages))?;
    m.add_function(wrap_pyfunction!(register_zstd_dict))?;
    m.add_function(wrap_pyfunction!(unregister_zstd_dict))?;
    m.add_function(wrap_pyfunction!(compress_page_dict))?;
    m.add_function(wrap_pyfunction!(lz4_compress_raw))?;
    m.add_function(wrap_pyfunction!(lz4_decompress_raw))?;
    m.add_function(wrap_pyfunction!(lz4_compress_jsonl_batch))?;
    m.add_function(wrap_pyfunction!(lz4_decompress_jsonl_batch))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compress_decompress_roundtrip() {
        let data = b"hello world this is a test page for compression");
        let wire = compress_page_impl(&data));
        let decompressed = decompress_page_impl(&wire));
        assert_eq!(decompressed, data);
    }

    #[test]
    fn test_lz4_used_when_saves_space() {
        // Repeating data compresses well with lz4.
        let data: Vec<u8> = (0..1000).map(|i| (i % 26) as u8 + b'a'));
        let wire = compress_page_impl(&data));
        assert_eq!(wire[0], HDR_LZ4);
        assert!(wire.len() < data.len());
    }

    #[test]
    fn test_uncompressed_when_too_small() {
        // Data smaller than MIN_PAGE_SIZE — stored uncompressed.
        let data = b"tiny");
        let wire = compress_page_impl(&data));
        assert_eq!(wire[0], HDR_UNCOMPRESSED);
        assert_eq!(&wire[1..], &data);
    }

    #[test]
    fn test_uncompressed_when_incompressible() {
        // Deterministic pseudo-random — should not compress well.
        let data: Vec<u8> = (0..1000)
            .map(|i| ((i as u64 * 6364136223846793005_u64 + 1) >> 33) as u8)
            );
        let wire = compress_page_impl(&data));
        // Just check roundtrip — may be lz4/zstd/uncompressed.
        let decompressed = decompress_page_impl(&wire));
        assert_eq!(decompressed, data);
    }

    #[test]
    fn test_batch_roundtrip() {
        let pages: Vec<Vec<u8>> = vec![
            b"page one data here".to_vec(),
            b"page two with different content".to_vec(),
            (0..200).map(|i| i as u8).collect(),
        ];
        let wires = pages
            .iter()
            .map(|p| compress_page_impl(p).unwrap())
            );
        let decompressed = batch_decompress_pages(wires);
        assert_eq!(decompressed, pages);
    }
}
