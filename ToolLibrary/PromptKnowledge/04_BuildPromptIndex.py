#!/usr/bin/env python3
from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent
NORMALIZED_DIR = ROOT / "normalized"
SEED_JSONL = NORMALIZED_DIR / "seed_rules.jsonl"
INDEX_PATH = ROOT / "index" / "fts.sqlite"


def normalized_files() -> list[Path]:
    if not NORMALIZED_DIR.is_dir():
        return []
    # Seed first so hand-curated docs win on doc_id collision (mirrors backend).
    return sorted(NORMALIZED_DIR.glob("*.jsonl"), key=lambda path: (path.name != "seed_rules.jsonl", path.name))


def text(value: object, default: str = "") -> str:
    if value is None or value == "":
        return default
    return str(value).strip()


def load_docs() -> list[dict]:
    docs: list[dict] = []
    seen: set[str] = set()
    for path in normalized_files():
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in lines:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue
            doc_id = text(payload.get("doc_id")) if isinstance(payload, dict) else ""
            if not doc_id or doc_id in seen:
                continue
            seen.add(doc_id)
            docs.append(payload)
    return docs


def doc_search_text(doc: dict) -> str:
    pieces = [
        text(doc.get("source_title")),
        text(doc.get("summary")),
        text(doc.get("content")),
        " ".join(str(item) for item in doc.get("tags") or []),
    ]
    for rule in doc.get("rules") if isinstance(doc.get("rules"), list) else []:
        if isinstance(rule, dict):
            pieces.append(text(rule.get("text")))
    return "\n".join(piece for piece in pieces if piece)


def main() -> int:
    docs = load_docs()
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(INDEX_PATH) as conn:
        conn.execute("DROP TABLE IF EXISTS prompt_docs")
        conn.execute("DROP TABLE IF EXISTS prompt_docs_fts")
        conn.execute("DROP TABLE IF EXISTS meta")
        conn.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("CREATE VIRTUAL TABLE prompt_docs_fts USING fts5(doc_id UNINDEXED, search_text, tokenize='trigram')")
        conn.execute(
            """
            CREATE TABLE prompt_docs(
                doc_id TEXT PRIMARY KEY,
                chunk_id TEXT,
                title TEXT,
                source_type TEXT,
                trust_level TEXT,
                model_family TEXT,
                provider TEXT,
                summary TEXT,
                content TEXT,
                tags_json TEXT,
                rules_json TEXT
            )
            """
        )
        for doc in docs:
            doc_id = text(doc.get("doc_id"))
            conn.execute(
                "INSERT INTO prompt_docs(doc_id, chunk_id, title, source_type, trust_level, model_family, provider, summary, content, tags_json, rules_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    doc_id,
                    text(doc.get("chunk_id")),
                    text(doc.get("source_title")),
                    text(doc.get("source_type")),
                    text(doc.get("trust_level")),
                    json.dumps(doc.get("model_family") or [], ensure_ascii=False),
                    text(doc.get("provider")),
                    text(doc.get("summary")),
                    text(doc.get("content")),
                    json.dumps(doc.get("tags") or [], ensure_ascii=False),
                    json.dumps(doc.get("rules") or [], ensure_ascii=False),
                ),
            )
            conn.execute("INSERT INTO prompt_docs_fts(doc_id, search_text) VALUES (?, ?)", (doc_id, doc_search_text(doc)))
        mtimes = [int(path.stat().st_mtime) for path in normalized_files() if path.exists()]
        source_mtime = max(mtimes) if mtimes else 0
        conn.execute("INSERT INTO meta(key, value) VALUES ('source_mtime', ?)", (str(source_mtime),))
        conn.commit()
    print(f"indexed {len(docs)} docs -> {INDEX_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
