"""
Git Forensics Detector - Git Repository Analysis for OSINT
=========================================================

Rust-accelerated git packfile forensics for extracting forensic signals
from git repositories.

Features:
- Memory-mapped packfile reading (zero-copy, ~100 MB/s)
- Streaming zlib decompression (64KB windows)
- Delta chain resolution (OFS-DELTA + REF-DELTA)
- Forensic extraction: emails, PGP keys, SSH fingerprints, timestamps
- Batch processing via Rayon parallel decompression
- M1 8GB optimized: bounded memory, cooperative thread pools

Use cases:
- OSINT from public git repositories (GitHub, GitLab)
- Threat actor attribution via commit metadata
- Credential scanning in git history
- Source code leak detection

Example:
    from hledac.universal.forensics.git_forensics import (
        GitForensicsDetector,
        GitForensicsResult,
    )

    detector = GitForensicsDetector()
    result = detector.analyze_packfile("/path/to/objects/pack/file.pack")
    
    # Access extracted IOCs
    for email in result.emails:
        print(f"Author: {email}")
    
    for key_id in result.pgp_keys:
        print(f"PGP Key: {key_id}")
"""
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import msgspec
from _core import aclose

logger = logging.getLogger(__name__)

# R6: Centralized Rust access via core.rust_backend
_GIT_FORENSICS_AVAILABLE = False
_GitForensicsExtractor = None

try:
    from hledac.universal._core.rust_backend import rust
    _GitForensicsExtractor = rust.raw.GitForensicsExtractor
    if _GitForensicsExtractor is not None:
        _GIT_FORENSICS_AVAILABLE = True
        logger.debug("Git forensics Rust module available")
    else:
        logger.debug("Git forensics Rust module not compiled (missing deep_git feature)")
except ImportError:
    logger.debug("Rust backend not available for git forensics")
    _GitForensicsExtractor = None


class GitForensicRecord(msgspec.Struct, frozen=True, gc=False):
    """Single git object forensic record — msgspec for M1 8GB memory efficiency."""
    sha1: str
    object_type: str
    author_email: str | None = None
    author_name: str | None = None
    committer_email: str | None = None
    committer_name: str | None = None
    timestamp: int | None = None
    timezone: str | None = None
    pgp_key_id: str | None = None
    ssh_fingerprint: str | None = None
    message_preview: str | None = None


class GitForensicsStats(msgspec.Struct, frozen=True, gc=False):
    """Statistics from packfile analysis — msgspec for M1 8GB memory efficiency."""
    total_objects: int
    commit_objects: int
    tree_objects: int
    blob_objects: int
    tag_objects: int
    delta_objects: int
    emails_extracted: int
    pgp_keys_found: int
    ssh_keys_found: int
    packfile_size_bytes: int
    extraction_time_ms: int


class GitForensicsResult(msgspec.Struct, frozen=True, gc=False):
    """Complete git forensics analysis result — msgspec for M1 8GB memory efficiency."""
    packfile_path: str
    timestamp: datetime
    records: tuple[GitForensicRecord, ...]
    stats: GitForensicsStats
    emails: tuple[str, ...]
    pgp_keys: tuple[str, ...]
    ssh_fingerprints: tuple[str, ...]
    author_names: tuple[str, ...]


class GitForensicsDetector:
    """
    Git forensics detector using Rust-accelerated packfile analysis.
    
    Extracts forensic signals from git packfiles:
    - Author/committer emails (as IOCs)
    - PGP key IDs
    - SSH fingerprints
    - Timestamps (for timeline analysis)
    
    M1 8GB optimized:
    - mmap-based zero-copy access
    - Streaming decompression (64KB windows)
    - Rayon parallel decompression for commits
    """
    
    __slots__ = ('_extractor',)
    
    def __init__(self) -> None:
        """
        Initialize git forensics detector.
        
        Lazy-initializes Rust extractor on first analysis.
        """
        self._extractor: Any = None
    
    @property
    def _get_extractor(self):
        """Lazy initialization of Rust extractor."""
        if self._extractor is None:
            if not _GIT_FORENSICS_AVAILABLE:
                raise RuntimeError(
                    "Git forensics not available. "
                    "Ensure Rust extension is compiled with deep_git feature."
    )
            self._extractor = _GitForensicsExtractor()
        return self._extractor
    
    def analyze_packfile(
        self,
        packfile_path: str | Path,
        max_objects: int | None = None,
    ) -> GitForensicsResult:
        """
        Analyze a git packfile for forensic signals.
        
        Args:
            packfile_path: Path to .pack file
            max_objects: Maximum objects to process (default: 100,000)
            
        Returns:
            GitForensicsResult with extracted records and statistics
            
        Raises:
            FileNotFoundError: Packfile doesn't exist
            RuntimeError: Rust module not available
        """
        packfile_path = Path(packfile_path)
        if not packfile_path.exists():
            raise FileNotFoundError(f"Packfile not found: {packfile_path}")
        
        extractor = self._get_extractor
        path_str = str(packfile_path)
        
        # Call Rust extraction
        raw_records = extractor.extract(path_str, max_objects)
        
        # Convert to typed records
        records = []
        emails_set = set()
        pgp_keys_set = set()
        ssh_fingerprints_set = set()
        author_names_set = set()
        
        for raw in raw_records:
            record = GitForensicRecord(
                sha1=raw.sha1,
                object_type=raw.object_type,
                author_email=raw.author_email,
                author_name=raw.author_name,
                committer_email=raw.committer_email,
                committer_name=raw.committer_name,
                timestamp=raw.timestamp,
                timezone=raw.timezone,
                pgp_key_id=raw.pgp_key_id,
                ssh_fingerprint=raw.ssh_fingerprint,
                message_preview=raw.message_preview,
    )
            records.append(record)
            
            # Collect unique IOCs
            if record.author_email:
                emails_set.add(record.author_email)
            if record.committer_email:
                emails_set.add(record.committer_email)
            if record.pgp_key_id:
                pgp_keys_set.add(record.pgp_key_id)
            if record.ssh_fingerprint:
                ssh_fingerprints_set.add(record.ssh_fingerprint)
            if record.author_name:
                author_names_set.add(record.author_name)
        
        # Build stats
        stats = extractor.stats
        forensics_stats = GitForensicsStats(
            total_objects=stats.total_objects,
            commit_objects=stats.commit_objects,
            tree_objects=stats.tree_objects,
            blob_objects=stats.blob_objects,
            tag_objects=stats.tag_objects,
            delta_objects=stats.delta_objects,
            emails_extracted=stats.emails_extracted,
            pgp_keys_found=stats.pgp_keys_found,
            ssh_keys_found=stats.ssh_keys_found,
            packfile_size_bytes=stats.packfile_size_bytes,
            extraction_time_ms=stats.extraction_time_ms,
    )
        
        return GitForensicsResult(
            packfile_path=str(packfile_path),
            timestamp=datetime.now(UTC),
            records=tuple(records),
            stats=forensics_stats,
            emails=tuple(sorted(emails_set)),
            pgp_keys=tuple(sorted(pgp_keys_set)),
            ssh_fingerprints=tuple(sorted(ssh_fingerprints_set)),
            author_names=tuple(sorted(author_names_set)),
    )
    
    def extract_commits_fast(self, packfile_path: str | Path) -> list[GitForensicRecord]:
        """
        Fast extraction of only commit objects using parallel decompression.
        
        Faster than analyze_packfile() when you only need commit metadata.
        
        Args:
            packfile_path: Path to .pack file
            
        Returns:
            List of commit records
        """
        packfile_path = Path(packfile_path)
        extractor = self._get_extractor
        path_str = str(packfile_path)
        
        raw_records = extractor.extract_commits_fast(path_str)
        
        records = []
        for raw in raw_records:
            records.append(GitForensicRecord(
                sha1=raw.sha1,
                object_type=raw.object_type,
                author_email=raw.author_email,
                author_name=raw.author_name,
                committer_email=raw.committer_email,
                committer_name=raw.committer_name,
                timestamp=raw.timestamp,
                timezone=raw.timezone,
                pgp_key_id=raw.pgp_key_id,
                ssh_fingerprint=raw.ssh_fingerprint,
                message_preview=raw.message_preview,
            ))
        
        return records
    
    def scan_stats(self, packfile_path: str | Path) -> GitForensicsStats:
        """
        Quick statistics scan without full extraction.
        
        Useful for quick triage of packfiles.
        
        Args:
            packfile_path: Path to .pack file
            
        Returns:
            Statistics about packfile contents
        """
        packfile_path = Path(packfile_path)
        extractor = self._get_extractor
        path_str = str(packfile_path)
        
        raw_stats = extractor.scan_stats(path_str)
        
        return GitForensicsStats(
            total_objects=raw_stats.total_objects,
            commit_objects=raw_stats.commit_objects,
            tree_objects=raw_stats.tree_objects,
            blob_objects=raw_stats.blob_objects,
            tag_objects=raw_stats.tag_objects,
            delta_objects=raw_stats.delta_objects,
            emails_extracted=raw_stats.emails_extracted,
            pgp_keys_found=raw_stats.pgp_keys_found,
            ssh_keys_found=raw_stats.ssh_keys_found,
            packfile_size_bytes=raw_stats.packfile_size_bytes,
            extraction_time_ms=raw_stats.extraction_time_ms,
    )


def quick_git_analysis(packfile_path: str | Path) -> GitForensicsResult:
    """
    Quick git forensics analysis function.
    
    Convenience function for one-off analysis.
    
    Args:
        packfile_path: Path to .pack file
        
    Returns:
        GitForensicsResult with findings
    """
    detector = GitForensicsDetector()
    return detector.analyze_packfile(packfile_path)
