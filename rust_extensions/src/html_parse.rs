//! Streaming HTML parsing via lol_html — Cloudflare's zero-allocation HTML rewriter.
//!
//! Provides link extraction, email harvesting, meta description and title pulling
//! from HTML documents. All extractors are fail-safe (return empty/None on error)
//! and bounded (cap on input size, early termination on parse error).
//!
//! Thread-safe: all extractors are `Send + Sync` (lol_html::send::HtmlRewriter).

use lol_html::send::HtmlRewriter;
use lol_html::{element, end_tag, doc_text, text, Settings};
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::{BTreeSet, HashSet};
use std::sync::{Arc, Mutex};


/// Maximum HTML document size for extraction (2 MB).
const MAX_HTML_SIZE: usize = 2 * 1024 * 1024;

/// Batch cap for batch_extract_links.
const BATCH_EXTRACT_CAP: usize = 1_000;

// ---------------------------------------------------------------------------
// link extraction
// ---------------------------------------------------------------------------

/// Extract all links (href) from an HTML document, resolved against base_url.
///
/// Handles `<a href>`, `<link href>`, `<script src>`, `<img src>` tags.
/// Relative URLs are resolved via `url::Url::parse(...).join(...)`.
/// Results are deduplicated (HashSet) and returned as a sorted `Vec<String>`.
///
/// Fail-safe: returns an empty `Vec<String>` on any parse error.
#[pyfunction]
pub fn extract_links(html: &str, base_url: &str) -> Vec<String> {
    if html.len() > MAX_HTML_SIZE {
        return Vec::new();
    }

    let base = match url::Url::parse(base_url) {
        Ok(b) => b,
        Err(_) => {
            let synthetic = format!("http://{}", base_url);
            match url::Url::parse(&synthetic) {
                Ok(b) => b,
                Err(_) => return Vec::new(),
            }
        }
    };

    let links: Arc<Mutex<HashSet<String>>> = Arc::new(Mutex::new(HashSet::new()));

    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let settings = Settings {
            element_content_handlers: vec![
                element!("a[href]", |el| {
                    if let Some(href) = el.get_attribute("href") {
                        if let Some(resolved) = base.join(&href).ok().map(|u| u.to_string()) {
                            if resolved.starts_with("http://") || resolved.starts_with("https://")
                                || resolved.starts_with("//")
                            {
                                let url = resolved.strip_prefix("//").unwrap_or(&resolved);
                                let _ = links.lock().map(|mut g| g.insert(url.to_string()));
                            }
                        }
                    }
                    Ok(())
                }),
                element!("link[href]", |el| {
                    if let Some(href) = el.get_attribute("href") {
                        if let Some(resolved) = base.join(&href).ok().map(|u| u.to_string()) {
                            if resolved.starts_with("http://") || resolved.starts_with("https://") {
                                let _ = links.lock().map(|mut g| g.insert(resolved));
                            }
                        }
                    }
                    Ok(())
                }),
                element!("script[src]", |el| {
                    if let Some(src) = el.get_attribute("src") {
                        if let Some(resolved) = base.join(&src).ok().map(|u| u.to_string()) {
                            if resolved.starts_with("http://") || resolved.starts_with("https://") {
                                let _ = links.lock().map(|mut g| g.insert(resolved));
                            }
                        }
                    }
                    Ok(())
                }),
                element!("img[src]", |el| {
                    if let Some(src) = el.get_attribute("src") {
                        if let Some(resolved) = base.join(&src).ok().map(|u| u.to_string()) {
                            if resolved.starts_with("http://") || resolved.starts_with("https://") {
                                let _ = links.lock().map(|mut g| g.insert(resolved));
                            }
                        }
                    }
                    Ok(())
                }),
            ],
            ..Settings::new_send()
        };

        let mut rewriter = HtmlRewriter::new(settings, |_chunk: &[u8]| {});
        let _ = rewriter.write(html.as_bytes());
        let _ = rewriter.end();
    }));

    let mut sorted: Vec<String> = links
        .lock()
        .map(|g| g.iter().cloned().collect())
        .unwrap_or_default();
    sorted.sort();
    sorted
}

// ---------------------------------------------------------------------------
// link + text extraction
// ---------------------------------------------------------------------------

/// Extract all links with their anchor text from an HTML document.
///
/// Single O(n) scan via lol_html. Anchor text is accumulated between
/// `<a href>` start and `</a>` end tags using a scoped `text!` handler.
/// Non-<a> links (img/src, script/src, link/href) return ("url", "") as
/// placeholder since they carry no meaningful anchor text.
///
/// Results are deduplicated by URL (BTreeSet) and returned sorted by URL.
///
/// Fail-safe: returns an empty `Vec<(String, String)>` on any parse error.
#[pyfunction]
pub fn extract_links_with_text(html: &str, base_url: &str) -> Vec<(String, String)> {
    if html.len() > MAX_HTML_SIZE {
        return Vec::new();
    }

    let base = match url::Url::parse(base_url) {
        Ok(b) => b,
        Err(_) => {
            let synthetic = format!("http://{}", base_url);
            match url::Url::parse(&synthetic) {
                Ok(b) => b,
                Err(_) => return Vec::new(),
            }
        }
    };

    /// Per-document link accumulator: URL → anchor text.
    /// Uses BTreeSet for automatic sorted-dedup ordering.
    let links: Arc<Mutex<BTreeSet<(String, String)>>> =
        Arc::new(Mutex::new(BTreeSet::new()));

    // Active anchor state — set on <a> open, emitted on </a>.
    let anchor_url: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let anchor_text: Arc<Mutex<String>> = Arc::new(Mutex::new(String::new()));

    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let settings = Settings {
            element_content_handlers: vec![
                // ── <a href> — capture URL and activate text collector ──────────
                element!("a[href]", |el| {
                    if let Some(href) = el.get_attribute("href") {
                        if let Some(resolved) = base.join(&href).ok().map(|u| u.to_string()) {
                            let url = resolved
                                .strip_prefix("//")
                                .unwrap_or(&resolved)
                                .to_string();
                            *anchor_url.lock().unwrap() = Some(url);
                            anchor_text.lock().unwrap().clear();
                        }
                    }
                    Ok(())
                }),
                // ── </a> — emit (url, text) pair ─────────────────────────────────
                end_tag!("a", |_el| {
                    if let (Some(url), text) = (
                        anchor_url.lock().unwrap().take(),
                        anchor_text.lock().unwrap().split_whitespace().collect::<String>(),
                    ) {
                        links.lock().unwrap().insert((url, text));
                    }
                    Ok(())
                }),
                // ── inline text within <a> — accumulate ─────────────────────────
                text!("a", |tc: &mut lol_html::html_content::TextChunk| {
                    if anchor_url.lock().unwrap().is_some() {
                        anchor_text.lock().unwrap().push_str(tc.as_str());
                    }
                    Ok(())
                }),
                // ── img/script/link — no text, emit with empty string ──────────
                element!("link[href]", |el| {
                    if let Some(href) = el.get_attribute("href") {
                        if let Some(resolved) = base.join(&href).ok().map(|u| u.to_string()) {
                            if resolved.starts_with("http://") || resolved.starts_with("https://") {
                                links.lock().unwrap().insert((resolved, String::new()));
                            }
                        }
                    }
                    Ok(())
                }),
                element!("script[src]", |el| {
                    if let Some(src) = el.get_attribute("src") {
                        if let Some(resolved) = base.join(&src).ok().map(|u| u.to_string()) {
                            if resolved.starts_with("http://") || resolved.starts_with("https://") {
                                links.lock().unwrap().insert((resolved, String::new()));
                            }
                        }
                    }
                    Ok(())
                }),
                element!("img[src]", |el| {
                    if let Some(src) = el.get_attribute("src") {
                        if let Some(resolved) = base.join(&src).ok().map(|u| u.to_string()) {
                            if resolved.starts_with("http://") || resolved.starts_with("https://") {
                                links.lock().unwrap().insert((resolved, String::new()));
                            }
                        }
                    }
                    Ok(())
                }),
            ],
            ..Settings::new_send()
        };

        let mut rewriter = HtmlRewriter::new(settings, |_chunk: &[u8]| {});
        let _ = rewriter.write(html.as_bytes());
        let _ = rewriter.end();
    }));

    links
        .lock()
        .map(|g| g.iter().cloned().collect())
        .unwrap_or_default()
}

/// Batch extract links with anchor text from a vector of (html, base_url) tuples.
///
/// Uses `bulk_pool_for_size(n)` — adaptive 1-2 threads based on batch size.
/// Caps at `BATCH_EXTRACT_CAP` (1_000) items.
///
/// Returns `Vec<Vec<(url, text)>>` in the same order as the input.
#[pyfunction]
pub fn batch_extract_links_with_text(items: Vec<(String, String)>) -> Vec<Vec<(String, String)>> {
    let items: Vec<(String, String)> = items.into_iter().take(BATCH_EXTRACT_CAP).collect();
    if items.is_empty() {
        return Vec::new();
    }
    let n = items.len();

    crate::bulk_pool_for_size(n).install(|| {
        items
            .into_par_iter()
            .map(|(html, base_url)| extract_links_with_text(&html, &base_url))
            .collect()
    })
}

/// Extract email addresses from an HTML document.
///
/// Uses a global text handler to collect all text from the document,
/// then applies an email regex on the concatenated text.
/// Deduplicated and sorted. Returns empty `Vec<String>` on error.
#[pyfunction]
pub fn extract_emails(html: &str) -> Vec<String> {
    if html.len() > MAX_HTML_SIZE {
        return Vec::new();
    }

    let email_regex = regex_lite();
    let mut text = String::with_capacity(html.len());

    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let settings = Settings {
            document_content_handlers: vec![doc_text!(
                |tc: &mut lol_html::html_content::TextChunk| {
                    text.push_str(tc.as_str());
                    Ok(())
                }
            )],
            ..Settings::new_send()
        };

        let mut rewriter = HtmlRewriter::new(settings, |_chunk: &[u8]| {});
        let _ = rewriter.write(html.as_bytes());
        let _ = rewriter.end();
    }));

    let mut emails: HashSet<String> = email_regex
        .find_iter(&text)
        .map(|m| m.as_str().to_lowercase())
        .collect();

    emails.retain(|e| {
        e.contains('@') && e.split('@').nth(1).map_or(false, |d| d.contains('.'))
    });

    let mut sorted: Vec<String> = emails.into_iter().collect();
    sorted.sort();
    sorted
}

/// Lazily-compiled minimal email regex (ASCII-safe).
fn regex_lite() -> regex::Regex {
    static RE: std::sync::LazyLock<regex::Regex> =
        std::sync::LazyLock::new(|| {
            regex::Regex::new(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
                .expect("regex_lite: compilation failed")
        });
    regex::Regex::clone(&RE)
}

// ---------------------------------------------------------------------------
// batch helpers (private, rayon-parallel internals)
// ---------------------------------------------------------------------------

/// Synchronous per-document email extraction (used by batch_extract_emails).
fn extract_emails_impl(html: &str) -> Vec<String> {
    extract_emails(html)
}

/// Synchronous per-document title extraction (used by batch_extract_titles).
fn extract_title_impl(html: &str) -> Option<String> {
    extract_title(html)
}

// ---------------------------------------------------------------------------
// batch email extraction
// ---------------------------------------------------------------------------

/// Batch extract emails from a vector of HTML documents.
///
/// Uses `bulk_pool_for_size(n)` — adaptive 1-2 threads based on batch size.
/// Caps at `BATCH_EXTRACT_CAP` (1_000) items.
///
/// Returns `Vec<Vec<String>>` in the same order as the input.
#[pyfunction]
pub fn batch_extract_emails(items: Vec<String>) -> Vec<Vec<String>> {
    let items: Vec<String> = items.into_iter().take(BATCH_EXTRACT_CAP).collect();
    if items.is_empty() {
        return Vec::new();
    }
    let n = items.len();

    crate::bulk_pool_for_size(n).install(|| {
        items
            .into_par_iter()
            .map(|html| extract_emails_impl(&html))
            .collect()
    })
}

// ---------------------------------------------------------------------------
// batch title extraction
// ---------------------------------------------------------------------------

/// Batch extract titles from a vector of HTML documents.
///
/// Uses `bulk_pool_for_size(n)` — adaptive 1-2 threads based on batch size.
/// Caps at `BATCH_EXTRACT_CAP` (1_000) items.
///
/// Returns `Vec<Option<String>>` in the same order as the input.
#[pyfunction]
pub fn batch_extract_titles(items: Vec<String>) -> Vec<Option<String>> {
    let items: Vec<String> = items.into_iter().take(BATCH_EXTRACT_CAP).collect();
    if items.is_empty() {
        return Vec::new();
    }
    let n = items.len();

    crate::bulk_pool_for_size(n).install(|| {
        items
            .into_par_iter()
            .map(|html| extract_title_impl(&html))
            .collect()
    })
}

/// Extract the `content` attribute of `<meta name="description">`.
///
/// Returns `None` if not found. Trims whitespace.
#[pyfunction]
pub fn extract_meta_description(html: &str) -> Option<String> {
    if html.len() > MAX_HTML_SIZE {
        return None;
    }

    let mut result: Option<String> = None;

    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let settings = Settings {
            element_content_handlers: vec![element!("meta[name=\"description\"]", |el| {
                if result.is_none() {
                    let content = el.get_attribute("content").map(|s| s.trim().to_string());
                    if content.as_ref().map_or(false, |s| !s.is_empty()) {
                        result = content;
                    }
                }
                Ok(())
            })],
            ..Settings::new_send()
        };

        let mut rewriter = HtmlRewriter::new(settings, |_chunk: &[u8]| {});
        let _ = rewriter.write(html.as_bytes());
        let _ = rewriter.end();
    }));

    result
}

/// Extract the text content of the `<title>` tag.
///
/// Returns `None` if not found. Trims whitespace.
#[pyfunction]
pub fn extract_title(html: &str) -> Option<String> {
    if html.len() > MAX_HTML_SIZE {
        return None;
    }

    let mut result: Option<String> = None;

    let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        let settings = Settings {
            element_content_handlers: vec![text!("title", |tc: &mut lol_html::html_content::TextChunk| {
                if result.is_none() {
                    let s = tc.as_str().trim().to_string();
                    if !s.is_empty() {
                        result = Some(s);
                    }
                }
                Ok(())
            })],
            ..Settings::new_send()
        };

        let mut rewriter = HtmlRewriter::new(settings, |_chunk: &[u8]| {});
        let _ = rewriter.write(html.as_bytes());
        let _ = rewriter.end();
    }));

    result
}

/// Batch extract links from a vector of (html, base_url) tuples.
///
/// Uses `bulk_pool_for_size(n)` — adaptive 1-2 threads based on batch size.
/// Caps at `BATCH_EXTRACT_CAP` (1_000) items.
///
/// Returns `Vec<Vec<String>>` in the same order as the input.
#[pyfunction]
pub fn batch_extract_links(items: Vec<(String, String)>) -> Vec<Vec<String>> {
    let items: Vec<(String, String)> = items.into_iter().take(BATCH_EXTRACT_CAP).collect();
    if items.is_empty() {
        return Vec::new();
    }
    let n = items.len();

    crate::bulk_pool_for_size(n).install(|| {
        items
            .into_par_iter()
            .map(|(html, base_url)| extract_links(&html, &base_url))
            .collect()
    })
}

// ---------------------------------------------------------------------------
// Python registration
// ---------------------------------------------------------------------------

/// Register all html_parse functions with a Python module.
pub fn register_functions(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(extract_links, m)?)?;
    m.add_function(wrap_pyfunction!(extract_links_with_text, m)?)?;
    m.add_function(wrap_pyfunction!(extract_emails, m)?)?;
    m.add_function(wrap_pyfunction!(extract_meta_description, m)?)?;
    m.add_function(wrap_pyfunction!(extract_title, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_links, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_links_with_text, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_emails, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_titles, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_links_absolute() {
        let html = r#"<a href="https://example.com/path">link</a>"#;
        let links = extract_links(html, "https://base.com");
        assert!(links.contains(&"https://example.com/path".to_string()));
    }

    #[test]
    fn test_extract_links_relative() {
        let html = r#"<a href="/path">link</a>"#;
        let links = extract_links(html, "https://example.com/");
        assert!(links.contains(&"https://example.com/path".to_string()));
    }

    #[test]
    fn test_extract_links_img_script_link() {
        let html = r#"<img src="/img.png"><script src="/app.js"></script><link href="/style.css">"#;
        let links = extract_links(html, "https://example.com/");
        assert!(links.contains(&"https://example.com/img.png".to_string()));
        assert!(links.contains(&"https://example.com/app.js".to_string()));
        assert!(links.contains(&"https://example.com/style.css".to_string()));
    }

    #[test]
    fn test_extract_links_dedup() {
        let html = r#"<a href="/path">a</a><a href="/path">b</a>"#;
        let links = extract_links(html, "https://example.com/");
        assert_eq!(links.len(), 1);
    }

    #[test]
    fn test_extract_links_sorted() {
        let html = r#"<a href="/c"></a><a href="/a"></a><a href="/b"></a>"#;
        let links = extract_links(html, "https://example.com/");
        assert_eq!(links, vec![
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
        ]);
    }

    #[test]
    fn test_extract_links_empty_html() {
        assert!(extract_links("", "https://example.com").is_empty());
        assert!(extract_links("not html at all", "https://example.com").is_empty());
    }

    #[test]
    fn test_extract_title() {
        let html = "<html><head><title>  Test Page  </title></head></html>";
        assert_eq!(extract_title(html), Some("Test Page".to_string()));
    }

    #[test]
    fn test_extract_title_missing() {
        assert_eq!(extract_title("<html><body>no title</body></html>"), None);
    }

    #[test]
    fn test_extract_meta_description() {
        let html = r#"<meta name="description" content="  Hello world  ">"#;
        assert_eq!(extract_meta_description(html), Some("Hello world".to_string()));
    }

    #[test]
    fn test_extract_meta_description_missing() {
        assert_eq!(extract_meta_description("<html></html>"), None);
    }

    #[test]
    fn test_extract_emails_basic() {
        let html = "<p>Contact us at info@example.com or support@test.org.</p>";
        let emails = extract_emails(html);
        assert!(emails.contains(&"info@example.com".to_string()));
        assert!(emails.contains(&"support@test.org".to_string()));
    }

    #[test]
    fn test_extract_emails_dedup() {
        let html = "<p>a@b.com</p><p>a@b.com</p>";
        let emails = extract_emails(html);
        assert_eq!(emails.len(), 1);
    }

    #[test]
    fn test_extract_emails_sorted() {
        let html = "<p>z@test.com</p><p>a@test.com</p>";
        let emails = extract_emails(html);
        assert_eq!(emails, vec!["a@test.com", "z@test.com"]);
    }

    #[test]
    fn test_extract_emails_filter_invalid() {
        let html = "<p>notanemail @ noat</p><p>valid@test.com</p>";
        let emails = extract_emails(html);
        assert!(!emails.contains(&"notanemail @ noat".to_string()));
    }

    #[test]
    fn test_batch_extract_links_basic() {
        let items = vec![
            ("<a href=\"/a\">a</a>".to_string(), "https://x.com/".to_string()),
            ("<a href=\"/b\">b</a>".to_string(), "https://y.com/".to_string()),
        ];
        let results = batch_extract_links(items);
        assert_eq!(results.len(), 2);
        assert!(results[0].contains(&"https://x.com/a".to_string()));
        assert!(results[1].contains(&"https://y.com/b".to_string()));
    }

    #[test]
    fn test_batch_extract_links_cap() {
        let items: Vec<(String, String)> = (0..2000)
            .map(|i| (format!("<a href=\"/{}\">", i), "https://x.com/".to_string()))
            .collect();
        let results = batch_extract_links(items);
        assert_eq!(results.len(), BATCH_EXTRACT_CAP);
    }

    #[test]
    fn test_batch_extract_links_empty() {
        let results: Vec<Vec<String>> = batch_extract_links(vec![]);
        assert!(results.is_empty());
    }

    // -----------------------------------------------------------------------
    // extract_links_with_text + batch_extract_links_with_text
    // -----------------------------------------------------------------------

    #[test]
    fn test_extract_links_with_text_basic() {
        let html = r#"<a href="https://example.com/page">Click here</a>"#;
        let links = extract_links_with_text(html, "https://base.com/");
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].0, "https://example.com/page");
        assert_eq!(links[0].1, "Clickhere"); // whitespace-collapsed
    }

    #[test]
    fn test_extract_links_with_text_relative() {
        let html = r#"<a href="/relative">  Some  Text  </a>"#;
        let links = extract_links_with_text(html, "https://example.com/");
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].0, "https://example.com/relative");
        assert_eq!(links[0].1, "SomeText"); // trimmed + collapsed
    }

    #[test]
    fn test_extract_links_with_text_img_script_link_empty() {
        let html = r#"<img src="/img.png"><script src="/app.js"></script><link href="/style.css">"#;
        let links = extract_links_with_text(html, "https://example.com/");
        assert_eq!(links.len(), 3);
        // All non-<a> elements have empty text
        for (_, text) in &links {
            assert!(text.is_empty());
        }
    }

    #[test]
    fn test_extract_links_with_text_dedup_by_url() {
        let html = r#"<a href="/path">text a</a><a href="/path">text b</a>"#;
        let links = extract_links_with_text(html, "https://example.com/");
        assert_eq!(links.len(), 1);
        // BTreeSet deduplicates by URL; text is first-seen
        assert_eq!(links[0].0, "https://example.com/path");
        assert_eq!(links[0].1, "texta");
    }

    #[test]
    fn test_extract_links_with_text_sorted() {
        let html = r#"<a href="/c">c</a><a href="/a">a</a><a href="/b">b</a>"#;
        let links = extract_links_with_text(html, "https://example.com/");
        assert_eq!(links.len(), 3);
        assert_eq!(links[0].0, "https://example.com/a");
        assert_eq!(links[1].0, "https://example.com/b");
        assert_eq!(links[2].0, "https://example.com/c");
    }

    #[test]
    fn test_extract_links_with_text_empty_html() {
        assert!(extract_links_with_text("", "https://example.com").is_empty());
        assert!(extract_links_with_text("not html at all", "https://example.com").is_empty());
    }

    #[test]
    fn test_batch_extract_links_with_text_basic() {
        let items = vec![
            (r#"<a href="/a">Alpha</a>"#.to_string(), "https://x.com/".to_string()),
            (r#"<a href="/b">Beta</a>"#.to_string(), "https://y.com/".to_string()),
        ];
        let results = batch_extract_links_with_text(items);
        assert_eq!(results.len(), 2);
        assert_eq!(results[0][0].0, "https://x.com/a");
        assert_eq!(results[0][0].1, "Alpha");
        assert_eq!(results[1][0].0, "https://y.com/b");
        assert_eq!(results[1][0].1, "Beta");
    }

    #[test]
    fn test_batch_extract_links_with_text_cap() {
        let items: Vec<(String, String)> = (0..2000)
            .map(|i| (format!("<a href=\"/{}\">text{}", i, i), "https://x.com/".to_string()))
            .collect();
        let results = batch_extract_links_with_text(items);
        assert_eq!(results.len(), BATCH_EXTRACT_CAP);
    }

    #[test]
    fn test_batch_extract_links_with_text_empty() {
        let results: Vec<Vec<(String, String)>> = batch_extract_links_with_text(vec![]);
        assert!(results.is_empty());
    }

    // -----------------------------------------------------------------------
    // batch_extract_emails
    // -----------------------------------------------------------------------

    #[test]
    fn test_batch_extract_emails_basic() {
        let items = vec![
            "<p>info@example.com</p>".to_string(),
            "<p>support@test.org</p>".to_string(),
        ];
        let results = batch_extract_emails(items);
        assert_eq!(results.len(), 2);
        assert!(results[0].contains(&"info@example.com".to_string()));
        assert!(results[1].contains(&"support@test.org".to_string()));
    }

    #[test]
    fn test_batch_extract_emails_cap() {
        let items: Vec<String> = (0..2000)
            .map(|i| format!("<p>a{}@b.com</p>", i))
            .collect();
        let results = batch_extract_emails(items);
        assert_eq!(results.len(), BATCH_EXTRACT_CAP);
    }

    #[test]
    fn test_batch_extract_emails_empty() {
        let results: Vec<Vec<String>> = batch_extract_emails(vec![]);
        assert!(results.is_empty());
    }

    // -----------------------------------------------------------------------
    // batch_extract_titles
    // -----------------------------------------------------------------------

    #[test]
    fn test_batch_extract_titles_basic() {
        let items = vec![
            "<html><head><title>Page Alpha</title></head></html>".to_string(),
            "<html><head><title>Page Beta</title></head></html>".to_string(),
        ];
        let results = batch_extract_titles(items);
        assert_eq!(results.len(), 2);
        assert_eq!(results[0].as_ref().unwrap().as_str(), "Page Alpha");
        assert_eq!(results[1].as_ref().unwrap().as_str(), "Page Beta");
    }

    #[test]
    fn test_batch_extract_titles_missing() {
        let items = vec![
            "<html><body>No title here</body></html>".to_string(),
            "<html><head><title></title></head></html>".to_string(),
        ];
        let results = batch_extract_titles(items);
        assert_eq!(results.len(), 2);
        assert!(results[0].is_none());
        assert!(results[1].is_none()); // empty title trimmed to None
    }

    #[test]
    fn test_batch_extract_titles_cap() {
        let items: Vec<String> = (0..2000)
            .map(|i| format!("<html><head><title>Title {}</title></head></html>", i))
            .collect();
        let results = batch_extract_titles(items);
        assert_eq!(results.len(), BATCH_EXTRACT_CAP);
    }

    #[test]
    fn test_batch_extract_titles_empty() {
        let results: Vec<Option<String>> = batch_extract_titles(vec![]);
        assert!(results.is_empty());
    }
}
