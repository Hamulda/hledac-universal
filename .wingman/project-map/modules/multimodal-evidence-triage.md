# multimodal-evidence-triage

**Type:** Multimodal  
**Path:** `multimodal/evidence_triage.py`  
**Status:** current

## Purpose

Automated evidence triage using multimodal analysis. Filters and prioritizes evidence.

## Key Functions

| Function | Purpose |
|----------|---------|
| `EvidenceTriage` | Main class |
| `triage(evidence)` | Score and categorize |
| `prioritize(queue)` | Sort by priority |
| `filter_irrelevant(evidence)` | Remove noise |

## Priority Factors

| Factor | Weight |
|--------|--------|
| Source credibility | High |
| IOC density | High |
| Freshness | Medium |
| Media richness | Medium |

## Invariants

- [MET-1] Priority range: 0-100
- [MET-2] Threshold: >70 = high priority
- [MET-3] Auto-categorize: IOC, narrative, metadata

## Dependencies

- `VisionEncoder`
- `WhisperTranscriber`
- `IOCProcessor`
