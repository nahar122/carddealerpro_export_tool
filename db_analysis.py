#!/usr/bin/env python3
"""DB analysis driver for the parallel-vocab heuristic.

Connects to the Supabase Postgres CardTemplate table (read-only) and runs
a pipeline of passes that:
  1. Discover the schema
  2. Dump per-column distributions
  3. Mine candidate vocab terms from the labeled subset/parallel columns
  4. Validate the current splitter against the labeled rows
  5. (run separately, after vocab patches) re-validate to confirm gains

Credentials are read from the SUPABASE_DB_PASSWORD env var. All queries are
SELECT-only.

Usage:
    SUPABASE_DB_PASSWORD=... python db_analysis.py <pass>
where <pass> is one of: schema, distrib, mine, validate, all.
"""

from __future__ import annotations

import csv
import os
import re
import sys
from collections import Counter
from pathlib import Path

import psycopg2
import psycopg2.extras

# Import the splitter so we test exactly what the production CSV transformer
# uses. Module-level vocab is loaded at import time from parallel_vocab.txt.
from transform_cards import (
    load_parallel_vocab,
    split_subset_parallel,
    transform_subset,
    VOCAB_PATH,
)

# The direct host (db.<ref>.supabase.co) is IPv6-only; on an IPv4-only network
# the connection fails to resolve. Use the regional pooler endpoint instead —
# it's IPv4-reachable. Username format for the pooler is "postgres.<project-ref>".
DB_HOST = "aws-1-us-east-1.pooler.supabase.com"
DB_PORT = 6543
DB_NAME = "postgres"
DB_USER = "postgres.exkeiyibgugzenshcteh"
TABLE = '"CardTemplate"'

OUT_DIR = Path(__file__).resolve().parent
SUFFIX_RE = re.compile(r"\s+(?:Rookie\s+Insert|Insert|Rookie)\s*$")


def connect():
    pwd = os.environ.get("SUPABASE_DB_PASSWORD")
    if not pwd:
        sys.exit("SUPABASE_DB_PASSWORD is not set; refusing to run.")
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER,
        password=pwd, sslmode="require",
    )


def pass_schema(conn):
    """Pass 1: dump the CardTemplate schema and a sample of rows."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'CardTemplate' "
            "ORDER BY ordinal_position"
        )
        cols = cur.fetchall()
        print(f"=== CardTemplate schema ({len(cols)} columns) ===")
        for name, dtype, nullable in cols:
            print(f"  {name:<32} {dtype:<20} {'NULL' if nullable == 'YES' else 'NOT NULL'}")

        col_names = [c[0] for c in cols]
        cur.execute(f"SELECT COUNT(*) FROM {TABLE}")
        total = cur.fetchone()[0]
        print(f"\n=== Row count: {total:,} ===")

        cur.execute(f"SELECT * FROM {TABLE} LIMIT 10")
        rows = cur.fetchall()
        print(f"\n=== 10 sample rows ===")
        for i, row in enumerate(rows, 1):
            print(f"--- row {i} ---")
            for name, val in zip(col_names, row):
                if val is not None and str(val) != "":
                    s = str(val)
                    if len(s) > 100:
                        s = s[:97] + "..."
                    print(f"  {name}: {s}")
        return col_names, total


def strip_suffix(s: str) -> str:
    """Remove trailing ' Insert', ' Rookie', ' Rookie Insert' from a labeled subset."""
    if not s:
        return ""
    return SUFFIX_RE.sub("", s).strip()


def pass_distrib(conn, sub_col: str, par_col: str):
    """Pass 2: per-column distinct-value distributions."""
    for col in (sub_col, par_col):
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {col}, COUNT(*) FROM {TABLE} "
                f"WHERE {col} IS NOT NULL AND {col} != '' "
                f"GROUP BY {col} ORDER BY 2 DESC"
            )
            rows = cur.fetchall()
        out = OUT_DIR / f"db_analysis_{col}.txt"
        with out.open("w", encoding="utf-8") as f:
            for val, cnt in rows:
                f.write(f"{cnt}\t{val}\n")
        print(f"Wrote {len(rows):,} distinct values to {out}")

    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {sub_col}, {par_col}, COUNT(*) FROM {TABLE} "
            f"GROUP BY {sub_col}, {par_col} ORDER BY 3 DESC LIMIT 200"
        )
        rows = cur.fetchall()
    out = OUT_DIR / "db_analysis_joint.txt"
    with out.open("w", encoding="utf-8") as f:
        f.write("count\tsubset\tparallel\n")
        for s, p, c in rows:
            f.write(f"{c}\t{s or ''}\t{p or ''}\n")
    print(f"Wrote 200 most-common (subset, parallel) pairs to {out}")


def pass_mine(conn, sub_col: str, par_col: str, tokens, qualifiers):
    """Pass 3: mine candidate vocab additions."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {sub_col}, {par_col} FROM {TABLE}")
        rows = cur.fetchall()

    sub_words: Counter[str] = Counter()
    par_words: Counter[str] = Counter()
    par_bigrams: Counter[str] = Counter()
    par_trigrams: Counter[str] = Counter()

    for s, p in rows:
        if s:
            for w in strip_suffix(s).split():
                sub_words[w.lower()] += 1
        if p:
            ws = p.split()
            for w in ws:
                par_words[w.lower()] += 1
            for i in range(len(ws) - 1):
                par_bigrams[" ".join(w.lower() for w in ws[i:i + 2])] += 1
            for i in range(len(ws) - 2):
                par_trigrams[" ".join(w.lower() for w in ws[i:i + 3])] += 1

    print(f"\n=== Vocab mining ({len(rows):,} rows) ===")

    # Token candidates: appear ≥10 times, ≥90% of usage in parallel column
    token_candidates = []
    for w, pc in par_words.items():
        if pc < 10:
            continue
        sc = sub_words.get(w, 0)
        ratio = pc / (pc + sc)
        if ratio >= 0.9 and w not in tokens:
            token_candidates.append((w, pc, sc, ratio))
    token_candidates.sort(key=lambda x: -x[1])
    print(f"\n[token candidates] ({len(token_candidates)} new, ≥10 par occurrences, ≥90% in parallel)")
    for w, pc, sc, r in token_candidates[:60]:
        print(f"  {w:<25} par={pc:<6} sub={sc:<5} ({r:.0%})")

    # Qualifier candidates: appear ≥10 times in parallel, 30–70% in parallel
    qualifier_candidates = []
    for w, pc in par_words.items():
        if pc < 10:
            continue
        sc = sub_words.get(w, 0)
        if sc + pc == 0:
            continue
        ratio = pc / (pc + sc)
        if 0.30 <= ratio < 0.90 and w not in qualifiers and w not in tokens:
            qualifier_candidates.append((w, pc, sc, ratio))
    qualifier_candidates.sort(key=lambda x: -x[1])
    print(f"\n[qualifier candidates] ({len(qualifier_candidates)} new)")
    for w, pc, sc, r in qualifier_candidates[:30]:
        print(f"  {w:<25} par={pc:<6} sub={sc:<5} ({r:.0%})")

    # Multi-word candidates: bigrams/trigrams that appear ≥5 times AND every
    # word inside is *not* a known parallel token (otherwise the splitter
    # already handles it via the single-word vocab).
    def needs_multi(ngram: str) -> bool:
        words = ngram.split()
        return any(w not in tokens for w in words)

    print(f"\n[multi-word candidates: bigrams ≥5 hits, ≥1 unknown word]")
    bg = sorted(
        ((g, c) for g, c in par_bigrams.items() if c >= 5 and needs_multi(g)),
        key=lambda x: -x[1],
    )
    for g, c in bg[:30]:
        print(f"  '{g}'  ({c})")

    print(f"\n[multi-word candidates: trigrams ≥5 hits, ≥1 unknown word]")
    tg = sorted(
        ((g, c) for g, c in par_trigrams.items() if c >= 5 and needs_multi(g)),
        key=lambda x: -x[1],
    )
    for g, c in tg[:20]:
        print(f"  '{g}'  ({c})")

    return {
        "tokens": [w for w, *_ in token_candidates],
        "qualifiers": [w for w, *_ in qualifier_candidates],
        "bigrams": [g for g, _ in bg[:50]],
        "trigrams": [g for g, _ in tg[:30]],
    }


def pass_validate(conn, sub_col: str, par_col: str, tokens, qualifiers):
    """Pass 4: run the splitter on each row and bucket the disagreements."""
    with conn.cursor() as cur:
        cur.execute(f"SELECT {sub_col}, {par_col} FROM {TABLE}")
        rows = cur.fetchall()

    buckets = Counter()
    mismatches = []

    for labeled_subset, labeled_parallel in rows:
        labeled_subset = labeled_subset or ""
        labeled_parallel = labeled_parallel or ""

        # Skip fully-empty rows
        if not labeled_subset and not labeled_parallel:
            continue

        # Detect RC marker from labeled subset
        has_rc = bool(re.search(r"\bRookie\b", labeled_subset))

        bare = strip_suffix(labeled_subset)
        # If bare is "Base" the human kept everything in parallel; reconstruct
        # raw input as just the parallel.
        if bare.lower() == "base":
            raw = labeled_parallel
        elif bare.lower() == "rookie":
            # subset is purely "Rookie" with no insert → raw is just parallel
            raw = labeled_parallel
            bare = ""
        else:
            # bare keeps the insert name; raw reconstructs as insert + parallel
            raw = (bare + " " + labeled_parallel).strip()

        attrs = "RC" if has_rc else ""
        pred_subset, pred_parallel, _case = transform_subset(
            raw, attrs, tokens, qualifiers
        )

        if pred_subset == labeled_subset and pred_parallel == labeled_parallel:
            buckets["match"] += 1
            continue

        # Categorize mismatch
        pred_lower = pred_parallel.lower()
        lab_lower = labeled_parallel.lower()
        if lab_lower.startswith(pred_lower) and pred_lower:
            bucket = "vocab_gap"
        elif pred_lower.startswith(lab_lower) and lab_lower and pred_lower != lab_lower:
            bucket = "over_split"
        elif strip_suffix(pred_subset) == strip_suffix(labeled_subset) and pred_parallel == labeled_parallel:
            bucket = "suffix_mismatch"
        else:
            bucket = "mismatch_other"
        buckets[bucket] += 1
        if len(mismatches) < 5000:  # cap for memory
            mismatches.append({
                "bucket": bucket,
                "raw": raw,
                "label_subset": labeled_subset,
                "label_parallel": labeled_parallel,
                "pred_subset": pred_subset,
                "pred_parallel": pred_parallel,
            })

    total = sum(buckets.values())
    print(f"\n=== Validation results ({total:,} non-empty rows) ===")
    for k in ("match", "vocab_gap", "over_split", "suffix_mismatch", "mismatch_other"):
        n = buckets[k]
        pct = 100 * n / total if total else 0
        print(f"  {k:<18} {n:>6,}  ({pct:5.1f}%)")

    out = OUT_DIR / "db_validation_mismatches.csv"
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "bucket", "raw", "label_subset", "label_parallel",
            "pred_subset", "pred_parallel",
        ])
        w.writeheader()
        w.writerows(mismatches)
    print(f"Wrote {len(mismatches):,} mismatches to {out}")
    return buckets


# Mapping of lineN → role, will be set after schema inspection.
LINE_MAP: dict[str, str] = {}


def detect_line_mapping(col_names: list[str]) -> dict[str, str]:
    """Heuristic mapping: line1=set, line2=player, line3=subset, line4=parallel.

    The plan says we'll confirm by eye, but we'll start with the strong-
    hypothesis defaults and verify after the first sample print.
    """
    mapping = {}
    for n in range(1, 5):
        col = f"line{n}"
        if col in col_names:
            mapping[col] = {1: "set", 2: "player", 3: "subset", 4: "parallel"}[n]
    return mapping


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    which = argv[1]

    conn = connect()
    try:
        col_names, total = pass_schema(conn)
        if which == "schema":
            return 0

        mapping = detect_line_mapping(col_names)
        sub_col = next((c for c, r in mapping.items() if r == "subset"), None)
        par_col = next((c for c, r in mapping.items() if r == "parallel"), None)
        if not sub_col or not par_col:
            print(f"Could not infer line→role mapping from columns: {col_names}",
                  file=sys.stderr)
            return 1
        print(f"\n=== Inferred mapping: {sub_col}=subset, {par_col}=parallel ===")

        tokens, qualifiers = load_parallel_vocab(VOCAB_PATH)
        if which in ("distrib", "all"):
            pass_distrib(conn, sub_col, par_col)
        if which in ("mine", "all"):
            pass_mine(conn, sub_col, par_col, tokens, qualifiers)
        if which in ("validate", "all"):
            pass_validate(conn, sub_col, par_col, tokens, qualifiers)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
