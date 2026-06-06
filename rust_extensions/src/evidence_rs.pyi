# Stubs pro `evidence_rs` (PyO3 extension).
# Verze: synchronizes s Cargo.toml `hledac-rust-extensions` 0.1.0+evidence-rs-0.1

from enum import Enum
from typing import Any

__version__: str
BLAKE3_OUT: int
MAX_NORMALIZE_LEN: int
MAX_PAYLOAD_BYTES: int

class IocType(Enum):
    Domain = "domain"
    Ipv4 = "ipv4"
    Ipv6 = "ipv6"
    Url = "url"
    Email = "email"
    Md5 = "md5"
    Sha1 = "sha1"
    Sha256 = "sha256"
    Unknown = "unknown"

def normalize_ioc(raw: str, ioc_type: IocType) -> str: ...
def blake3_hash(data: bytes) -> bytes: ...
def content_hash(value: str) -> str: ...
def chain_hash(prev_chain_hex: str, content_hash_hex: str, event_id: str) -> tuple[str, str]: ...
def is_duplicate(hash32: bytes, bloom: Any) -> bool: ...
def serialize_event(arch_bytes: bytes) -> bytes: ...
def validate_event_archive(arch: bytes) -> bool: ...
