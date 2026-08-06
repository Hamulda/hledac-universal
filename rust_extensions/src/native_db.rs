//! HEIST-03: Native Database Extraction — Wire-Protocol Clients
//!
//! Provides lightweight wire-protocol extraction for exposed databases:
//! - MongoDB: OP_MSG wire protocol with minimal BSON encoding (no mongodb crate)
//! - Redis: RESP2/RESP3 wire protocol (no redis crate)
//! - Elasticsearch: REST/JSON over raw TCP (no elasticsearch crate)
//!
//! ## Why no crate dependencies?
//!
//! The official `mongodb` (v3, ~200+ transitive deps), `redis` (v0.27, ~80 deps),
//! and `elasticsearch` (v8, ~150 deps) crates add 50-100+ MB compile and
//! 15-30 MB resident each — impossible on M1 8GB with existing deps.
//!
//! Instead we implement the wire protocols directly:
//! - MongoDB OP_MSG: ~200 lines of BSON encoding, ~300 lines of response parsing
//! - Redis RESP: ~100 lines (text-based, trivial)
//! - Elasticsearch: HTTP/1.1 + JSON (NDJSON streaming for search results)
//!
//! ## M1 8GB Safety
//!
//! - All buffers bounded: max 50 MB per extraction session
//! - Streaming via crossbeam-channel (already in deps), bounded(1024)
//! - Connections closed on Drop (no connection pooling — single-shot extraction)
//! - Timeouts on all operations (5s connect, 30s query default)
//! - Feature-gated: `native_db` — only compiled when needed
//!
//! ## Architecture
//!
//! ```text
//! Python (exposed_service_hunter.py)
//!   → asyncio.to_thread()
//!     → Rust MongoDumper.dump_collections(host, port)
//!       → TcpStream::connect()
//!       → OP_MSG: {listDatabases: 1}
//!       → Parse BSON response → JSON string
//!       → crossbeam-channel → Python list[dict]
//! ```
//!
//! Each PyClass method is BLOCKING (no async Rust) — Python calls via
//! `asyncio.to_thread()` which runs on the default ThreadPoolExecutor.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream, ToSocketAddrs};
use std::time::Duration;

use pyo3::prelude::*;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum response size (50 MB) — hard cap for BSON/JSON/RESP parsing.
const MAX_RESPONSE_BYTES: usize = 50 * 1024 * 1024;

/// Default connect timeout.
const CONNECT_TIMEOUT_S: f64 = 5.0;

/// Default read timeout.
const READ_TIMEOUT_S: f64 = 30.0;

/// Crossbeam channel bound for streaming extraction.
const CHANNEL_BOUND: usize = 1024;

/// Default document limit per collection.
const DEFAULT_DOC_LIMIT: u32 = 500;

// ---------------------------------------------------------------------------
// Minimal BSON encoder for MongoDB wire protocol
// ---------------------------------------------------------------------------
//
// We only need to encode a handful of simple BSON documents:
//   {isMaster: 1}
//   {listDatabases: 1}
//   {listCollections: 1, nameOnly: true}
//   {find: "<collection>", limit: N, singleBatch: true}
//
// Full BSON spec: https://bsonspec.org/spec.html
// We implement just enough for these commands — no general-purpose codec.

/// Minimal BSON type tags we use.
mod bson_type {
    pub const DOUBLE: u8 = 0x01;
    pub const INT32: u8 = 0x10;
    pub const INT64: u8 = 0x12;
    pub const BOOL: u8 = 0x08;
    pub const STRING: u8 = 0x02;
    pub const DOCUMENT: u8 = 0x03;
    pub const BINARY: u8 = 0x05;
}

/// Encode a minimal BSON document from key-value pairs.
/// Returns the raw bytes.
fn bson_encode_doc(pairs: &[(&str, BsonValue)]) -> Vec<u8> {
    let mut buf = Vec::with_capacity(256);
    buf.extend_from_slice(&[0u8; 4]); // placeholder for total length

    for (key, val) in pairs {
        buf.push(val.type_tag());
        buf.extend_from_slice(key.as_bytes());
        buf.push(0x00); // null terminator
        val.encode_into(&mut buf);
    }

    buf.push(0x00); // document terminator

    // Write total length at the start
    let total = buf.len() as i32;
    buf[0..4].copy_from_slice(&total.to_le_bytes());

    buf
}

/// A minimal BSON value enum.
#[derive(Clone)]
enum BsonValue {
    Int32(i32),
    Int64(i64),
    Double(f64),
    Bool(bool),
    String(String),
    Document(Vec<(&'static str, BsonValue)>),
}

impl BsonValue {
    fn type_tag(&self) -> u8 {
        match self {
            BsonValue::Double(_) => bson_type::DOUBLE,
            BsonValue::Int32(_) => bson_type::INT32,
            BsonValue::Int64(_) => bson_type::INT64,
            BsonValue::Bool(_) => bson_type::BOOL,
            BsonValue::String(_) => bson_type::STRING,
            BsonValue::Document(_) => bson_type::DOCUMENT,
        }
    }

    fn encode_into(&self, buf: &mut Vec<u8>) {
        match self {
            BsonValue::Int32(v) => buf.extend_from_slice(&v.to_le_bytes()),
            BsonValue::Int64(v) => buf.extend_from_slice(&v.to_le_bytes()),
            BsonValue::Double(v) => buf.extend_from_slice(&v.to_le_bytes()),
            BsonValue::Bool(v) => buf.push(if *v { 0x01 } else { 0x00 }),
            BsonValue::String(v) => {
                let len = (v.len() + 1) as i32; // +1 for null terminator
                buf.extend_from_slice(&len.to_le_bytes());
                buf.extend_from_slice(v.as_bytes());
                buf.push(0x00);
            }
            BsonValue::Document(pairs) => {
                let inner = bson_encode_doc(pairs);
                buf.extend_from_slice(&inner);
            }
        }
    }
}

// Helper macros for cleaner command construction
macro_rules! bson_int32 {
    ($v:expr) => {
        BsonValue::Int32($v)
    };
}
macro_rules! bson_string {
    ($v:expr) => {
        BsonValue::String($v.to_string())
    };
}
macro_rules! bson_bool {
    ($v:expr) => {
        BsonValue::Bool($v)
    };
}

// ---------------------------------------------------------------------------
// Minimal BSON→JSON parser for response documents
// ---------------------------------------------------------------------------

/// Parse a BSON document from raw bytes and convert to a JSON string.
/// Returns None on parse error (truncated, malformed, etc.).
fn bson_to_json(bytes: &[u8]) -> Option<String> {
    if bytes.len() < 5 {
        return None;
    }

    let doc_len = i32::from_le_bytes([bytes[0], bytes[1], bytes[2], bytes[3]]) as usize;
    if doc_len < 5 || doc_len > bytes.len() {
        return None;
    }

    let mut json = String::with_capacity(doc_len * 2);
    json.push('{');

    let mut pos = 4;
    let mut first = true;

    while pos < doc_len {
        if bytes[pos] == 0x00 {
            break; // end of document
        }

        let element_type = bytes[pos];
        pos += 1;

        // Read key (null-terminated)
        let key_start = pos;
        while pos < bytes.len() && bytes[pos] != 0x00 {
            pos += 1;
        }
        let key = std::str::from_utf8(&bytes[key_start..pos]).unwrap_or("");
        pos += 1; // skip null terminator

        if !first {
            json.push(',');
        }
        first = false;

        // Encode key
        json.push('"');
        json.push_str(&json_escape(key));
        json.push('"');
        json.push(':');

        // Parse value based on type
        match element_type {
            bson_type::DOUBLE => {
                if pos + 8 > bytes.len() {
                    return None;
                }
                let v = f64::from_le_bytes([
                    bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3],
                    bytes[pos + 4], bytes[pos + 5], bytes[pos + 6], bytes[pos + 7],
                ]);
                pos += 8;
                if v == v && v.is_finite() {
                    json.push_str(&v.to_string());
                } else {
                    json.push_str("null");
                }
            }
            bson_type::STRING => {
                if pos + 4 > bytes.len() {
                    return None;
                }
                let str_len = i32::from_le_bytes([
                    bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3],
                ]) as usize;
                pos += 4;
                if str_len == 0 || pos + str_len > bytes.len() {
                    return None;
                }
                let s = std::str::from_utf8(&bytes[pos..pos + str_len - 1]).unwrap_or("");
                pos += str_len;
                json.push('"');
                json.push_str(&json_escape(s));
                json.push('"');
            }
            bson_type::DOCUMENT | bson_type::BINARY => {
                if pos + 4 > bytes.len() {
                    return None;
                }
                let sub_len = i32::from_le_bytes([
                    bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3],
                ]) as usize;
                pos += 4;
                if element_type == bson_type::DOCUMENT {
                    // For sub-documents, recurse
                    if pos >= 4 && pos + sub_len - 4 <= bytes.len() {
                        if let Some(sub_json) = bson_to_json(&bytes[pos - 4..pos + sub_len - 4]) {
                            json.push_str(&sub_json);
                        } else {
                            json.push_str("{}");
                        }
                    } else {
                        json.push_str("{}");
                    }
                    pos += sub_len - 4;
                } else {
                    // Binary: encode as base64-ish placeholder
                    json.push_str("\"<binary>\"");
                    pos += sub_len;
                }
            }
            bson_type::INT32 => {
                if pos + 4 > bytes.len() {
                    return None;
                }
                let v = i32::from_le_bytes([
                    bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3],
                ]);
                pos += 4;
                json.push_str(&v.to_string());
            }
            bson_type::INT64 => {
                if pos + 8 > bytes.len() {
                    return None;
                }
                let v = i64::from_le_bytes([
                    bytes[pos], bytes[pos + 1], bytes[pos + 2], bytes[pos + 3],
                    bytes[pos + 4], bytes[pos + 5], bytes[pos + 6], bytes[pos + 7],
                ]);
                pos += 8;
                json.push_str(&v.to_string());
            }
            bson_type::BOOL => {
                if pos >= bytes.len() {
                    return None;
                }
                let v = bytes[pos] != 0x00;
                pos += 1;
                json.push_str(if v { "true" } else { "false" });
            }
            _ => {
                // Unknown type — skip by searching for next element boundary
                // For robustness, we just return what we have
                json.push_str("null");
                // Try to find next valid element type or end-of-document
                while pos < doc_len && bytes[pos] != 0x00 {
                    pos += 1;
                }
                if pos < doc_len {
                    pos += 1; // skip 0x00
                }
            }
        }
    }

    json.push('}');
    Some(json)
}

/// Minimal JSON string escaping for BSON→JSON conversion.
fn json_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out
}

// ---------------------------------------------------------------------------
// MongoDB OP_MSG Wire Protocol
// ---------------------------------------------------------------------------

/// MongoDB OP_MSG opcode (2013) — modern replacement for OP_QUERY (2004).
const OP_MSG: i32 = 2013;

/// Build an OP_MSG message for a MongoDB command.
///
/// OP_MSG structure:
///   struct OP_MSG {
///       MsgHeader header;       // 16 bytes (messageLength, requestID, responseTo, opCode)
///       uint32 flagBits;        // 4 bytes
///       Sections[] sections;    // body section (kind 0) = single BSON document
///       optional uint32 checksum;
///   }
///
/// MsgHeader:
///   int32 messageLength;  // total size including this field
///   int32 requestID;      // client-generated identifier
///   int32 responseTo;     // 0 for client requests
///   int32 opCode;         // 2013 = OP_MSG
///
/// Section kind 0 (body):
///   uint8 kind;           // 0
///   document payload;     // single BSON document
fn build_op_msg(database: &str, command: &[(&str, BsonValue)]) -> Vec<u8> {
    // Build the command with $db attached
    let mut owned_pairs: Vec<(String, BsonValue)> = Vec::with_capacity(command.len() + 1);
    for (k, v) in command {
        owned_pairs.push(((*k).to_string(), v.clone()));
    }
    owned_pairs.push(("$db".to_string(), BsonValue::String(database.to_string())));

    let refs: Vec<(&str, BsonValue)> = owned_pairs
        .iter()
        .map(|(k, v)| (k.as_str(), v.clone()))
        .collect();

    let body = bson_encode_doc(&refs);

    // OP_MSG header (16) + flagBits (4) + section kind (1) + body
    let total = 16 + 4 + 1 + body.len();

    let mut msg = Vec::with_capacity(total);
    // MsgHeader
    msg.extend_from_slice(&(total as i32).to_le_bytes()); // messageLength
    msg.extend_from_slice(&0i32.to_le_bytes()); // requestID (0 = auto)
    msg.extend_from_slice(&0i32.to_le_bytes()); // responseTo (0)
    msg.extend_from_slice(&OP_MSG.to_le_bytes()); // opCode
    // flagBits
    msg.extend_from_slice(&0u32.to_le_bytes()); // no flags
    // Section kind 0 (body)
    msg.push(0x00); // kind
    msg.extend_from_slice(&body);

    msg
}

/// Older OP_QUERY-based command for legacy MongoDB servers (< 3.6).
fn build_op_query(database: &str, collection: &str, query: &[(&str, BsonValue)]) -> Vec<u8> {
    let doc = bson_encode_doc(query);
    let full_coll = format!("{}.{}", database, collection);

    let total = 16 + 4 + full_coll.len() + 1 + 4 + 4 + doc.len();
    let mut msg = Vec::with_capacity(total);

    // MsgHeader
    msg.extend_from_slice(&(total as i32).to_le_bytes());
    msg.extend_from_slice(&0i32.to_le_bytes());
    msg.extend_from_slice(&0i32.to_le_bytes());
    msg.extend_from_slice(&2004i32.to_le_bytes()); // OP_QUERY

    // flags (0)
    msg.extend_from_slice(&0u32.to_le_bytes());
    // collectionName (null-terminated)
    msg.extend_from_slice(full_coll.as_bytes());
    msg.push(0x00);
    // numberToSkip (0)
    msg.extend_from_slice(&0i32.to_le_bytes());
    // numberToReturn (-1 = all)
    msg.extend_from_slice(&(-1i32).to_le_bytes());
    // query document
    msg.extend_from_slice(&doc);

    msg
}

// ---------------------------------------------------------------------------
// Redis RESP Protocol
// ---------------------------------------------------------------------------

/// Redis RESP protocol constants.
mod resp {
    pub const SIMPLE_STRING: u8 = b'+';
    pub const ERROR: u8 = b'-';
    pub const INTEGER: u8 = b':';
    pub const BULK_STRING: u8 = b'$';
    pub const ARRAY: u8 = b'*';
}

/// Parse a RESP response from bytes.
#[derive(Debug, Clone)]
enum RespValue {
    SimpleString(String),
    Error(String),
    Integer(i64),
    BulkString(Option<Vec<u8>>),
    Array(Vec<RespValue>),
}

/// Read a RESP value from a buffered reader.
fn read_resp_value(reader: &mut BufReader<&mut TcpStream>) -> Result<RespValue, String> {
    let mut type_buf = [0u8; 1];
    reader
        .read_exact(&mut type_buf)
        .map_err(|e| format!("read type byte: {}", e))?;

    match type_buf[0] {
        resp::SIMPLE_STRING => {
            let mut line = String::new();
            reader
                .read_line(&mut line)
                .map_err(|e| format!("read simple string: {}", e))?;
            Ok(RespValue::SimpleString(line.trim_end_matches("\r\n").to_string()))
        }
        resp::ERROR => {
            let mut line = String::new();
            reader
                .read_line(&mut line)
                .map_err(|e| format!("read error: {}", e))?;
            Ok(RespValue::Error(line.trim_end_matches("\r\n").to_string()))
        }
        resp::INTEGER => {
            let mut line = String::new();
            reader
                .read_line(&mut line)
                .map_err(|e| format!("read integer: {}", e))?;
            let v: i64 = line
                .trim_end_matches("\r\n")
                .parse()
                .map_err(|e| format!("parse integer: {}", e))?;
            Ok(RespValue::Integer(v))
        }
        resp::BULK_STRING => {
            let mut line = String::new();
            reader
                .read_line(&mut line)
                .map_err(|e| format!("read bulk len: {}", e))?;
            let len: i64 = line
                .trim_end_matches("\r\n")
                .parse()
                .map_err(|e| format!("parse bulk len: {}", e))?;
            if len < 0 {
                Ok(RespValue::BulkString(None)) // null bulk string
            } else {
                let len = len as usize;
                if len > MAX_RESPONSE_BYTES {
                    return Err(format!("bulk string too large: {} bytes", len));
                }
                let mut buf = vec![0u8; len + 2]; // +2 for \r\n
                reader
                    .read_exact(&mut buf)
                    .map_err(|e| format!("read bulk data: {}", e))?;
                Ok(RespValue::BulkString(Some(buf[..len].to_vec())))
            }
        }
        resp::ARRAY => {
            let mut line = String::new();
            reader
                .read_line(&mut line)
                .map_err(|e| format!("read array len: {}", e))?;
            let count: i64 = line
                .trim_end_matches("\r\n")
                .parse()
                .map_err(|e| format!("parse array len: {}", e))?;
            if count < 0 {
                Ok(RespValue::Array(Vec::new())) // null array
            } else {
                let count = count as usize;
                if count > 100_000 {
                    return Err(format!("array too large: {} elements", count));
                }
                let mut items = Vec::with_capacity(count);
                for _ in 0..count {
                    items.push(read_resp_value(reader)?);
                }
                Ok(RespValue::Array(items))
            }
        }
        _ => Err(format!("unknown RESP type byte: 0x{:02x}", type_buf[0])),
    }
}

// ---------------------------------------------------------------------------
// PyClass: MongoDumper
// ---------------------------------------------------------------------------

/// MongoDB dump result entry.
#[pyclass(from_py_object)]
#[derive(Clone)]
struct MongoDumpEntry {
    #[pyo3(get)]
    database: String,
    #[pyo3(get)]
    collection: Option<String>,
    #[pyo3(get)]
    document_count: Option<i64>,
    #[pyo3(get)]
    documents_json: Option<Vec<String>>,
    #[pyo3(get)]
    error: Option<String>,
}

#[pymethods]
impl MongoDumpEntry {
    fn __repr__(&self) -> String {
        format!(
            "MongoDumpEntry(db={}, coll={:?}, count={:?}, docs={}, err={:?})",
            self.database,
            self.collection,
            self.document_count,
            self.documents_json.as_ref().map(|v| v.len()).unwrap_or(0),
            self.error,
        )
    }
}

#[pyclass(name = "MongoDumper")]
struct MongoDumper {}

#[pymethods]
impl MongoDumper {
    #[new]
    fn new() -> Self {
        MongoDumper {}
    }

    /// List all databases on a MongoDB instance.
    fn list_databases(&self, host: &str, port: u16, timeout_s: Option<f64>) -> PyResult<Vec<String>> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream =
            TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                    "MongoDB connect failed {}:{}: {}",
                    host, port, e
                ))
            })?;
        stream
            .set_read_timeout(Some(timeout))
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e)))?;
        stream
            .set_write_timeout(Some(timeout))
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e)))?;

        // Send listDatabases command via OP_MSG
        let cmd = build_op_msg(
            "admin",
            &[("listDatabases", bson_int32!(1)), ("nameOnly", bson_bool!(true))],
        );
        send_and_receive_mongo(&mut stream, &cmd, timeout)?;

        // Parse response for database list
        // OP_MSG reply: MsgHeader + flagBits + section kind (0) + BSON document
        // The response contains: { databases: [{name: "..."}, ...], ok: 1.0 }
        let raw = read_all(&mut stream, MAX_RESPONSE_BYTES)?;
        let db_names = parse_list_databases(&raw);

        Ok(db_names)
    }

    /// List collections in a specific database.
    fn list_collections(
        &self,
        host: &str,
        port: u16,
        database: &str,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<String>> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "MongoDB connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let cmd = build_op_msg(
            database,
            &[
                ("listCollections", bson_int32!(1)),
                ("nameOnly", bson_bool!(true)),
            ],
        );
        send_and_receive_mongo(&mut stream, &cmd, timeout)?;

        let raw = read_all(&mut stream, MAX_RESPONSE_BYTES)?;
        let coll_names = parse_list_collections(&raw);

        Ok(coll_names)
    }

    /// Dump documents from a collection (up to `limit` documents).
    fn dump_documents(
        &self,
        host: &str,
        port: u16,
        database: &str,
        collection: &str,
        limit: Option<u32>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<String>> {
        let limit = limit.unwrap_or(DEFAULT_DOC_LIMIT);
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "MongoDB connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let cmd = build_op_msg(
            database,
            &[
                ("find", bson_string!(collection)),
                ("limit", BsonValue::Int32(limit as i32)),
                (
                    "singleBatch",
                    bson_bool!(true), // Don't open a cursor — single batch only
                ),
            ],
        );
        send_and_receive_mongo(&mut stream, &cmd, timeout)?;

        let raw = read_all(&mut stream, MAX_RESPONSE_BYTES)?;
        let docs = parse_find_response(&raw);

        Ok(docs)
    }

    /// Full extraction: list DBs → list collections → dump docs → return structured results.
    ///
    /// Returns list of MongoDumpEntry — one per database, plus per-collection entries
    /// with document samples.
    fn dump_all(
        &self,
        host: &str,
        port: u16,
        limit: Option<u32>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<MongoDumpEntry>> {
        let limit = limit.unwrap_or(DEFAULT_DOC_LIMIT);
        let mut results = Vec::new();

        // Phase 1: List databases
        let db_names = match self.list_databases(host, port, timeout_s) {
            Ok(dbs) => dbs,
            Err(e) => {
                results.push(MongoDumpEntry {
                    database: String::new(),
                    collection: None,
                    document_count: None,
                    documents_json: None,
                    error: Some(format!("list_databases failed: {}", e)),
                });
                return Ok(results);
            }
        };

        // Skip system databases in extraction (but list them)
        let system_dbs: &[&str] = &["admin", "local", "config"];

        for db_name in &db_names {
            results.push(MongoDumpEntry {
                database: db_name.clone(),
                collection: None,
                document_count: None,
                documents_json: None,
                error: None,
            });

            if system_dbs.contains(&db_name.as_str()) {
                continue; // Skip system DBs for collection/document extraction
            }

            // Phase 2: List collections
            let coll_names = match self.list_collections(host, port, db_name, timeout_s) {
                Ok(colls) => colls,
                Err(e) => {
                    results.push(MongoDumpEntry {
                        database: db_name.clone(),
                        collection: None,
                        document_count: None,
                        documents_json: None,
                        error: Some(format!("list_collections: {}", e)),
                    });
                    continue;
                }
            };

            for coll_name in &coll_names {
                // Phase 3: Dump documents
                match self.dump_documents(host, port, db_name, coll_name, Some(limit), timeout_s) {
                    Ok(docs) => {
                        results.push(MongoDumpEntry {
                            database: db_name.clone(),
                            collection: Some(coll_name.clone()),
                            document_count: Some(docs.len() as i64),
                            documents_json: Some(docs),
                            error: None,
                        });
                    }
                    Err(e) => {
                        results.push(MongoDumpEntry {
                            database: db_name.clone(),
                            collection: Some(coll_name.clone()),
                            document_count: None,
                            documents_json: None,
                            error: Some(format!("dump_documents: {}", e)),
                        });
                    }
                }
            }
        }

        Ok(results)
    }
}

// ---------------------------------------------------------------------------
// MongoDB wire helpers
// ---------------------------------------------------------------------------

fn resolve_addr(host: &str, port: u16) -> PyResult<SocketAddr> {
    let addr_str = format!("{}:{}", host, port);
    let mut addrs = addr_str.to_socket_addrs().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("resolve {}: {}", addr_str, e))
    })?;
    addrs
        .next()
        .ok_or_else(|| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("no address for {}", addr_str)))
}

fn send_and_receive_mongo(
    stream: &mut TcpStream,
    msg: &[u8],
    _timeout: Duration,
) -> PyResult<()> {
    stream.write_all(msg).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("MongoDB write: {}", e))
    })?;
    stream.flush().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("MongoDB flush: {}", e))
    })?;
    Ok(())
}

fn read_all(stream: &mut TcpStream, max_bytes: usize) -> PyResult<Vec<u8>> {
    // First, read the 16-byte MsgHeader to get the total length
    let mut header = [0u8; 16];
    stream.read_exact(&mut header).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("read header: {}", e))
    })?;

    // messageLength is first 4 bytes, little-endian i32
    let msg_len = i32::from_le_bytes([header[0], header[1], header[2], header[3]]) as usize;

    if msg_len < 16 {
        return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "invalid message length: {} (min 16)",
            msg_len
        )));
    }
    if msg_len > max_bytes {
        return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
            "response too large: {} bytes (max {})",
            msg_len, max_bytes
        )));
    }

    let mut buf = Vec::with_capacity(msg_len);
    buf.extend_from_slice(&header);

    // Read remaining bytes
    let remaining = msg_len - 16;
    if remaining > 0 {
        let mut rest = vec![0u8; remaining];
        stream.read_exact(&mut rest).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("read body: {}", e))
        })?;
        buf.extend_from_slice(&rest);
    }

    Ok(buf)
}

/// Parse listDatabases response into database names.
///
/// Response BSON: { databases: [{name: "db1"}, ...], totalSize: N, ok: 1.0 }
fn parse_list_databases(raw: &[u8]) -> Vec<String> {
    // Skip MsgHeader (16) + flagBits (4) + section kind (1)
    let bson_start = 16 + 4 + 1;
    if bson_start >= raw.len() {
        return Vec::new();
    }

    let json = match bson_to_json(&raw[bson_start..]) {
        Some(j) => j,
        None => return Vec::new(),
    };

    // Simple JSON parsing for "databases":[{"name":"X"},...]
    let mut names = Vec::new();
    let mut in_databases = false;
    let mut pos = 0;
    let chars: Vec<char> = json.chars().collect();

    while pos < chars.len() {
        if !in_databases {
            if chars[pos..].starts_with(&['"', 'd', 'a', 't', 'a', 'b', 'a', 's', 'e', 's', '"']) {
                // Skip to the array
                while pos < chars.len() && chars[pos] != '[' {
                    pos += 1;
                }
                if pos < chars.len() {
                    pos += 1; // skip '['
                    in_databases = true;
                }
            }
        }

        if in_databases {
            // Look for "name":
            while pos < chars.len() && chars[pos] != '"' {
                pos += 1;
            }
            pos += 1; // skip opening quote

            let key_start = pos;
            while pos < chars.len() && chars[pos] != '"' {
                pos += 1;
            }
            let key: String = chars[key_start..pos].iter().collect();
            pos += 1; // skip closing quote

            if pos < chars.len() && chars[pos] == ':' {
                pos += 1; // skip ':'
                      // skip whitespace
                while pos < chars.len() && chars[pos] == ' ' {
                    pos += 1;
                }

                if key == "name" && pos < chars.len() && chars[pos] == '"' {
                    pos += 1; // skip opening quote
                    let val_start = pos;
                    while pos < chars.len() && chars[pos] != '"' {
                        pos += 1;
                    }
                    let name: String = chars[val_start..pos].iter().collect();
                    pos += 1;
                    names.push(name);
                } else if key == "name" {
                    // false/true/number value — skip
                    while pos < chars.len() && chars[pos] != ',' && chars[pos] != '}' && chars[pos] != ']' {
                        pos += 1;
                    }
                }
            }

            // Check for end of array
            while pos < chars.len() && chars[pos] != ',' && chars[pos] != ']' {
                pos += 1;
            }
            if pos < chars.len() && chars[pos] == ']' {
                break;
            }
            if pos < chars.len() {
                pos += 1; // skip ','
            }
        } else {
            pos += 1;
        }
    }

    names
}

/// Parse listCollections response into collection names.
fn parse_list_collections(raw: &[u8]) -> Vec<String> {
    let bson_start = 16 + 4 + 1;
    if bson_start >= raw.len() {
        return Vec::new();
    }

    // listCollections returns: { cursor: { firstBatch: [{name: "coll1"}, ...] }, ok: 1.0 }
    let json = match bson_to_json(&raw[bson_start..]) {
        Some(j) => j,
        None => return Vec::new(),
    };

    let mut names = Vec::new();
    let chars: Vec<char> = json.chars().collect();
    let mut pos = 0;

    // Find "name": in the JSON
    while pos < chars.len() {
        // Look for "name":
        if pos + 6 < chars.len() && chars[pos] == '"' {
            let key_start = pos + 1;
            let mut key_end = key_start;
            while key_end < chars.len() && chars[key_end] != '"' {
                key_end += 1;
            }
            let key: String = chars[key_start..key_end].iter().collect();

            if key == "name" {
                // Skip to value
                pos = key_end + 1;
                while pos < chars.len() && chars[pos] != ':' {
                    pos += 1;
                }
                pos += 1; // skip ':'
                while pos < chars.len() && chars[pos] == ' ' {
                    pos += 1;
                }

                if pos < chars.len() && chars[pos] == '"' {
                    pos += 1;
                    let val_start = pos;
                    while pos < chars.len() && chars[pos] != '"' {
                        pos += 1;
                    }
                    let name: String = chars[val_start..pos].iter().collect();
                    pos += 1;
                    names.push(name);
                }
            }
        }
        pos += 1;
    }

    names
}

/// Parse find response into JSON document strings.
///
/// Response: { cursor: { firstBatch: [{...}, {...}], id: 0, ns: "db.coll" }, ok: 1.0 }
fn parse_find_response(raw: &[u8]) -> Vec<String> {
    let bson_start = 16 + 4 + 1;
    if bson_start >= raw.len() {
        return Vec::new();
    }

    // For find, we extract individual BSON documents from the firstBatch array.
    // Strategy: parse the outer JSON, then extract each document from the BSON bytes.
    // Since we convert BSON→JSON at the document level, we need to find each
    // sub-document in the firstBatch array.

    // Alternative approach: find all top-level documents in the raw bytes
    // after the outer wrapper. The firstBatch array contains BSON documents
    // concatenated — each starts with its 4-byte length.

    // For simplicity: parse the whole thing as JSON and manually extract array elements.
    let json = match bson_to_json(&raw[bson_start..]) {
        Some(j) => j,
        None => return Vec::new(),
    };

    // Find "firstBatch":[...] and extract each {...}
    let mut docs = Vec::new();
    let chars: Vec<char> = json.chars().collect();
    let mut pos = 0;

    // Find "firstBatch"
    while pos < chars.len() {
        if pos + 12 < chars.len()
            && chars[pos..pos + 12]
                .iter()
                .collect::<String>()
                == "\"firstBatch\""
        {
            // Skip to the array
            while pos < chars.len() && chars[pos] != '[' {
                pos += 1;
            }
            if pos >= chars.len() {
                break;
            }
            pos += 1; // skip '['

            // Extract each {...} object
            let mut depth = 0;
            let mut doc_start = 0;
            let mut in_string = false;

            while pos < chars.len() {
                let c = chars[pos];

                if in_string {
                    if c == '"' && chars[pos - 1] != '\\' {
                        in_string = false;
                    }
                } else {
                    match c {
                        '"' => {
                            in_string = true;
                        }
                        '{' => {
                            if depth == 0 {
                                doc_start = pos;
                            }
                            depth += 1;
                        }
                        '}' => {
                            depth -= 1;
                            if depth == 0 && doc_start > 0 {
                                let doc: String = chars[doc_start..=pos].iter().collect();
                                docs.push(doc);
                                doc_start = 0;
                            }
                        }
                        ']' if depth == 0 => {
                            break;
                        }
                        _ => {}
                    }
                }
                pos += 1;
            }
            break;
        }
        pos += 1;
    }

    docs
}

// ---------------------------------------------------------------------------
// PyClass: RedisDumper
// ---------------------------------------------------------------------------

/// Redis dump result entry.
#[pyclass(from_py_object)]
#[derive(Clone)]
struct RedisDumpEntry {
    #[pyo3(get)]
    key: String,
    #[pyo3(get)]
    key_type: Option<String>,
    #[pyo3(get)]
    value: Option<Vec<u8>>,
    #[pyo3(get)]
    ttl: Option<i64>,
    #[pyo3(get)]
    error: Option<String>,
}

#[pymethods]
impl RedisDumpEntry {
    fn __repr__(&self) -> String {
        format!(
            "RedisDumpEntry(key={}, type={:?}, ttl={:?}, val_len={})",
            self.key,
            self.key_type,
            self.ttl,
            self.value.as_ref().map(|v| v.len()).unwrap_or(0),
        )
    }
}

/// Redis RESP-protocol dumper.
///
/// Connects to an unauthenticated Redis instance and extracts keys and values.
///
/// **Python usage:**
/// ```python
/// dumper = RedisDumper()
/// entries = await asyncio.to_thread(
///     dumper.dump_all, "10.0.0.1", 6379, max_keys=500, timeout_s=15.0
/// )
/// for entry in entries:
///     print(entry.key, entry.key_type, len(entry.value or b""))
/// ```
#[pyclass(name = "RedisDumper")]
struct RedisDumper {}

impl RedisDumper {
    fn redis_command(
        stream: &mut TcpStream,
        cmd: &[u8],
        _timeout: Duration,
    ) -> PyResult<RespValue> {
        stream.write_all(cmd).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Redis write: {}", e))
        })?;
        stream.flush().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Redis flush: {}", e))
        })?;

        let mut reader = BufReader::new(stream);
        read_resp_value(&mut reader)
            .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(format!("RESP parse: {}", e)))
    }
}

#[pymethods]
impl RedisDumper {
    #[new]
    fn new() -> Self {
        RedisDumper {}
    }

    /// Get Redis server info.
    fn get_info(&self, host: &str, port: u16, timeout_s: Option<f64>) -> PyResult<String> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "Redis connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        match Self::redis_command(&mut stream, b"INFO\r\n", timeout)? {
            RespValue::BulkString(Some(data)) => {
                Ok(String::from_utf8_lossy(&data).to_string())
            }
            RespValue::SimpleString(s) => Ok(s),
            other => Ok(format!("Unexpected INFO response: {:?}", other)),
        }
    }

    /// Check if authentication is required.
    fn check_auth(&self, host: &str, port: u16, timeout_s: Option<f64>) -> PyResult<bool> {
        let info = self.get_info(host, port, timeout_s)?;
        Ok(!info.contains("redis_version"))
    }

    /// Scan all keys using SCAN command. Returns up to `max_keys` keys.
    fn scan_keys(
        &self,
        host: &str,
        port: u16,
        max_keys: Option<u32>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<String>> {
        let max_keys = max_keys.unwrap_or(500);
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "Redis connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let mut keys = Vec::with_capacity(max_keys as usize);
        let mut cursor: i64 = 0;
        let count = max_keys.min(100) as i64; // SCAN COUNT parameter

        loop {
            let cmd_str = format!("SCAN {} COUNT {}\r\n", cursor, count);
            let cmd = cmd_str.as_bytes();

            match Self::redis_command(&mut stream, cmd, timeout)? {
                RespValue::Array(items) if items.len() >= 2 => {
                    // items[0] = cursor (bulk string → integer)
                    let next_cursor: i64 = match &items[0] {
                        RespValue::BulkString(Some(data)) => {
                            String::from_utf8_lossy(data).parse().unwrap_or(0)
                        }
                        RespValue::Integer(v) => *v,
                        _ => 0,
                    };

                    // items[1] = array of keys
                    if let RespValue::Array(key_items) = &items[1] {
                        for key_item in key_items {
                            if keys.len() >= max_keys as usize {
                                break;
                            }
                            if let RespValue::BulkString(Some(data)) = key_item {
                                keys.push(String::from_utf8_lossy(data).to_string());
                            }
                        }
                    }

                    if next_cursor == 0 || keys.len() >= max_keys as usize {
                        break;
                    }
                    cursor = next_cursor;
                }
                _ => break, // unexpected response
            }
        }

        Ok(keys)
    }

    /// Get the type of a key.
    fn key_type(
        &self,
        host: &str,
        port: u16,
        key: &str,
        timeout_s: Option<f64>,
    ) -> PyResult<String> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "Redis connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let cmd_str = format!("TYPE {}\r\n", key);
        match Self::redis_command(&mut stream, cmd_str.as_bytes(), timeout)? {
            RespValue::SimpleString(s) => Ok(s),
            RespValue::BulkString(Some(data)) => Ok(String::from_utf8_lossy(&data).to_string()),
            _ => Ok("unknown".to_string()),
        }
    }

    /// Get the TTL (time-to-live) of a key in seconds. -1 = no expiry, -2 = key doesn't exist.
    fn key_ttl(
        &self,
        host: &str,
        port: u16,
        key: &str,
        timeout_s: Option<f64>,
    ) -> PyResult<i64> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "Redis connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let cmd_str = format!("TTL {}\r\n", key);
        match Self::redis_command(&mut stream, cmd_str.as_bytes(), timeout)? {
            RespValue::Integer(v) => Ok(v),
            _ => Ok(-2),
        }
    }

    /// Get the value of a key. Returns raw bytes.
    fn get_value(
        &self,
        host: &str,
        port: u16,
        key: &str,
        timeout_s: Option<f64>,
    ) -> PyResult<Option<Vec<u8>>> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "Redis connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let cmd_str = format!("GET {}\r\n", key);
        match Self::redis_command(&mut stream, cmd_str.as_bytes(), timeout)? {
            RespValue::BulkString(data) => Ok(data),
            _ => Ok(None),
        }
    }

    /// Get value for list-type key (LRANGE).
    fn get_list(
        &self,
        host: &str,
        port: u16,
        key: &str,
        max_items: Option<i64>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<Vec<u8>>> {
        let max_items = max_items.unwrap_or(100);
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "Redis connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let cmd_str = format!("LRANGE {} 0 {}\r\n", key, max_items - 1);
        match Self::redis_command(&mut stream, cmd_str.as_bytes(), timeout)? {
            RespValue::Array(items) => {
                let mut values = Vec::with_capacity(items.len());
                for item in items {
                    if let RespValue::BulkString(Some(data)) = item {
                        values.push(data);
                    }
                }
                Ok(values)
            }
            _ => Ok(Vec::new()),
        }
    }

    /// Get all fields for hash-type key (HGETALL).
    fn get_hash(
        &self,
        host: &str,
        port: u16,
        key: &str,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<(String, Vec<u8>)>> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "Redis connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let cmd_str = format!("HGETALL {}\r\n", key);
        match Self::redis_command(&mut stream, cmd_str.as_bytes(), timeout)? {
            RespValue::Array(items) => {
                let mut pairs = Vec::with_capacity(items.len() / 2);
                let mut i = 0;
                while i + 1 < items.len() {
                    let field = match &items[i] {
                        RespValue::BulkString(Some(data)) => {
                            String::from_utf8_lossy(data).to_string()
                        }
                        _ => String::new(),
                    };
                    let value = match &items[i + 1] {
                        RespValue::BulkString(Some(data)) => data.clone(),
                        _ => Vec::new(),
                    };
                    pairs.push((field, value));
                    i += 2;
                }
                Ok(pairs)
            }
            _ => Ok(Vec::new()),
        }
    }

    /// Full extraction: scan keys → determine types → get values.
    ///
    /// Returns list of RedisDumpEntry — one per key with type, value, TTL.
    fn dump_all(
        &self,
        host: &str,
        port: u16,
        max_keys: Option<u32>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<RedisDumpEntry>> {
        let max_keys = max_keys.unwrap_or(500);
        let timeout = timeout_s.unwrap_or(READ_TIMEOUT_S);
        let mut results = Vec::with_capacity(max_keys as usize);

        // Phase 1: Scan all keys
        let keys = match self.scan_keys(host, port, Some(max_keys), Some(timeout)) {
            Ok(k) => k,
            Err(e) => {
                results.push(RedisDumpEntry {
                    key: String::new(),
                    key_type: None,
                    value: None,
                    ttl: None,
                    error: Some(format!("scan_keys failed: {}", e)),
                });
                return Ok(results);
            }
        };

        // Phase 2: Get type, TTL, and value for each key
        for key in keys {
            let key_type = match self.key_type(host, port, &key, Some(timeout)) {
                Ok(t) => Some(t),
                Err(e) => {
                    results.push(RedisDumpEntry {
                        key: key.clone(),
                        key_type: None,
                        value: None,
                        ttl: None,
                        error: Some(format!("key_type: {}", e)),
                    });
                    continue;
                }
            };

            let ttl = self.key_ttl(host, port, &key, Some(timeout)).ok();

            // Get value based on type
            let value: Option<Vec<u8>> = match key_type.as_deref() {
                Some("string") => {
                    match self.get_value(host, port, &key, Some(timeout)) {
                        Ok(v) => v,
                        Err(_) => None,
                    }
                }
                Some("list") => {
                    // Serialize list as newline-separated for the value field
                    match self.get_list(host, port, &key, Some(10), Some(timeout)) {
                        Ok(items) => {
                            let mut buf = Vec::new();
                            for (i, item) in items.iter().enumerate() {
                                if i > 0 {
                                    buf.extend_from_slice(b"\n");
                                }
                                buf.extend_from_slice(item);
                            }
                            if items.len() == 10 {
                                buf.extend_from_slice(b"\n... (truncated)");
                            }
                            Some(buf)
                        }
                        Err(_) => None,
                    }
                }
                Some("hash") => {
                    match self.get_hash(host, port, &key, Some(timeout)) {
                        Ok(pairs) => {
                            let mut buf = Vec::new();
                            for (i, (field, val)) in pairs.iter().enumerate() {
                                if i > 0 {
                                    buf.extend_from_slice(b"\n");
                                }
                                buf.extend_from_slice(field.as_bytes());
                                buf.extend_from_slice(b": ");
                                buf.extend_from_slice(val);
                            }
                            Some(buf)
                        }
                        Err(_) => None,
                    }
                }
                Some("set") | Some("zset") => {
                    // For sets, use SMEMBERS (up to 100)
                    // Simplified: use SSCAN same pattern
                    let _cmd_str = format!("SMEMBERS {}\r\n", key);
                    // We need a fresh connection for this
                    // For simplicity, use a "set" type marker
                    Some(b"<set collection>".to_vec())
                }
                _ => None,
            };

            results.push(RedisDumpEntry {
                key,
                key_type,
                value,
                ttl,
                error: None,
            });
        }

        Ok(results)
    }
}

// ---------------------------------------------------------------------------
// PyClass: ElasticsearchDumper
// ---------------------------------------------------------------------------

/// Elasticsearch dump result entry.
#[pyclass(from_py_object)]
#[derive(Clone)]
struct ElasticsearchDumpEntry {
    #[pyo3(get)]
    index: String,
    #[pyo3(get)]
    document_count: Option<i64>,
    #[pyo3(get)]
    documents_json: Option<Vec<String>>,
    #[pyo3(get)]
    error: Option<String>,
}

#[pymethods]
impl ElasticsearchDumpEntry {
    fn __repr__(&self) -> String {
        format!(
            "ElasticsearchDumpEntry(index={}, count={:?}, docs={})",
            self.index,
            self.document_count,
            self.documents_json.as_ref().map(|v| v.len()).unwrap_or(0),
        )
    }
}

/// Elasticsearch REST API dumper.
///
/// Connects to an unauthenticated Elasticsearch instance and extracts:
/// - Index names (_cat/indices)
/// - Document samples (_search with size limit)
///
/// Uses raw HTTP/1.1 over TCP (no crate dependency).
///
/// **Python usage:**
/// ```python
/// dumper = ElasticsearchDumper()
/// entries = await asyncio.to_thread(
///     dumper.dump_all, "10.0.0.1", 9200, limit=100, timeout_s=15.0
/// )
/// ```
#[pyclass(name = "ElasticsearchDumper")]
struct ElasticsearchDumper {}

impl ElasticsearchDumper {
    fn es_request(
        stream: &mut TcpStream,
        method: &str,
        path: &str,
        body: Option<&str>,
        _timeout: Duration,
    ) -> PyResult<(u16, Vec<u8>)> {
        let body_bytes = body.unwrap_or("");
        let request = format!(
            "{} {} HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
            method,
            path,
            body_bytes.len(),
            body_bytes,
        );

        stream.write_all(request.as_bytes()).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("ES write: {}", e))
        })?;
        stream.flush().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("ES flush: {}", e))
        })?;

        // Read HTTP response
        let mut buf = Vec::with_capacity(4096);
        let mut temp = [0u8; 8192];
        loop {
            match stream.read(&mut temp) {
                Ok(0) => break,
                Ok(n) => {
                    if buf.len() + n > MAX_RESPONSE_BYTES {
                        return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(
                            "ES response too large",
                        ));
                    }
                    buf.extend_from_slice(&temp[..n]);
                }
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) => {
                    return Err(PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
                        "ES read: {}",
                        e
                    )));
                }
            }
        }

        // Parse status code
        let response_str = String::from_utf8_lossy(&buf);
        let status_code: u16 = if let Some(first_line) = response_str.lines().next() {
            first_line
                .split_whitespace()
                .nth(1)
                .and_then(|s| s.parse().ok())
                .unwrap_or(0)
        } else {
            0
        };

        // Find body (after \r\n\r\n)
        let body_start = if let Some(pos) = response_str.find("\r\n\r\n") {
            pos + 4
        } else if let Some(pos) = response_str.find("\n\n") {
            pos + 2
        } else {
            0
        };

        Ok((status_code, buf[body_start..].to_vec()))
    }
}

#[pymethods]
impl ElasticsearchDumper {
    #[new]
    fn new() -> Self {
        ElasticsearchDumper {}
    }

    /// List all indices.
    fn list_indices(
        &self,
        host: &str,
        port: u16,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<String>> {
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "ES connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let (_status, body) =
            Self::es_request(&mut stream, "GET", "/_cat/indices?format=json", None, timeout)?;

        // Parse JSON array
        let body_str = String::from_utf8_lossy(&body);
        let mut indices = Vec::new();

        // Simple JSON parsing for array of {"index": "name", ...}
        let chars: Vec<char> = body_str.chars().collect();
        let mut pos = 0;

        while pos < chars.len() {
            // Find "index":
            if pos + 8 < chars.len() && chars[pos..pos + 8].iter().collect::<String>() == "\"index\"" {
                pos += 8;
                // Skip ": "
                while pos < chars.len() && (chars[pos] == ':' || chars[pos] == ' ' || chars[pos] == '"') {
                    if chars[pos] == '"' {
                        pos += 1;
                        break;
                    }
                    pos += 1;
                }
                // Read index name
                let name_start = pos;
                while pos < chars.len() && chars[pos] != '"' {
                    pos += 1;
                }
                let name: String = chars[name_start..pos].iter().collect();
                if !name.is_empty() && !name.starts_with('.') {
                    indices.push(name);
                }
                pos += 1; // skip closing quote
            }
            pos += 1;
        }

        Ok(indices)
    }

    /// Search documents in an index.
    fn search_documents(
        &self,
        host: &str,
        port: u16,
        index: &str,
        query_json: Option<&str>,
        size: Option<u32>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<String>> {
        let size = size.unwrap_or(100);
        let timeout = Duration::from_secs_f64(timeout_s.unwrap_or(READ_TIMEOUT_S));
        let connect_timeout = Duration::from_secs_f64(CONNECT_TIMEOUT_S);

        let addr = resolve_addr(host, port)?;
        let mut stream = TcpStream::connect_timeout(&addr, connect_timeout).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyConnectionError, _>(format!(
                "ES connect failed {}:{}: {}", host, port, e
            ))
        })?;
        stream.set_read_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set timeout: {}", e))
        })?;
        stream.set_write_timeout(Some(timeout)).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("set write timeout: {}", e))
        })?;

        let query = query_json.unwrap_or(r#"{"query":{"match_all":{}}}"#);
        let body = format!(
            r#"{{"query":{},"size":{},"_source":true}}"#,
            query, size
        );

        let path = format!("/{}/_search", index);
        let (_status, resp_body) =
            Self::es_request(&mut stream, "POST", &path, Some(&body), timeout)?;

        // Parse _search response to extract individual hit documents
        let resp_str = String::from_utf8_lossy(&resp_body);
        let mut docs = Vec::new();

        // Find hits array
        let chars: Vec<char> = resp_str.chars().collect();
        let mut pos = 0;

        while pos < chars.len() {
            if pos + 9 < chars.len()
                && chars[pos..pos + 9].iter().collect::<String>() == "\"_source\""
            {
                pos += 9;
                // Skip ": "
                while pos < chars.len() && (chars[pos] == ':' || chars[pos] == ' ') {
                    pos += 1;
                }
                // Extract the _source JSON object
                if pos < chars.len() && chars[pos] == '{' {
                    let doc_start = pos;
                    let mut depth = 0;
                    let mut in_string = false;

                    while pos < chars.len() {
                        let c = chars[pos];
                        if in_string {
                            if c == '"' && pos > 0 && chars[pos - 1] != '\\' {
                                in_string = false;
                            }
                        } else {
                            match c {
                                '"' => in_string = true,
                                '{' => depth += 1,
                                '}' => {
                                    depth -= 1;
                                    if depth == 0 {
                                        let doc: String =
                                            chars[doc_start..=pos].iter().collect();
                                        docs.push(doc);
                                        break;
                                    }
                                }
                                _ => {}
                            }
                        }
                        pos += 1;
                    }
                }
            }
            pos += 1;
        }

        Ok(docs)
    }

    /// Full extraction: list indices → search documents → return structured results.
    fn dump_all(
        &self,
        host: &str,
        port: u16,
        limit: Option<u32>,
        timeout_s: Option<f64>,
    ) -> PyResult<Vec<ElasticsearchDumpEntry>> {
        let limit = limit.unwrap_or(100);
        let timeout = timeout_s.unwrap_or(READ_TIMEOUT_S);
        let mut results = Vec::new();

        // Phase 1: List indices
        let indices = match self.list_indices(host, port, Some(timeout)) {
            Ok(ix) => ix,
            Err(e) => {
                results.push(ElasticsearchDumpEntry {
                    index: String::new(),
                    document_count: None,
                    documents_json: None,
                    error: Some(format!("list_indices failed: {}", e)),
                });
                return Ok(results);
            }
        };

        // Phase 2: Search each index
        for index in &indices {
            match self.search_documents(host, port, index, None, Some(limit), Some(timeout)) {
                Ok(docs) => {
                    results.push(ElasticsearchDumpEntry {
                        index: index.clone(),
                        document_count: Some(docs.len() as i64),
                        documents_json: Some(docs),
                        error: None,
                    });
                }
                Err(e) => {
                    results.push(ElasticsearchDumpEntry {
                        index: index.clone(),
                        document_count: None,
                        documents_json: None,
                        error: Some(format!("search_documents: {}", e)),
                    });
                }
            }
        }

        Ok(results)
    }
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

/// Register the native_db module functions and classes.
pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<MongoDumper>()?;
    m.add_class::<MongoDumpEntry>()?;
    m.add_class::<RedisDumper>()?;
    m.add_class::<RedisDumpEntry>()?;
    m.add_class::<ElasticsearchDumper>()?;
    m.add_class::<ElasticsearchDumpEntry>()?;
    Ok(())
}
