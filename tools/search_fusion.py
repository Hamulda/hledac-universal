from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def normalize_url(url: str) -> str:
    if not url:
        return ""
    p = urlsplit(url.strip())
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path.rstrip("/")
    return urlunsplit((p.scheme.lower() or "https", netloc, path, "", ""))

def normalize_title(title: str) -> str:
    return " ".join((title or "").lower().split())

def reciprocal_rank_fusion(rows: list[dict], k: int = 60) -> list[dict]:
    merged: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (normalize_url(row.get("url", "")), normalize_title(row.get("title", "")))
        if key == ("", ""):
            continue
        score = 1.0 / (k + int(row.get("rank", 9999)))
        cur = merged.get(key)
        if cur is None:
            item = dict(row)
            item["_rrf"] = score
            item["_providers"] = {row.get("provider", "")}
            merged[key] = item
        else:
            cur["_rrf"] += score
            cur["_providers"].add(row.get("provider", ""))
            if len(row.get("snippet", "")) > len(cur.get("snippet", "")):
                cur["snippet"] = row.get("snippet", "")
    out = []
    for row in merged.values():
        row["provider_count"] = len(row.pop("_providers"))
        row["score"] = row.pop("_rrf") + 0.02 * row["provider_count"]
        out.append(row)
    out.sort(key=lambda x: x["score"], reverse=True)
    return out

def top_k(rows: list[dict], k: int = 10) -> list[dict]:
    return reciprocal_rank_fusion(rows)[:k]
