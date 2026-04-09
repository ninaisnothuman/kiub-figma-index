#!/usr/bin/env python3
"""
find.py — Slack-style design lookup CLI for the Kiub Figma project.

Usage:
  python3 find.py "upgrade plan modal"
  python3 find.py "slack reaction tasks" --limit 5
  python3 find.py "settings" --include-junk

Reads index.json (built by build_index.py). Pure-Python BM25-ish ranking
over the search_blob field, plus a status-based rerank
(active > draft > experiment > archived; junk excluded by default).

This is the same lookup a Slack /find-design slash command would run.
"""

import argparse
import json
import math
import re
from collections import Counter
from pathlib import Path

INDEX_PATH = Path(__file__).resolve().parent.parent / "index.json"

STATUS_BOOST = {
    "active": 1.0,
    "draft": 0.7,
    "experiment": 0.5,
    "archived": 0.4,
    "junk": 0.05,
}


def tokenize(text):
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


def bm25_score(query_tokens, doc_tokens, df, n_docs, avgdl, k1=1.5, b=0.75):
    tf = Counter(doc_tokens)
    dl = len(doc_tokens) or 1
    score = 0.0
    for q in query_tokens:
        if q not in df:
            continue
        idf = math.log(1 + (n_docs - df[q] + 0.5) / (df[q] + 0.5))
        f = tf.get(q, 0)
        denom = f + k1 * (1 - b + b * dl / avgdl)
        score += idf * (f * (k1 + 1)) / (denom or 1)
    return score


def search(query, limit=3, include_junk=False):
    index = json.loads(INDEX_PATH.read_text())
    pages = index["pages"]
    if not include_junk:
        pages = [p for p in pages if p.get("status") != "junk"]

    docs = [tokenize(p["search_blob"]) for p in pages]
    # Tag/cover field tokens get an exact-match boost on top of BM25
    cover_tokens = [
        set(tokenize(" ".join(p.get("covers", []) + p.get("tags", []) + p.get("file_tags", []))))
        for p in pages
    ]
    avgdl = sum(len(d) for d in docs) / max(len(docs), 1)
    df = Counter()
    for d in docs:
        for t in set(d):
            df[t] += 1

    q_tokens = tokenize(query)
    scored = []
    for page, doc, ctokens in zip(pages, docs, cover_tokens):
        s = bm25_score(q_tokens, doc, df, len(docs), avgdl)
        if s == 0:
            continue
        # +1.5 per query token that is an exact tag/cover hit
        cover_hits = sum(1 for q in q_tokens if q in ctokens)
        s += 1.5 * cover_hits
        s *= STATUS_BOOST.get(page.get("status", "active"), 0.5)
        scored.append((s, page))

    scored.sort(key=lambda x: -x[0])
    return scored[:limit]


def fmt(scored):
    if not scored:
        return "(no matches)"
    out = []
    for i, (score, p) in enumerate(scored, 1):
        section = f" / {p['section']}" if p.get("section") else ""
        line = (
            f"{i}. [{p['status']}] {p['file_name']}{section} → {p['page_name']}\n"
            f"   {p['purpose'].strip()}\n"
            f"   tags: {', '.join(p.get('covers', []))}\n"
            f"   {p['deeplink']}\n"
            f"   score={score:.2f}"
        )
        out.append(line)
    return "\n\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query", nargs="+")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--include-junk", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    query = " ".join(args.query)
    results = search(query, limit=args.limit, include_junk=args.include_junk)
    if args.json:
        print(json.dumps([
            {"score": s, **{k: v for k, v in p.items() if k != "search_blob"}}
            for s, p in results
        ], indent=2))
    else:
        print(f"Query: {query}\n")
        print(fmt(results))


if __name__ == "__main__":
    main()
