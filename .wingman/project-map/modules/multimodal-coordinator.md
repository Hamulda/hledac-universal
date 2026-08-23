# Multimodal Coordinator

## Metadata

- **Entry Path:** modules/multimodal-coordinator
- **Status:** current
- **Source:** coordinators/multimodal_coordinator.py
- **Evidence Level:** source
- **Last Verified:** 2026-08-20
- **Category:** module

## Summary

Coordinator for handling multi-modal data: images, audio, video, documents.

## Source Paths

- `coordinators/multimodal_coordinator.py`
- `multimodal/`
- `forensics/enrichment_service.py`

## Use When

- Processing images with OCR
- Audio transcription
- Video analysis
- Document parsing (PDF, DOCX)

## Do Not Use When

- Text-only processing
- Low-resource environments

## Capabilities

- OCR via `ocrmac` (macOS native)
- Audio transcription
- Image forensics (metadata, steganography)
- Document structure extraction

## Related Entries

- modules/security-coordinator
- features/ioc-extraction
