# transport-nym

**Type:** Transport Layer  
**Path:** `transport/nym_transport.py`  
**Status:** current

## Purpose

Nym anonymity network transport. Mixnet-based anonymity with Sphinx packets.

## Key Functions

| Function | Purpose |
|----------|---------|
| `NymTransport` | Main class |
| `send_mixnet(packet)` | Send via mixnet |
| `receive_mixnet()` | Receive from mixnet |

## Invariants

- [TN-1] Sphinx packet format for mixnet transport
- [TN-2] Cover traffic: configurable bandwidth padding
- [TN-3] Gateway selection: automatic with manual override

## Dependencies

- `nymcore` or Rust Nym bindings
