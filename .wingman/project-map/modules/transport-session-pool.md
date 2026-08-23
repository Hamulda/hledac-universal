# transport-session-pool

**Type:** Transport Layer  
**Path:** `transport/session_pool.py`  
**Status:** current

## Purpose

HTTP session pool with connection reuse, keep-alive, and cookie management. Reduces TLS handshake overhead.

## Key Functions

| Function | Purpose |
|----------|---------|
| `SessionPool` | Main class |
| `get_session(domain)` | Get pooled session |
| `release_session(session)` | Return to pool |
| `clear_expired()` | Clear stale sessions |

## Invariants

- [TSP-1] Max sessions per domain: 5
- [TSP-2] Session TTL: 5 minutes
- [TSP-3] Cookie policy: respect Set-Cookie + security flags

## M1 Memory Notes

~1KB per session object. Max 100 pooled sessions total.
