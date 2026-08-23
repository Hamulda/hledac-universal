# multimodal-vision-encoder

**Type:** Multimodal  
**Path:** `multimodal/vision_encoder.py`  
**Status:** current

## Purpose

Vision encoder for image understanding and visual IOC extraction.

## Key Functions

| Function | Purpose |
|----------|---------|
| `VisionEncoder` | Main class |
| `encode_image(image)` | Get image embedding |
| `detect_objects(image)` | Object detection |
| `extract_text(image)` | OCR |
| `classify_scene(image)` | Scene classification |

## Models

| Model | Backbone | Task |
|-------|----------|------|
| CLIP | ViT-L | General |
| DETR | ResNet | Objects |
| Donut | Transformer | Doc VQA |

## Invariants

- [MVE-1] Default: CLIP for embeddings
- [MVE-2] Batch: up to 8 images
- [MVE-3] Preprocessing: resize to 224-512px

## M1 Memory Notes

CLIP ~1GB, DETR ~2GB. Share via analyzer pool.
