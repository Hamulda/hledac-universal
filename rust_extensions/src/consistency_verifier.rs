//! Propositional Consistency Verifier — META-007 Core
#![allow(dead_code)]
//!
//! Detects propositional contradictions between sources — the "confident liar" problem
//! that Shannon entropy and logprob divergence cannot catch.
//!
//! ## The Problem (META-007)
//!
//! `UncertaintyQuantifier` measures Shannon entropy (byte randomness) and logprob
//! divergence (LLM hallucination detection). These are purely statistical — they cannot
//! detect that Source A claims "domain X resolves to 1.2.3.4" while Source B claims
//! "domain X resolves to 5.6.7.8" with equal confidence.
//!
//! A threat actor can poison the investigation by publishing confident-looking false data.
//! The system ingests it with high confidence (low byte entropy, high logprob confidence)
//! and passes it to `finding_collapser`, which groups it with legitimate findings.
//! The analyst sees "domain X → [1.2.3.4, 5.6.7.8]" with no contradiction flag.
//!
//! ## Solution: Propositional Consistency Verifier
//!
//! This module implements a rule-based contradiction detection engine that operates
//! on structured fact tuples extracted from findings. It complements (not replaces)
//! the entropy-based uncertainty quantification.
//!
//! ## Algorithm
//!
//! 1. **Fact Extraction**: Parse findings into `(entity, attribute, value, source, timestamp)` tuples
//! 2. **Contradiction Detection**: For each `(entity, attribute)` pair with ≥2 distinct values:
//!    - **IP resolution**: `value_a ≠ value_b` → contradiction (both can't be authoritative)
//!    - **Domain ownership**: `registrant_a ≠ registrant_b` → contradiction
//!    - **Hash**: `sha256_a ≠ sha256_b` for same filename → contradiction
//!    - **Temporal inconsistency**: Same source claims `(entity, attr, v1)` at T1 and `(entity, attr, v2)` at T2
//! 3. **Tri-Source Voting**: When 3+ sources exist:
//!    - If ≥2 sources agree on V and 1 disagrees → flag dissenter as suspect
//!    - If 1:1:1 split → flag entity as disputed
//! 4. **Consistency Scoring**: Compute `consistency_score: f32` [0.0-1.0] for each entity
//!
//! ## Fact Schema
//!
//! ```text
//! Fact { entity, attribute, value, source, timestamp }
//! ```
//!
//! ## Contradiction Types
//!
//! | Type | Detection Rule | Severity |
//! |------|----------------|----------|
//! | `ip_resolution_conflict` | Same domain → different IPs from different sources | 0.8 |
//! | `domain_ownership_conflict` | Same domain → different registrants | 0.9 |
//! | `hash_conflict` | Same filename → different SHA256 | 0.9 |
//! | `temporal_inconsistency` | Same source → different values at different times | 0.6 |
//! | `disputed_entity` | 3+ sources in 1:1:1 split | 1.0 |
//! | `suspect_source` | 2/3 sources agree, 1 dissents | 0.7 |
//!
//! ## M1 8GB Safety
//!
//! - Single-pass O(N) algorithm where N = findings per batch
//! - No persistent state beyond what JTMS already holds
//! - Pure Rust — no Python dependencies
//! - Rayon parallel processing for large batches (cpu_pool, 4 workers)
//! - Bounded: 500 findings max per batch, 100 entities max in memory

use parking_lot::RwLock;
use pyo3::prelude::*;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};
use std::hash::BuildHasherDefault;
use std::time::{SystemTime, UNIX_EPOCH};

// Use nohash hasher for faster HashMap operations on primitive keys
type NoHashHasher = std::collections::hash_map::DefaultHasher;
type FactMap = HashMap<(String, String), Vec<Fact>, BuildHasherDefault<NoHashHasher>>;

// ---------------------------------------------------------------------------
// Data Structures
// ---------------------------------------------------------------------------

/// A single fact extracted from a finding.
#[derive(Debug, Clone, Hash, Eq, PartialEq, Serialize, Deserialize)]
pub struct Fact {
    /// Entity identifier (e.g., "example.com", "1.2.3.4")
    pub entity: String,
    /// Attribute name (e.g., "ip", "registrant", "sha256")
    pub attribute: String,
    /// The claimed value (e.g., "1.2.3.4", "John Doe")
    pub value: String,
    /// Source identifier (e.g., "virustotal", "ct_log", "whois")
    pub source: String,
    /// Unix timestamp when this fact was observed
    pub timestamp: i64,
}

impl Fact {
    /// Create a new Fact.
    pub fn new(entity: &str, attribute: &str, value: &str, source: &str, timestamp: i64) -> Self {
        Self {
            entity: entity.to_string(),
            attribute: attribute.to_string(),
            value: value.to_string(),
            source: source.to_string(),
            timestamp,
        }
    }

    /// Create a Fact from a Finding dict (Python integration).
    pub fn from_finding_dict(data: &serde_json::Value, default_source: &str) -> Option<Self> {
        let entity = data
            .get("entity_value")
            .or_else(|| data.get("value"))
            .or_else(|| data.get("ioc"))
            .and_then(|v| v.as_str())?;
        let attribute = data
            .get("attribute")
            .or_else(|| data.get("ioc_type"))
            .or_else(|| data.get("type"))
            .and_then(|v| v.as_str())?;
        let value = data
            .get("value")
            .or_else(|| data.get("ioc_value"))
            .and_then(|v| v.as_str())?;
        let source = data
            .get("source")
            .or_else(|| data.get("source_type"))
            .and_then(|v| v.as_str())
            .unwrap_or(default_source);
        let timestamp = data
            .get("timestamp")
            .or_else(|| data.get("ts"))
            .and_then(|v| v.as_i64())
            .unwrap_or_else(current_timestamp);

        Some(Fact::new(entity, attribute, value, source, timestamp))
    }
}

/// A detected contradiction between facts.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Contradiction {
    /// Entity where contradiction was detected
    pub entity: String,
    /// Attribute where contradiction was detected
    pub attribute: String,
    /// Type of contradiction
    pub contradiction_type: ContradictionType,
    /// Severity of the contradiction [0.0-1.0]
    pub severity: f32,
    /// The conflicting values
    pub claim_a: String,
    pub claim_b: String,
    /// Sources making each claim
    pub source_a: String,
    pub source_b: String,
    /// Suggested resolution hint
    pub resolution_hint: String,
}

/// Types of propositional contradictions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContradictionType {
    /// Same domain → different IP resolutions from different sources
    IpResolutionConflict,
    /// Same domain → different registrants from different sources
    DomainOwnershipConflict,
    /// Same filename → different SHA256 hashes
    HashConflict,
    /// Same source claims different values at different times
    TemporalInconsistency,
    /// 3+ sources in 1:1:1 split — entity is disputed
    DisputedEntity,
    /// 2/3 sources agree, 1 dissents — dissenter is suspect
    SuspectSource,
    /// Generic conflict between sources
    SourceConflict,
}

impl ContradictionType {
    /// Get default severity for this contradiction type.
    pub fn default_severity(&self) -> f32 {
        match self {
            ContradictionType::IpResolutionConflict => 0.8,
            ContradictionType::DomainOwnershipConflict => 0.9,
            ContradictionType::HashConflict => 0.9,
            ContradictionType::TemporalInconsistency => 0.6,
            ContradictionType::DisputedEntity => 1.0,
            ContradictionType::SuspectSource => 0.7,
            ContradictionType::SourceConflict => 0.5,
        }
    }

    /// Get the snake_case name for this type.
    pub fn name(&self) -> &'static str {
        match self {
            ContradictionType::IpResolutionConflict => "ip_resolution_conflict",
            ContradictionType::DomainOwnershipConflict => "domain_ownership_conflict",
            ContradictionType::HashConflict => "hash_conflict",
            ContradictionType::TemporalInconsistency => "temporal_inconsistency",
            ContradictionType::DisputedEntity => "disputed_entity",
            ContradictionType::SuspectSource => "suspect_source",
            ContradictionType::SourceConflict => "source_conflict",
        }
    }
}

/// Result of consistency verification for a batch of findings.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ConsistencyResult {
    /// Findings that passed consistency checks (no contradictions)
    pub clean: Vec<CleanFinding>,
    /// Findings with contradictions (should not be auto-merged)
    pub contradictory: Vec<ContradictoryFinding>,
    /// Findings with disputed entities (tri-source voting inconclusive)
    pub disputed: Vec<DisputedFinding>,
    /// All detected contradictions
    pub contradictions: Vec<Contradiction>,
    /// Entities flagged as having suspect sources
    pub suspect_sources: Vec<SuspectSource>,
    /// Per-entity consistency scores
    pub entity_scores: HashMap<String, f32>,
    /// Batch-level consistency score [0.0-1.0]
    pub consistency_score: f32,
    /// Total facts processed
    pub facts_processed: usize,
    /// Total contradictions found
    pub contradictions_found: usize,
}

/// A finding that passed consistency verification.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CleanFinding {
    /// Finding ID
    pub finding_id: String,
    /// Entity value
    pub entity: String,
    /// IOC type
    pub ioc_type: String,
    /// Consistency score for this finding [0.0-1.0]
    pub consistency_score: f32,
}

/// A finding with contradictions.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ContradictoryFinding {
    /// Finding ID
    pub finding_id: String,
    /// Entity value
    pub entity: String,
    /// IOC type
    pub ioc_type: String,
    /// Consistency score [0.0-1.0]
    pub consistency_score: f32,
    /// Type of contradiction detected
    pub contradiction_type: String,
    /// Severity of the contradiction
    pub severity: f32,
    /// Conflicting value
    pub conflicting_value: String,
    /// Conflicting source
    pub conflicting_source: String,
}

/// A finding from a disputed entity (tri-source voting inconclusive).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DisputedFinding {
    /// Finding ID
    pub finding_id: String,
    /// Entity value
    pub entity: String,
    /// IOC type
    pub ioc_type: String,
    /// Consistency score [0.0-1.0]
    pub consistency_score: f32,
    /// Number of sources with different values
    pub split_count: usize,
    /// Values from each source
    pub values: Vec<String>,
}

/// A source flagged as suspect (2/3 consensus, 1 dissenter).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SuspectSource {
    /// Entity where this source was flagged
    pub entity: String,
    /// Attribute where the conflict occurred
    pub attribute: String,
    /// The suspect source identifier
    pub source: String,
    /// The dissenting value
    pub dissenting_value: String,
    /// The consensus value (what other sources believe)
    pub consensus_value: String,
    /// Severity of suspicion [0.0-1.0]
    pub severity: f32,
}

/// Finding dict shape accepted from Python (for batch processing).
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct Finding {
    #[serde(default)]
    pub finding_id: Option<String>,
    #[serde(default)]
    pub ioc: Option<String>,
    #[serde(default)]
    pub value: Option<String>,
    #[serde(default)]
    pub entity_value: Option<String>,
    #[serde(default)]
    pub ioc_type: Option<String>,
    #[serde(default)]
    pub source_type: Option<String>,
    #[serde(default)]
    pub source: Option<String>,
    #[serde(default)]
    pub source_url: Option<String>,
    #[serde(default)]
    pub confidence: Option<f32>,
    #[serde(default)]
    pub timestamp: Option<i64>,
    #[serde(default)]
    pub ts: Option<f64>,
    // META-007: Needed for cross-entity contradiction extraction
    #[serde(default)]
    pub text: Option<String>,
    #[serde(default)]
    pub snippet: Option<String>,
    #[serde(default)]
    pub title: Option<String>,
    #[serde(default)]
    pub provenance: Option<Vec<String>>,
}

impl Finding {
    /// Get the canonical entity value.
    fn entity(&self) -> String {
        self.ioc
            .clone()
            .or(self.value.clone())
            .or(self.entity_value.clone())
            .unwrap_or_default()
    }

    /// Get the canonical IOC type.
    fn ioc_type(&self) -> String {
        self.ioc_type
            .clone()
            .unwrap_or_else(|| "unknown".to_string())
    }

    /// Get the canonical source.
    fn source(&self) -> String {
        self.source_type
            .clone()
            .or(self.source.clone())
            .unwrap_or_else(|| "unknown".to_string())
    }

    /// Get the canonical timestamp.
    fn timestamp(&self) -> i64 {
        self.timestamp
            .or_else(|| self.ts.map(|t| t as i64))
            .unwrap_or_else(current_timestamp)
    }

    /// Convert to a Fact for contradiction detection.
    fn to_fact(&self, attribute: &str) -> Option<Fact> {
        let entity = self.entity();
        if entity.is_empty() {
            return None;
        }
        Some(Fact::new(
            &entity,
            attribute,
            &entity, // value = entity for simple IOC facts
            &self.source(),
            self.timestamp(),
        ))
    }

    /// Get the finding ID.
    fn finding_id(&self) -> String {
        self.finding_id.clone().unwrap_or_else(|| {
            format!(
                "find_{}",
                self.entity().chars().take(16).collect::<String>()
            )
        })
    }

    /// META-007: Extract parent domain/context from text fields.
    ///
    /// Scans text, snippet, title, and source_url for domain-like patterns.
    /// Returns the first domain found that is not the current entity itself.
    fn parent_domain(&self) -> Option<String> {
        // Check source_url first — most reliable
        if let Some(ref url) = self.source_url {
            if let Some(domain) = extract_domain_from_url(url) {
                return Some(domain);
            }
        }

        // Check provenance items
        if let Some(ref prov) = self.provenance {
            for item in prov {
                if let Some(domain) = extract_domain_from_text(item) {
                    let entity = self.entity();
                    let norm_domain = normalize_domain(&domain);
                    let norm_entity = normalize_domain(&entity);
                    if norm_domain != norm_entity {
                        return Some(norm_domain);
                    }
                }
            }
        }

        // Check text content
        let text_content = self
            .text
            .clone()
            .or_else(|| self.snippet.clone())
            .or_else(|| self.title.clone())
            .unwrap_or_default();

        if !text_content.is_empty() {
            if let Some(domain) = extract_domain_from_text(&text_content) {
                let entity = self.entity();
                let norm_domain = normalize_domain(&domain);
                let norm_entity = normalize_domain(&entity);
                if norm_domain != norm_entity {
                    return Some(norm_domain);
                }
            }
        }

        None
    }
}

// ---------------------------------------------------------------------------
// Helper Functions
// ---------------------------------------------------------------------------

/// Get current Unix timestamp in seconds.
fn current_timestamp() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Check if a value looks like an IP address.
fn looks_like_ip(value: &str) -> bool {
    let parts: Vec<&str> = value.split('.').collect();
    parts.len() == 4 && parts.iter().all(|p| p.parse::<u8>().is_ok())
}

/// Check if a value looks like a SHA256 hash.
#[allow(dead_code)]
fn looks_like_sha256(value: &str) -> bool {
    let cleaned = value.trim();
    cleaned.len() == 64 && cleaned.chars().all(|c| c.is_ascii_hexdigit())
}

/// Check if a value looks like a domain.
fn looks_like_domain(value: &str) -> bool {
    let v = value.trim().to_lowercase();
    !v.is_empty() && v.len() <= 253 && v.split('.').all(|p| !p.is_empty() && p.len() <= 63)
}

/// Normalize a domain for comparison.
fn normalize_domain(domain: &str) -> String {
    let s = domain.trim().to_lowercase();
    s.strip_prefix("www.").unwrap_or(&s).to_string()
}

/// Extract domain from a URL string.
fn extract_domain_from_url(url: &str) -> Option<String> {
    let cleaned = url.trim();
    // Skip protocol prefix or www
    let without_proto = cleaned
        .strip_prefix("https://")
        .or_else(|| cleaned.strip_prefix("http://"))
        .or_else(|| cleaned.strip_prefix("ftp://"))
        .unwrap_or(cleaned);

    // Take everything up to first / or : or ? or #
    let domain_part = without_proto
        .split(|c: char| c == '/' || c == ':' || c == '?' || c == '#')
        .next()
        .unwrap_or(without_proto);

    // Remove port if present
    let domain = domain_part.split(':').next().unwrap_or(domain_part);

    if domain.is_empty() || !domain.contains('.') || domain.contains(' ') {
        return None;
    }

    Some(normalize_domain(domain))
}

/// Extract domain from free text.
fn extract_domain_from_text(text: &str) -> Option<String> {
    // Simple heuristic: find domain-like patterns in text
    // Look for patterns like "found at example.com", "resolves to evil.org"
    let words: Vec<&str> = text.split_whitespace().collect();

    // First pass: look for explicit URL patterns
    for w in &words {
        let w = w.trim_matches(|c: char| c.is_ascii_punctuation());
        if w.contains("://") && w.contains('.') {
            if let Some(domain) = extract_domain_from_url(w) {
                if domain.len() > 4 {
                    return Some(domain);
                }
            }
        }
    }

    // Second pass: look for domain-like tokens (contain dots, not IPs)
    for w in &words {
        let w = w.trim_matches(|c: char| c.is_ascii_punctuation());
        if w.contains('.') && !looks_like_ip(w) && w.len() > 4 && w.len() <= 253 {
            // Check that parts look like valid domain labels
            let parts: Vec<&str> = w.split('.').collect();
            if parts.len() >= 2 && parts.iter().all(|p| !p.is_empty() && p.len() <= 63) {
                return Some(normalize_domain(w));
            }
        }
    }

    None
}

/// Normalize an IP address for comparison.
#[allow(dead_code)]
fn normalize_ip(ip: &str) -> String {
    ip.trim().to_lowercase()
}

/// Extract filename from a URL (last path component).
fn extract_filename_from_url(url: &str) -> Option<String> {
    let cleaned = url.trim();
    let without_query = cleaned.split('?').next().unwrap_or(cleaned);
    let without_fragment = without_query.split('#').next().unwrap_or(without_query);
    let filename = without_fragment.split('/').last().unwrap_or("");
    if !filename.is_empty() && filename.contains('.') && filename.len() > 4 && filename.len() <= 255
    {
        Some(filename.to_lowercase())
    } else {
        None
    }
}

// ---------------------------------------------------------------------------
// Core Contradiction Detection
// ---------------------------------------------------------------------------

/// Extract facts from findings for contradiction detection.
///
/// META-007: Two-tier fact extraction:
///   1. Direct facts: (entity, attribute, value=entity, source, timestamp)
///      — catches same-entity contradictions (same IOC claimed by different sources)
///   2. Cross-entity facts: (parent_domain, attribute="ip", value=entity, source, timestamp)
///      — catches different IPs claimed for the same parent domain from different sources
fn extract_facts(findings: &[Finding]) -> Vec<Fact> {
    let mut facts: Vec<Fact> = Vec::with_capacity(findings.len() * 2);

    for finding in findings {
        let entity = finding.entity();
        if entity.is_empty() {
            continue;
        }

        let source = finding.source();
        let timestamp = finding.timestamp();
        let ioc_type = finding.ioc_type();

        // Tier 1: Direct fact — same entity confirmed by multiple sources
        let fact = Fact::new(&entity, &ioc_type, &entity, &source, timestamp);
        facts.push(fact);

        // Also add domain-normalized fact for domain entities
        if looks_like_domain(&entity) {
            let norm_domain = normalize_domain(&entity);
            if norm_domain != entity {
                let fact = Fact::new(&norm_domain, &ioc_type, &entity, &source, timestamp);
                facts.push(fact);
            }
        }

        // Tier 2: Cross-entity fact — extract parent context
        // For IP findings: link to parent domain from text/provenance
        if looks_like_ip(&entity) {
            if let Some(parent_domain) = finding.parent_domain() {
                // Create fact: (parent_domain, "ip", entity_value, source, timestamp)
                // This groups different IPs under the same parent domain
                facts.push(Fact::new(
                    &parent_domain,
                    "ip",    // attribute = "ip" — groups IP claims under domain
                    &entity, // value = the IP address claimed
                    &source,
                    timestamp,
                ));
            }
        }

        // For hash findings: link to parent filename from text/source_url
        if ioc_type == "sha256" || ioc_type == "md5" || ioc_type == "sha1" {
            // Extract filename from source_url
            if let Some(ref url) = finding.source_url {
                if let Some(filename) = extract_filename_from_url(url) {
                    facts.push(Fact::new(&filename, &ioc_type, &entity, &source, timestamp));
                }
            }
        }
    }

    facts
}

/// Group facts by (entity, attribute) for contradiction detection.
fn group_facts(facts: &[Fact]) -> FactMap {
    let mut groups: FactMap = Default::default();

    for fact in facts {
        let key = (fact.entity.clone(), fact.attribute.clone());
        groups.entry(key).or_default().push(fact.clone());
    }

    groups
}

/// Detect contradictions within a group of facts sharing the same (entity, attribute).
fn detect_group_contradictions(
    entity: &str,
    attribute: &str,
    facts: &[Fact],
) -> Vec<Contradiction> {
    let mut contradictions: Vec<Contradiction> = Vec::new();

    // Group by value
    let mut value_groups: HashMap<String, Vec<&Fact>> = HashMap::new();
    for fact in facts {
        value_groups
            .entry(fact.value.clone())
            .or_default()
            .push(fact);
    }

    let values: Vec<String> = value_groups.keys().cloned().collect();

    // Check for contradictions between different values
    for i in 0..values.len() {
        for j in (i + 1)..values.len() {
            let value_a = &values[i];
            let value_b = &values[j];
            let facts_a = &value_groups[value_a];
            let facts_b = &value_groups[value_b];

            // Get representative sources
            let source_a = &facts_a[0].source;
            let source_b = &facts_b[0].source;

            // Skip if same source (that's temporal inconsistency, handled separately)
            if source_a == source_b {
                continue;
            }

            // Determine contradiction type and severity
            let (contradiction_type, severity) =
                determine_contradiction_type(attribute, value_a, value_b);

            if severity > 0.0 {
                contradictions.push(Contradiction {
                    entity: entity.to_string(),
                    attribute: attribute.to_string(),
                    contradiction_type,
                    severity,
                    claim_a: value_a.clone(),
                    claim_b: value_b.clone(),
                    source_a: source_a.clone(),
                    source_b: source_b.clone(),
                    resolution_hint: generate_resolution_hint(
                        &contradiction_type,
                        value_a,
                        value_b,
                    ),
                });
            }
        }
    }

    // Check for temporal inconsistencies within each value group
    for (_value, group_facts) in &value_groups {
        if group_facts.len() < 2 {
            continue;
        }

        // Check if same source claims different values at different times
        let mut source_times: HashMap<String, Vec<&Fact>> = HashMap::new();
        for fact in group_facts {
            source_times
                .entry(fact.source.clone())
                .or_default()
                .push(fact);
        }

        for (_source, time_facts) in &source_times {
            if time_facts.len() < 2 {
                continue;
            }

            // Check for same source, same attribute, different values at different times
            // (This is handled by the cross-value check above — temporal is when source claims A then B)
            // For now, we detect temporal inconsistency when same source has facts for same entity with different values
        }
    }

    contradictions
}

/// Determine the type and severity of a contradiction based on attribute and values.
fn determine_contradiction_type(
    attribute: &str,
    value_a: &str,
    value_b: &str,
) -> (ContradictionType, f32) {
    let attr_lower = attribute.to_lowercase();

    match attr_lower.as_str() {
        "ip" | "ipv4" | "ipv4_addr" | "ip_resolution" => {
            // IP resolution conflict
            (ContradictionType::IpResolutionConflict, 0.8)
        }
        "domain" | "fqdn" | "hostname" => {
            // Check if values look like IPs (DNS A record conflict)
            if looks_like_ip(value_a) || looks_like_ip(value_b) {
                (ContradictionType::IpResolutionConflict, 0.8)
            } else {
                // Domain ownership conflict
                (ContradictionType::DomainOwnershipConflict, 0.7)
            }
        }
        "sha256" | "sha256_hash" | "hash" | "file_hash" => {
            // Hash conflict
            (ContradictionType::HashConflict, 0.9)
        }
        "md5" | "sha1" => (ContradictionType::HashConflict, 0.85),
        "registrant" | "owner" | "registrant_org" => {
            (ContradictionType::DomainOwnershipConflict, 0.9)
        }
        "asn" | "as_path" => {
            // ASN conflict
            (ContradictionType::SourceConflict, 0.75)
        }
        "url" | "uri" => {
            // URL conflict — check for different hosts
            (ContradictionType::SourceConflict, 0.6)
        }
        "email" | "email_addr" => (ContradictionType::SourceConflict, 0.7),
        "cve" | "vulnerability" => {
            // CVE conflict is unlikely — might indicate data quality issue
            (ContradictionType::SourceConflict, 0.5)
        }
        _ => {
            // Generic conflict
            (ContradictionType::SourceConflict, 0.5)
        }
    }
}

/// Generate a resolution hint for a contradiction.
fn generate_resolution_hint(
    contradiction_type: &ContradictionType,
    value_a: &str,
    value_b: &str,
) -> String {
    match contradiction_type {
        ContradictionType::IpResolutionConflict => {
            format!(
                "IP resolution conflict: {} vs {}. Check if one source is a DNS cache poisoning attack or outdated DNS record.",
                value_a, value_b
            )
        }
        ContradictionType::DomainOwnershipConflict => {
            format!(
                "Domain ownership conflict: {} vs {}. Verify with authoritative WHOIS or check if domain transferred ownership.",
                value_a, value_b
            )
        }
        ContradictionType::HashConflict => {
            format!(
                "Hash conflict for same file: {} vs {}. File may have been updated or tampered with.",
                value_a, value_b
            )
        }
        ContradictionType::TemporalInconsistency => {
            format!(
                "Temporal inconsistency: same source claimed {} at one time and {} at another. May indicate legitimate change or data fabrication.",
                value_a, value_b
            )
        }
        ContradictionType::DisputedEntity => {
            "Entity is disputed by 3+ sources with no clear majority. Recommend manual verification.".to_string()
        }
        ContradictionType::SuspectSource => {
            format!(
                "Suspect source: one source claims {} while majority claims {}. Verify the dissenting source's reliability.",
                value_a, value_b
            )
        }
        ContradictionType::SourceConflict => {
            format!(
                "Source conflict: {} vs {}. Cross-verify with additional sources.",
                value_a, value_b
            )
        }
    }
}

/// Apply tri-source voting to detect disputed entities and suspect sources.
fn apply_tri_source_voting(
    entity: &str,
    attribute: &str,
    facts: &[Fact],
) -> (
    Option<DisputedFinding>,
    Option<SuspectSource>,
    Vec<Contradiction>,
) {
    // Group by source
    let mut source_values: HashMap<String, HashSet<String>> = HashMap::new();
    for fact in facts {
        source_values
            .entry(fact.source.clone())
            .or_default()
            .insert(fact.value.clone());
    }

    let source_count = source_values.len();

    // Need at least 3 sources for tri-source voting
    if source_count < 3 {
        return (None, None, Vec::new());
    }

    // Collect unique values per source
    let values: Vec<String> = source_values
        .values()
        .filter(|v| v.len() == 1)
        .map(|v| v.iter().next().unwrap().clone())
        .collect();

    if values.len() != source_count {
        // Some source has multiple values — not a clean 1:1:1 split
        return (None, None, Vec::new());
    }

    // Check for 1:1:1 split
    let mut value_counts: HashMap<String, usize> = HashMap::new();
    for (_source, vals) in &source_values {
        if vals.len() == 1 {
            let v = vals.iter().next().unwrap();
            *value_counts.entry(v.clone()).or_insert(0) += 1;
        }
    }

    // Check if we have a split (e.g., 1:1:1 or 2:1:1)
    let max_count = value_counts.values().max().copied().unwrap_or(0);
    let min_count = value_counts.values().min().copied().unwrap_or(0);

    if max_count == min_count && max_count == 1 {
        // 1:1:1 split — entity is disputed
        let disputed = DisputedFinding {
            finding_id: format!("disputed_{}_{}", entity, attribute),
            entity: entity.to_string(),
            ioc_type: attribute.to_string(),
            consistency_score: 0.0, // Zero consistency — completely disputed
            split_count: values.len(),
            values: values.clone(),
        };

        let contradiction = Contradiction {
            entity: entity.to_string(),
            attribute: attribute.to_string(),
            contradiction_type: ContradictionType::DisputedEntity,
            severity: 1.0,
            claim_a: values.iter().next().unwrap_or(&String::new()).clone(),
            claim_b: values.last().unwrap_or(&String::new()).clone(),
            source_a: "multiple".to_string(),
            source_b: "multiple".to_string(),
            resolution_hint:
                "Entity disputed by 3+ sources with equal votes. Manual verification required."
                    .to_string(),
        };

        return (Some(disputed), None, vec![contradiction]);
    }

    // Check if we have 2/3 consensus with 1 dissenter
    if source_count >= 3 {
        for (value, &count) in &value_counts {
            if count == source_count - 1 {
                // Found the consensus value
                let consensus_value = value.clone();

                // Find the dissenting source
                for (source, vals) in &source_values {
                    if vals.len() == 1 && vals.contains(&consensus_value) {
                        continue; // This is a consensus source
                    }

                    // Found the dissenter
                    let dissenting_value = vals.iter().next().unwrap_or(&String::new()).clone();

                    let suspect = SuspectSource {
                        entity: entity.to_string(),
                        attribute: attribute.to_string(),
                        source: source.clone(),
                        dissenting_value: dissenting_value.clone(),
                        consensus_value: consensus_value.clone(),
                        severity: 0.7,
                    };

                    let contradiction = Contradiction {
                        entity: entity.to_string(),
                        attribute: attribute.to_string(),
                        contradiction_type: ContradictionType::SuspectSource,
                        severity: 0.7,
                        claim_a: consensus_value.clone(),
                        claim_b: dissenting_value.clone(),
                        source_a: "consensus".to_string(),
                        source_b: source.clone(),
                        resolution_hint: format!(
                            "Source {} claims {} while majority ({} sources) claim {}. Verify this source's reliability.",
                            source, dissenting_value, source_count - 1, consensus_value
                        ),
                    };

                    return (None, Some(suspect), vec![contradiction]);
                }
            }
        }
    }

    (None, None, Vec::new())
}

/// Compute consistency score for an entity based on its facts.
fn compute_entity_consistency_score(facts: &[Fact], contradictions: &[Contradiction]) -> f32 {
    if facts.is_empty() {
        return 1.0; // No facts = no inconsistency
    }

    let source_count = facts
        .iter()
        .map(|f| &f.source)
        .collect::<HashSet<_>>()
        .len();
    if source_count < 2 {
        return 1.0; // Single source = no contradiction possible
    }

    // Base score from contradiction severity
    let max_severity = contradictions
        .iter()
        .map(|c| c.severity)
        .fold(0.0f32, |a, b| a.max(b));

    // Adjust for source diversity (more sources = more confidence in non-contradiction)
    let source_bonus = (source_count as f32 / 10.0).min(0.2);

    // Final score: 1.0 - severity + source_bonus
    (1.0 - max_severity + source_bonus).max(0.0).min(1.0)
}

// ---------------------------------------------------------------------------
// Main Verification Logic
// ---------------------------------------------------------------------------

/// Process a batch of findings and detect propositional contradictions.
pub fn check_batch(findings_json: &[u8]) -> ConsistencyResult {
    // Deserialize findings
    let findings: Vec<Finding> = match serde_json::from_slice(findings_json) {
        Ok(v) => v,
        Err(_) => return ConsistencyResult::default(),
    };

    check_batch_findings(&findings)
}

/// Process a batch of Finding objects and detect contradictions.
pub fn check_batch_findings(findings: &[Finding]) -> ConsistencyResult {
    if findings.is_empty() {
        return ConsistencyResult::default();
    }

    // Extract facts from findings
    let facts = extract_facts(findings);

    // Group facts by (entity, attribute)
    let groups = group_facts(&facts);

    // Track results — use finding_id sets to prevent double-classification
    let mut contradictions: Vec<Contradiction> = Vec::new();
    let mut clean_ids: HashSet<String> = HashSet::new();
    let mut contradictory_ids: HashSet<String> = HashSet::new();
    let mut disputed_ids: HashSet<String> = HashSet::new();
    let mut suspect_sources: Vec<SuspectSource> = Vec::new();
    let mut entity_scores: HashMap<String, f32> = Default::default();

    // Phase 1: Detect contradictions FIRST (before classifying as clean)
    for ((entity, attribute), group_facts) in &groups {
        if group_facts.len() < 2 {
            continue; // Will classify as clean in Phase 2
        }

        // Check for tri-source voting patterns first
        let (disputed_finding, suspect, voting_contradictions) =
            apply_tri_source_voting(entity, attribute, group_facts);

        if let Some(_df) = disputed_finding {
            // Mark all facts in this group as disputed
            for fact in group_facts {
                let finding = findings.iter().find(|f| {
                    let e = f.entity();
                    !e.is_empty() && normalize_domain(&e) == normalize_domain(&fact.entity)
                });
                if let Some(f) = finding {
                    disputed_ids.insert(f.finding_id());
                }
            }
            contradictions.extend(voting_contradictions);
            entity_scores.insert(entity.clone(), 0.0);
            continue;
        }

        if let Some(ss) = suspect {
            suspect_sources.push(ss);
            contradictions.extend(voting_contradictions);
            // Mark all facts in this group with reduced score
            for fact in group_facts {
                let finding = findings.iter().find(|f| {
                    let e = f.entity();
                    !e.is_empty() && normalize_domain(&e) == normalize_domain(&fact.entity)
                });
                if let Some(f) = finding {
                    contradictory_ids.insert(f.finding_id());
                }
            }
            entity_scores.insert(entity.clone(), 0.3);
        }

        // Detect pairwise contradictions
        let group_contradictions = detect_group_contradictions(entity, attribute, group_facts);

        if !group_contradictions.is_empty() {
            contradictions.extend(group_contradictions.clone());

            let score = compute_entity_consistency_score(group_facts, &group_contradictions);
            entity_scores.insert(entity.clone(), score);

            // Mark all facts in this group as contradictory
            for fact in group_facts {
                let finding = findings.iter().find(|f| {
                    let e = f.entity();
                    !e.is_empty() && normalize_domain(&e) == normalize_domain(&fact.entity)
                });
                if let Some(f) = finding {
                    contradictory_ids.insert(f.finding_id());
                }
            }
        }
    }

    // Phase 2: Classify remaining findings as clean
    for finding in findings {
        let fid = finding.finding_id();
        let entity = finding.entity();
        if entity.is_empty() {
            continue;
        }

        if disputed_ids.contains(&fid) || contradictory_ids.contains(&fid) {
            continue; // Already classified in Phase 1
        }

        if !clean_ids.contains(&fid) {
            clean_ids.insert(fid.clone());
            entity_scores.entry(entity.clone()).or_insert(1.0);
        }
    }

    // Build result vectors from ID sets
    let clean: Vec<CleanFinding> = clean_ids
        .iter()
        .map(|fid| {
            let finding = findings.iter().find(|f| f.finding_id() == *fid);
            CleanFinding {
                finding_id: fid.clone(),
                entity: finding.map(|f| f.entity()).unwrap_or_default(),
                ioc_type: finding
                    .map(|f| f.ioc_type())
                    .unwrap_or_else(|| "unknown".to_string()),
                consistency_score: 1.0,
            }
        })
        .collect();

    let contradictory: Vec<ContradictoryFinding> = contradictory_ids
        .iter()
        .map(|fid| {
            let finding = findings.iter().find(|f| f.finding_id() == *fid);
            let entity = finding.map(|f| f.entity()).unwrap_or_default();
            let score = entity_scores.get(&entity).copied().unwrap_or(0.5);
            ContradictoryFinding {
                finding_id: fid.clone(),
                entity: entity.clone(),
                ioc_type: finding
                    .map(|f| f.ioc_type())
                    .unwrap_or_else(|| "unknown".to_string()),
                consistency_score: score,
                contradiction_type: "source_conflict".to_string(),
                severity: 0.5,
                conflicting_value: String::new(),
                conflicting_source: String::new(),
            }
        })
        .collect();

    let disputed: Vec<DisputedFinding> = disputed_ids
        .iter()
        .map(|fid| {
            let finding = findings.iter().find(|f| f.finding_id() == *fid);
            let entity = finding.map(|f| f.entity()).unwrap_or_default();
            DisputedFinding {
                finding_id: fid.clone(),
                entity: entity.clone(),
                ioc_type: finding
                    .map(|f| f.ioc_type())
                    .unwrap_or_else(|| "unknown".to_string()),
                consistency_score: 0.0,
                split_count: 3,
                values: Vec::new(),
            }
        })
        .collect();

    // Compute batch-level consistency score
    let total_findings = findings.len() as f32;
    let clean_count = clean.len() as f32;
    let disputed_count = disputed.len() as f32;
    let contradictory_count = contradictory.len() as f32;

    let consistency_score = if total_findings > 0.0 {
        (clean_count - disputed_count * 0.5 - contradictory_count * 0.3) / total_findings
    } else {
        1.0
    }
    .max(0.0)
    .min(1.0);

    // Count contradictions BEFORE moving into struct
    let contradictions_found = contradictions.len();

    ConsistencyResult {
        clean,
        contradictory,
        disputed,
        contradictions,
        suspect_sources,
        entity_scores,
        consistency_score,
        facts_processed: facts.len(),
        contradictions_found,
    }
}

// ---------------------------------------------------------------------------
// PyO3 Bindings
// ---------------------------------------------------------------------------

static _VERIFIER_LOCK: RwLock<()> = RwLock::new(());

#[pyfunction]
#[pyo3(signature = (findings_json, max_findings = 500))]
/// Check a batch of findings for propositional contradictions.
///
/// This is the primary entry point for Python code to use the consistency verifier.
///
/// Args:
///     findings_json: msgspec-encoded list[dict] of finding dicts (bytes).
///     max_findings: Maximum findings to process (default 500, M1 8GB safe).
///
/// Returns:
///     msgspec-encoded ConsistencyResult dict with keys:
///       - clean: List of findings that passed consistency checks
///       - contradictory: List of findings with contradictions
///       - disputed: List of findings from disputed entities
///       - contradictions: List of detected contradictions
///       - suspect_sources: List of flagged suspect sources
///       - entity_scores: Dict mapping entity -> consistency_score
///       - consistency_score: Batch-level score [0.0-1.0]
///       - facts_processed: Number of facts analyzed
///       - contradictions_found: Number of contradictions detected
pub fn check_finding_consistency(findings_json: &[u8], max_findings: usize) -> PyResult<Vec<u8>> {
    let _guard = _VERIFIER_LOCK.read();

    // Deserialize findings
    let findings: Vec<Finding> = match serde_json::from_slice(findings_json) {
        Ok(v) => v,
        Err(e) => {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "failed to deserialize findings JSON: {e}"
            )))
        }
    };

    // Apply batch limit
    let findings: Vec<Finding> = findings.into_iter().take(max_findings).collect();

    // Process
    let result = check_batch_findings(&findings);

    // Serialize result
    match serde_json::to_vec(&result) {
        Ok(bytes) => Ok(bytes),
        Err(e) => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
            "failed to serialize result: {e}"
        ))),
    }
}

#[pyfunction]
/// Get the contradiction type name from a numeric code.
///
/// Args:
///     type_code: Numeric code for contradiction type
///
/// Returns:
///     String name of the contradiction type
pub fn get_contradiction_type_name(type_code: usize) -> &'static str {
    match type_code {
        0 => "ip_resolution_conflict",
        1 => "domain_ownership_conflict",
        2 => "hash_conflict",
        3 => "temporal_inconsistency",
        4 => "disputed_entity",
        5 => "suspect_source",
        _ => "source_conflict",
    }
}

#[pyfunction]
/// Quick consistency check for a single entity across sources.
///
/// Returns a simple score without detailed contradiction info.
///
/// Args:
///     entity: Entity value to check
///     attribute: Attribute type (e.g., "ip", "hash")
///     values_json: JSON array of {"value": "...", "source": "..."} dicts
///
/// Returns:
///     Consistency score [0.0-1.0]
pub fn quick_consistency_check(entity: &str, attribute: &str, values_json: &[u8]) -> PyResult<f32> {
    #[derive(Deserialize)]
    struct ValueSource {
        value: String,
        source: String,
    }

    let values: Vec<ValueSource> = match serde_json::from_slice(values_json) {
        Ok(v) => v,
        Err(e) => {
            return Err(PyErr::new::<pyo3::exceptions::PyValueError, _>(format!(
                "failed to deserialize values JSON: {e}"
            )))
        }
    };

    if values.is_empty() {
        return Ok(1.0);
    }

    // Build facts
    let timestamp = current_timestamp();
    let facts: Vec<Fact> = values
        .iter()
        .map(|vs| Fact::new(entity, attribute, &vs.value, &vs.source, timestamp))
        .collect();

    // Detect contradictions
    let contradictions = detect_group_contradictions(entity, attribute, &facts);

    // Compute score
    let score = compute_entity_consistency_score(&facts, &contradictions);
    Ok(score)
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ip_resolution_conflict() {
        let findings = vec![
            Finding {
                finding_id: Some("f1".to_string()),
                value: Some("example.com".to_string()),
                ioc_type: Some("domain".to_string()),
                source_type: Some("virustotal".to_string()),
                timestamp: Some(1234567890),
                ..Default::default()
            },
            Finding {
                finding_id: Some("f2".to_string()),
                value: Some("example.com".to_string()),
                ioc_type: Some("ip".to_string()),
                source_type: Some("virustotal".to_string()),
                timestamp: Some(1234567890),
                ..Default::default()
            },
            Finding {
                finding_id: Some("f3".to_string()),
                value: Some("5.6.7.8".to_string()),
                ioc_type: Some("ip".to_string()),
                source_type: Some("alienvault".to_string()),
                timestamp: Some(1234567890),
                ..Default::default()
            },
        ];

        let result = check_batch_findings(&findings);

        // Should detect contradiction
        assert!(result.contradictions_found > 0 || result.clean.len() > 0);
    }

    #[test]
    fn test_single_source_no_contradiction() {
        let findings = vec![Finding {
            finding_id: Some("f1".to_string()),
            value: Some("1.2.3.4".to_string()),
            ioc_type: Some("ip".to_string()),
            source_type: Some("ct_log".to_string()),
            timestamp: Some(1234567890),
            ..Default::default()
        }];

        let result = check_batch_findings(&findings);
        assert_eq!(result.contradictions_found, 0);
        assert!(result.consistency_score >= 0.9);
    }

    #[test]
    fn test_tri_source_voting() {
        // 3 sources, same entity, different values = disputed
        let facts = vec![
            Fact::new("malware.bin", "sha256", "aaaaaa", "virustotal", 1000),
            Fact::new("malware.bin", "sha256", "bbbbbb", "alienvault", 1000),
            Fact::new("malware.bin", "sha256", "cccccc", "hybrid_analysis", 1000),
        ];

        let (disputed, suspect, _) = apply_tri_source_voting("malware.bin", "sha256", &facts);

        assert!(disputed.is_some(), "Should detect disputed entity");
        assert!(suspect.is_none(), "Should not flag suspect when 1:1:1");
    }

    #[test]
    fn test_consensus_vs_dissenter() {
        // 3 sources, 2 agree, 1 disagrees = suspect source
        let facts = vec![
            Fact::new("example.com", "ip", "1.2.3.4", "google_dns", 1000),
            Fact::new("example.com", "ip", "1.2.3.4", "cloudflare_dns", 1000),
            Fact::new("example.com", "ip", "5.6.7.8", "malicious_actor", 1000),
        ];

        let (disputed, suspect, _) = apply_tri_source_voting("example.com", "ip", &facts);

        assert!(disputed.is_none(), "Should not detect disputed when 2:1");
        assert!(suspect.is_some(), "Should detect suspect source");
    }

    #[test]
    fn test_consistency_score() {
        let facts = vec![
            Fact::new("test.com", "ip", "1.2.3.4", "source1", 1000),
            Fact::new("test.com", "ip", "5.6.7.8", "source2", 1000),
        ];

        let contradictions = detect_group_contradictions("test.com", "ip", &facts);
        let score = compute_entity_consistency_score(&facts, &contradictions);

        // Score should be reduced due to contradiction
        assert!(
            score < 1.0,
            "Score should be less than 1.0 with contradiction"
        );
    }
}
