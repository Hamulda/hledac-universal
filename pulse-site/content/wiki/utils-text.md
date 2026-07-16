+++
title = "utils/text/"
weight = 40
description = "<think> Let me analyze the `utils/text` module based on the structural context provided.  ## Key Observations:  1. **Purpose**: This module provides text analysis utilities for detecting:  - Unicode a..."

[extra]
tier = 2
file_count = 4
total_lines = 1348
languages = "Python"
parent_path = "utils"
+++

<think>
Let me analyze the `utils/text` module based on the structural context provided.

## Key Observations:

1. **Purpose**: This module provides text analysis utilities for detecting:
 - Unicode attacks (bidirectional text, homoglyphs, zero-width characters, normalization anomalies)
 - Encoded data (Base64, Base32, Base85, Hex, URL encoding)
 - Hash algorithms (300+ algorithms supported)

2. **Structure**:
 - 4 Python files
 - 1348 total lines (337 avg per file)
 - No outgoing dependencies
 - No incoming dependencies
 - Tier 2 module

3. **Key Components**:
 - `UnicodeAttackAnalyzer` - detects Unicode attack vectors
 - `BaseEncodingDetector` - detects various encodings in text
 - `HashIdentifier` - identifies 300+ hash algorithms

4. **Architectural Role**: This appears to be a leaf/utility module with no dependencies and no dependents in the current codebase. However, it has broad functionality that could be used by many modules.

5. **Fan-out/Fan-in**: Zero incoming dependencies detected, zero outgoing dependencies. This is unusual - it may be a standalone utility module.

Let me write the overview following the rules:
- First sentence must state what the module DOES or its PURPOSE
- Focus on purpose, responsibilities, architectural role
- Note complexity: file count, line count, symbol density
- 4-8 sentences
- Vary sentence structure
- Do NOT list file names or class names unless central
- No speculation

The module provides text analysis capabilities for security-related detection: Unicode attack patterns, encoding patterns, and hash algorithm identification. It's a self-contained utility module with no external dependencies.
</think>

This module provides security-focused text analysis capabilities, detecting Unicode-based attack vectors (including homoglyphs, bidirectional text, zero-width characters, and normalization anomalies), identifying encoded content (Base64, Base32, Base85, hexadecimal, and URL encoding with support for nested encoding chains), and recognizing over 300 hash algorithm patterns with integration points for hashcat and John the Ripper. The module operates entirely independently, requiring no external dependencies while exposing 52 functions and 15 classes across approximately 1,350 lines of Python code—a dense, self-contained toolkit for parsing and validating text-based security signals. Despite its zero detected incoming dependencies in the current codebase, the module's broad detection capabilities suggest it serves as a foundational utility for security analysis tasks such as scanning documents, analyzing cryptographic patterns, and processing runtime workloads. Streaming file analysis is supported for both Unicode and encoding detection, enabling processing of large files at optimized throughput, while statistical validation (including Shannon entropy calculation) helps reduce false positives in detection results.

## Structure

| Language | Files |
|---|---|
| Python | 4 |

### Largest Files

- `unicode_analyzer.py` (469 lines)
- `encoding_detector.py` (443 lines)
- `hash_identifier.py` (325 lines)
- `__init__.py` (111 lines)


## Dependencies

No outgoing dependencies detected.

## Dependents

No incoming dependencies detected.

## Key Symbols

<p><strong>Key definitions:</strong></p>
<ul>
<li>
<p><code>identify</code> (Function) in hash_identifier.py — referenced in 11 files</p>
<details><summary>Identify hash algorithm from hash string.</summary>
<div class="doc-comment">
<p>Identify hash algorithm from hash string.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string to identify</p>
<p></p>
<p>Returns:</p>
<p>List of probable hash algorithms with confidence scores</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: bench_f214_python314_runtime.py, cryptographic_intelligence.py, document_intelligence.py, dspy_optimizer.py, dspy_programs.py +5 more</li></ul>
</li>
<li>
<p><code>analyze_file</code> (Function) in unicode_analyzer.py — referenced in 5 files</p>
<details><summary>Stream-analyze a file for Unicode attacks.</summary>
<div class="doc-comment">
<p>Stream-analyze a file for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to the file to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: check_msgspec_migration.py, digital_ghost_detector.py, enrichment_service.py, migrate_dataclass_to_msgspec.py</li></ul>
</li>
<li>
<p><code>HashIdentifier</code> (Class) in hash_identifier.py — referenced in 4 files</p>
<details><summary>Identifies hash algorithms from hash strings.</summary>
<div class="doc-comment">
<p>Identifies hash algorithms from hash strings.</p>
<p></p>
<p>Supports 300+ hash algorithms with pattern, length, and charset matching.</p>
<p>Integrates with hashcat and John the Ripper.</p>
<p></p>
<p>Example:</p>
<p>identifier = HashIdentifier()</p>
<p>matches = await identifier.identify("5d41402abc4b2a76b9719d911017c592")</p>
<p>for match in matches:</p>
<p>print(f"{match.algorithm}: {match.confidence}")</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py, bench_f214_python314_runtime.py, profile_f214_runtime_workloads.py</li></ul>
</li>
<li>
<p><code>identify_in_file</code> (Function) in hash_identifier.py — referenced in 3 files</p>
<details><summary>Scan file for hash patterns.</summary>
<div class="doc-comment">
<p>Scan file for hash patterns.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to file to scan</p>
<p></p>
<p>Returns:</p>
<p>List of hash findings</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: bench_f214_python314_runtime.py, profile_f214_runtime_workloads.py</li></ul>
</li>
<li>
<p><code>BaseEncodingDetector</code> (Class) in encoding_detector.py — referenced in 2 files</p>
<details><summary>Detects various base encodings in text.</summary>
<div class="doc-comment">
<p>Detects various base encodings in text.</p>
<p></p>
<p>Supports Base64, Base32, Base85, Hexadecimal, and URL encoding.</p>
<p>Includes statistical validation and nested encoding detection.</p>
<p></p>
<p>Example:</p>
<p>detector = BaseEncodingDetector()</p>
<p>text = "Here is encoded data: SGVsbG8gV29ybGQh"</p>
<p>findings = await detector.detect_text(text)</p>
<p>for finding in findings:</p>
<p>print(f"Found {finding.encoding_type} at {finding.position}")</p>
</div>
</details>
<ul><li class="ref-list">Referenced by: __init__.py</li></ul>
</li>
</ul>

<details><summary><strong>Function</strong> (52)</summary>
<ul>
<li><code>analyze_file</code> (unicode_analyzer.py)
<details><summary>Stream-analyze a file for Unicode attacks.</summary>
<div class="doc-comment">
<p>Stream-analyze a file for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to the file to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>_analyze_nested</code> (encoding_detector.py)
<details><summary>Analyze finding for nested encodings.</summary>
<div class="doc-comment">
<p>Analyze finding for nested encodings.</p>
<p></p>
<p>Args:</p>
<p>finding: The encoding finding to analyze</p>
<p></p>
<p>Returns:</p>
<p>Optional encoding chain if nested encodings found</p>
</div>
</details>
</li>
<li><code>identify</code> (hash_identifier.py)
<details><summary>Identify hash algorithm from hash string.</summary>
<div class="doc-comment">
<p>Identify hash algorithm from hash string.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string to identify</p>
<p></p>
<p>Returns:</p>
<p>List of probable hash algorithms with confidence scores</p>
</div>
</details>
</li>
<li><code>identify_in_file</code> (hash_identifier.py)
<details><summary>Scan file for hash patterns.</summary>
<div class="doc-comment">
<p>Scan file for hash patterns.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to file to scan</p>
<p></p>
<p>Returns:</p>
<p>List of hash findings</p>
</div>
</details>
</li>
<li><code>_detect_base64</code> (encoding_detector.py)
<details><summary>Detect Base64 encoded strings.</summary>
<div class="doc-comment">
<p>Detect Base64 encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of Base64 findings</p>
</div>
</details>
</li>
<li><code>detect_file</code> (encoding_detector.py)
<details><summary>Stream-process large file for encoding detection.</summary>
<div class="doc-comment">
<p>Stream-process large file for encoding detection.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to text file</p>
<p></p>
<p>Returns:</p>
<p>List of encoding findings</p>
</div>
</details>
</li>
<li><code>_detect_bidi_attacks</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect bidirectional text attacks in text - optimized version.</span></li>
<li><code>compute_skeleton_hash</code> (unicode_analyzer.py)
<details><summary>Compute UTS #39 skeleton hash for confusables detection.</summary>
<div class="doc-comment">
<p>Compute UTS #39 skeleton hash for confusables detection.</p>
<p></p>
<p>Applies:</p>
<p>- NFD normalization</p>
<p>- Basic confusable mapping (using loaded mappings if available)</p>
<p>- Re-NFD normalization</p>
<p>- Returns sha256(skeleton)[:16]</p>
<p></p>
<p>This is used for:</p>
<p>- Spoof network clustering (same skeleton = possible confusables)</p>
<p>- Internal signal only (skeleton text is NOT stored)</p>
<p></p>
<p>Args:</p>
<p>text: Input text (typically hostname or URL segment)</p>
<p></p>
<p>Returns:</p>
<p>16-char hex digest of skeleton hash</p>
</div>
</details>
</li>
<li><code>_detect_hex</code> (encoding_detector.py)
<details><summary>Detect hexadecimal encoded strings.</summary>
<div class="doc-comment">
<p>Detect hexadecimal encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of hex findings</p>
</div>
</details>
</li>
<li><code>_detect_base32</code> (encoding_detector.py)
<details><summary>Detect Base32 encoded strings.</summary>
<div class="doc-comment">
<p>Detect Base32 encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of Base32 findings</p>
</div>
</details>
</li>
<li><code>analyze_text</code> (unicode_analyzer.py)
<details><summary>Analyze text for Unicode attacks.</summary>
<div class="doc-comment">
<p>Analyze text for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>text: The text to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>_detect_base85</code> (encoding_detector.py)
<details><summary>Detect Base85/Ascii85 encoded strings.</summary>
<div class="doc-comment">
<p>Detect Base85/Ascii85 encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of Base85 findings</p>
</div>
</details>
</li>
<li><code>_detect_url_encoding</code> (encoding_detector.py)
<details><summary>Detect URL/percent-encoded strings.</summary>
<div class="doc-comment">
<p>Detect URL/percent-encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of URL encoding findings</p>
</div>
</details>
</li>
<li><code>_calculate_risk_score</code> (unicode_analyzer.py)
<details><summary>Calculate overall risk score based on findings.</summary>
<div class="doc-comment">
<p>Calculate overall risk score based on findings.</p>
<p></p>
<p>Returns:</p>
<p>Risk score from 0.0 (no risk) to 100.0 (critical)</p>
</div>
</details>
</li>
<li><code>detect_mixed_script</code> (unicode_analyzer.py)
<details><summary>Detect mixed-script usage in text (potential spoofing indicator).</summary>
<div class="doc-comment">
<p>Detect mixed-script usage in text (potential spoofing indicator).</p>
<p></p>
<p>Args:</p>
<p>text: Input text to check</p>
<p></p>
<p>Returns:</p>
<p>True if mixed scripts detected</p>
</div>
</details>
</li>
<li><code>_detect_normalization_anomalies</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect Unicode normalization anomalies in text - optimized version.</span></li>
<li><code>detect_text</code> (encoding_detector.py)
<details><summary>Detect encodings in text.</summary>
<div class="doc-comment">
<p>Detect encodings in text.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of encoding findings</p>
</div>
</details>
</li>
<li><code>_extract_salt</code> (hash_identifier.py)
<details><summary>Extract salt from hash:salt or salt:hash format.</summary>
<div class="doc-comment">
<p>Extract salt from hash:salt or salt:hash format.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string potentially containing salt</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (hash_part, salt_part)</p>
</div>
</details>
</li>
<li><code>_match_by_charset</code> (hash_identifier.py)
<details><summary>Match hash by charset.</summary>
<div class="doc-comment">
<p>Match hash by charset.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string</p>
<p></p>
<p>Returns:</p>
<p>List of matching algorithms</p>
</div>
</details>
</li>
<li><code>_calculate_entropy</code> (encoding_detector.py)
<details><summary>Calculate Shannon entropy of data.</summary>
<div class="doc-comment">
<p>Calculate Shannon entropy of data.</p>
<p></p>
<p>Args:</p>
<p>data: Binary data to analyze</p>
<p></p>
<p>Returns:</p>
<p>Entropy in bits per byte</p>
</div>
</details>
</li>
<li><code>_get_preview</code> (encoding_detector.py)
<details><summary>Get a preview of decoded content.</summary>
<div class="doc-comment">
<p>Get a preview of decoded content.</p>
<p></p>
<p>Args:</p>
<p>data: Binary data</p>
<p>max_length: Maximum preview length</p>
<p></p>
<p>Returns:</p>
<p>String preview of content</p>
</div>
</details>
</li>
<li><code>_detect_charset</code> (hash_identifier.py)
<details><summary>Detect the character set of a hash string.</summary>
<div class="doc-comment">
<p>Detect the character set of a hash string.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string to analyze</p>
<p></p>
<p>Returns:</p>
<p>Character set type (hex, base64, alphanumeric, mixed)</p>
</div>
</details>
</li>
<li><code>identify_batch</code> (hash_identifier.py)
<details><summary>Identify multiple hashes in batch.</summary>
<div class="doc-comment">
<p>Identify multiple hashes in batch.</p>
<p></p>
<p>Args:</p>
<p>hashes: List of hash strings</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping hash strings to matches</p>
</div>
</details>
</li>
<li><code>__exit__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager exit.</span></li>
<li><code>create_unicode_analyzer</code> (unicode_analyzer.py)
<details><summary>Factory function to create a Unicode attack analyzer.</summary>
<div class="doc-comment">
<p>Factory function to create a Unicode attack analyzer.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration for the analyzer</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAttackAnalyzer instance or None if creation fails</p>
</div>
</details>
</li>
<li><code>_match_by_pattern</code> (hash_identifier.py)
<details><summary>Match hash by pattern (e.g., $1$, $2a$).</summary>
<div class="doc-comment">
<p>Match hash by pattern (e.g., $1$, $2a$).</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string</p>
<p></p>
<p>Returns:</p>
<p>List of (algorithm, pattern) tuples</p>
</div>
</details>
</li>
<li><code>initialize</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the analyzer by loading confusable mappings.</span></li>
<li><code>_load_confusable_mappings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Load confusable character mappings - optimized version.</span></li>
<li><code>create_and_initialize_unicode_analyzer</code> (unicode_analyzer.py)
<details><summary>Factory function to create and initialize a Unicode attack analyzer.</summary>
<div class="doc-comment">
<p>Factory function to create and initialize a Unicode attack analyzer.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration for the analyzer</p>
<p></p>
<p>Returns:</p>
<p>Initialized UnicodeAttackAnalyzer instance or None if creation fails</p>
</div>
</details>
</li>
<li><code>_match_by_length</code> (hash_identifier.py)
<details><summary>Match hash by length.</summary>
<div class="doc-comment">
<p>Match hash by length.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string</p>
<p></p>
<p>Returns:</p>
<p>List of matching algorithms</p>
</div>
</details>
</li>
<li><code>_detect_homoglyphs</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect homoglyph/confusable characters in text - optimized version.</span></li>
<li><code>_detect_zero_width</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect zero-width characters in text - optimized version.</span></li>
<li><code>detect_encodings</code> (encoding_detector.py)
<details><summary>Convenience function to detect encodings in text.</summary>
<div class="doc-comment">
<p>Convenience function to detect encodings in text.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p>config: Optional configuration</p>
<p></p>
<p>Returns:</p>
<p>List of encoding findings</p>
</div>
</details>
</li>
<li><code>_is_printable</code> (encoding_detector.py)
<details><summary>Check if data is printable ASCII.</summary>
<div class="doc-comment">
<p>Check if data is printable ASCII.</p>
<p></p>
<p>Args:</p>
<p>data: Binary data to check</p>
<p></p>
<p>Returns:</p>
<p>True if all bytes are printable ASCII</p>
</div>
</details>
</li>
<li><code>create_encoding_detector</code> (encoding_detector.py)
<details><summary>Create a configured BaseEncodingDetector instance.</summary>
<div class="doc-comment">
<p>Create a configured BaseEncodingDetector instance.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration</p>
<p></p>
<p>Returns:</p>
<p>Configured BaseEncodingDetector instance</p>
</div>
</details>
</li>
<li><code>_get_hashcat_mode</code> (hash_identifier.py)
<details><summary>Get hashcat mode for algorithm.</summary>
<div class="doc-comment">
<p>Get hashcat mode for algorithm.</p>
<p></p>
<p>Args:</p>
<p>algorithm: Algorithm name</p>
<p></p>
<p>Returns:</p>
<p>Hashcat mode number or None</p>
</div>
</details>
</li>
<li><code>_get_john_format</code> (hash_identifier.py)
<details><summary>Get John the Ripper format for algorithm.</summary>
<div class="doc-comment">
<p>Get John the Ripper format for algorithm.</p>
<p></p>
<p>Args:</p>
<p>algorithm: Algorithm name</p>
<p></p>
<p>Returns:</p>
<p>John format string or None</p>
</div>
</details>
</li>
<li><code>create_hash_identifier</code> (hash_identifier.py)
<details><summary>Create a configured HashIdentifier instance.</summary>
<div class="doc-comment">
<p>Create a configured HashIdentifier instance.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration</p>
<p></p>
<p>Returns:</p>
<p>Configured HashIdentifier instance</p>
</div>
</details>
</li>
<li><code>__init__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the Unicode attack analyzer.</span></li>
<li><code>cleanup</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Clean up resources and free memory.</span></li>
<li><code>__init__</code> (encoding_detector.py)
<details><summary>Initialize the encoding detector.</summary>
<div class="doc-comment">
<p>Initialize the encoding detector.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration object</p>
</div>
</details>
</li>
<li><code>__init__</code> (hash_identifier.py)
<details><summary>Initialize the hash identifier.</summary>
<div class="doc-comment">
<p>Initialize the hash identifier.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration object</p>
</div>
</details>
</li>
<li><code>_get_context</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Extract context around a position in text.</span></li>
<li><code>get_stats</code> (encoding_detector.py)
<details><summary>Get detection statistics.</summary>
<div class="doc-comment">
<p>Get detection statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of detection statistics</p>
</div>
</details>
</li>
<li><code>get_stats</code> (hash_identifier.py)
<details><summary>Get identification statistics.</summary>
<div class="doc-comment">
<p>Get identification statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of statistics</p>
</div>
</details>
</li>
<li><code>reset_stats</code> (encoding_detector.py) — <span class="doc-comment-inline">Reset detection statistics.</span></li>
<li><code>reset_stats</code> (hash_identifier.py) — <span class="doc-comment-inline">Reset statistics.</span></li>
<li><code>identify_hash</code> (hash_identifier.py) — <span class="doc-comment-inline">Convenience function to identify a hash.</span></li>
<li><code>has_findings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Check if any findings were detected.</span></li>
<li><code>get_finding_count</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get total number of findings.</span></li>
<li><code>get_summary</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get summary of analysis results.</span></li>
<li><code>__enter__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager entry.</span></li>
</ul>
</details>

<details><summary><strong>Class</strong> (15)</summary>
<ul>
<li><code>UnicodeAttackAnalyzer</code> (unicode_analyzer.py)
<details><summary>High-speed Unicode attack surface analyzer.</summary>
<div class="doc-comment">
<p>High-speed Unicode attack surface analyzer.</p>
<p></p>
<p>Detects various Unicode-based attacks including zero-width characters,</p>
<p>homoglyph substitution, bidirectional text attacks, and normalization anomalies.</p>
<p>Optimized for 100+ MB/s processing speed.</p>
</div>
</details>
</li>
<li><code>BaseEncodingDetector</code> (encoding_detector.py)
<details><summary>Detects various base encodings in text.</summary>
<div class="doc-comment">
<p>Detects various base encodings in text.</p>
<p></p>
<p>Supports Base64, Base32, Base85, Hexadecimal, and URL encoding.</p>
<p>Includes statistical validation and nested encoding detection.</p>
<p></p>
<p>Example:</p>
<p>detector = BaseEncodingDetector()</p>
<p>text = "Here is encoded data: SGVsbG8gV29ybGQh"</p>
<p>findings = await detector.detect_text(text)</p>
<p>for finding in findings:</p>
<p>print(f"Found {finding.encoding_type} at {finding.position}")</p>
</div>
</details>
</li>
<li><code>HashIdentifier</code> (hash_identifier.py)
<details><summary>Identifies hash algorithms from hash strings.</summary>
<div class="doc-comment">
<p>Identifies hash algorithms from hash strings.</p>
<p></p>
<p>Supports 300+ hash algorithms with pattern, length, and charset matching.</p>
<p>Integrates with hashcat and John the Ripper.</p>
<p></p>
<p>Example:</p>
<p>identifier = HashIdentifier()</p>
<p>matches = await identifier.identify("5d41402abc4b2a76b9719d911017c592")</p>
<p>for match in matches:</p>
<p>print(f"{match.algorithm}: {match.confidence}")</p>
</div>
</details>
</li>
<li><code>EncodingFinding</code> (encoding_detector.py)
<details><summary>Sprint F300: msgspec.Struct for detected encoding in text.</summary>
<div class="doc-comment">
<p>Sprint F300: msgspec.Struct for detected encoding in text.</p>
<p></p>
<p>Attributes:</p>
<p>encoding_type: Type of encoding (base64, hex, etc.)</p>
<p>position: Position in original text</p>
<p>length: Length of the encoded string</p>
<p>confidence: Confidence score (0.0-1.0)</p>
<p>decoded_preview: Preview of decoded content</p>
<p>nested_chain: Optional nested encoding chain</p>
<p>original: Original encoded string</p>
<p>is_printable: Whether decoded content is printable ASCII</p>
<p>entropy: Shannon entropy of decoded content</p>
</div>
</details>
</li>
<li><code>UnicodeAnalysisResult</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Complete result of Unicode attack analysis.</span></li>
<li><code>HashMatch</code> (hash_identifier.py)
<details><summary>Represents a hash algorithm match.</summary>
<div class="doc-comment">
<p>Represents a hash algorithm match.</p>
<p></p>
<p>Attributes:</p>
<p>algorithm: Name of the hash algorithm</p>
<p>confidence: Confidence score (0.0-1.0)</p>
<p>length: Length of the hash string</p>
<p>charset: Character set used (hex, base64, etc.)</p>
<p>pattern: Pattern that matched (if any)</p>
<p>hashcat_mode: Hashcat mode number (if available)</p>
<p>john_format: John the Ripper format (if available)</p>
</div>
</details>
</li>
<li><code>EncodingConfig</code> (encoding_detector.py)
<details><summary>Sprint F300: msgspec.Struct for encoding detection configuration.</summary>
<div class="doc-comment">
<p>Sprint F300: msgspec.Struct for encoding detection configuration.</p>
<p></p>
<p>Attributes:</p>
<p>min_length: Minimum length to consider for encoding</p>
<p>max_depth: Maximum depth for nested encoding detection</p>
<p>detect_nested: Whether to detect nested encodings</p>
<p>chunk_size: Chunk size for streaming file processing</p>
<p>min_entropy: Minimum entropy threshold</p>
<p>max_entropy: Maximum entropy threshold</p>
</div>
</details>
</li>
<li><code>HashFinding</code> (hash_identifier.py)
<details><summary>Sprint F300: msgspec.Struct for hash found in text.</summary>
<div class="doc-comment">
<p>Sprint F300: msgspec.Struct for hash found in text.</p>
<p></p>
<p>Attributes:</p>
<p>position: Position in the text</p>
<p>hash_string: The hash string found</p>
<p>matches: List of possible algorithm matches</p>
<p>context: Context around the hash (20 chars before/after)</p>
</div>
</details>
</li>
<li><code>HashConfig</code> (hash_identifier.py)
<details><summary>Sprint F300: msgspec.Struct for hash identification configuration.</summary>
<div class="doc-comment">
<p>Sprint F300: msgspec.Struct for hash identification configuration.</p>
<p></p>
<p>Attributes:</p>
<p>min_confidence: Minimum confidence threshold</p>
<p>top_k_results: Number of top results to return</p>
<p>detect_salted: Whether to detect salted hashes</p>
<p>batch_size: Batch size for processing</p>
</div>
</details>
</li>
<li><code>EncodingChain</code> (encoding_detector.py)
<details><summary>Represents a chain of nested encodings.</summary>
<div class="doc-comment">
<p>Represents a chain of nested encodings.</p>
<p></p>
<p>Attributes:</p>
<p>encodings: List of encoding types in order (e.g., ["base64", "hex"])</p>
<p>final_content: Final decoded content</p>
<p>depth: Depth of the encoding chain</p>
</div>
</details>
</li>
<li><code>UnicodeConfig</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Configuration for Unicode attack analysis.</span></li>
<li><code>HomoglyphFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for homoglyph/confusable character detection.</span></li>
<li><code>BidiFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for bidirectional text attack detection.</span></li>
<li><code>NormalizationFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for Unicode normalization anomaly detection.</span></li>
<li><code>ZeroWidthFinding</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Sprint F300: msgspec.Struct for zero-width character detection.</span></li>
</ul>
</details>

<details><summary><strong>Method</strong> (46)</summary>
<ul>
<li><code>analyze_file</code> (unicode_analyzer.py)
<details><summary>Stream-analyze a file for Unicode attacks.</summary>
<div class="doc-comment">
<p>Stream-analyze a file for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to the file to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>_analyze_nested</code> (encoding_detector.py)
<details><summary>Analyze finding for nested encodings.</summary>
<div class="doc-comment">
<p>Analyze finding for nested encodings.</p>
<p></p>
<p>Args:</p>
<p>finding: The encoding finding to analyze</p>
<p></p>
<p>Returns:</p>
<p>Optional encoding chain if nested encodings found</p>
</div>
</details>
</li>
<li><code>identify</code> (hash_identifier.py)
<details><summary>Identify hash algorithm from hash string.</summary>
<div class="doc-comment">
<p>Identify hash algorithm from hash string.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string to identify</p>
<p></p>
<p>Returns:</p>
<p>List of probable hash algorithms with confidence scores</p>
</div>
</details>
</li>
<li><code>identify_in_file</code> (hash_identifier.py)
<details><summary>Scan file for hash patterns.</summary>
<div class="doc-comment">
<p>Scan file for hash patterns.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to file to scan</p>
<p></p>
<p>Returns:</p>
<p>List of hash findings</p>
</div>
</details>
</li>
<li><code>_detect_base64</code> (encoding_detector.py)
<details><summary>Detect Base64 encoded strings.</summary>
<div class="doc-comment">
<p>Detect Base64 encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of Base64 findings</p>
</div>
</details>
</li>
<li><code>detect_file</code> (encoding_detector.py)
<details><summary>Stream-process large file for encoding detection.</summary>
<div class="doc-comment">
<p>Stream-process large file for encoding detection.</p>
<p></p>
<p>Args:</p>
<p>file_path: Path to text file</p>
<p></p>
<p>Returns:</p>
<p>List of encoding findings</p>
</div>
</details>
</li>
<li><code>_detect_bidi_attacks</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect bidirectional text attacks in text - optimized version.</span></li>
<li><code>compute_skeleton_hash</code> (unicode_analyzer.py)
<details><summary>Compute UTS #39 skeleton hash for confusables detection.</summary>
<div class="doc-comment">
<p>Compute UTS #39 skeleton hash for confusables detection.</p>
<p></p>
<p>Applies:</p>
<p>- NFD normalization</p>
<p>- Basic confusable mapping (using loaded mappings if available)</p>
<p>- Re-NFD normalization</p>
<p>- Returns sha256(skeleton)[:16]</p>
<p></p>
<p>This is used for:</p>
<p>- Spoof network clustering (same skeleton = possible confusables)</p>
<p>- Internal signal only (skeleton text is NOT stored)</p>
<p></p>
<p>Args:</p>
<p>text: Input text (typically hostname or URL segment)</p>
<p></p>
<p>Returns:</p>
<p>16-char hex digest of skeleton hash</p>
</div>
</details>
</li>
<li><code>_detect_hex</code> (encoding_detector.py)
<details><summary>Detect hexadecimal encoded strings.</summary>
<div class="doc-comment">
<p>Detect hexadecimal encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of hex findings</p>
</div>
</details>
</li>
<li><code>_detect_base32</code> (encoding_detector.py)
<details><summary>Detect Base32 encoded strings.</summary>
<div class="doc-comment">
<p>Detect Base32 encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of Base32 findings</p>
</div>
</details>
</li>
<li><code>analyze_text</code> (unicode_analyzer.py)
<details><summary>Analyze text for Unicode attacks.</summary>
<div class="doc-comment">
<p>Analyze text for Unicode attacks.</p>
<p></p>
<p>Args:</p>
<p>text: The text to analyze</p>
<p></p>
<p>Returns:</p>
<p>UnicodeAnalysisResult with all findings</p>
</div>
</details>
</li>
<li><code>_detect_base85</code> (encoding_detector.py)
<details><summary>Detect Base85/Ascii85 encoded strings.</summary>
<div class="doc-comment">
<p>Detect Base85/Ascii85 encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of Base85 findings</p>
</div>
</details>
</li>
<li><code>_detect_url_encoding</code> (encoding_detector.py)
<details><summary>Detect URL/percent-encoded strings.</summary>
<div class="doc-comment">
<p>Detect URL/percent-encoded strings.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of URL encoding findings</p>
</div>
</details>
</li>
<li><code>_calculate_risk_score</code> (unicode_analyzer.py)
<details><summary>Calculate overall risk score based on findings.</summary>
<div class="doc-comment">
<p>Calculate overall risk score based on findings.</p>
<p></p>
<p>Returns:</p>
<p>Risk score from 0.0 (no risk) to 100.0 (critical)</p>
</div>
</details>
</li>
<li><code>detect_mixed_script</code> (unicode_analyzer.py)
<details><summary>Detect mixed-script usage in text (potential spoofing indicator).</summary>
<div class="doc-comment">
<p>Detect mixed-script usage in text (potential spoofing indicator).</p>
<p></p>
<p>Args:</p>
<p>text: Input text to check</p>
<p></p>
<p>Returns:</p>
<p>True if mixed scripts detected</p>
</div>
</details>
</li>
<li><code>_detect_normalization_anomalies</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect Unicode normalization anomalies in text - optimized version.</span></li>
<li><code>detect_text</code> (encoding_detector.py)
<details><summary>Detect encodings in text.</summary>
<div class="doc-comment">
<p>Detect encodings in text.</p>
<p></p>
<p>Args:</p>
<p>text: Input text to scan</p>
<p></p>
<p>Returns:</p>
<p>List of encoding findings</p>
</div>
</details>
</li>
<li><code>_extract_salt</code> (hash_identifier.py)
<details><summary>Extract salt from hash:salt or salt:hash format.</summary>
<div class="doc-comment">
<p>Extract salt from hash:salt or salt:hash format.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string potentially containing salt</p>
<p></p>
<p>Returns:</p>
<p>Tuple of (hash_part, salt_part)</p>
</div>
</details>
</li>
<li><code>_match_by_charset</code> (hash_identifier.py)
<details><summary>Match hash by charset.</summary>
<div class="doc-comment">
<p>Match hash by charset.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string</p>
<p></p>
<p>Returns:</p>
<p>List of matching algorithms</p>
</div>
</details>
</li>
<li><code>_calculate_entropy</code> (encoding_detector.py)
<details><summary>Calculate Shannon entropy of data.</summary>
<div class="doc-comment">
<p>Calculate Shannon entropy of data.</p>
<p></p>
<p>Args:</p>
<p>data: Binary data to analyze</p>
<p></p>
<p>Returns:</p>
<p>Entropy in bits per byte</p>
</div>
</details>
</li>
<li><code>_get_preview</code> (encoding_detector.py)
<details><summary>Get a preview of decoded content.</summary>
<div class="doc-comment">
<p>Get a preview of decoded content.</p>
<p></p>
<p>Args:</p>
<p>data: Binary data</p>
<p>max_length: Maximum preview length</p>
<p></p>
<p>Returns:</p>
<p>String preview of content</p>
</div>
</details>
</li>
<li><code>_detect_charset</code> (hash_identifier.py)
<details><summary>Detect the character set of a hash string.</summary>
<div class="doc-comment">
<p>Detect the character set of a hash string.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string to analyze</p>
<p></p>
<p>Returns:</p>
<p>Character set type (hex, base64, alphanumeric, mixed)</p>
</div>
</details>
</li>
<li><code>identify_batch</code> (hash_identifier.py)
<details><summary>Identify multiple hashes in batch.</summary>
<div class="doc-comment">
<p>Identify multiple hashes in batch.</p>
<p></p>
<p>Args:</p>
<p>hashes: List of hash strings</p>
<p></p>
<p>Returns:</p>
<p>Dictionary mapping hash strings to matches</p>
</div>
</details>
</li>
<li><code>__exit__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager exit.</span></li>
<li><code>_match_by_pattern</code> (hash_identifier.py)
<details><summary>Match hash by pattern (e.g., $1$, $2a$).</summary>
<div class="doc-comment">
<p>Match hash by pattern (e.g., $1$, $2a$).</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string</p>
<p></p>
<p>Returns:</p>
<p>List of (algorithm, pattern) tuples</p>
</div>
</details>
</li>
<li><code>initialize</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the analyzer by loading confusable mappings.</span></li>
<li><code>_load_confusable_mappings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Load confusable character mappings - optimized version.</span></li>
<li><code>_match_by_length</code> (hash_identifier.py)
<details><summary>Match hash by length.</summary>
<div class="doc-comment">
<p>Match hash by length.</p>
<p></p>
<p>Args:</p>
<p>hash_string: Hash string</p>
<p></p>
<p>Returns:</p>
<p>List of matching algorithms</p>
</div>
</details>
</li>
<li><code>_detect_homoglyphs</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect homoglyph/confusable characters in text - optimized version.</span></li>
<li><code>_detect_zero_width</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Detect zero-width characters in text - optimized version.</span></li>
<li><code>_is_printable</code> (encoding_detector.py)
<details><summary>Check if data is printable ASCII.</summary>
<div class="doc-comment">
<p>Check if data is printable ASCII.</p>
<p></p>
<p>Args:</p>
<p>data: Binary data to check</p>
<p></p>
<p>Returns:</p>
<p>True if all bytes are printable ASCII</p>
</div>
</details>
</li>
<li><code>_get_hashcat_mode</code> (hash_identifier.py)
<details><summary>Get hashcat mode for algorithm.</summary>
<div class="doc-comment">
<p>Get hashcat mode for algorithm.</p>
<p></p>
<p>Args:</p>
<p>algorithm: Algorithm name</p>
<p></p>
<p>Returns:</p>
<p>Hashcat mode number or None</p>
</div>
</details>
</li>
<li><code>_get_john_format</code> (hash_identifier.py)
<details><summary>Get John the Ripper format for algorithm.</summary>
<div class="doc-comment">
<p>Get John the Ripper format for algorithm.</p>
<p></p>
<p>Args:</p>
<p>algorithm: Algorithm name</p>
<p></p>
<p>Returns:</p>
<p>John format string or None</p>
</div>
</details>
</li>
<li><code>__init__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Initialize the Unicode attack analyzer.</span></li>
<li><code>cleanup</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Clean up resources and free memory.</span></li>
<li><code>__init__</code> (encoding_detector.py)
<details><summary>Initialize the encoding detector.</summary>
<div class="doc-comment">
<p>Initialize the encoding detector.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration object</p>
</div>
</details>
</li>
<li><code>__init__</code> (hash_identifier.py)
<details><summary>Initialize the hash identifier.</summary>
<div class="doc-comment">
<p>Initialize the hash identifier.</p>
<p></p>
<p>Args:</p>
<p>config: Optional configuration object</p>
</div>
</details>
</li>
<li><code>_get_context</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Extract context around a position in text.</span></li>
<li><code>get_stats</code> (encoding_detector.py)
<details><summary>Get detection statistics.</summary>
<div class="doc-comment">
<p>Get detection statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of detection statistics</p>
</div>
</details>
</li>
<li><code>get_stats</code> (hash_identifier.py)
<details><summary>Get identification statistics.</summary>
<div class="doc-comment">
<p>Get identification statistics.</p>
<p></p>
<p>Returns:</p>
<p>Dictionary of statistics</p>
</div>
</details>
</li>
<li><code>reset_stats</code> (encoding_detector.py) — <span class="doc-comment-inline">Reset detection statistics.</span></li>
<li><code>reset_stats</code> (hash_identifier.py) — <span class="doc-comment-inline">Reset statistics.</span></li>
<li><code>has_findings</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Check if any findings were detected.</span></li>
<li><code>get_finding_count</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get total number of findings.</span></li>
<li><code>get_summary</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Get summary of analysis results.</span></li>
<li><code>__enter__</code> (unicode_analyzer.py) — <span class="doc-comment-inline">Context manager entry.</span></li>
</ul>
</details>

<details><summary><strong>Constant</strong> (20)</summary>
<ul>
<li><code>BASE64_REGEX</code> (encoding_detector.py)</li>
<li><code>BASE32_REGEX</code> (encoding_detector.py)</li>
<li><code>BASE85_REGEX</code> (encoding_detector.py)</li>
<li><code>HEX_REGEX</code> (encoding_detector.py)</li>
<li><code>URL_ENCODING_REGEX</code> (encoding_detector.py)</li>
<li><code>MIN_ENTROPY</code> (encoding_detector.py)</li>
<li><code>MAX_ENTROPY</code> (encoding_detector.py)</li>
<li><code>LENGTH_HASHES</code> (hash_identifier.py)</li>
<li><code>PATTERN_HASHES</code> (hash_identifier.py)</li>
<li><code>HASHCAT_MODES</code> (hash_identifier.py)</li>
<li><code>JOHN_FORMATS</code> (hash_identifier.py)</li>
<li><code>HEX_CHARSET</code> (hash_identifier.py)</li>
<li><code>BASE64_CHARSET</code> (hash_identifier.py)</li>
<li><code>ALPHANUM_CHARSET</code> (hash_identifier.py)</li>
<li><code>_COMPILED_PATTERN_HASHES</code> (hash_identifier.py)</li>
<li><code>_HEX_HASH_SCAN_RE</code> (hash_identifier.py)</li>
<li><code>_COMPILED_SCAN_PATTERN_HASHES</code> (hash_identifier.py)</li>
<li><code>UNICODE_ANALYZER_AVAILABLE</code> (__init__.py)</li>
<li><code>ENCODING_DETECTOR_AVAILABLE</code> (__init__.py)</li>
<li><code>HASH_IDENTIFIER_AVAILABLE</code> (__init__.py)</li>
</ul>
</details>



## Metrics

| Metric | Value |
|---|---|
| Files | 4 |
| Total lines | 1348 |
| Avg lines/file | 337 |
| Languages | Python |
| Outgoing deps | 0 |
| Incoming deps | 0 |
| Tier | 2 |

