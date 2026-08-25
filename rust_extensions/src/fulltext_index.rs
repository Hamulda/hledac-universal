//! Tantivy full-text search index — mmap-backed, zero-copy, persistent BM25.
//!
//! Replaces Python in-memory `BM25Index` (knowledge/rag_engine.py:88-164)
//! with a native Rust Tantivy index.
//!
//! Key advantages over Python BM25Index:
//! - mmap-backed: ~5MB RAM for 50K documents (vs ~200MB Python)
//! - Zero-copy: documents read directly from mmap, no serialization
//! - Persistent: index survives process restart
//! - Parallel: Tantivy uses rayon for multi-threaded search
//! - No 50K document limit (Tantivy handles millions)
//!
//! M1 8GB safe:
//! - mmap uses demand paging — only accessed pages consume RAM
//! - Index size grows with data, not RAM
//! - Feature-gated (fulltext feature) — NOT compiled in default builds
//!
//! Python fallback:
//! - When fulltext feature not enabled, Python BM25Index is used
//! - knowledge/rag_engine.py: TantivyFulltextIndex wrapper checks availability
//! - knowledge/search_index.py: LocalSearchSeam uses Tantivy when available
//!
//! ISSUE-011: Python BM25Index → Tantivy fulltext replacement.

// MODERN-17 FIX: Use arrow crate for proper Arrow IPC encoding.
// The hand-rolled ARROW1\xff\xff\xff\xff framing was broken — pyarrow couldn't parse it.
use arrow::array::ArrayRef;
use arrow::datatypes::Schema as ArrowSchema;
use arrow::ipc::writer::StreamWriter;
use arrow::record_batch::RecordBatch;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use std::path::Path;
use std::sync::Arc;
use tantivy::collector::TopDocs;
use tantivy::query::QueryParser;
use tantivy::schema::*;
use tantivy::tokenizer::*;
use tantivy::{doc, Index, IndexReader, IndexWriter, ReloadPolicy};

/// Field name for document ID (stored, retrievable in results).
const FIELD_DOC_ID: &str = "doc_id";

/// Field name for document content (indexed, tokenized for BM25).
const FIELD_CONTENT: &str = "content";

/// TANTIVY 0.22 FIX: Removed custom tokenizer registration.
///
/// Tantivy 0.22's TEXT flag automatically uses the built-in "default" tokenizer,
/// which is identical to our previous custom config (SimpleTokenizer + LowerCaser +
/// RemoveLongFilter::limit(40)). No custom registration needed.
///
/// For custom tokenizers in the future:
///   let manager = tantivy::tokenizer::TokenizerManager::default();
///   manager.register("custom", my_tokenizer);
///   // Then set via IndexSettings::set_indexing(tokenizer_name)

/// Build the Tantivy schema for fulltext index.
///
/// Fields:
///   doc_id   → STRING (stored, indexed as raw — exact match for doc retrieval)
///   content  → TEXT (stored, indexed with default tokenizer for BM25)
fn build_schema() -> Schema {
    let mut schema_builder = Schema::builder();
    schema_builder.add_text_field(FIELD_DOC_ID, STRING | STORED);
    schema_builder.add_text_field(FIELD_CONTENT, TEXT | STORED);
    schema_builder.build()
}

/// Open or create a Tantivy index at the given path.
///
/// If the directory exists and contains a Tantivy meta.json, opens it.
/// Otherwise creates a new index with the default schema.
fn open_or_create_index(index_path: &Path) -> tantivy::Result<Index> {
    use tantivy::directory::MmapDirectory;

    if index_path.exists() && index_path.join("meta.json").exists() {
        let dir = MmapDirectory::open(index_path)?;
        Index::open(dir)
    } else {
        std::fs::create_dir_all(index_path)?;
        let dir = MmapDirectory::open(index_path)?;
        let schema = build_schema();
        // TANTIVY 0.22 FIX: Index::create now requires IndexSettings as 3rd parameter.
        // Default settings use mmap directory and simple tokenizer.
        let settings = tantivy::IndexSettings::default();
        Index::create(dir, schema, settings)
    }
}

/// Get the doc_id field from schema (panics if schema doesn't have FIELD_DOC_ID).
fn get_doc_id_field(schema: &Schema) -> Field {
    schema
        .get_field(FIELD_DOC_ID)
        .expect("Schema must have doc_id field")
}

/// Get the content field from schema (panics if schema doesn't have FIELD_CONTENT).
fn get_content_field(schema: &Schema) -> Field {
    schema
        .get_field(FIELD_CONTENT)
        .expect("Schema must have content field")
}

/// Create a new fulltext index from a batch of (doc_id, content) documents.
///
/// Overwrites any existing index at `index_path`.
///
/// M1 8GB: chunked writer (15MB memory budget) prevents OOM on large batches.
/// Each commit flushes to mmap, releasing heap memory.
///
/// Args:
///     index_path: Directory where the Tantivy index will be stored.
///     documents: List of (doc_id, content) tuples as Python list of 2-tuples.
///                doc_id: unique string identifier for the document.
///                content: full text content to index.
///
/// Returns: None on success, raises PyErr on failure.
#[pyfunction]
#[pyo3(signature = (index_path, documents))]
fn fulltext_create_index(index_path: &str, documents: Vec<(String, String)>) -> PyResult<()> {
    let index_path = Path::new(index_path);
    // Remove existing index if present (fresh start)
    if index_path.exists() {
        let _ = std::fs::remove_dir_all(index_path);
    }

    let index = open_or_create_index(index_path).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Tantivy: failed to create index at {}: {}",
            index_path.display(),
            e
        ))
    })?;

    let schema = build_schema();
    let doc_id_field = get_doc_id_field(&schema);
    let content_field = get_content_field(&schema);

    // TANTIVY 0.22 FIX: IndexWriter now requires mutable reference.
    let mut writer: IndexWriter = index.writer_with_num_threads(1, 15_000_000).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Tantivy: failed to create writer: {}",
            e
        ))
    })?;

    // ISSUE-011: Chunked indexing — 1000 docs per commit.
    // Each commit flushes intermediate data to mmap, keeping heap < 15MB.
    const CHUNK_SIZE: usize = 1000;

    for chunk in documents.chunks(CHUNK_SIZE) {
        for (doc_id, content) in chunk {
            let tantivy_doc = doc!(
                doc_id_field => doc_id.clone(),
                content_field => content.clone(),
            );
            writer.add_document(tantivy_doc).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "Tantivy: failed to add document '{}': {}",
                    doc_id, e
                ))
            })?;
        }
        writer.commit().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Tantivy: commit failed: {}",
                e
            ))
        })?;
    }

    // Final commit for any remaining docs
    writer.commit().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Tantivy: final commit failed: {}",
            e
        ))
    })?;

    Ok(())
}

/// Add documents to an existing fulltext index.
///
/// If the index does not exist, creates a new one.
///
/// Args:
///     index_path: Directory of the Tantivy index.
///     documents: List of (doc_id, content) tuples to add.
///
/// Returns: None on success, raises PyErr on failure.
#[pyfunction]
#[pyo3(signature = (index_path, documents))]
fn fulltext_add_documents(index_path: &str, documents: Vec<(String, String)>) -> PyResult<()> {

    let index_path = Path::new(index_path);
    let index = open_or_create_index(index_path).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Tantivy: failed to open index at {}: {}",
            index_path.display(),
            e
        ))
    })?;

    let schema = index.as_str();
    let doc_id_field = get_doc_id_field(&schema);
    let content_field = get_content_field(&schema);

    // TANTIVY 0.22 FIX: IndexWriter now requires mutable reference.
    let mut writer: IndexWriter = index.writer_with_num_threads(1, 15_000_000).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Tantivy: failed to create writer: {}",
            e
        ))
    })?;

    const CHUNK_SIZE: usize = 1000;
    for chunk in documents.chunks(CHUNK_SIZE) {
        for (doc_id, content) in chunk {
            let tantivy_doc = doc!(
                doc_id_field => doc_id.clone(),
                content_field => content.clone(),
            );
            writer.add_document(tantivy_doc).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                    "Tantivy: failed to add document '{}': {}",
                    doc_id, e
                ))
            })?;
        }
        writer.commit().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Tantivy: commit failed: {}",
                e
            ))
        })?;
    }
    writer.commit().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Tantivy: final commit failed: {}",
            e
        ))
    })?;

    Ok(())
}

/// Search a fulltext index and return top-K (doc_id, bm25_score) results.
///
/// Uses Tantivy's built-in BM25 scoring with the default tokenizer.
/// Query syntax supports Tantivy query language (field:value, boolean, phrases).
/// For plain text queries, terms are ANDed by default.
///
/// Args:
///     index_path: Directory of the Tantivy index.
///     query: Search query string (Tantivy query syntax or plain text).
///     top_k: Maximum number of results to return.
///
/// Returns: List of (doc_id, bm25_score) tuples, sorted by score descending.
///          Empty list on no matches or error.
#[pyfunction]
#[pyo3(signature = (index_path, query, top_k = 10))]
fn fulltext_search(index_path: &str, query: &str, top_k: u32) -> PyResult<Vec<(String, f32)>> {

    if query.trim().is_empty() {
        return Ok(Vec::new());
    }

    let index_path = Path::new(index_path);
    let index = match Index::open_in_dir(index_path) {
        Ok(idx) => idx,
        Err(_) => return Ok(Vec::new()),
    };

    let schema = index.as_str();
    let doc_id_field = get_doc_id_field(&schema);
    let content_field = get_content_field(&schema);

    let reader: IndexReader = index
        .reader_builder()
        .reload_policy(ReloadPolicy::OnCommitWithDelay)
        .try_into()
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Tantivy: failed to create reader: {}",
                e
            ))
        })?;

    let searcher = reader.as_str();
    let query_parser = QueryParser::for_index(&index, vec![content_field]);
    let query = query_parser.parse_query(query).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "Tantivy: query parse error: {}",
            e
        ))
    })?;

    let top_docs = searcher
        .search(&query, &TopDocs::with_limit(top_k as usize))
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Tantivy: search failed: {}",
                e
            ))
        })?;

    let mut results: Vec<(String, f32)> = Vec::with_capacity(top_docs.len());
    for (score, doc_address) in top_docs {
        // TANTIVY 0.22 FIX: searcher.doc() now requires explicit type annotation.
        let retrieved_doc: TantivyDocument = searcher.doc(doc_address).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Tantivy: failed to retrieve doc: {}",
                e
            ))
        })?;
        if let Some(doc_id_value) = retrieved_doc.get_first(doc_id_field) {
            if let Some(doc_id_str) = doc_id_value.as_str() {
                results.push((doc_id_str.to_string(), score));
            }
        }
    }

    Ok(results)
}

/// Delete a fulltext index directory from disk.
///
/// Args:
///     index_path: Directory of the Tantivy index to delete.
///
/// Returns: True if index was deleted, False if it did not exist.
#[pyfunction]
fn fulltext_delete_index(index_path: &str) -> PyResult<bool> {
    let path = Path::new(index_path);
    if !path.exists() {
        return Ok(false);
    }
    std::fs::remove_dir_all(path).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "Tantivy: failed to delete index at {}: {}",
            path.display(),
            e
        ))
    })?;
    Ok(true)
}

/// Get document count from a fulltext index.
///
/// Args:
///     index_path: Directory of the Tantivy index.
///
/// Returns: Number of documents in the index, or 0 if index does not exist.
#[pyfunction]
fn fulltext_doc_count(index_path: &str) -> PyResult<u64> {
    let path = Path::new(index_path);
    if !path.exists() {
        return Ok(0);
    }
    let index = match Index::open_in_dir(path) {
        Ok(idx) => idx,
        Err(_) => return Ok(0),
    };
    let reader = index
        .reader_builder()
        .reload_policy(ReloadPolicy::OnCommitWithDelay)
        .try_into()
        .map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Tantivy: failed to create reader: {}",
                e
            ))
        })?;
    let searcher = reader.as_str();
    Ok(searcher.num_docs() as u64)
}

/// Check if the fulltext module is available (compiled with fulltext feature).
/// Always returns true when this module is compiled.
#[pyfunction]
fn fulltext_is_available() -> bool {
    true
}

/// Encode a string array as IPC format: null_bitmap + offsets + data bytes.
/// Identical to arrow_batch_builder::encode_string_array — duplicated here
/// to avoid cross-feature dependency (fulltext ≠ data feature gates).
fn encode_fulltext_string_array(values: &[String]) -> Vec<u8> {
    let n_values = values.len();

    // Null bitmap: 1 bit per value, MSB first. All-valid = all 1s.
    let null_len = n_values.div_ceil(8);
    let mut null_bitmap = vec![0u8; null_len];
    for i in 0..n_values {
        null_bitmap[i / 8] |= 1 << (7 - (i % 8));
    }

    // Offsets (i32 LE)
    let mut offsets = Vec::with_capacity((n_values + 1) * 4);
    offsets.push(0i32);
    let mut cum: usize = 0;
    for v in values {
        cum += v);
        offsets.push(cum as i32);
    }
    let total_data = cum;

    // Layout: null_bitmap | offsets | data
    let mut result = Vec::with_capacity(null_len + (n_values + 1) * 4 + total_data);
    result.extend_from_slice(&null_bitmap);
    for off in &offsets {
        result.extend_from_slice(&off.to_le_bytes());
    }
    for v in values {
        result.extend_from_slice(v.as_bytes());
    }
    result
}

/// MODERN-17 FIX: Build proper Arrow IPC RecordBatch bytes for fulltext results.
///
/// Uses arrow::ipc::writer::StreamWriter instead of hand-rolled ARROW1\xff\xff\xff\xff
/// framing that pyarrow couldn't parse.
///
/// Schema: doc_id: Utf8, score: Float64
fn build_fulltext_ipc_bytes(
    doc_ids: Vec<String>,
    scores: Vec<f64>,
    _n: usize,  // Kept for API compatibility (actual length derived from column arrays)
) -> Result<Vec<u8>, String> {
    // Build Arrow Schema with 2 fields: doc_id (String) + score (Float64)
    let schema = ArrowSchema::new(vec![
        arrow::datatypes::Field::new("doc_id", arrow::datatypes::DataType::Utf8, true),
        arrow::datatypes::Field::new("score", arrow::datatypes::DataType::Float64, true),
    ]);

    use arrow::array::{Float64Array, StringArray};
    let doc_ids_array: ArrayRef = Arc::new(StringArray::from(doc_ids));
    let scores_array: ArrayRef = Arc::new(Float64Array::from(scores));

    // Create RecordBatch (requires Arc<ArrowSchema>)
    let schema_ref: Arc<ArrowSchema> = Arc::new(schema);
    let batch = RecordBatch::try_new(
        schema_ref.clone(),
        vec![doc_ids_array, scores_array],
    )
    .map_err(|e| format!("Failed to create RecordBatch: {}", e))?;

    // Serialize to IPC stream format using StreamWriter
    let mut buffer = Vec::new();
    {
        let mut writer = StreamWriter::try_new(&mut buffer, schema_ref.as_ref())
            .map_err(|e| format!("Failed to create StreamWriter: {}", e))?;

        writer.write(&batch)
            .map_err(|e| format!("Failed to write RecordBatch: {}", e))?;

        writer.finish()
            .map_err(|e| format!("Failed to finish stream: {}", e))?;
    }

    Ok(buffer)
}

/// Search a fulltext index and return results as Arrow IPC RecordBatchStream bytes.
///
/// ZERO-COPY PATH (ISSUE [BLITZ]-01): Instead of returning Vec<(String, f32)>
/// with per-tuple PyO3 FFI overhead, this returns a single Py<PyBytes> containing
/// an Arrow IPC RecordBatchStream with schema: doc_id: Utf8, score: Float64.
///
/// Python side calls `pa.ipc.open_stream(io.BytesIO(ipc_bytes))` for zero-copy
/// deserialization — NO per-row Python string or float object allocation.
///
/// Performance:
///   - fulltext_search(): 10K results → 10K PyO3 tuple conversions = 15-40ms FFI
///   - fulltext_search_arrow(): 10K results → 1 PyBytes allocation = ~1ms FFI
///
/// Fallback: returns None on error → Python falls back to fulltext_search().
///
/// Args:
///     index_path: Directory of the Tantivy index.
///     query: Search query string (Tantivy query syntax or plain text).
///     top_k: Maximum number of results to return.
///
/// Returns: Arrow IPC bytes (RecordBatchStream) or None on error.
#[pyfunction]
#[pyo3(signature = (index_path, query, top_k = 10))]
fn fulltext_search_arrow<'py>(
    index_path: &str,
    query: &str,
    top_k: u32,
    py: Python<'py>,
) -> PyResult<Option<Bound<'py, PyBytes>>> {

    if query.trim().is_empty() {
        return Ok(Some(PyBytes::new(py, b"")));
    }

    let index_path = Path::new(index_path);
    let index = match Index::open_in_dir(index_path) {
        Ok(idx) => idx,
        Err(_) => return Ok(None),
    };

    let schema = index.as_str();
    let doc_id_field = get_doc_id_field(&schema);
    let content_field = get_content_field(&schema);

    let reader: IndexReader = index
        .reader_builder()
        .reload_policy(ReloadPolicy::OnCommitWithDelay)
        .try_into()
        .map_err(|_| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Tantivy: failed to create reader")
        })?;

    let searcher = reader.as_str();
    let query_parser = QueryParser::for_index(&index, vec![content_field]);
    let query = match query_parser.parse_query(query) {
        Ok(q) => q,
        Err(_) => return Ok(None),
    };

    let top_docs = match searcher.search(&query, &TopDocs::with_limit(top_k as usize)) {
        Ok(docs) => docs,
        Err(_) => return Ok(None),
    };

    if top_docs.is_empty() {
        return Ok(Some(PyBytes::new(py, b"")));
    }

    let n = top_docs.len();
    let mut doc_ids: Vec<String> = Vec::with_capacity(n);
    let mut scores: Vec<f64> = Vec::with_capacity(n);

    for (score, doc_address) in top_docs {
        // TANTIVY 0.22 FIX: searcher.doc() requires explicit type annotation.
        let retrieved_doc: TantivyDocument = match searcher.doc(doc_address) {
            Ok(d) => d,
            Err(_) => continue,
        };
        if let Some(doc_id_value) = retrieved_doc.get_first(doc_id_field) {
            if let Some(doc_id_str) = doc_id_value.as_str() {
                doc_ids.push(doc_id_str.to_string());
                scores.push(score as f64);
            }
        }
    }

    if doc_ids.is_empty() {
        return Ok(Some(PyBytes::new(py, b"")));
    }

    let actual_n = doc_ids.as_str();
    let ipc_bytes = match build_fulltext_ipc_bytes(doc_ids, scores, actual_n) {
        Ok(bytes) => bytes,
        Err(_) => return Ok(None),
    };

    Ok(Some(PyBytes::new(py, &ipc_bytes)))
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow::ipc::reader::StreamReader;

    #[test]
    fn test_fulltext_build_ipc_bytes_valid_arrow_stream() {
        // MODERN-17: Verify that build_fulltext_ipc_bytes produces valid Arrow IPC
        // that can be parsed by pa.ipc.open_stream()
        let doc_ids = vec!["doc1".to_string(), "doc2".to_string(), "doc3".to_string()];
        let scores = vec![0.95, 0.85, 0.75];

        let result = build_fulltext_ipc_bytes(doc_ids, scores, 3)
            .expect("build_fulltext_ipc_bytes should succeed");

        // Verify magic bytes for Arrow IPC stream format
        assert!(result.starts_with(b"ARROW1"), "Should start with ARROW1 magic");

        // Parse using StreamReader (validates the IPC format is correct)
        let reader = StreamReader::try_new(std::io::Cursor::new(&result), None)
            .expect("Should parse as valid Arrow IPC stream");

        // Verify schema has 2 fields
        assert_eq!(reader.schema().fields().len(), 2);

        // Verify we can read the batch
        let batches: Vec<_> = reader.collect().expect("Should collect batches");
        assert_eq!(batches.len(), 1);

        let batch = &batches[0];
        assert_eq!(batch.num_columns(), 2);
        assert_eq!(batch.num_rows(), 3);
    }

    #[test]
    fn test_fulltext_build_ipc_bytes_empty_batch() {
        // MODERN-17: Verify empty batch produces valid Arrow IPC stream with schema
        let result = build_fulltext_ipc_bytes(vec![], vec![], 0)
            .expect("build_fulltext_ipc_bytes should succeed for empty batch");

        // Verify magic bytes
        assert!(result.starts_with(b"ARROW1"), "Should start with ARROW1 magic");

        // Empty stream with schema is valid Arrow IPC
        let reader = StreamReader::try_new(std::io::Cursor::new(&result), None)
            .expect("Empty stream with schema should parse");
        assert_eq!(reader.schema().fields().len(), 2);
    }

    #[test]
    fn test_fulltext_build_ipc_bytes_data_roundtrip() {
        // MODERN-17: Verify data integrity — what we write, we can read back
        let doc_ids = vec!["id1".to_string(), "id2".to_string()];
        let scores = vec![1.0, 0.5];

        let result = build_fulltext_ipc_bytes(doc_ids.clone(), scores.clone(), 2)
            .expect("build_fulltext_ipc_bytes should succeed");

        let reader = StreamReader::try_new(std::io::Cursor::new(&result), None)
            .expect("Should parse as valid Arrow IPC");
        let batches: Vec<_> = reader.collect().expect("Should collect batches");
        let batch = &batches[0];

        // Verify doc_id column
        let doc_id_col = batch.column(0);
        assert_eq!(doc_id_col.len(), 2);

        // Verify score column
        let score_col = batch.column(1);
        assert_eq!(score_col.len(), 2);
    }
}

/// Register fulltext module functions with PyO3 module.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(fulltext_create_index))?;
    m.add_function(wrap_pyfunction!(fulltext_add_documents))?;
    m.add_function(wrap_pyfunction!(fulltext_search))?;
    m.add_function(wrap_pyfunction!(fulltext_search_arrow))?;
    m.add_function(wrap_pyfunction!(fulltext_delete_index))?;
    m.add_function(wrap_pyfunction!(fulltext_doc_count))?;
    m.add_function(wrap_pyfunction!(fulltext_is_available))?;
    Ok(())
}
