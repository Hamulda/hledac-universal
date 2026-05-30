# DHT BEP-9 + Social Intelligence Adapters

## Sprint F229: BEP-9 Metadata Extraction + Fediverse/Matrix Intelligence

### Implemented Components

#### 1. DHT BEP-9 Metadata Fetcher (`dht/metadata_fetcher.py`)

**Class:** `TorrentMetadataFetcher`

BEP-9 extension protocol implementation for fetching torrent metadata without downloading the full torrent file.

**Protocol Flow:**
1. TCP connect to peer (try up to 3 peers)
2. BitTorrent handshake with extension protocol bit set (`reserved |= 0x10`)
3. Send extended handshake: `{"m": {"ut_metadata": 1}}`
4. Receive extended handshake → get `ut_metadata` extension ID + `metadata_size`
5. Request metadata in16KB pieces
6. Receive and reassemble all pieces
7. Verify SHA1(metadata) == infohash
8. Bencode decode → `TorrentInfo`

**Key Methods:**
- `fetch_metadata(infohash, peers, timeout)` → `TorrentInfo | None`
- `extract_intel_from_torrent(info, infohash)` → `list[dict]`
- `clear_cache()`

**M1 Constraints:**
- Max5 concurrent metadata fetches (`MAX_CONCURRENT_FETCHES = 5`)
- 30s timeout default
- Try up to 3 peers per infohash

**OSINT Findings Extracted:**
- File names → potential leaked data indicators
- Directory structure → organizational pattern signals
- Total size → data exfiltration scale estimate
- Tracker list → infrastructure indicators

#### 2. Fediverse Adapter (`discovery/fediverse_adapter.py`)

**Class:** `FediverseAdapter`

Search public Mastodon/Fediverse instances for OSINT signals.

**Target Instances:**
- `infosec.exchange` — InfoSec community
- `mastodon.social` — General, large
- `scholar.social` — Academic
- `fosstodon.org` — Tech/FOSS
- `hachyderm.io` — Tech, moderated

**Key Methods:**
- `search_public_timeline(query, max_results)` → `list[dict]`
- `search_hashtags(hashtag, max_results)` → `list[dict]`
- `get_account_posts(account, limit)` → `list[dict]`

**M1 Constraints:**
- Max 2 concurrent instances (`MAX_CONCURRENT_INSTANCES = 2`)
- 10s timeout per request
- 5s rate limit between requests per instance

**Gate:** `HLEDAC_ENABLE_SOCIAL=1`

#### 3. Matrix Public Adapter (`discovery/matrix_adapter.py`)

**Class:** `MatrixPublicAdapter`

Search Matrix public rooms for intelligence signals. Uses `matrix-client.matrix.org` homeserver.

**Key Methods:**
- `search_public_rooms(search_term, limit)` → `list[MatrixRoom]`
- `get_room_messages(room_id, limit)` → `list[dict]`
- `register_guest()` → `str` (access token)
- `search_and_fetch_rooms(search_term, max_messages)` → `list[dict]`

**M1 Constraints:**
- Max 50 messages per room (`MAX_ROOM_MESSAGES = 50`)
- 10s timeout per request
- Guest token cached for 1 hour

**Gate:** `HLEDAC_ENABLE_SOCIAL=1`

### Wiring

#### DHT Adapter (`discovery/dht_adapter.py`)

Added `async_fetch_dht_metadata()` function:
- After BEP-5 `get_peers()` returns peers
- Calls `TorrentMetadataFetcher.fetch_metadata(infohash, peers)`
- Returns dict with metadata + OSINT findings

#### Alternative Protocol Fetcher (`fetching/alternative_protocol_fetcher.py`)

Added two new protocol fetchers:
- `_fetch_from_fediverse()` — Fediverse search + account posts
- `_fetch_from_matrix()` — Matrix public rooms + messages

Both gated by `HLEDAC_ENABLE_SOCIAL=1`:
```python
if os.getenv("HLEDAC_ENABLE_SOCIAL", "").strip() == "1":
    tasks.append(_fetch_from_fediverse(query, sem))
    tasks.append(_fetch_from_matrix(query, sem))
```

Added convenience functions:
- `fetch_fediverse_only(query)` → `list[CanonicalFinding]`
- `fetch_matrix_only(query)` → `list[CanonicalFinding]`

### Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `HLEDAC_ENABLE_DHT` | Enable DHT BEP-5/BEP-9 | `0` |
| `HLEDAC_ENABLE_SOCIAL` | Enable Fediverse/Matrix | `0` |
| `HLEDAC_ENABLE_ALT_PROTOCOLS` | Enable IPFS/Gopher/Gemini/I2P | `0` |

### Files Created/Modified

| File | Action |
|------|--------|
| `dht/metadata_fetcher.py` | **NEW** — BEP-9 implementation |
| `discovery/fediverse_adapter.py` | **NEW** — Fediverse adapter |
| `discovery/matrix_adapter.py` | **NEW** — Matrix adapter |
| `discovery/dht_adapter.py` | **MODIFIED** — Added `async_fetch_dht_metadata()` |
| `fetching/alternative_protocol_fetcher.py` | **MODIFIED** — Added social protocol fetchers |

### Testing

Run probe tests:
```bash
HLEDAC_ENABLE_SOCIAL=1 HLEDAC_ENABLE_DHT=1 uv run pytest tests/test_alt_protocols.py -v
```

### Architecture Notes

- All adapters are fail-safe: exceptions are caught and logged, empty results returned
- Lazy imports to avoid circular dependencies
- M1 memory constraints enforced via semaphores
- Rate limiting implemented per-instance
- Guest token caching for Matrix to avoid re-registration
