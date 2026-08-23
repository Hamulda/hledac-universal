# blockchain-osint

**Type:** Feature  
**Path:** `recon/bitcoin_utxo_analyzer.py`, `recon/blockchain_analyzer.py`  
**Status:** current

## Purpose

Cryptocurrency blockchain intelligence for wallet attribution and transaction tracing.

## Supported Chains

| Chain | Explorer | API |
|-------|----------|-----|
| Bitcoin | Blockstream | blockstream.info |
| Ethereum | Etherscan | Etherscan API |
| Monero | Local node | RPC |

## Capabilities

| Capability | Purpose |
|------------|---------|
| Wallet lookup | Address → known exchanges |
| TX tracing | Follow funds through addresses |
| UTXO analysis | Unspent output tracking |
| Cluster analysis | Link addresses to entities |

## Data Sources

- Blockchain explorers (read-only)
- Exchange deposit address databases
- Known wallet tagging services

## Invariants

- [BCO-1] Rate limit: respect explorer limits
- [BCO-2] Privacy: use Tor for blockchain queries
- [BCO-3] Cache: 1 hour TTL for balance checks
