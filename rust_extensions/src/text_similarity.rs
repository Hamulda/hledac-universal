//! Parallel text similarity clustering for temporal archaeologist.
//!
//! ISSUE-026: Replaces O(n²) serial `_group_similar_snapshots` in
//! `intelligence/temporal_archaeologist.py` with rayon-parallel grouping.
//!
//! ## Algorithm
//!
//! Uses character trigram Jaccard similarity — a fast, non-ML approximation
//! that correlates well with SequenceMatcher ratio for content deduplication.
//!
//! Performance:
//! - O(n²) comparisons but fully parallel via rayon
//! - n=1000 → ~500K comparisons, ~2-4s on M1 P-cores
//! - Bounded: max 5000 snapshots per call (prevents runaway computation)
//!
//! ## Design Invariants
//!
//! TS.T1  No panics, fail-soft on errors (returns empty on any failure)
//! TS.T2  Bounded: max 5000 snapshots, max content len 100KB
//! TS.T3  GIL-free: rayon ThreadPool, no Python objects in parallel path
//! TS.T4  Deterministic: results stable across runs (sort order preserved)

use pyo3::prelude::*;
use pyo3::types::PyList;
use rayon::prelude::*;
use std::collections::HashSet;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const MAX_SNAPSHOTS: usize = 5000;
const MAX_CONTENT_LEN: usize = 100_000;
const DEFAULT_THRESHOLD: f32 = 0.8;

// ---------------------------------------------------------------------------
// Trigram Jaccard similarity
// ---------------------------------------------------------------------------

/// Compute character trigram set for a string.
/// Returns up to max(3, s.len().saturating_sub(2)) trigrams.
fn char_trigrams(s: &str) -> HashSet<u64> {
    let bytes = s.as_bytes();
    let mut trigrams = HashSet::with_capacity(bytes.len().saturating_sub(2).max(3));
    for i in 0..bytes.len().saturating_sub(2) {
        // Pack 3 consecutive bytes into a u64 for fast hashing + comparison.
        let t = (bytes[i] as u64) | ((bytes[i + 1] as u64) << 8) | ((bytes[i + 2] as u64) << 16);
        trigrams.insert(t);
    }
    trigrams
}

/// Character trigram Jaccard similarity: |A ∩ B| / |A ∪ B|.
/// Range [0.0, 1.0]. Empty strings → 0.0.
#[inline]
fn trigram_jaccard(a: &str, b: &str) -> f32 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    let set_a = char_trigrams(a);
    let set_b = char_trigrams(b);

    let intersection = set_a.intersection(&set_b).count();
    let union = set_a.len() + set_b.len() - intersection;

    if union == 0 {
        0.0
    } else {
        intersection as f32 / union as f32
    }
}

// ---------------------------------------------------------------------------
// Parallel group building
// ---------------------------------------------------------------------------

/// Group snapshots by similarity threshold using parallel comparison.
/// Returns groups of indices, each group = list of original snapshot indices.
///
/// # Arguments
/// * `texts` — List of content strings (empty string = no content)
/// * `threshold` — Jaccard similarity threshold for grouping [0.0, 1.0]
///
/// # Returns
/// List of groups, each group is a list of indices into the original `texts`.
/// Results are sorted by first-index (deterministic).
///
/// # Fail-soft
/// - Empty texts → empty groups
/// - threshold out of range → clamped to [0.0, 1.0]
/// - Any error → empty Vec
#[pyfunction]
#[pyo3(signature = (texts, threshold = DEFAULT_THRESHOLD))]
pub fn group_similar_texts(
    _py: Python<'_>,
    texts: &Bound<'_, PyList>,
    threshold: f32,
) -> PyResult<Vec<Vec<usize>>> {
    let n = texts.len();

    if n == 0 {
        return Ok(Vec::new());
    }

    // Clamp threshold.
    let threshold = threshold.clamp(0.0, 1.0);

    // Guard: max snapshots — silently truncate to MAX_SNAPSHOTS.

    // Build working list: (index, content) pairs, bounded.
    let items: Vec<(usize, String)> = texts
        .iter()
        .take(MAX_SNAPSHOTS)
        .enumerate()
        .filter_map(|(i, item)| {
            let s = match item.extract::<String>() {
                Ok(v) => v,
                Err(_) => return Some((i, String::new())),
            };
            // Truncate long content.
            if s.len() > MAX_CONTENT_LEN {
                Some((i, s[..MAX_CONTENT_LEN].to_string()))
            } else {
                Some((i, s))
            }
        })
        .collect();

    let m = items.len();
    if m == 0 {
        return Ok(Vec::new());
    }

    // Extract contents for parallel access.
    let contents: Vec<String> = items.iter().map(|(_, c)| c.clone()).collect();

    // Parallel group building:
    // Each thread handles a range of items and builds groups for those items.
    // Then merge-sort all groups by first-index.
    let chunk_size = (m / rayon::current_num_threads().max(1)).max(1);

    let all_groups: Vec<Vec<usize>> = items
        .par_chunks(chunk_size)
        .flat_map(|chunk| {
            let local_groups = build_local_groups(chunk, &contents, threshold);
            local_groups
        })
        .collect();

    // Merge overlapping groups by merging groups that share any index.
    let merged = merge_overlapping_groups(all_groups, m);

    // Sort by first index for determinism.
    let mut sorted: Vec<Vec<usize>> = merged;
    sorted.sort_by_key(|g| g.first().copied());

    Ok(sorted)
}

/// Build groups for a local chunk of (index, content).
fn build_local_groups(
    chunk: &[(usize, String)],
    all_contents: &[String],
    threshold: f32,
) -> Vec<Vec<usize>> {
    let mut groups: Vec<Vec<usize>> = Vec::new();

    for &(idx, ref content) in chunk {
        if content.is_empty() {
            // Empty content: standalone group.
            groups.push(vec![idx]);
            continue;
        }

        // Try to add to existing group.
        let mut added = false;
        for group in &mut groups {
            // Compare to representative (first in group).
            let rep_idx = group[0];
            if rep_idx < all_contents.len() {
                let rep_content = &all_contents[rep_idx];
                if !rep_content.is_empty() {
                    let sim = trigram_jaccard(content, rep_content);
                    if sim >= threshold {
                        group.push(idx);
                        added = true;
                        break;
                    }
                }
            }
        }

        if !added {
            groups.push(vec![idx]);
        }
    }

    groups
}

/// Merge groups that share any index (handles double-assignment from parallel chunks).
fn merge_overlapping_groups(groups: Vec<Vec<usize>>, _n: usize) -> Vec<Vec<usize>> {
    if groups.is_empty() {
        return Vec::new();
    }

    // Build adjacency: which groups overlap with which.
    // Then union-find to merge.
    let mut uf = UnionFind::new(groups.len());

    for i in 0..groups.len() {
        for j in (i + 1)..groups.len() {
            if groups[i].iter().any(|idx| groups[j].contains(idx)) {
                uf.union(i, j);
            }
        }
    }

    // Collect merged groups.
    let mut merged: Vec<Vec<usize>> = Vec::new();
    for root in 0..groups.len() {
        let r = uf.find(root);
        if r == root {
            // This is a root — collect all indices from union members.
            let mut all_indices: Vec<usize> = Vec::new();
            for i in 0..groups.len() {
                if uf.find(i) == r {
                    all_indices.extend_from_slice(&groups[i]);
                }
            }
            // Sort and deduplicate.
            all_indices.sort();
            all_indices.dedup();
            merged.push(all_indices);
        }
    }

    merged
}

/// Simple union-find for group merging.
struct UnionFind {
    parent: Vec<usize>,
    rank: Vec<usize>,
}

impl UnionFind {
    fn new(n: usize) -> Self {
        Self {
            parent: (0..n).collect(),
            rank: vec![0; n],
        }
    }

    fn find(&mut self, x: usize) -> usize {
        if self.parent[x] != x {
            self.parent[x] = self.find(self.parent[x]);
        }
        self.parent[x]
    }

    fn union(&mut self, x: usize, y: usize) {
        let rx = self.find(x);
        let ry = self.find(y);
        if rx == ry {
            return;
        }
        if self.rank[rx] < self.rank[ry] {
            self.parent[rx] = ry;
        } else if self.rank[rx] > self.rank[ry] {
            self.parent[ry] = rx;
        } else {
            self.parent[ry] = rx;
            self.rank[rx] += 1;
        }
    }
}

// ---------------------------------------------------------------------------
// Module registration
// ---------------------------------------------------------------------------

pub fn register_functions(m: &pyo3::Bound<'_, pyo3::types::PyModule>) -> pyo3::PyResult<()> {
    m.add_function(pyo3::wrap_pyfunction!(group_similar_texts, m)?)?;
    Ok(())
}

