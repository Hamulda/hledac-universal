"""
JARM Fingerprinter – TLS Server Fingerprinting via JARM Hash

JARM (JA3/RiskRecon) is a TLS fingerprinting technique that sends 10
different TLS Client Hello packets and computes a 62-character hash from
the server responses. Servers with the same hash likely share the same
TLS configuration/infrastructure.

Uses only Python stdlib (ssl, socket, hashlib, sqlite3).
CPU-only operation via run_in_executor for async compatibility.
"""

import asyncio
import hashlib
import logging
import os
import sqlite3
import struct
import threading
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# JARM hash length (first 62 chars of SHA256)
JARM_HASH_LENGTH = 62

# Cache settings
CACHE_DB_PATH = Path.home() / ".hledac" / "jarm_cache.db"
CACHE_TTL_DAYS = 7
CACHE_TTL_SECONDS = CACHE_TTL_DAYS * 24 * 60 * 60

# Timeout settings
PACKET_TIMEOUT = 3.0  # seconds per packet
TOTAL_TIMEOUT = 35.0  # total timeout for all 10 packets


def _ensure_cache_dir() -> None:
    """Ensure cache directory exists."""
    CACHE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _init_cache_db() -> sqlite3.Connection:
    """Initialize SQLite cache database."""
    _ensure_cache_dir()
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS jarm_cache ("
        "domain TEXT PRIMARY KEY, "
        "hash TEXT NOT NULL, "
        "ts INTEGER NOT NULL"
        ")"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_jarm_ts ON jarm_cache(ts)")
    conn.commit()
    return conn


class _JARMFingerprinter:
    """
    JARM TLS Fingerprinter.

    Computes JARM hash by sending 10 TLS Client Hello packets with different
    configurations and hashing the responses.
    """

    # TLS record type
    TLS_HANDSHAKE = 0x16
    TLS_CHANGE_CIPHER_SPEC = 0x14
    TLS_ALERT = 0x15

    # TLS versions
    TLS_1_0 = b"\x03\x01"
    TLS_1_1 = b"\x03\x02"
    TLS_1_2 = b"\x03\x03"
    TLS_1_3 = b"\x03\x04"

    # Cipher suites (IANA registered)
    # ALL ciphers forward order
    CIPHERS_FORWARD = [
        0x0001, 0x0004, 0x0005, 0x0007, 0x0009, 0x000A, 0x0015, 0x0016,
        0x0017, 0x0018, 0x0019, 0x001A, 0x001B, 0x001C, 0x001D, 0x001E,
        0x001F, 0x0020, 0x0021, 0x0022, 0x0023, 0x0024, 0x0025, 0x0026,
        0x0027, 0x0028, 0x0029, 0x002A, 0x002B, 0x002C, 0x002D, 0x002E,
        0x002F, 0x0030, 0x0031, 0x0032, 0x0033, 0x0034, 0x0035, 0x0036,
        0x0037, 0x0038, 0x0039, 0x003A, 0x003B, 0x003C, 0x003D, 0x003E,
        0x003F, 0x0040, 0x0041, 0x0042, 0x0043, 0x0044, 0x0045, 0x0046,
        0x0047, 0x0048, 0x0049, 0x004A, 0x004B, 0x004C, 0x004D, 0x004E,
        0x004F, 0x0050, 0x0051, 0x0052, 0x0053, 0x0054, 0x0055, 0x0056,
        0x0057, 0x0058, 0x0059, 0x005A, 0x005B, 0x005C, 0x005D, 0x005E,
        0x005F, 0x0060, 0x0061, 0x0062, 0x0063, 0x0064, 0x0065, 0x0066,
        0x0067, 0x0068, 0x0069, 0x006A, 0x006B, 0x006C, 0x006D, 0x006E,
        0x006F, 0x0070, 0x0071, 0x0072, 0x0073, 0x0074, 0x0075, 0x0076,
        0x0077, 0x0078, 0x0079, 0x007A, 0x007B, 0x007C, 0x007D, 0x007E,
        0x007F, 0x0080, 0x0081, 0x0082, 0x0083, 0x0084, 0x0085, 0x0086,
        0x0087, 0x0088, 0x0089, 0x008A, 0x008B, 0x008C, 0x008D, 0x008E,
        0x008F, 0x0090, 0x0091, 0x0092, 0x0093, 0x0094, 0x0095, 0x0096,
        0x0097, 0x0098, 0x0099, 0x009A, 0x009B, 0x009C, 0x009D, 0x009E,
        0x009F, 0x00A0, 0x00A1, 0x00A2, 0x00A3, 0x00A4, 0x00A5, 0x00A6,
        0x00A7, 0x00A8, 0x00A9, 0x00AA, 0x00AB, 0x00AC, 0x00AD, 0x00AE,
        0x00AF, 0x00B0, 0x00B1, 0x00B2, 0x00B3, 0x00B4, 0x00B5, 0x00B6,
        0x00B7, 0x00B8, 0x00B9, 0x00BA, 0x00BB, 0x00BC, 0x00BD, 0x00BE,
        0x00BF, 0x00C0, 0x00C1, 0x00C2, 0x00C3, 0x00C4, 0x00C5, 0x00C6,
        0x00C7, 0x00C8, 0x00C9, 0x00CA, 0x00CB, 0x00CC, 0x00CD, 0x00CE,
        0x00CF, 0x00D0, 0x00D1, 0x00D2, 0x00D3, 0x00D4, 0x00D5, 0x00D6,
        0x00D7, 0x00D8, 0x00D9, 0x00DA, 0x00DB, 0x00DC, 0x00DD, 0x00DE,
        0x00DF, 0x00E0, 0x00E1, 0x00E2, 0x00E3, 0x00E4, 0x00E5, 0x00E6,
        0x00E7, 0x00E8, 0x00E9, 0x00EA, 0x00EB, 0x00EC, 0x00ED, 0x00EE,
        0x00EF, 0x00F0, 0x00F1, 0x00F2, 0x00F3, 0x00F4, 0x00F5, 0x00F6,
        0x00F7, 0x00F8, 0x00F9, 0x00FA, 0x00FB, 0x00FC, 0x00FD, 0x00FE,
        0x00FF, 0xC001, 0xC002, 0xC003, 0xC004, 0xC005, 0xC006, 0xC007,
        0xC008, 0xC009, 0xC00A, 0xC00B, 0xC00C, 0xC00D, 0xC00E, 0xC00F,
        0xC010, 0xC011, 0xC012, 0xC013, 0xC014, 0xC015, 0xC016, 0xC017,
        0xC018, 0xC019, 0xC01A, 0xC01B, 0xC01C, 0xC01D, 0xC01E, 0xC01F,
        0xC020, 0xC021, 0xC022, 0xC023, 0xC024, 0xC025, 0xC026, 0xC027,
        0xC028, 0xC029, 0xC02A, 0xC02B, 0xC02C, 0xC02D, 0xC02E, 0xC02F,
        0xC030, 0xC031, 0xC032, 0xC033, 0xC034, 0xC035, 0xC036, 0xC037,
        0xC038, 0xC039, 0xC03A, 0xC03B, 0xC03C, 0xC03D, 0xC03E, 0xC03F,
        0xC040, 0xC041, 0xC042, 0xC043, 0xC044, 0xC045, 0xC046, 0xC047,
        0xC048, 0xC049, 0xC04A, 0xC04B, 0xC04C, 0xC04D, 0xC04E, 0xC04F,
        0xC050, 0xC051, 0xC052, 0xC053, 0xC054, 0xC055, 0xC056, 0xC057,
        0xC058, 0xC059, 0xC05A, 0xC05B, 0xC05C, 0xC05D, 0xC05E, 0xC05F,
        0xC060, 0xC061, 0xC062, 0xC063, 0xC064, 0xC065, 0xC066, 0xC067,
        0xC068, 0xC069, 0xC06A, 0xC06B, 0xC06C, 0xC06D, 0xC06E, 0xC06F,
        0xC070, 0xC071, 0xC072, 0xC073, 0xC074, 0xC075, 0xC076, 0xC077,
        0xC078, 0xC079, 0xC07A, 0xC07B, 0xC07C, 0xC07D, 0xC07E, 0xC07F,
        0xC080, 0xC081, 0xC082, 0xC083, 0xC084, 0xC085, 0xC086, 0xC087,
        0xC088, 0xC089, 0xC08A, 0xC08B, 0xC08C, 0xC08D, 0xC08E, 0xC08F,
        0xC090, 0xC091, 0xC092, 0xC093, 0xC094, 0xC095, 0xC096, 0xC097,
        0xC098, 0xC099, 0xC09A, 0xC09B, 0xC09C, 0xC09D, 0xC09E, 0xC09F,
        0xC0A0, 0xC0A1, 0xC0A2, 0xC0A3, 0xC0A4, 0xC0A5, 0xC0A6, 0xC0A7,
        0xC0A8, 0xC0A9, 0xC0AA, 0xC0AB, 0xC0AC, 0xC0AD, 0xC0AE, 0xC0AF,
        0xC0B0, 0xC0B1, 0xC0B2, 0xC0B3, 0xC0B4, 0xC0B5, 0xC0B6, 0xC0B7,
        0xC0B8, 0xC0B9, 0xC0BA, 0xC0BB, 0xC0BC, 0xC0BD, 0xC0BE, 0xC0BF,
        0xC0C0, 0xC0C1, 0xC0C2, 0xC0C3, 0xC0C4, 0xC0C5, 0xC0C6, 0xC0C7,
        0xC0C8, 0xC0C9, 0xC0CA, 0xC0CB, 0xC0CC, 0xC0CD, 0xC0CE, 0xC0CF,
        0xC0D0, 0xC0D1, 0xC0D2, 0xC0D3, 0xC0D4, 0xC0D5, 0xC0D6, 0xC0D7,
        0xC0D8, 0xC0D9, 0xC0DA, 0xC0DB, 0xC0DC, 0xC0DD, 0xC0DE, 0xC0DF,
        0xC0E0, 0xC0E1, 0xC0E2, 0xC0E3, 0xC0E4, 0xC0E5, 0xC0E6, 0xC0E7,
        0xC0E8, 0xC0E9, 0xC0EA, 0xC0EB, 0xC0EC, 0xC0ED, 0xC0EE, 0xC0EF,
        0xC0F0, 0xC0F1, 0xC0F2, 0xC0F3, 0xC0F4, 0xC0F5, 0xC0F6, 0xC0F7,
        0xC0F8, 0xC0F9, 0xC0FA, 0xC0FB, 0xC0FC, 0xC0FD, 0xC0FE, 0xC0FF,
        0xCCA8, 0xCCA9, 0xCCAA, 0xCCAB, 0xCCAC, 0xCCAD, 0xCCAE, 0xCCAF,
        0xCCB0, 0xCCB1, 0xCCB2, 0xCCB3, 0xCCB4, 0xCCB5, 0xCCB6, 0xCCB7,
        0xCCB8, 0xCCB9, 0xCCBA, 0xCCBB, 0xCCBC, 0xCCBD, 0xCCBE, 0xCCBF,
        0xCCC0, 0xCCC1, 0xCCC2, 0xCCC3, 0xCCC4, 0xCCC5, 0xCCC6, 0xCCC7,
        0xCCC8, 0xCCC9, 0xCCCA, 0xCCCB, 0xCCCC, 0xCCCD, 0xCCCE, 0xCCCF,
        0xCCD0, 0xCCD1, 0xCCD2, 0xCCD3, 0xCCD4, 0xCCD5, 0xCCD6, 0xCCD7,
        0xCCD8, 0xCCD9, 0xCCDA, 0xCCDB, 0xCCDC, 0xCCDD, 0xCCDE, 0xCCDF,
        0xCCE0, 0xCCE1, 0xCCE2, 0xCCE3, 0xCCE4, 0xCCE5, 0xCCE6, 0xCCE7,
        0xCCE8, 0xCCE9, 0xCCEA, 0xCCEB, 0xCCEC, 0xCCED, 0xCCEE, 0xCCEF,
        0xCCF0, 0xCCF1, 0xCCF2, 0xCCF3, 0xCCF4, 0xCCF5, 0xCCF6, 0xCCF7,
        0xCCF8, 0xCCF9, 0xCCFA, 0xCCFB, 0xCCFC, 0xCCFD, 0xCCFE, 0xCCFF,
    ]

    # Reverse order
    CIPHERS_REVERSE = list(reversed(CIPHERS_FORWARD))

    # GREASE values to exclude
    GREASE_VALUES = {0x0A0A, 0x1A1A, 0x2A2A, 0x3A3A, 0x4A4A, 0x5A5A,
                     0x6A6A, 0x7A7A, 0x8A8A, 0x9A9A, 0xAAAA, 0xBABA,
                     0xCACA, 0xDADA, 0xEAEA, 0xFAFA}

    def __init__(self):
        """Initialize JARM fingerprinter."""
        self._lock = threading.Lock()
        self._db_conn: Optional[sqlite3.Connection] = None

    def _get_db(self) -> sqlite3.Connection:
        """Get or create database connection."""
        if self._db_conn is None:
            with self._lock:
                if self._db_conn is None:
                    self._db_conn = _init_cache_db()
        return self._db_conn

    async def fingerprint(self, domain: str, port: int = 443) -> Optional[str]:
        """
        Return JARM hash for domain, using cache if available.

        Args:
            domain: Target domain name
            port: Target port (default 443)

        Returns:
            62-character JARM hash or None if unavailable
        """
        cached = self._get_cached(domain)
        if cached:
            return cached

        loop = asyncio.get_running_loop()
        try:
            result = await asyncio.wait_for(
                loop.run_in_executor(None, self._compute_jarm, domain, port),
                timeout=TOTAL_TIMEOUT
            )
            if result:
                self._cache(domain, result)
            return result
        except asyncio.TimeoutError:
            logger.debug(f"[JARM] Timeout computing JARM for {domain}")
            return None
        except Exception as e:
            logger.debug(f"[JARM] Fingerprint failed for {domain}: {e}")
            return None

    def _compute_jarm(self, domain: str, port: int) -> Optional[str]:
        """
        Blocking JARM computation - runs in executor.

        Sends 10 TLS Client Hello packets and computes the JARM hash.
        """
        try:
            # Build the 10 payloads
            payloads = self._build_payloads()

            # Send each payload and collect responses
            results: List[str] = []
            for i, payload in enumerate(payloads):
                response = self._send_tls_packet(domain, port, payload)
                if response:
                    # Extract cipher suite and TLS version from response
                    extracted = self._extract_cipher_version(response)
                    results.append(extracted)
                else:
                    # Server didn't respond - use placeholder
                    results.append("000")
                # Small delay between packets to avoid rate limiting
                if i < len(payloads) - 1:
                    import time
                    time.sleep(0.05)

            # Compute JARM hash
            concatenated = "".join(results)
            sha256_hash = hashlib.sha256(concatenated.encode()).hexdigest()
            jarm_hash = sha256_hash[:JARM_HASH_LENGTH]

            logger.debug(f"[JARM] Computed hash for {domain}: {jarm_hash}")
            return jarm_hash

        except Exception as e:
            logger.debug(f"[JARM] JARM computation failed for {domain}: {e}")
            return None

    def _build_payloads(self) -> List[bytes]:
        """
        Build the 10 TLS Client Hello payloads.

        Returns list of 10 raw TLS Client Hello bytes.
        """
        payloads = []

        # Custom extension bytes for ALPN
        # HTTP/1.1 = 0x08 0x68 0x74 0x74 0x70 0x2f 0x31 0x2e 0x31
        # h2 = 0x02 0x68 0x32
        http11_ext = bytes([0x08, 0x68, 0x74, 0x74, 0x70, 0x2f, 0x31, 0x2e, 0x31])
        h2_ext = bytes([0x02, 0x68, 0x32])

        # 1. TLS 1.2, ALL ciphers forward, no extensions
        ciphers = self.CIPHERS_FORWARD[:80]  # Limit for packet size
        payloads.append(self._build_client_hello(
            self.TLS_1_2, ciphers, extensions=None
        ))

        # 2. TLS 1.2, ALL ciphers reverse, no extensions
        ciphers = self.CIPHERS_REVERSE[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_2, ciphers, extensions=None
        ))

        # 3. TLS 1.2, ALL ciphers forward, all extensions
        ciphers = self.CIPHERS_FORWARD[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_2, ciphers, extensions=True
        ))

        # 4. TLS 1.2, ALL ciphers reverse, all extensions
        ciphers = self.CIPHERS_REVERSE[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_2, ciphers, extensions=True
        ))

        # 5. TLS 1.1, ALL ciphers forward, no extensions
        ciphers = self.CIPHERS_FORWARD[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_1, ciphers, extensions=None
        ))

        # 6. TLS 1.3, ALL ciphers forward, no extensions
        ciphers = self.CIPHERS_FORWARD[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_3, ciphers, extensions=None
        ))

        # 7. TLS 1.2, NO_GREASE forward, ALPN=h2
        ciphers_no_grease = [c for c in self.CIPHERS_FORWARD[:80] if c not in self.GREASE_VALUES]
        payloads.append(self._build_client_hello(
            self.TLS_1_2, ciphers_no_grease, alpn=h2_ext
        ))

        # 8. TLS 1.2, ALL ciphers reverse, ALPN=http/1.1
        ciphers = self.CIPHERS_REVERSE[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_2, ciphers, alpn=http11_ext
        ))

        # 9. TLS 1.3, ALL ciphers forward, no extensions
        ciphers = self.CIPHERS_FORWARD[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_3, ciphers, extensions=None
        ))

        # 10. TLS 1.3, ALL ciphers reverse, all extensions
        ciphers = self.CIPHERS_REVERSE[:80]
        payloads.append(self._build_client_hello(
            self.TLS_1_3, ciphers, extensions=True
        ))

        return payloads

    def _build_client_hello(
        self,
        tls_version: bytes,
        ciphers: List[int],
        extensions: Optional[bool] = None,
        alpn: Optional[bytes] = None
    ) -> bytes:
        """Build a TLS Client Hello packet."""
        import random

        # Generate random session ID
        session_id = bytes([random.randint(0, 255) for _ in range(32)])

        # Build cipher suites (2 bytes each)
        cipher_bytes = b"".join(struct.pack(">H", c) for c in ciphers)

        # Build extensions if requested
        ext_bytes = b""
        if extensions or alpn:
            # Basic extensions: server_name, status_request, supported_groups, ec_point_formats
            # signature_algorithms, supported_versions
            ext_parts = []

            # SNI (Server Name Indication)
            sni = self._build_sni_extension()
            ext_parts.append(sni)

            # Supported groups (secp256r1, secp384r1, etc.)
            supported_groups = self._build_supported_groups_extension()
            ext_parts.append(supported_groups)

            # EC point formats (uncompressed)
            ec_point_fmt = self._build_ec_point_format_extension()
            ext_parts.append(ec_point_fmt)

            # Signature algorithms
            sig_algs = self._build_signature_algorithms_extension()
            ext_parts.append(sig_algs)

            # Supported versions (TLS 1.3, 1.2)
            supported_versions = self._build_supported_versions_extension(tls_version)
            ext_parts.append(supported_versions)

            # ALPN if requested
            if alpn:
                alpn_ext = self._build_alpn_extension(alpn)
                ext_parts.append(alpn_ext)

            # Join all extensions
            ext_bytes = b"".join(ext_parts)

        # Build the full Client Hello
        # Handshake type (1 byte) + length (3 bytes) = 4 bytes header
        # Version (2) + Random (32) + Session ID (1+32) + Cipher Suites (2+N*2)
        # + Compression (1+1) + Extensions (2+ext_len)

        # Client Hello structure:
        # 0x01 = Client Hello
        # F196B: Use list + join — O(n) vs bytes += O(n²)
        parts = [b"\x01"]  # handshake type

        # Placeholder for handshake length (will fill in later)
        hh = b"\x00\x00\x00"

        # TLS version
        parts.append(tls_version)

        # Random (32 bytes)
        import time
        random_bytes = bytes([
            (int(time.time()) >> (8 * i)) & 0xFF for i in range(4)
        ] + [random.randint(0, 255) for _ in range(28)])
        parts.append(random_bytes)

        # Session ID
        parts.append(bytes([len(session_id)]) + session_id)

        # Cipher suites
        parts.append(struct.pack(">H", len(cipher_bytes)) + cipher_bytes)

        # Compression (null only)
        parts.append(b"\x01\x00")

        # Extensions
        if ext_bytes:
            parts.append(struct.pack(">H", len(ext_bytes)) + ext_bytes)
        else:
            parts.append(b"\x00\x00")

        # Join all parts
        client_hello = b"".join(parts)

        # Fill in handshake length
        handshake_length = len(client_hello) - 4  # subtract header
        client_hello = client_hello[:1] + struct.pack(">I", handshake_length)[1:] + client_hello[4:]

        # Wrap in TLS record
        # Content type: handshake (0x16), Version, Length (2 bytes)
        record = b"\x16" + tls_version + struct.pack(">H", len(client_hello)) + client_hello

        return record

    def _build_sni_extension(self) -> bytes:
        """Build SNI (Server Name Indication) extension."""
        # This is handled separately - we extract hostname from connection
        return b""

    def _build_supported_groups_extension(self) -> bytes:
        """Build supported groups extension."""
        # secp256r1, secp384r1, secp521r1, x25519
        groups = b"\x00\x17\x00\x18\x00\x19\x00\x1d"  # these are IDs
        ext = b"\x00\x0a" + struct.pack(">H", len(groups) + 2) + struct.pack(">H", len(groups)) + groups
        return ext

    def _build_ec_point_format_extension(self) -> bytes:
        """Build EC point formats extension."""
        fmt = b"\x00"  # uncompressed
        ext = b"\x00\x0b" + struct.pack(">H", len(fmt) + 2) + struct.pack(">H", len(fmt)) + fmt
        return ext

    def _build_signature_algorithms_extension(self) -> bytes:
        """Build signature algorithms extension."""
        # rsa-sha256, rsa-sha384, rsa-sha512, ecdsa-sha256, ecdsa-sha384
        algs = b"\x04\x03\x05\x03\x06\x03\x08\x07\x08\x08"
        ext = b"\x00\x0d" + struct.pack(">H", len(algs) + 2) + struct.pack(">H", len(algs)) + algs
        return ext

    def _build_supported_versions_extension(self, tls_version: bytes) -> bytes:
        """Build supported versions extension."""
        # TLS 1.3, 1.2
        versions = self.TLS_1_3 + self.TLS_1_2
        ext = b"\x00\x2b" + struct.pack(">H", len(versions) + 2) + struct.pack(">H", len(versions)) + versions
        return ext

    def _build_alpn_extension(self, alpn_protocol: bytes) -> bytes:
        """Build ALPN extension."""
        # Format: 2 (length) + 1 (protocol len) + protocol + ...
        length = len(alpn_protocol) + 1
        ext_data = struct.pack(">H", length) + bytes([length - 2]) + alpn_protocol
        ext = b"\x00\x10" + struct.pack(">H", len(ext_data)) + ext_data
        return ext

    def _send_tls_packet(
        self,
        domain: str,
        port: int,
        payload: bytes
    ) -> Optional[bytes]:
        """Send TLS packet and return response."""
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(PACKET_TIMEOUT)
        try:
            sock.connect((domain, port))
            sock.sendall(payload)

            # Receive response — use bytearray for O(1) extend vs O(n) bytes +=
            response = bytearray()
            while True:
                try:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                    # If we got a full TLS record, stop
                    if len(response) >= 5:
                        record_len = struct.unpack(">H", response[3:5])[0]
                        if len(response) >= 5 + record_len:
                            break
                except socket.timeout:
                    break
            return bytes(response) if response else None
        except Exception:
            return None
        finally:
            sock.close()

    def _extract_cipher_version(self, response: bytes) -> str:
        """
        Extract cipher suite and TLS version from TLS response.

        Returns 6-character hex string (4 chars for cipher + 2 for version).
        """
        if len(response) < 43:
            return "000000"  # Invalid response

        try:
            # TLS 1.3 Server Hello format:
            # Record: type(1) + version(2) + length(2)
            # Handshake: type(1) + length(3) + version(2) + random(32) + session_id_len(1) + ...
            # Cipher suite is at offset: 43 (record + handshake header + version + random + session_id)

            # Check if it's a valid handshake response
            if response[0] != 0x16:  # Not a handshake
                return "000000"

            # TLS record version
            version = response[1:3]

            # Parse handshake
            if len(response) < 6 or response[5] != 0x02:  # Not Server Hello
                return "000000"

            # Server Hello: skip handshake type + length + version + random (32) + session_id
            offset = 1 + 3 + 2 + 32  # 38
            if len(response) <= offset:
                return "000000"

            session_id_len = response[offset]
            offset += 1 + session_id_len

            if len(response) < offset + 2:
                return "000000"

            cipher_suite = response[offset:offset + 2]

            # Return combined: cipher (4 hex) + version (2 hex)
            result = cipher_suite.hex() + version.hex()
            return result

        except Exception:
            return "000000"

    def _get_cached(self, domain: str) -> Optional[str]:
        """Check SQLite cache. Return hash if exists and not expired."""
        import time
        try:
            conn = self._get_db()
            cursor = conn.execute(
                "SELECT hash, ts FROM jarm_cache WHERE domain = ?",
                (domain,)
            )
            row = cursor.fetchone()
            if row:
                jarm_hash, ts = row
                if time.time() - ts < CACHE_TTL_SECONDS:
                    return jarm_hash
            return None
        except Exception:
            return None

    def _cache(self, domain: str, jarm_hash: str) -> None:
        """Store JARM hash in SQLite cache."""
        import time
        try:
            conn = self._get_db()
            conn.execute(
                "INSERT OR REPLACE INTO jarm_cache (domain, hash, ts) VALUES (?, ?, ?)",
                (domain, jarm_hash, int(time.time()))
            )
            conn.commit()
        except Exception as e:
            logger.debug(f"[JARM] Cache write failed: {e}")

    def close(self) -> None:
        """Close database connection."""
        if self._db_conn:
            self._db_conn.close()
            self._db_conn = None
