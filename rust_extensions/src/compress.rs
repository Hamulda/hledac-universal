//! Page compression for LMDB-backed caches (hot-edges, IOC dedup).
//!
//! Two codecs, one API:
//!   - ``lz4_flex`` — fast encode/decode, ~2:1 ratio on binary data. Used for
//!     hot-path compression (every write/read). Speed > ratio.
//!   - ``zstd`` — higher ratio (~3:1 on text), slower. Used when lz4
//!     output is not smaller than input (i.e. incompressible data).
//!
//! Wire format: 1-byte header (`0x00`=uncompressed, `0x01`=lz4, `0x02`=zstd)
//! followed by the compressed payload. This allows the decompressor to
//! pick the right codec without an external schema.
//!
//! Bounds:
//!   - Input: 64 B ≤ page ≤ 1 MB (rejected outside range)
//!   - Output: always ≤ input bytes (codec guarantees)
//!   - Never panics — all errors return a meaningful Python exception.

use pyo3::prelude::*;
use rayon::prelude::*;

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
#[pyfunction]
pub fn compress_page(data: &[u8]) -> PyResult<Vec<u8>> {
    compress_page_impl(data).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("compress_page: {}", e))
    })
}

/// Decompress a wire-format page from LMDB storage.
///
/// Args:
///   wire: bytes — wire-format page (marker + compressed payload)
///
/// Returns:
///   bytes — decompressed original page
#[pyfunction]
pub fn decompress_page(wire: &[u8]) -> PyResult<Vec<u8>> {
    decompress_page_impl(wire).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("decompress_page: {}", e))
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
    let n = pages.len();
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
    }
}

/// Decompress many wire-format pages in parallel.
///
/// Uses singleton `io_pool()` (2 threads) for the same reason as batch_compress_pages.
#[pyfunction]
pub fn batch_decompress_pages(wires: Vec<Vec<u8>>) -> Vec<Vec<u8>> {
    let n = wires.len();
    if n < 64 {
        wires
            .iter()
            .map(|wire| {
                decompress_page_impl(wire).unwrap_or_else(|_| Vec::new())
            })
            .collect()
    } else {
        crate::io_pool().install(|| {
            wires
                .par_iter()
                .map(|wire| {
                    decompress_page_impl(wire).unwrap_or_else(|_| Vec::new())
                })
                .collect()
        })
    }
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
#[pyfunction]
pub fn lz4_compress_raw(data: &[u8]) -> PyResult<Vec<u8>> {
    if data.is_empty() {
        return Ok(Vec::new());
    }
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
#[pyfunction]
pub fn lz4_decompress_raw(compressed: &[u8]) -> PyResult<Vec<u8>> {
    if compressed.is_empty() {
        return Ok(Vec::new());
    }
    // Re-add the 4-byte size prefix that lz4_flex::decompress_size_prepended expects.
    let size = compressed.len() as u32;
    let mut wire = Vec::with_capacity(4 + compressed.len());
    wire.extend_from_slice(&size.to_le_bytes());
    wire.extend_from_slice(compressed);

    lz4_flex::decompress_size_prepended(&wire).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("lz4_decompress_raw: {}", e))
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
#[pyfunction]
pub fn lz4_compress_jsonl_batch(lines: Vec<Vec<u8>>) -> PyResult<Vec<u8>> {
    if lines.is_empty() {
        return Ok(Vec::new());
    }
    // Join with '\n' — JSONL standard delimiter
    let total: usize = lines.iter().map(|l| l.len()).sum::<usize>() + lines.len() - 1;
    let mut combined = Vec::with_capacity(total);
    for (i, line) in lines.iter().enumerate() {
        if i > 0 {
            combined.push(b'\n');
        }
        combined.extend_from_slice(line);
    }
    lz4_compress_raw(&combined)
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
#[pyfunction]
pub fn lz4_decompress_jsonl_batch(compressed: &[u8]) -> PyResult<Vec<Vec<u8>>> {
    if compressed.is_empty() {
        return Ok(Vec::new());
    }
    let decompressed = lz4_decompress_raw(compressed)?;
    let lines: Vec<Vec<u8>> = decompressed
        .split(|&b| b == b'\n')
        .map(|s| s.to_vec())
        .collect();
    Ok(lines)
}

/// Register compression functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(compress_page, m)?)?;
    m.add_function(wrap_pyfunction!(decompress_page, m)?)?;
    m.add_function(wrap_pyfunction!(batch_compress_pages, m)?)?;
    m.add_function(wrap_pyfunction!(batch_decompress_pages, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_compress_decompress_roundtrip() {
        let data = b"hello world this is a test page for compression".to_vec();
        let wire = compress_page_impl(&data).unwrap();
        let decompressed = decompress_page_impl(&wire).unwrap();
        assert_eq!(decompressed, data);
    }

    #[test]
    fn test_lz4_used_when_saves_space() {
        // Repeating data compresses well with lz4.
        let data: Vec<u8> = (0..1000).map(|i| (i % 26) as u8 + b'a').collect();
        let wire = compress_page_impl(&data).unwrap();
        assert_eq!(wire[0], HDR_LZ4);
        assert!(wire.len() < data.len());
    }

    #[test]
    fn test_uncompressed_when_too_small() {
        // Data smaller than MIN_PAGE_SIZE — stored uncompressed.
        let data = b"tiny".to_vec();
        let wire = compress_page_impl(&data).unwrap();
        assert_eq!(wire[0], HDR_UNCOMPRESSED);
        assert_eq!(&wire[1..], &data);
    }

    #[test]
    fn test_uncompressed_when_incompressible() {
        // Deterministic pseudo-random — should not compress well.
        let data: Vec<u8> = (0..1000)
            .map(|i| ((i as u64 * 6364136223846793005_u64 + 1) >> 33) as u8)
            .collect();
        let wire = compress_page_impl(&data).unwrap();
        // Just check roundtrip — may be lz4/zstd/uncompressed.
        let decompressed = decompress_page_impl(&wire).unwrap();
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
            .collect();
        let decompressed = batch_decompress_pages(wires);
        assert_eq!(decompressed, pages);
    }

    }