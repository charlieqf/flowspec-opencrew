#!/usr/bin/env python3
"""Validate registry/sources.json and enrich GitHub sources (stdlib only).

Design doc 6.2 (01): discover/curate sources before fetch. This minimal version
validates the registry shape, and for github_repo sources optionally records
stars/license/updated_at from the GitHub API so a human can decide trust_level.
Writes reports/crawl_runs/discover.json. No fetching of doc bodies here (that is
02_SourceFetch).
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SOURCES = ROOT / "registry" / "sources.json"
REPORT = ROOT / "reports" / "crawl_runs" / "discover.json"
REQUIRED = ("source_id", "source_type", "trust_level", "enabled")
TRUST = {"official", "community_high_signal", "local_experience", "community_article", "experimental"}


def github_meta(repo: str) -> dict:
    try:
        req = urllib.request.Request(f"https://api.github.com/repos/{repo}", headers={"User-Agent": "OpenCrew-PromptKnowledge/0.1", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read(1_000_000))
        return {"stars": data.get("stargazers_count"), "license": (data.get("license") or {}).get("spdx_id"), "updated_at": data.get("updated_at")}
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError) as err:
        return {"error": str(err)}


def main() -> int:
    if not SOURCES.is_file():
        print("no registry/sources.json", file=sys.stderr)
        return 1
    registry = json.loads(SOURCES.read_text(encoding="utf-8"))
    sources = registry.get("sources") if isinstance(registry, dict) else []
    report = {"checked_at": int(time.time()), "count": len(sources or []), "problems": [], "sources": []}
    for source in sources or []:
        sid = source.get("source_id") if isinstance(source, dict) else None
        missing = [field for field in REQUIRED if not source.get(field) and source.get(field) is not False] if isinstance(source, dict) else REQUIRED
        if missing:
            report["problems"].append({"source_id": sid, "missing": list(missing)})
        if isinstance(source, dict) and source.get("trust_level") not in TRUST:
            report["problems"].append({"source_id": sid, "bad_trust_level": source.get("trust_level")})
        entry = {"source_id": sid, "type": source.get("source_type"), "enabled": bool(source.get("enabled"))}
        if isinstance(source, dict) and source.get("source_type") == "github_repo" and source.get("repo"):
            entry["github"] = github_meta(str(source["repo"]))
        report["sources"].append(entry)
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"count": report["count"], "problems": report["problems"]}, ensure_ascii=False))
    return 1 if report["problems"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
