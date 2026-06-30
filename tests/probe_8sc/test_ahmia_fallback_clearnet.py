"""Sprint 8SC: Ahmia clearnet fallback."""

import pytest

from hledac.universal.intelligence.onion_seed_manager import OnionSeedManager


def test_ahmia_regex_in_manager_matches_v3():
    """The _RE_ONION_V3 used in onion_seed_manager matches V3 onions."""
    from hledac.universal.intelligence.onion_seed_manager import _RE_ONION_V3

    v3_onion = "zqktlwiuavvvqqt4ybvgvi7tyo4hjl5xgfuvpdf6otjiycgwqbym2qad"
    html = f"<html><body><a href='http://{v3_onion}.onion/'>Hidden Wiki</a></body></html>"

    found = _RE_ONION_V3.findall(html)
    assert v3_onion + ".onion" in found
