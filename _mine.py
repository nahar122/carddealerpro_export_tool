"""Mine parallel vocabulary candidates from the actual CardTemplate format.

Real format (discovered):
  line3 = subset, sometimes with `#<num>` appended, sometimes with `Insert`
          or `Rookie Insert` suffix
  line4 = `(parallel)` in parens, OR `#<num>` when no parallel

So:
  - the parallel content is the inside of the parens in line4
  - the bare subset is line3 stripped of trailing `#...`, ` Insert`,
    ` Rookie Insert`, ` Rookie`, optional trailing fraction `(/N)`
"""
import os
import re
import psycopg2
from collections import Counter

from transform_cards import load_parallel_vocab, VOCAB_PATH

conn = psycopg2.connect(
    host='aws-1-us-east-1.pooler.supabase.com', port=6543, dbname='postgres',
    user='postgres.exkeiyibgugzenshcteh',
    password=os.environ['SUPABASE_DB_PASSWORD'], sslmode='require',
)
cur = conn.cursor()

# Strip trailing card-number tokens, suffix words, and fraction qualifiers
SUBSET_TAIL = re.compile(
    r"(\s+#[A-Za-z0-9-]+(\s+/?\d+)?)?"          # optional trailing #card-num
    r"(\s+\(/\d+\))?"                            # optional /serial
    r"(\s+Rookie\s+Insert|\s+Insert|\s+Rookie)?" # optional suffix word(s)
    r"\s*$"
)

PARALLEL_PARENS = re.compile(r"^\((.+)\)$")
SERIAL_TAIL = re.compile(r"\s*/\d+\s*$")  # e.g. trailing "/150"


def bare_subset(line3: str) -> str:
    s = (line3 or "").strip()
    if not s:
        return ""
    # Iteratively strip trailing patterns
    prev = None
    while prev != s:
        prev = s
        s = SUBSET_TAIL.sub("", s).strip()
    return s


def parallel_text(line4: str) -> str | None:
    """Return the inside-parens parallel description, or None if line4 isn't a parallel."""
    s = (line4 or "").strip()
    if not s:
        return None
    m = PARALLEL_PARENS.match(s)
    if not m:
        return None
    inner = m.group(1).strip()
    # Strip trailing /N serial number
    inner = SERIAL_TAIL.sub("", inner).strip()
    return inner if inner else None


cur.execute('SELECT line3, line4 FROM "CardTemplate"')
rows = cur.fetchall()

sub_words: Counter[str] = Counter()
par_words: Counter[str] = Counter()
par_bigrams: Counter[str] = Counter()
par_trigrams: Counter[str] = Counter()
par_full: Counter[str] = Counter()  # full inner-parens strings

n_with_parallel = 0
n_total = 0

for line3, line4 in rows:
    n_total += 1
    bs = bare_subset(line3)
    if bs:
        for w in bs.split():
            sub_words[w.lower()] += 1
    pt = parallel_text(line4)
    if pt is not None:
        n_with_parallel += 1
        par_full[pt] += 1
        ws = pt.split()
        for w in ws:
            par_words[w.lower()] += 1
        for i in range(len(ws) - 1):
            par_bigrams[" ".join(w.lower() for w in ws[i:i + 2])] += 1
        for i in range(len(ws) - 2):
            par_trigrams[" ".join(w.lower() for w in ws[i:i + 3])] += 1

print(f"=== {n_total:,} rows scanned, {n_with_parallel:,} have a parens parallel in line4 ===")
print(f"\n=== Top 25 full parallel strings ===")
for s, c in par_full.most_common(25):
    print(f"  {c:>5}  ({s})")

tokens, qualifiers = load_parallel_vocab(VOCAB_PATH)
print(f"\n=== Existing vocab: {len(tokens)} tokens, {len(qualifiers)} qualifiers ===")

# Candidate token: appears >=10 times in parallel, >=85% of usages in parallel
print(f"\n=== TOKEN CANDIDATES (>=10 parallel-uses, >=85% in parallel, not already in vocab) ===")
candidates = []
for w, pc in par_words.items():
    if pc < 10:
        continue
    if w in tokens:
        continue
    sc = sub_words.get(w, 0)
    ratio = pc / (pc + sc)
    if ratio >= 0.85:
        candidates.append((w, pc, sc, ratio))
candidates.sort(key=lambda x: -x[1])
for w, pc, sc, r in candidates:
    in_q = " (already qualifier)" if w in qualifiers else ""
    print(f"  {w:<22} par={pc:<6} sub={sc:<5} ({r:.0%}){in_q}")

# Qualifier candidate: 30–85% in parallel
print(f"\n=== QUALIFIER CANDIDATES (>=10 parallel-uses, 30–85% in parallel, not already in vocab) ===")
qcandidates = []
for w, pc in par_words.items():
    if pc < 10:
        continue
    if w in tokens or w in qualifiers:
        continue
    sc = sub_words.get(w, 0)
    if sc + pc == 0:
        continue
    ratio = pc / (pc + sc)
    if 0.30 <= ratio < 0.85:
        qcandidates.append((w, pc, sc, ratio))
qcandidates.sort(key=lambda x: -x[1])
for w, pc, sc, r in qcandidates[:30]:
    print(f"  {w:<22} par={pc:<6} sub={sc:<5} ({r:.0%})")

# Multi-word candidates: bigrams/trigrams >=5 hits where >=1 word is unknown
def has_unknown(ng: str) -> bool:
    return any(w not in tokens and w not in qualifiers for w in ng.split())

print(f"\n=== BIGRAM CANDIDATES (>=5 hits, >=1 unknown word) ===")
bg = sorted(((g, c) for g, c in par_bigrams.items() if c >= 5 and has_unknown(g)), key=lambda x: -x[1])
for g, c in bg[:40]:
    print(f"  {c:>5}  '{g}'")

print(f"\n=== TRIGRAM CANDIDATES (>=5 hits, >=1 unknown word) ===")
tg = sorted(((g, c) for g, c in par_trigrams.items() if c >= 5 and has_unknown(g)), key=lambda x: -x[1])
for g, c in tg[:25]:
    print(f"  {c:>5}  '{g}'")

# Save raw distributions for review
with open("db_analysis_par_full.txt", "w", encoding="utf-8") as f:
    for s, c in par_full.most_common():
        f.write(f"{c}\t{s}\n")
with open("db_analysis_par_words.txt", "w", encoding="utf-8") as f:
    for w, c in par_words.most_common():
        f.write(f"{c}\t{w}\n")
with open("db_analysis_sub_words.txt", "w", encoding="utf-8") as f:
    for w, c in sub_words.most_common():
        f.write(f"{c}\t{w}\n")

print("\nWrote db_analysis_par_full.txt, db_analysis_par_words.txt, db_analysis_sub_words.txt")
conn.close()
