"""F-265 verification driver — runs all 22 hermetic tests outside pytest  # noqa: N999
(because the project's tests/conftest.py has a pre-existing hypothesis shadowing
bug that breaks pytest sessionstart globally).
"""
import importlib.util
import sys

sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac')
sys.path.insert(0, '/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal')

# Bypass the broken intelligence/__init__.py (circular import pre-existing
# project bug) — load the module file directly.
_MODULE_PATH = "/Users/vojtechhamada/PycharmProjects/Hledac/hledac/universal/intelligence/open_source_collectors.py"
_spec = importlib.util.spec_from_file_location("osc", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
osc = importlib.util.module_from_spec(_spec)
sys.modules["osc"] = osc
_spec.loader.exec_module(osc)

import asyncio  # noqa: E402
import inspect  # noqa: E402
import json  # noqa: E402
from unittest.mock import patch  # noqa: E402


def _ok(text, status=200):
    return osc.FetchResult(status_code=status, text=text, error=None)


def _fail_404():
    return osc.FetchResult(status_code=404, text=None, error="not found")


class _Counter:
    """Manual call counter — works with any async function signature."""
    def __init__(self):
        self.n = 0


def _patch_fetch(handler):
    """Patch async_fetch_public_text with `handler(url, timeout_s, max_bytes)`.
    Returns (context, counter) — counter.n is updated per call.
    """
    counter = _Counter()

    async def wrapped(url, timeout_s, max_bytes):
        counter.n += 1
        return await handler(url, timeout_s, max_bytes)

    return patch.object(osc, "async_fetch_public_text", new=wrapped), counter


results = []


def t(name, fn):
    osc._paste_cache.clear()
    osc._paste_inflight.clear()
    osc._paste_host_sems.clear()
    try:
        fn()
        results.append(("PASS", name))
    except Exception as e:
        results.append(("FAIL", f"{name}: {e}"))


# === Static / refactor evidence ===
def t1():
    for fn in (osc._scrape_privatebin, osc._scrape_ghostbin, osc._scrape_0bin):
        assert "_scrape_paste_site" in inspect.getsource(fn)
t("wrappers call base", t1)


def t2():
    assert inspect.iscoroutinefunction(osc._scrape_paste_site)
t("base is coroutine", t2)


def t3():
    for a in (osc.PRIVATEBIN_ADAPTER, osc.GHOSTBIN_ADAPTER, osc.ZEROBIN_ADAPTER):
        try:
            a.site_id = "x"
            raise AssertionError("not frozen")
        except (AttributeError, Exception):
            pass
t("adapters frozen", t3)


def t4():
    for fn in (osc._scrape_privatebin, osc._scrape_ghostbin, osc._scrape_0bin):
        params = list(inspect.signature(fn).parameters.values())
        assert len(params) == 1 and params[0].name == "paste_id"
t("wrappers preserve signature", t4)


# === URL builders ===
def t5():
    urls = osc.PRIVATEBIN_ADAPTER.build_url("abc")
    assert isinstance(urls, list) and len(urls) == 2 and "v2" in urls[0] and "v1" in urls[1]
    assert osc.GHOSTBIN_ADAPTER.build_url("x") == "https://ghostbin.com/paste/x/raw"
    assert osc.ZEROBIN_ADAPTER.build_url("x") == "https://0bin.net/p/x"
t("URL builders", t5)


# === Parsers ===
def t6():
    assert osc.PRIVATEBIN_ADAPTER.parse(json.dumps({"ct": "a", "adata": [1]}), "pid") == "[PrivateBin encrypted - id:pid]"  # noqa: E501
    assert osc.PRIVATEBIN_ADAPTER.parse(json.dumps({"content": "x"}), "pid") == "x"
    assert osc.PRIVATEBIN_ADAPTER.parse("not json", "pid") is None
t("privatebin parser", t6)


def t7():
    assert osc.GHOSTBIN_ADAPTER.parse("hello", "pid") == "hello"
    assert osc.GHOSTBIN_ADAPTER.parse("", "pid") is None
t("ghostbin parser", t7)


def t8():
    html = '<pre class="paste-content">this is long enough content</pre>'
    assert osc.ZEROBIN_ADAPTER.parse(html, "pid") == "this is long enough content"
    assert osc.ZEROBIN_ADAPTER.parse("<p>nope</p>", "pid") is None
t("0bin parser", t8)


# === Cache ===
def t9():
    async def handler(url, ts, mb):
        return _ok("text")
    ctx, c = _patch_fetch(handler)
    with ctx:
        r1 = asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc"))
        r2 = asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc"))
        assert r1 == "text" and r2 == "text" and c.n == 1
t("cache hit short-circuits", t9)


def t10():
    async def handler(url, ts, mb):
        return _ok("text")
    ctx, c = _patch_fetch(handler)
    with ctx:
        asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc"))
        key = ("ghostbin", "abc")
        ts, body = osc._paste_cache[key]
        osc._paste_cache[key] = (ts - osc._PASTE_CACHE_TTL_S - 1, body)
        asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc"))
        assert c.n == 2
t("TTL expiry re-fetches", t10)


def t11():
    async def handler(url, ts, mb):
        return _ok("text")
    ctx, c = _patch_fetch(handler)
    with ctx:
        for i in range(osc._PASTE_CACHE_MAX):
            asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, f"id_{i:06d}"))
        assert len(osc._paste_cache) == osc._PASTE_CACHE_MAX
        asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "id_overflow"))
        evict_count = max(1, int(osc._PASTE_CACHE_MAX * osc._PASTE_CACHE_EVICT_FRAC))
        assert len(osc._paste_cache) == osc._PASTE_CACHE_MAX - evict_count + 1
        assert ("ghostbin", "id_000000") not in osc._paste_cache
t("FIFO eviction", t11)


def t12():
    async def handler(url, ts, mb):
        return _fail_404()
    ctx, c = _patch_fetch(handler)
    with ctx:
        r1 = asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "missing"))
        r2 = asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "missing"))
        assert r1 is None and r2 is None and c.n == 1
t("negative cached", t12)


# === Dedup ===
def t13():
    async def slow(url, ts, mb):
        await asyncio.sleep(0.02)
        return _ok("text")
    ctx, c = _patch_fetch(slow)
    with ctx:
        async def runner():
            return await asyncio.gather(*[osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "shared") for _ in range(10)])

        rs = asyncio.run(runner())
        assert all(r == "text" for r in rs) and c.n == 1, f"got {c.n}"
t("in-flight dedup 10->1", t13)


# === Semaphore ===
def t14():
    async def handler(url, ts, mb):
        return _ok("text")
    ctx, c = _patch_fetch(handler)
    with ctx:
        assert len(osc._paste_host_sems) == 0
        asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc"))
        assert osc._paste_host_sems["ghostbin.com"]._value == osc._PASTE_HOST_SEMAPHORE
t("semaphore lazy", t14)


def t15():
    in_flight = 0
    max_concurrent = 0

    async def track(url, ts, mb):
        nonlocal in_flight, max_concurrent
        in_flight += 1
        max_concurrent = max(max_concurrent, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return _ok("text")

    ctx, c = _patch_fetch(track)
    with ctx:
        async def runner():
            await asyncio.gather(*[osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, f"id_{i}") for i in range(10)])

        asyncio.run(runner())
    assert max_concurrent <= osc._PASTE_HOST_SEMAPHORE, f"max={max_concurrent}"
t("semaphore cap", t15)


# === Fail-soft ===
def t16():
    async def handler(url, ts, mb):
        return _fail_404()
    ctx, c = _patch_fetch(handler)
    with ctx:
        assert asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc")) is None
t("404 -> None", t16)


def t17():
    async def fail(url, ts, mb):
        raise ConnectionError("net")

    ctx, c = _patch_fetch(fail)
    with ctx:
        assert asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc")) is None
t("exception -> None", t17)


def t18():
    async def cancel(url, ts, mb):
        raise asyncio.CancelledError()

    ctx, c = _patch_fetch(cancel)
    with ctx:
        try:
            asyncio.run(osc._scrape_paste_site(osc.GHOSTBIN_ADAPTER, "abc"))
            raise AssertionError("should have raised")
        except asyncio.CancelledError:
            pass
t("CancelledError reraises", t18)


# === PrivateBin fallback ===
def t19():
    async def fallback(url, ts, mb):
        if "v2" in url:
            return _fail_404()
        if "v1" in url:
            return _ok(json.dumps({"content": "v1 content"}))
        return _fail_404()

    ctx, c = _patch_fetch(fallback)
    with ctx:
        assert asyncio.run(osc._scrape_paste_site(osc.PRIVATEBIN_ADAPTER, "pid")) == "v1 content"
t("privatebin v2->v1", t19)


def t20():
    cnt = 0

    async def fallback(url, ts, mb):
        nonlocal cnt
        cnt += 1
        if "v2" in url:
            return _ok("not valid")
        if "v1" in url:
            return _ok(json.dumps({"content": "fb"}))
        return _fail_404()

    ctx, c = _patch_fetch(fallback)
    with ctx:
        assert asyncio.run(osc._scrape_paste_site(osc.PRIVATEBIN_ADAPTER, "pid")) == "fb"
        assert cnt == 2
t("privatebin v2 invalid -> v1", t20)


# === Bounds ===
def t21():
    assert 100 <= osc._PASTE_CACHE_MAX <= 5000
    assert 60.0 <= osc._PASTE_CACHE_TTL_S <= 86_400.0
    assert 1 <= osc._PASTE_HOST_SEMAPHORE <= 10
    for a in (osc.PRIVATEBIN_ADAPTER, osc.GHOSTBIN_ADAPTER, osc.ZEROBIN_ADAPTER):
        assert a.max_bytes == 2 * 1024 * 1024
        assert a.timeout_s == 10.0
t("M1 bounds", t21)


# === Wrappers route through base ===
def t22():
    async def handler(url, ts, mb):
        return _ok("body")
    ctx, c = _patch_fetch(handler)
    with ctx:
        async def runner():
            r1 = await osc._scrape_ghostbin("p")
            r2 = await osc._scrape_privatebin("p")
            r3 = await osc._scrape_0bin("p")
            return r1, r2, r3

        r1, r2, r3 = asyncio.run(runner())
        assert r1 == "body" and c.n == 3
t("wrappers route", t22)


# Report
passed = sum(1 for s, _ in results if s == "PASS")
total = len(results)
for s, n in results:
    print(f"  {s}  {n}")
print(f"\n{'=' * 60}")
print(f"RESULT: {passed}/{total} tests passed ({passed / total * 100:.0f}%)")
print(f"{'=' * 60}")
sys.exit(0 if passed == total else 1)
