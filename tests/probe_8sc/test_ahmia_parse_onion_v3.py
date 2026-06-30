"""Sprint 8SC: Ahmia V3 onion parsing."""

import pytest

from hledac.universal.intelligence.onion_seed_manager import (
    OnionSeedManager,
    _RE_ONION_V3,
)


def test_ahmia_regex_extracts_v3_onion():
    """_RE_ONION_V3 extracts 56-char V3 .onion addresses from HTML."""
    v3_onion = "zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad"
    html = f"<html><body><a href='http://{v3_onion}.onion/'>Wiki</a></body></html>"

    found = _RE_ONION_V3.findall(html)
    assert v3_onion + ".onion" in found
    assert len(found) == 1


def test_ahmia_regex_rejects_v2():
    """V2 onion (16 chars) is not matched by V3 regex."""
    v2_onion = "kpvz7ki2z5gund"
    html = f"<html><body><a href='http://{v2_onion}.onion/'>Wiki</a></body></html>"

    found = _RE_ONION_V3.findall(html)
    assert len(found) == 0
