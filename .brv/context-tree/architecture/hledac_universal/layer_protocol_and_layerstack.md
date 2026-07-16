---
title: layer_protocol_and_layerstack
summary: Layer Protocol with LayerStack for event propagation, mount/unmount lifecycle, UDS IPC
tags: []
related: [facts/project/hledac_universal_claude_md.md]
keywords: []
createdAt: '2026-07-16T11:05:39.336Z'
updatedAt: '2026-07-16T11:05:39.336Z'
---
## Reason
Document Layer Protocol and LayerStack architecture from CLAUDE.md

## Raw Concept
**Task:**
Document Layer Protocol and LayerStack for event-driven architecture

**Files:**
- layers/layer_protocol.py
- layers/__init__.py

**Flow:**
SprintScheduler -> LayerStack.mount(ctx) -> Layer.on_event(ctx, event) -> broadcast()

**Timestamp:** 2026-07-16

**Patterns:**
- `mount.*30s.*rollback` - Mount timeout 30s with rollback on error
- `unmount.*10s.*best-effort` - Unmount timeout 10s, best-effort
- `on_event.*30s` - On_event timeout 30s per layer

## Narrative
### Structure
Layer Protocol (runtime_checkable) defines mount/unmount/on_event interface. LayerStack manages lifecycle and event propagation in mount order.

### Dependencies
SprintScheduler instantiates LayerStack. LayerContext provides service registry.

### Highlights
UDS Protocol for zero-copy IPC via Unix Domain Socket with msgspec.msgpack. Lazy singleton accessors for layer instances.

### Rules
Rule 1: Mount/unmount/on_event have timeouts (30s/10s/30s)
Rule 2: Layers execute in mount order for on_event
Rule 3: Event propagation stops if layer returns None or sets halted=True
Rule 4: Unmount rolls back in reverse order on failure

## Facts
- **layerstack_sync**: LayerStack uses asyncio.Lock for thread-safe mount/unmount [project]
- **layercontext_slots**: LayerContext uses __slots__ for memory efficiency [project]
- **uds_serialization**: UDS Protocol uses msgspec.msgpack for zero-copy serialization [project]
- **layer_caching**: Layers cached via lru_cache(maxsize=1) singleton pattern [convention]
