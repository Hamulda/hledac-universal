# Coordinator OperationRegistry — No-Action Decision

**Date:** 2026-05-18
**Type:** Architecture Decision Record (no-action)
**Authority:** CoordinatorRoutingAuthority Audit
**Commit:** `docs(coordinators): reject OperationRegistry for canonical runtime`

---

## Decision: No OperationRegistry

**Rejected:** Introducing `OperationRegistry` as a new routing/facade layer for coordinators.

**Rationale:** `OperationRegistry` would stabilize the quarantined legacy routing chain
(`LayerManager → CoordinationLayer → CoordinatorRegistry → route_operation()`), not the
canonical runtime. The canonical sprint path uses direct coordinator instantiation and the
`start/step/shutdown` Protocol spine — no capability-based routing exists on that path.

---

## Evidence

| Finding | Source |
|---------|--------|
| Canonical runtime uses `SprintScheduler` + direct `CoordinatorCatalog` lazy-loading | `core.__main__.run_sprint()`, `runtime/sprint_scheduler.py` |
| `get_capabilities`/`get_load_factor`/`can_accept_operation` have zero active production callers | `COORDINATOR_CAPABILITY_PROTOCOL_AUDIT.md` |
| `CoordinatorRegistry` is marked DEPRECATED + DORMANT; makes no production claims | `coordinator_registry.py` docstring |
| Legacy routing chain authority is NONE; sealed from canonical path | `tests/test_coordinator_routing_authority_seal.py` |

---

## Future Type Safety Path

If coordinator type safety is needed at the catalog boundary, target the `start/step/shutdown`
Protocol — not `OperationType` routing:

```
CanonicalCoordinatorProtocol:
    async def start(ctx) -> None
    async def step(ctx) -> dict
    async def shutdown(ctx) -> None
```

This aligns with the actual execution spine used by all active coordinators.

---

## Verification Commands

```bash
# coordinator routing authority seal
pytest tests/test_coordinator_routing_authority_seal.py -q --tb=short

# coordinator import smoke
python3 -c "from coordinators import catalog; print(list(catalog.domains.keys())[:3])"
```