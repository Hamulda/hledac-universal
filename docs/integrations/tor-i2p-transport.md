# Tor / I2P / Nym Transport Integration

> **Source:** Agent exploration of `transport/` module, July 2026
> **Env gates:** `HLEDAC_ENABLE_TOR`, `HLEDAC_ENABLE_I2P`, `HLEDAC_ENABLE_NYM`

---

## Overview

The transport layer provides anonymous network transport via Tor, I2P, and Nym mixnet.
All three inherit from the abstract `Transport` base class (`transport/base.py:188`).

```
Transport (ABC)
├── TorTransport      # SOCKS5 tunnel to tor daemon
├── I2PTransport      # SAM bridge to i2pd daemon
├── NymTransport      # WebSocket to nym-client subprocess
└── ... (clearnet variants)
```

**Routing decision** (`transport/transport_router.py:92`):
- `.onion` / `.onion/` → `tor_socks` lane
- `.i2p` / `.b32.i2p` → `i2p_socks` lane
- `.freenet` → freenet lane

---

## 1. Tor Transport

### Files
| File | Role |
|------|------|
| `transport/tor_transport.py` | `TorTransport` class, fetch implementation |
| `transport/tor_manager.py` | `TorManager` — tor daemon lifecycle (does NOT spawn tor) |
| `transport/darknet_session_provider.py` | Per-host session reuse with 300s TTL |

### Class: `TorTransport`

**Location:** `transport/tor_transport.py:39`

```python
class TorTransport(Transport):
    def __init__(
        self,
        data_dir=None,
        control_port=9051,
        socks_port=9050,
    ) -> None:
```

**Key methods:**
| Method | Line | Description |
|--------|------|-------------|
| `async start(self) -> bool` | 83 | Verify tor daemon is reachable via SOCKS/Control ports |
| `async stop(self) -> None` | 152 | Close sessions |
| `async is_running(self) -> bool` | 251 | SOCKS port liveness check |
| `async fetch(self, config) -> TransportResult` | 357 | Fetch via curl_cffi through SOCKS proxy |
| `def health_cost(self) -> float` | 281 | Health score for lane selection |

**SOCKS Proxy:** `socks5://127.0.0.1:9050` (configurable via `TOR_SOCKS_PROXY_URL` env var)
**Control Port:** `9051` (configurable)

**Circuit management:**
- `MaxCircuitDirtiness 600` — torrc setting (line 34)
- Domain-per-circuit isolation via `_domain_circuits` dict
- Circuit failure tracking: `_circuit_failures`, `_circuit_request_count`
- Health check: `async _check_circuit_health()` at line 224

**Fetch path (line 357):**
- `.onion` domains use `fetch_via_curl_cffi()` with `CURL_CFFI_PROXY = socks5h://127.0.0.1:9050`
- Falls back to `TorTransport._session_tor` (httpx via `AsyncProxyTransport`)

### Singleton
`get_tor_transport_singleton()` / `set_tor_transport_singleton()` — `transport/tor_transport.py:16-25`

### Env Gate
```
HLEDAC_ENABLE_TOR=1
```
- Checked at `coordinators/fetch_coordinator.py:381`
- Registered in `utils/flag_registry.py:128`: `group='network'`, `requires_daemon='tor'`, `ram_per_session_mb=80`
- Privacy lane: `runtime/privacy_budget.py:59`
- Sidecar adapter: `runtime/sidecars/discovery/_onion.py:13`

### Constraint
> **Does NOT spawn tor daemon.** Assumes `tor` is already running and accessible via SOCKS (port 9050) and Control (port 9051).

---

## 2. I2P Transport

### Files
| File | Role |
|------|------|
| `transport/i2p_transport.py` | `I2PTransport` class, SAM bridge |
| `transport/i2p_client.py` | Standalone eepsite HTTP client |

### Class: `I2PTransport`

**Location:** `transport/i2p_transport.py:34`

```python
class I2PTransport(Transport):
    def __init__(
        self,
        data_dir=None,
        socks_port=I2P_SOCKS_PORT,
        sam_port=I2P_SAM_PORT,
        http_port=I2P_HTTP_PORT,
    ) -> None:
```

**Ports (constants):**
| Constant | Value | Line |
|----------|-------|------|
| `I2P_SOCKS_PORT` | `7654` | 25 |
| `I2P_SAM_PORT` | `7656` | 26 |
| `I2P_HTTP_PORT` | `8888` | 27 |

**Key methods:**
| Method | Line | Description |
|--------|------|-------------|
| `async _try_sam_mode(self) -> bool` | 126 | Try SAM protocol on port 7656; sets `transport_mode = 'sam'` on success |
| `async get_session(self, scheme='http')` | 251 | Get or create httpx session (SAM or SOCKS mode) |
| `async fetch(self, config) -> TransportResult` | 324 | Fetch via I2P tunnel |

**Transport modes:**
- `sam` — SAM (Simple Anonymous Messaging) protocol via raw TCP socket at `127.0.0.1:7656`
- `socks` — fallback SOCKS5 proxy mode

**Sessions maintained:** `_session_http` and `_session_socks` (lazy singletons)

### Singleton
`get_i2p_session()` / `close_i2p_session()` exported from `transport/i2p_transport.py`
`set_i2p_transport_singleton()` / `get_i2p_transport_singleton()` via `contextvars.ContextVar` in `transport_router.py:273`

### Env Gate
```
HLEDAC_ENABLE_I2P=1
```
- Registered in `utils/flag_registry.py:129`: `group='network'`, `requires_daemon='i2p'`, `ram_per_session_mb=60`
- Privacy lane: `runtime/privacy_budget.py:60`
- Sidecar adapter: `runtime/sidecars/discovery/_i2p.py:13`

### Constraint
> **Connects to `i2pd` daemon** via SAM protocol. Requires `i2pd` running with SAM bridge enabled.

---

## 3. Nym Mixnet Transport

### File
`transport/nym_transport.py`

### Class: `NymTransport`

**Location:** `transport/nym_transport.py:41`

```python
class NymTransport(Transport):
    def __init__(
        self,
        data_dir=None,
        nym_client_path='nym-client',
        websocket_port=1977,
        max_queue_size=100,
    ) -> None:
```

**Key methods:**
| Method | Line | Description |
|--------|------|-------------|
| `async start(self)` | 77 | Spawns `nym-client` subprocess, establishes WebSocket |
| `async stop(self, graceful=True)` | 186 | Graceful shutdown of subprocess and queue |
| `def health_cost(self) -> float` | 124 | Health score |

**WebSocket port:** `1977` (default)
**Queue:** `_outgoing_queue`, `_sender_task`, `_receiver_task` for async message handling

### Availability Check
```python
NYM_CLIENT_AVAILABLE: bool = shutil.which('nym-client') is not None
```
Compile-time check — if `nym-client` binary not in PATH, `available` is `False`.

### Env Gate
```
HLEDAC_ENABLE_NYM=1
```
- Registered in `utils/flag_registry.py:130`: `group='network'`, `requires_daemon='nym'`, `ram_per_session_mb=120`
- Privacy lane: `runtime/privacy_budget.py:61`

### Constraint
> **Spawns `nym-client` subprocess** via `asyncio.create_subprocess_exec()`. Requires `nym-client` binary in PATH. Most RAM-intensive of the three: 120 MB per session.

---

## 4. Transport Supervisor

**File:** `transport/transport_supervisor.py:48`

`TransportSupervisor` manages lifecycle and RAM budget across all registered transports.

```python
class TransportSupervisor:
    def __init__(
        self,
        keepalive_interval=KEEPALIVE_INTERVAL_S=30.0,
        ram_budget_mb=TRANSPORT_RAM_BUDGET_MB,
    ) -> None:
```

**Responsibilities:**
- 30s keepalive watchdog loop across all transports
- RAM budget enforcement (total across all transports)
- Transport registration/unregistration at runtime

---

## 5. Storage Trinity — Quick Ref

| Layer | Tech | Purpose | M1 8GB Cap |
|-------|------|---------|------------|
| HOT | SqliteVecStore | float16 embeddings (256d) | 512 MB |
| WARM | LanceDB | IVF-PQ quantized entity embeddings | 8 GB |
| COLD | DuckDB | Canonical findings, IOC history | 16 GB |
| KEYVALUE | LMDB | WAL, dedup, q-tables, hot-edges | 128 MB |
| STRING | diskcache | URLs, HTML, safetensors | 256 MB |

---

## 6. Key Entry Points

| Component | File | Line |
|-----------|------|------|
| `Transport` base class | `transport/base.py` | 188 |
| `TransportConfig` | `transport/base.py` | 60 |
| `TransportResult` | `transport/base.py` | 80 |
| `TransportRouter.route()` | `transport/transport_router.py` | 92 |
| `DarknetSessionProvider` | `transport/darknet_session_provider.py` | 51 |
| `TransportSupervisor` | `transport/transport_supervisor.py` | 48 |

---

## 7. RAM Budget Summary

| Transport | RAM/session | Requires daemon |
|-----------|------------|----------------|
| Tor | 80 MB | `tor` (SOCKS 9050, Control 9051) |
| I2P | 60 MB | `i2pd` (SAM 7656) |
| Nym | 120 MB | `nym-client` subprocess |
