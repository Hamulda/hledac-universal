# transport-i2p

**Type:** Transport Layer  
**Path:** `transport/i2p_transport.py`  
**Status:** current

## Purpose

I2P (Invisible Internet Project) anonymity network transport. Dark web reconnaissance via I2P tunnels.

## Key Functions

| Function | Purpose |
|----------|---------|
| `I2PTransport` | Main class |
| `create_tunnel()` | Create I2P tunnel |
| `fetch(destination)` | Fetch via I2P |
| `list_destinations()` | List known I2P destinations |

## Invariants

- [TI2P-1] SAM protocol v3.1 for I2P communication
- [TI2P-2] Tunnel lifetime: 10 minutes max
- [TI2P-3] Destination caching: 5 minutes TTL

## M1 Memory Notes

I2P router runs separate JVM process. ~200MB heap.
