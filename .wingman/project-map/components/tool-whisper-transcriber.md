# tool-whisper-transcriber

**Type:** Tool  
**Path:** `tools/whisper_transcriber.py`  
**Status:** current

## Purpose

Whisper-based audio transcription for video/audio IOC extraction.

## Key Functions

| Function | Purpose |
|----------|---------|
| `WhisperTranscriber` | Main class |
| `transcribe(audio)` | Transcribe audio |
| `transcribe_url(url)` | Download and transcribe |

## Models

| Model | Size | Accuracy |
|-------|------|----------|
| tiny | 39MB | Baseline |
| base | 74MB | Good |
| small | 148MB | Better |
| medium | 500MB | High |

## Invariants

- [TWT-1] Default: base model (M1 balance)
- [TWT-2] Language: auto-detect or specify
- [TWT-3] Fallback: text-only if audio fails

## M1 Memory Notes

Whisper.cpp with Metal acceleration. ~1GB peak.

## Dependencies

- `whisper.cpp` bindings or `openai-whisper`
