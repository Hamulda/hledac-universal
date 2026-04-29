"""
Test 8: fetch_via_curl_cffi has an active runtime call site in public_fetcher.py.

Verifies F206M activation: curl_cffi stealth lane is wired into
the public acquisition path, not just imported by tests.
"""

import ast


def test_fetch_via_curl_cffi_has_active_callsite_in_public_fetcher():
    """
    Static/source test: fetch_via_curl_cffi must appear as a called function
    in fetching/public_fetcher.py — not only imported.

    This is the canonical F206M "seal" test: the lane is live.
    """
    import pathlib

    pf_path = pathlib.Path(__file__).parents[2] / "fetching" / "public_fetcher.py"
    src = pf_path.read_text()

    # Must be imported at top level
    assert "from hledac.universal.transport.curl_cffi_fetch import fetch_via_curl_cffi" in src, \
        "fetch_via_curl_cffi must be imported at top level in public_fetcher.py"
    assert "fetch_via_curl_cffi" in src, \
        "fetch_via_curl_cffi must appear in public_fetcher.py"

    # Must be called (not just imported)
    # We look for the actual invocation pattern
    tree = ast.parse(src)
    calls = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id == "fetch_via_curl_cffi":
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute) and node.func.attr == "fetch_via_curl_cffi":
                calls.append(f"attr.{node.func.attr}")

    assert "fetch_via_curl_cffi" in calls, (
        f"fetch_via_curl_cffi must be called in public_fetcher.py, "
        f"found nodes: {calls}"
    )


def test_should_use_curl_cffi_is_imported_in_public_fetcher():
    """should_use_curl_cffi must be imported from curl_cffi_transport."""
    import pathlib

    pf_path = pathlib.Path(__file__).parents[2] / "fetching" / "public_fetcher.py"
    src = pf_path.read_text()

    assert "from hledac.universal.transport.curl_cffi_transport import should_use_curl_cffi" in src, (
        "should_use_curl_cffi must be imported in public_fetcher.py"
    )


def test_curl_cffi_injection_is_before_tor_session_setup():
    """
    curl_cffi lane is injected between stealth_session and Tor session setup.
    This ordering ensures curl_cffi runs on clearnet BEFORE darknet routing.
    """
    import pathlib

    pf_path = pathlib.Path(__file__).parents[2] / "fetching" / "public_fetcher.py"
    src = pf_path.read_text()

    curl_idx = src.find("# --- F206M: curl_cffi stealth lane")
    tor_idx = src.find("# --- P4: Tor session setup")
    stealth_idx = src.find("# --- P4: Canonical stealth session setup")

    assert curl_idx > stealth_idx > 0, (
        f"curl_cffi injection must come after stealth_session setup "
        f"(stealth_idx={stealth_idx}, curl_idx={curl_idx})"
    )
    assert tor_idx > curl_idx > 0, (
        f"Tor session setup must come after curl_cffi injection "
        f"(curl_idx={curl_idx}, tor_idx={tor_idx})"
    )
