---
confidence: 0.82
sources: [memory/resource_governor/_index.md, architecture/hledac_universal/_index.md]
synthesized_at: '2026-07-24T21:05:20.862Z'
type: synthesis
title: Zero-Copy IPC via msgspec.msgpack Spans Memory + Architecture
summary: msgspec.msgpack with gc=False enables zero-copy serialization for LayerStack UDS IPC and MPSC batch events.
tags: [msgspec, zero-copy, uds-ipc, mpsc, serialization]
related: []
keywords: [msgspec, msgpack, zero-copy, UDS, MPSC, LayerStack, crossbeam, gc=False]
createdAt: '2026-07-24T21:05:20.862Z'
updatedAt: '2026-07-24T21:05:20.862Z'
---

# Zero-Copy IPC via msgspec.msgpack Spans Memory + Architecture

Both memory/resource_governor MPSC optimization (Issue-007) and architecture/layer_protocol LayerStack use msgspec with gc=False for zero-copy performance, but neither cross-references the other.

## Evidence

- **memory/resource_governor**: MPSC batch communication saves ~200 bytes/future × N futures via msgspec gc=False; crossbeam bounded channels
- **architecture/hledac_universal**: layer_protocol_and_layerstack.md: LayerStack uses UDS IPC via msgspec.msgpack for mount/unmount lifecycle
