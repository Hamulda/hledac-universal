# tool-vlm-analyzer

**Type:** Tool  
**Path:** `tools/vlm_analyzer.py`  
**Status:** current

## Purpose

Vision-Language Model analyzer for image understanding and IOC extraction from images.

## Key Functions

| Function | Purpose |
|----------|---------|
| `VLMAnalyzer` | Main class |
| `analyze_image(image)` | Analyze image content |
| `extract_text(image)` | OCR + understanding |
| `describe_scene(image)` | Scene description |

## Models

| Model | Use Case |
|-------|---------|
| BakLLaVA | Local, M1 optimized |
| CogVLM | High accuracy |
| Claude Vision | Cloud fallback |

## Invariants

- [TVLM-1] Max image size: 4MB
- [TVLM-2] Timeout: 30s per image
- [TVLM-3] Fallback: text extraction if VLM unavailable

## M1 Memory Notes

BakLLaVA ~4GB VRAM. VLLM server mode for sharing.
