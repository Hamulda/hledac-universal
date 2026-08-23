# multimodal-media-engine

**Type:** Multimodal  
**Path:** `multimodal/media_engine.py`  
**Status:** current

## Purpose

Central media processing engine. Coordinates vision, audio, and document analysis.

## Key Functions

| Function | Purpose |
|----------|---------|
| `MediaEngine` | Main class |
| `process(file_path)` | Process any media type |
| `detect_type(content)` | Detect media type |
| `route_to_analyzer(media)` | Route to appropriate analyzer |

## Supported Types

| Type | Analyzer |
|------|----------|
| Image | VisionEncoder |
| Video | Frame extraction + Vision |
| Audio | WhisperTranscriber |
| PDF | DocumentIntelligence |
| Document | ContentExtractor |

## Invariants

- [MME-1] Auto-detect type from magic bytes
- [MME-2] Max size: 100MB per file
- [MME-3] Streaming: for large videos

## M1 Memory Notes

Analyzer pooling to share model memory.
