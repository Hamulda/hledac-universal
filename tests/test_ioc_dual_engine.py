"""Tests for the IOC dual-engine (Rust regex + Brain NER).

Covers:
* ``CanonicalIOC`` construction + dedup by (ioc_type, value, context_hash)
* ``extract_iocs_from_texts_dual`` runs both engines in parallel and merges
* ``Capability.NER_MODEL`` gate: NER skipped when ``_is_ner_available()`` is False
* ``extract_iocs_dual_engine`` returns unified ``list[CanonicalIOC]``
"""

from __future__ import annotations

from unittest import mock

import pytest

from hledac.universal.hledac_types.canonical import (
    CanonicalIOC,
    dedup_canonical_iocs,
    make_canonical_ioc,
)


# ---------------------------------------------------------------------------
# CanonicalIOC unit tests
# ---------------------------------------------------------------------------


def test_make_canonical_ioc_drops_empty():
    assert make_canonical_ioc("", "ipv4") is None
    assert make_canonical_ioc("1.2.3.4", "") is None


def test_make_canonical_ioc_default_confidence_by_source():
    rust = make_canonical_ioc("1.2.3.4", "ipv4", source="rust_regex")
    ner = make_canonical_ioc("EvilCorp", "organization", source="brain_ner")
    assert rust.confidence == 0.8
    assert ner.confidence == 0.65


def test_dedup_prefers_higher_confidence():
    items = [
        make_canonical_ioc("1.2.3.4", "ipv4", confidence=0.5, source="brain_ner"),
        make_canonical_ioc("1.2.3.4", "ipv4", confidence=0.9, source="rust_regex"),
    ]
    out = dedup_canonical_iocs(items)
    assert len(out) == 1
    assert out[0].confidence == 0.9
    assert out[0].source == "rust_regex"


def test_dedup_keeps_distinct_types():
    items = [
        make_canonical_ioc("EvilCorp", "domain", source="rust_regex"),
        make_canonical_ioc("EvilCorp", "organization", source="brain_ner"),
    ]
    out = dedup_canonical_iocs(items)
    assert len(out) == 2  # distinct ioc_type -> both kept


# ---------------------------------------------------------------------------
# Dual-engine integration (mocked backends)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dual_engine_merges_rust_and_ner():
    from hledac.universal.pipeline import public_patterns

    rust_batch = [[("1.2.3.4", "ipv4")]]

    with mock.patch.object(
        public_patterns, "extract_iocs_from_texts", return_value=rust_batch
    ), mock.patch(
        "hledac.universal.brain.ner_engine._is_ner_available", return_value=True
    ), mock.patch(
        "hledac.universal.brain.ner_engine.extract_iocs_from_text",
        return_value=[{"value": "EvilCorp", "ioc_type": "organization", "confidence": 0.7}],
    ):
        result = await public_patterns.extract_iocs_from_texts_dual(["page A"])

    assert len(result) == 1
    flat = {(i.ioc_type, i.value, i.source) for i in result[0]}
    assert ("ipv4", "1.2.3.4", "rust_regex") in flat
    assert ("organization", "EvilCorp", "brain_ner") in flat


@pytest.mark.asyncio
async def test_dual_engine_gate_skips_ner_when_unavailable():
    from hledac.universal.pipeline import public_patterns

    rust_batch = [[("9.9.9.9", "ipv4")]]

    with mock.patch.object(
        public_patterns, "extract_iocs_from_texts", return_value=rust_batch
    ), mock.patch(
        "hledac.universal.brain.ner_engine._is_ner_available", return_value=False
    ) as gate, mock.patch(
        "hledac.universal.brain.ner_engine.extract_iocs_from_text",
        return_value=[{"value": "EvilCorp", "ioc_type": "organization"}],
    ) as ner:
        result = await public_patterns.extract_iocs_from_texts_dual(["page B"])

    # NER must not be invoked when the capability gate is closed.
    assert gate.called
    assert not ner.called
    assert len(result) == 1
    assert {i.source for i in result[0]} == {"rust_regex"}


@pytest.mark.asyncio
async def test_dual_engine_per_text_returns_canonical():
    from hledac.universal.pipeline import public_patterns

    with mock.patch.object(
        public_patterns, "extract_iocs_from_text", return_value=[("1.2.3.4", "ipv4")]
    ), mock.patch(
        "hledac.universal.brain.ner_engine._is_ner_available", return_value=False
    ):
        result = await public_patterns.extract_iocs_dual_engine("some text")

    assert all(isinstance(i, CanonicalIOC) for i in result)
    assert result and result[0].value == "1.2.3.4"
