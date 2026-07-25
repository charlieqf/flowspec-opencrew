#!/usr/bin/env python3
"""Fetch enabled registry sources into raw/<source_id>/ (stdlib only).

Design doc 6.2: offline ingestion, not per-conversation crawling. Reads
registry/sources.json; for each enabled source with a `urls` list, GETs each
url and writes the body plus a meta.json (url, status, content_type, fetched_at,
license). Networking is opt-in by running this tool; it never runs at request
time. Respects a short timeout and a descriptive User-Agent.
"""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "registry" / "sources.json"
RAW_DIR = ROOT / "raw"
USER_AGENT = "OpenCrew-PromptKnowledge/0.1 (+offline prompt knowledge ingestion)"
TIMEOUT = 15
MAX_BYTES = 2_000_000


def safe_name(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip()) or "file"
    return name[:120]


def fetch_one(url: str) -> tuple[int, str, bytes]:
    if not str(url).lower().startswith("https://"):
        return 0, "error: only https sources are fetched", b""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read(MAX_BYTES + 1)
            return resp.status, resp.headers.get("Content-Type", ""), body[:MAX_BYTES]
    except urllib.error.HTTPError as err:
        return err.code, "", b""
    except Exception as err:  # noqa: BLE001 - record any transport failure
        return 0, f"error: {err}", b""


def main() -> int:
    if not SOURCES.is_file():
        print("no registry/sources.json", file=sys.stderr)
        return 1
    registry = json.loads(SOURCES.read_text(encoding="utf-8"))
    sources = registry.get("sources") if isinstance(registry, dict) else []
    fetched = 0
    for source in sources or []:
        if not isinstance(source, dict) or not source.get("enabled"):
            continue
        urls = source.get("urls") if isinstance(source.get("urls"), list) else []
        if not urls:
            continue
        source_id = safe_name(source.get("source_id"))
        out_dir = RAW_DIR / source_id
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for index, url in enumerate(urls):
            status, content_type, body = fetch_one(str(url))
            ok = status == 200 and body
            filename = safe_name(f"{index:02d}_{str(url).rsplit('/', 1)[-1] or 'index'}")
            if ok:
                (out_dir / filename).write_bytes(body)
                fetched += 1
            files.append({"url": url, "status": status, "content_type": content_type, "bytes": len(body), "file": filename if ok else ""})
            print(f"[{ '200' if ok else status}] {url}")
        meta = {
            "source_id": source.get("source_id"),
            "source_type": source.get("source_type"),
            "model_family": source.get("model_family") or [],
            "provider": source.get("provider") or "",
            "trust_level": source.get("trust_level") or "community_article",
            "license": source.get("license") or "unknown",
            "fetched_at": int(time.time()),
            "files": files,
        }
        (out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"fetched {fetched} file(s) -> {RAW_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
