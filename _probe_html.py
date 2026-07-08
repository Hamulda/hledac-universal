#!/usr/bin/env python3
"""Check if rust.batch_extract_emails is subscriptable."""
import sys
sys.path.insert(0, "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal")

from core.rust_backend import rust
import typing

# Mimic public_fetcher exactly
_rust_backend = rust
any_rust = typing.cast(typing.Any, _rust_backend)

rust_emails = any_rust.batch_extract_emails
rust_titles = any_rust.batch_extract_titles

print("rust_emails:", rust_emails)
print("rust_titles:", rust_titles)
print()
print("rust_emails[0]:", rust_emails[0])  # This is what public_fetcher does!
print()
print("callable?", callable(rust_emails))
