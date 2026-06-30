import warnings

def test_intelligence_ti_deprecation():
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        try:
            import importlib
            import intelligence.ti_feed_adapter
            importlib.reload(intelligence.ti_feed_adapter)
        except Exception:  # noqa: BLE001
            pass
    cats = [str(x.category) for x in w]
    assert any("DeprecationWarning" in c for c in cats), \
        "intelligence.ti_feed_adapter musí emitovat DeprecationWarning"
