"""Validate transform_cards' splitter against the labeled CardTemplate.

For each row:
  - Recover the labeled (subset, parallel) by stripping # card-num from line3
    and ()s from line4.
  - Reconstruct what the splitter's raw input would have been by removing
    Phase 1's Insert/Rookie suffixes from the subset and concatenating with
    the parallel.
  - Run transform_subset on that raw input and compare.
"""
import csv
import os
import re
import psycopg2
from collections import Counter

from transform_cards import load_parallel_vocab, transform_subset, VOCAB_PATH

# Trailing card-number patterns inside line3 (eat #X, #X-Y, optionally with
# trailing serial like " /150" or "(/99)").
SUBSET_CARDNUM = re.compile(r"\s+#[A-Za-z0-9-]+(?:\s+/?\d+)?(?:\s*\(/\d+\))?\s*$")

# Phase-1 suffix patterns appended to the subset.
SUFFIX = re.compile(r"\s+(?:Rookie\s+Insert|Insert|Rookie)\s*$")

# Parens parallel: capture inside; tolerates trailing /N before the closing paren.
PARALLEL_PARENS = re.compile(r"^\((.+)\)\s*$")
SERIAL_TAIL = re.compile(r"\s*/\d+\s*$")


def split_label_subset(line3: str) -> tuple[str, str]:
    """Return (label_subset_no_cardnum, bare_subset_no_suffix).

    label_subset_no_cardnum is what Phase 1 should produce (with Insert/
    Rookie suffix retained). bare_subset_no_suffix is the pre-rules form,
    used to rebuild raw input.
    """
    s = (line3 or "").strip()
    if not s:
        return "", ""
    # Iteratively strip card-number tails (in case there are multiple)
    prev = None
    while prev != s:
        prev = s
        s = SUBSET_CARDNUM.sub("", s).strip()
    label = s
    bare = SUFFIX.sub("", s).strip()
    return label, bare


def split_label_parallel(line4: str) -> str:
    """Return labeled parallel string (parens stripped) or '' if line4 is just a #card-num."""
    s = (line4 or "").strip()
    if not s:
        return ""
    m = PARALLEL_PARENS.match(s)
    if not m:
        return ""  # line4 is a card-number or other noise, not a parallel
    inner = SERIAL_TAIL.sub("", m.group(1)).strip()
    return inner


def main():
    conn = psycopg2.connect(
        host='aws-1-us-east-1.pooler.supabase.com', port=6543, dbname='postgres',
        user='postgres.exkeiyibgugzenshcteh',
        password=os.environ['SUPABASE_DB_PASSWORD'], sslmode='require',
    )
    tokens, qualifiers = load_parallel_vocab(VOCAB_PATH)
    print(f"Loaded {len(tokens)} tokens, {len(qualifiers)} qualifiers")

    cur = conn.cursor()
    cur.execute('SELECT line3, line4 FROM "CardTemplate"')
    rows = cur.fetchall()
    conn.close()
    print(f"Pulled {len(rows):,} rows")

    buckets = Counter()
    samples = {b: [] for b in (
        "match", "vocab_gap", "over_split", "suffix_only", "case_only", "other"
    )}
    mismatches = []

    for line3, line4 in rows:
        label_sub, bare_sub = split_label_subset(line3)
        label_par = split_label_parallel(line4)

        # Skip fully-empty rows
        if not label_sub and not label_par:
            continue

        # Detect RC (subset ends with "Rookie" or "Rookie Insert")
        has_rc = bool(re.search(r"\bRookie\b", label_sub))

        # Reconstruct raw input the splitter would have seen.
        bare_lower = bare_sub.lower()
        if bare_lower == "base":
            raw = label_par
        elif bare_lower == "rookie":
            raw = label_par
        elif bare_lower == "":
            raw = label_par
        else:
            raw = (bare_sub + " " + label_par).strip()

        attrs = "RC" if has_rc else ""
        pred_sub, pred_par, _case = transform_subset(raw, attrs, tokens, qualifiers)

        if pred_sub == label_sub and pred_par == label_par:
            buckets["match"] += 1
            continue

        # Categorize mismatch
        if pred_sub.lower() == label_sub.lower() and pred_par.lower() == label_par.lower():
            bucket = "case_only"
        elif (pred_par.startswith(label_par) or label_par.startswith(pred_par)) \
                and pred_par != label_par and label_par:
            bucket = "vocab_gap" if len(pred_par) < len(label_par) else "over_split"
        elif pred_par == label_par:
            bucket = "suffix_only"
        else:
            bucket = "other"
        buckets[bucket] += 1
        if len(samples[bucket]) < 25:
            samples[bucket].append({
                "raw": raw,
                "label": (label_sub, label_par),
                "pred": (pred_sub, pred_par),
            })
        if len(mismatches) < 8000:
            mismatches.append({
                "bucket": bucket,
                "raw": raw,
                "label_subset": label_sub,
                "label_parallel": label_par,
                "pred_subset": pred_sub,
                "pred_parallel": pred_par,
            })

    total = sum(buckets.values())
    print(f"\n=== Validation results ({total:,} non-empty rows) ===")
    for k in ("match", "vocab_gap", "over_split", "suffix_only", "case_only", "other"):
        n = buckets[k]
        pct = 100 * n / total if total else 0
        print(f"  {k:<14} {n:>6,}  ({pct:5.1f}%)")

    for b in ("vocab_gap", "over_split", "suffix_only", "case_only", "other"):
        if not samples[b]:
            continue
        print(f"\n--- {b} samples ---")
        for ex in samples[b][:8]:
            print(f"  raw={ex['raw']!r}")
            print(f"     label  ={ex['label']}")
            print(f"     predict={ex['pred']}")

    out = "db_validation_mismatches.csv"
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "bucket", "raw", "label_subset", "label_parallel",
            "pred_subset", "pred_parallel",
        ])
        w.writeheader()
        w.writerows(mismatches)
    print(f"\nWrote {len(mismatches):,} mismatches to {out}")


if __name__ == "__main__":
    main()
