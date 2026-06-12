from __future__ import annotations


# ty: lazy import — the search package is in [search] extra. Module-level
# `DDGS = None` makes ty complain about None being called. Wrap in a
# function so the symbol is only resolved when actually needed.
def _import_ddgs() -> type | None:
    try:
        from ddgs import DDGS  # type: ignore[import-not-found]
        return DDGS
    except ImportError:
        return None

DDGS: type | None = _import_ddgs()
DDGS_AVAILABLE: bool = DDGS is not None

# Default set intentionally excludes google by default due fragility/captcha risk.
DEFAULT_TEXT_BACKENDS = ("brave", "bing", "duckduckgo", "mojeek", "wikipedia")
DEFAULT_NEWS_BACKENDS = ("bing", "duckduckgo", "yahoo")

def _normalize_text_item(item: dict, backend: str, rank: int) -> dict:
    return {
        "title": item.get("title") or "",
        "url": item.get("href") or item.get("url") or "",
        "snippet": item.get("body") or item.get("snippet") or "",
        "backend": backend,
        "rank": rank,
        "provider": "ddgs_text",
        "source": f"ddgs:{backend}",
    }

def _normalize_news_item(item: dict, backend: str, rank: int) -> dict:
    return {
        "title": item.get("title") or "",
        "url": item.get("url") or "",
        "snippet": item.get("body") or "",
        "backend": backend,
        "rank": rank,
        "provider": "ddgs_news",
        "source": f"ddgs-news:{backend}",
        "date": item.get("date"),
    }

def _unavailable_text(backends: tuple[str, ...]) -> list[dict]:
    return [{"title": "", "url": "", "snippet": "ddgs_not_installed", "backend": b, "rank": 9999, "provider": "ddgs_error", "source": f"ddgs:{b}"} for b in backends]  # noqa: E501

def _unavailable_news(backends: tuple[str, ...]) -> list[dict]:
    return [{"title": "", "url": "", "snippet": "ddgs_not_installed", "backend": b, "rank": 9999, "provider": "ddgs_error", "source": f"ddgs-news:{b}"} for b in backends]  # noqa: E501

def search_text_sync(query: str, backends: tuple[str, ...] = DEFAULT_TEXT_BACKENDS, max_results_per_backend: int = 4, timeout: int = 6) -> list[dict]:  # noqa: E501
    if not DDGS_AVAILABLE or DDGS is None:
        return _unavailable_text(backends)
    all_rows: list[dict] = []
    ddgs = DDGS(timeout=timeout)
    for backend in backends:
        try:
            rows = ddgs.text(query, backend=backend, max_results=max_results_per_backend)
            for i, row in enumerate(rows, 1):
                all_rows.append(_normalize_text_item(row, backend, i))
        except Exception as e:
            all_rows.append({
                "title": "",
                "url": "",
                "snippet": f"backend_failed:{backend}:{e}",
                "backend": backend,
                "rank": 9999,
                "provider": "ddgs_error",
                "source": f"ddgs:{backend}",
            })
    return all_rows

def search_news_sync(query: str, backends: tuple[str, ...] = DEFAULT_NEWS_BACKENDS, max_results_per_backend: int = 3, timeout: int = 6) -> list[dict]:  # noqa: E501
    if not DDGS_AVAILABLE or DDGS is None:
        return _unavailable_news(backends)
    all_rows: list[dict] = []
    ddgs = DDGS(timeout=timeout)
    for backend in backends:
        try:
            rows = ddgs.news(query, backend=backend, max_results=max_results_per_backend)
            for i, row in enumerate(rows, 1):
                all_rows.append(_normalize_news_item(row, backend, i))
        except Exception as e:
            all_rows.append({
                "title": "",
                "url": "",
                "snippet": f"news_backend_failed:{backend}:{e}",
                "backend": backend,
                "rank": 9999,
                "provider": "ddgs_error",
                "source": f"ddgs-news:{backend}",
            })
    return all_rows
