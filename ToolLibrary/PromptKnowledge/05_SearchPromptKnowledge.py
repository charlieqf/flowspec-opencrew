#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
INDEX_PATH = ROOT / "index" / "fts.sqlite"


def ensure_index() -> None:
    if INDEX_PATH.is_file():
        return
    subprocess.check_call([sys.executable, str(ROOT / "04_BuildPromptIndex.py")])


def build_fts_query(query: str) -> str:
    # Mirror the backend trigram query builder: ASCII words plus sliding 3-char
    # CJK windows so Chinese substrings match the trigram-tokenized index.
    pieces: list[str] = []
    for token in re.findall(r"[A-Za-z0-9_]{2,}", query):
        if token not in pieces:
            pieces.append(token)
    for run in re.findall(r"[一-鿿]+", query):
        for index in range(len(run) - 2):
            gram = run[index:index + 3]
            if gram not in pieces:
                pieces.append(gram)
    return " OR ".join(f'"{piece}"' for piece in pieces[:64])


def search(query: str, limit: int) -> list[dict]:
    ensure_index()
    fts_query = build_fts_query(query)
    if not fts_query:
        return []
    with sqlite3.connect(INDEX_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT d.*, bm25(prompt_docs_fts) AS rank
            FROM prompt_docs_fts
            JOIN prompt_docs d ON d.doc_id = prompt_docs_fts.doc_id
            WHERE prompt_docs_fts MATCH ?
            ORDER BY rank
            LIMIT ?
            """,
            (fts_query, max(1, min(limit, 20))),
        ).fetchall()
    return [
        {
            "doc_id": row["doc_id"],
            "title": row["title"],
            "trust_level": row["trust_level"],
            "model_family": json.loads(row["model_family"] or "[]"),
            "provider": row["provider"],
            "summary": row["summary"],
            "rules": json.loads(row["rules_json"] or "[]"),
            "rank": row["rank"],
        }
        for row in rows
    ]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()
    print(json.dumps(search(args.query, args.limit), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
