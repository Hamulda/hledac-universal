# stealth-networking

**Type:** Domain  
**Status:** current

## Scope

All stealth/anonymity networking capabilities.

## Components

| Component | Path | Purpose |
|-----------|------|---------|
| TorTransport | `transport/tor_transport.py` | Tor circuits |
| I2PTransport | `transport/i2p_transport.py` | I2P tunnels |
| ArtiTransport | `transport/arti_transport.py` | Rust Tor |
| NymTransport | `transport/nym_transport.py` | Mixnet |
| StealthEngine | `security/stealth_engine.py` | Browser fingerprinting |
| HeaderSpoofer | `stealth/header_spoofer.py` | HTTP headers |

## Threat Model

- Network observation resistance
- Browser fingerprinting countermeasures
- JA3/JA4 fingerprint spoofing
- Timing correlation mitigation

## M1 Constraints

- Tor daemon: separate process ~50MB
- I2P router: JVM ~200MB heap
- Arti: embedded ~30MB
