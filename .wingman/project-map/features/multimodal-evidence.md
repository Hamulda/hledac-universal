# multimodal-evidence

**Type:** Feature  
**Path:** `multimodal/`  
**Status:** current

## Purpose

Multimodal evidence processing: images, video, audio, documents → structured IOC.

## Pipeline

```
Raw Media
├── Image → VisionEncoder → OCR + Objects + Scene
├── Video → Frame extraction → Per-frame analysis
├── Audio → Whisper → Transcription → NLP
└── PDF   → DocumentIntelligence → Text + Tables
    ↓
IOCExtractor → CanonicalFinding → DuckDB
```

## Capabilities

| Modality | Capability | Output |
|----------|------------|--------|
| Image | OCR | Text IOCs |
| Image | Object detection | Visual IOCs |
| Video | Frame analysis | Motion IOCs |
| Audio | Transcription | Spoken IOCs |
| PDF | Layout analysis | Structured data |

## M1 Constraints

- Batch size: 4-8 images (VRAM)
- Whisper: base model (74MB)
- CLIP: shared pool

## Quality Gates

- Confidence threshold: 0.7
- Deduplication against existing IOCs
