//! Streaming HTML parsing via lol_html — Cloudflare's zero-allocation HTML rewriter.
//!
//! Provides link extraction, email harvesting, meta description and title pulling
//! from HTML documents. All extractors are fail-safe (return empty/None on error)
//! and bounded (cap on input size, early termination on parse error).
//!
//! Thread-safe: all extractors are `Send + Sync` (lol_html::send::HtmlRewriter).

use lol_html::send::HtmlRewriter;
use lol_html::{element, doc_text, text, Settings};
use pyo3::prelude::*;
use rayon::prelude::*;
use std::collections::HashSet;
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
    static RE: once_cell::sync::Lazy<regex::Regex> =
        once_cell::sync::Lazy::new(|| {
            regex::Regex::new(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
                .expect("regex_lite: compilation failed")
        });
    regex::Regex::clone(&RE)
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
/// Uses `rayon::par_iter` via the bounded `crate::bulk_pool()` (4 workers, 2 MiB stacks).
/// Caps at `BATCH_EXTRACT_CAP` (1_000) items.
///
/// Returns `Vec<Vec<String>>` in the same order as the input.
#[pyfunction]
pub fn batch_extract_links(items: Vec<(String, String)>) -> Vec<Vec<String>> {
    let items: Vec<(String, String)> = items.into_iter().take(BATCH_EXTRACT_CAP).collect();
    if items.is_empty() {
        return Vec::new();
    }

    crate::bulk_pool().install(|| {
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
    m.add_function(wrap_pyfunction!(extract_emails, m)?)?;
    m.add_function(wrap_pyfunction!(extract_meta_description, m)?)?;
    m.add_function(wrap_pyfunction!(extract_title, m)?)?;
    m.add_function(wrap_pyfunction!(batch_extract_links, m)?)?;
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
}
