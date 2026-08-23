# Multimodal Processing

## Metadata

- **Entry Path:** features/multimodal-processing
- **Status:** current
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** feature

## Summary

Processing of images, audio, video, and documents with native macOS optimization.

## Source Paths

- coordinators/multimodal_coordinator.py
- multimodal/
- forensics/enrichment_service.py

## Capabilities

| Modality | Technology | Purpose |
|----------|------------|---------|
| Images | ocrmac | Native macOS OCR |
| Images | metadata extraction | EXIF, PNG tEXt |
| Images | steganography | LSB detection |
| Audio | transcription | Whisper via MLX |
| Video | frame extraction | Key frames |
| Documents | PDF parsing | Text/structure |
| Documents | DOCX parsing | Word documents |

## M1 Optimization

OCR uses ocrmac (macOS Vision framework) - native Metal acceleration.

## Related Entries

- modules/multimodal-coordinator
- features/ioc-extraction
