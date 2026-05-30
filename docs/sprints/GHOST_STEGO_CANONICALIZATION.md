# Ghost/Stego Canonicalization — Sprint F3FORENSICS_ACTIVATE

## Decision Summary

| Detector | forensics/ (wrapper) | security/ (canonical) | Winner |
|----------|---------------------|----------------------|--------|
| Digital Ghost | 404L, standalone functions | 546L, DigitalGhostDetector class + full analysis | **security/** |
| Steganography | 221L, chi_square only | 882L, StatisticalStegoDetector + chi-square+RS+DCT | **security/** |

## Digital Ghost Detector

### forensics/digital_ghost_detector.py (404 lines)
- Exports: `analyze_file_ghosts()`, `analyze_directory_ghosts()`, `GhostArtifact`, `DigitalGhostResult`
- Approach: standalone function-based analysis
- MAX_FILE_SIZE: 5MB
- Methods: `_detect_zlib_ghosts`, `_detect_byte_pattern_anomalies`, `_detect_string_fragments`, `_detect_duplicate_patterns`

### security/digital_ghost_detector.py (546 lines)
- Exports: `DigitalGhostDetector` class, `detect_digital_ghosts()` function, `GhostSignal`, `RecoveredContent`, `DigitalGhostAnalysis`
- Approach: full OOP class with `analyze_file()`, `analyze_text_content()`, 10+ analysis methods
- MAX_FILE_SIZE: 50MB
- Additional: `_analyze_metadata_residuals`, `_detect_deletion_indicators`, `_attempt_content_recovery`, `_analyze_temporal_patterns`, `_generate_recommendations`

**Verdict**: `security/digital_ghost_detector.py` is canonical — larger, more complete, class-based with more detection methods.

## Steganography Detector

### forensics/steganography_detector.py (221 lines)
- Exports: `analyze_image_steganography()`, `SteganalysisResult`, `chi_square()`, `entropy()`
- Approach: lightweight chi-square only
- MAX_FILE_SIZE: 100MB
- Note: Pure-Python fallback only

### security/stego_detector.py (882 lines)
- Exports: `StatisticalStegoDetector` class, `create_stego_detector()`, `quick_stego_check()`, `StegoConfig`, `StegoResult`, `ChiSquareResult`, `RSResult`, `DCTResult`
- Approach: full statistical steganalysis with chi-square + RS analysis + DCT analysis
- Async MPS (Metal) and CPU backends
- MAX_FILE_SIZE: configurable (default 50MB)

**Verdict**: `security/stego_detector.py` is canonical — comprehensive multi-method detection with GPU acceleration.

## Action Taken

1. Updated `forensics/__init__.py` `_load_steganography_detector()` docstring to document that `security/stego_detector.py` is canonical.
2. Updated `forensics/__init__.py` `_load_digital_ghost_detector()` docstring to document that `security/digital_ghost_detector.py` is canonical.

## Wiring Status

- `forensics/enrichment_service.py` imports from `forensics.digital_ghost_detector` and `forensics.steganography_detector`
- `forensics/metadata_extractor.py` imports `analyze_image_steganography` from `forensics.steganography_detector`
- `security/__init__.py` re-exports both canonical implementations
- Both forensics files remain functional wrappers for backward compatibility

## GHOST_INVARIANTS Compliance

No violations found. Both forensics files are standalone utilities, not part of the async sprint pipeline.