#!/usr/bin/env python3
"""Transform a card-export CSV into the 5-column listing format.

Output columns (in order): set, player, subset, card number, parallel.
The "card number" column has values prefixed with '#'.

Usage:
    python transform_cards.py input.csv [output.csv] [--debug]

If output.csv is omitted, the result is written next to the input as
<input_stem>_transformed.csv. With --debug, every row's subset/parallel
split decision is logged to stderr — useful for spotting vocab gaps.
"""

import csv
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
VOCAB_PATH = SCRIPT_DIR / 'parallel_vocab.txt'

# Built-in fallback vocab if parallel_vocab.txt is missing. Kept short — the
# external file is the source of truth.
DEFAULT_TOKENS = frozenset({
    'red', 'blue', 'green', 'yellow', 'orange', 'purple', 'pink', 'black',
    'white', 'gold', 'silver', 'bronze', 'aqua', 'teal', 'fuchsia', 'emerald',
    'refractor', 'refractors', 'prizm', 'prizms', 'mosaic', 'lava', 'hyper',
    'velocity', 'wave', 'raywave', 'foil', 'ice', 'mirror', 'pandora', 'holo',
    'burnt umber', 'cherry blossoms', 'light blue', 'dark blue',
})
DEFAULT_QUALIFIERS = frozenset({
    'die', 'cut', 'numbered', 'shock', 'border', 'stripes', '&', 'and', 'of',
    'the', 'sp', 'ssp', 'sps', 'to',
})

# Brands that get "Panini" inserted between the year and the brand
# (rule 2a and its sub-rules) when the year is >= 2010 and the set
# name does not already contain "Panini".
PANINI_BRANDS = {
    "panini", "score", "donruss", "optic", "select", "prizm", "mosaic",
    "hoops", "contenders", "absolute", "phoenix", "prestige", "chronicles",
    "rookies & stars", "totally certified", "court kings", "certified",
    "national treasures", "immaculate", "spectra", "origins", "obsidian",
    "black", "flux", "encased", "limited", "threads", "playoff", "xr",
    "plates & patches", "pinnacle", "crown royale", "one", "elite",
    "stars & stripes", "clearly donruss",
}

# Brands that get "Topps" inserted between the year and the brand
# (rule 2c and its sub-rules) when the year is >= 2010 and the set
# name does not already contain "Topps".
TOPPS_BRANDS = {
    "topps", "bowman", "finest", "heritage", "pristine", "gypsy queen",
    "allen & ginter", "stadium club", "chrome", "archives", "big league",
    "bowman chrome", "bowman's best", "update", "tier one",
    "definitive collection", "dynasty", "triple threads", "diamond icons",
    "inception", "museum collection", "five star", "tribute",
}

# Matches the "YYYY" or "YYYY-YY" prefix at the start of a set name
# followed by whitespace, so we can insert a brand-family token after it.
YEAR_PATTERN = re.compile(r'^(\d{4}(?:-\d{2})?)\s+')


def transform_set(set_name: str, year_str: str, brand: str) -> str:
    """Apply the rule-2 set-name transformations."""
    if not set_name:
        return set_name

    # 2e: "University" -> "U" anywhere it appears.
    set_name = re.sub(r'\bUniversity\b', 'U', set_name)

    # 2a-iii: any set containing "Hoops" should read "Panini NBA Hoops".
    if re.search(r'\bHoops\b', set_name) and 'NBA Hoops' not in set_name:
        set_name = re.sub(r'\bHoops\b', 'NBA Hoops', set_name, count=1)

    has_panini = 'Panini' in set_name
    has_topps = 'Topps' in set_name
    has_fleer = 'Fleer' in set_name
    has_finest = bool(re.search(r'\bFinest\b', set_name))
    has_nba_hoops = 'NBA Hoops' in set_name

    try:
        year = int(year_str) if year_str else None
    except ValueError:
        year = None

    brand_lower = (brand or '').strip().lower()

    prefix = None

    # 2d: brand "Fleer" must produce "Fleer" in the set name.
    if brand_lower == 'fleer' and not has_fleer:
        prefix = 'Fleer'
    # 2b: any "Finest" set needs "Topps" between the year and "Finest".
    elif has_finest and not has_topps:
        prefix = 'Topps'
    # 2a-iii companion: Hoops sets need the "Panini" prefix as well.
    elif has_nba_hoops and not has_panini:
        prefix = 'Panini'
    elif year is not None and year >= 2010:
        # 2a / 2c: brand-family decides which prefix to add when year >= 2010.
        if brand_lower in PANINI_BRANDS and not has_panini:
            prefix = 'Panini'
        elif brand_lower in TOPPS_BRANDS and not has_topps:
            prefix = 'Topps'

    if prefix and YEAR_PATTERN.match(set_name):
        set_name = YEAR_PATTERN.sub(rf'\1 {prefix} ', set_name, count=1)

    return set_name


def load_parallel_vocab(path: Path) -> tuple[frozenset, frozenset]:
    """Load PARALLEL_TOKENS and PARALLEL_QUALIFIERS from a vocab file.

    The file uses '[tokens]' / '[qualifiers]' section headers, one term per
    line, multi-word entries on a single line. Lines starting with '#' are
    comments. All terms are lowercased.
    """
    if not path.exists():
        print(f"Warning: vocab file not found at {path}; using built-in defaults",
              file=sys.stderr)
        return DEFAULT_TOKENS, DEFAULT_QUALIFIERS

    tokens: set[str] = set()
    qualifiers: set[str] = set()
    section = None

    with path.open('r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            low = line.lower()
            if low == '[tokens]':
                section = tokens
                continue
            if low == '[qualifiers]':
                section = qualifiers
                continue
            if section is None:
                continue
            section.add(low)

    return frozenset(tokens), frozenset(qualifiers)


def _tokenize_subset(subset: str, tokens: frozenset, qualifiers: frozenset):
    """Split *subset* into logical tokens, greedily merging multi-word vocab.

    Returns a list of (original_text, lowercased_text) tuples. Multi-word
    vocab entries (e.g. 'cherry blossoms') become a single logical token.
    """
    raw = subset.split()
    if not raw:
        return []

    multi = sorted(
        (entry for entry in (tokens | qualifiers) if ' ' in entry),
        key=lambda s: -len(s.split()),
    )
    max_n = max((len(m.split()) for m in multi), default=1)

    logical: list[tuple[str, str]] = []
    i = 0
    while i < len(raw):
        matched = False
        for n in range(min(max_n, len(raw) - i), 1, -1):
            chunk_low = ' '.join(raw[i:i + n]).lower()
            if chunk_low in tokens or chunk_low in qualifiers:
                logical.append((' '.join(raw[i:i + n]), chunk_low))
                i += n
                matched = True
                break
        if not matched:
            logical.append((raw[i], raw[i].lower()))
            i += 1

    return logical


def split_subset_parallel(subset: str, tokens: frozenset,
                          qualifiers: frozenset) -> tuple[str, str]:
    """Split a subset string into (insert_name, parallel) using the vocab.

    Algorithm: find the leftmost index whose tail tokens are all in
    tokens|qualifiers AND contain at least one tokens member. Everything
    before that index is the insert; the tail is the parallel.

    Returns ("", "") for an empty subset, ("<insert>", "") if no parallel
    is detected, ("", "<parallel>") if the whole string is a parallel.
    """
    if not subset or not subset.strip():
        return "", ""

    logical = _tokenize_subset(subset, tokens, qualifiers)
    if not logical:
        return "", ""

    for idx in range(len(logical)):
        tail = logical[idx:]
        if not tail:
            continue
        all_vocab = all(t[1] in tokens or t[1] in qualifiers for t in tail)
        has_token = any(t[1] in tokens for t in tail)
        if all_vocab and has_token:
            prefix = ' '.join(t[0] for t in logical[:idx])
            suffix = ' '.join(t[0] for t in tail)
            return prefix, suffix

    return ' '.join(t[0] for t in logical), ""


def transform_subset(subset: str, attributes: str, tokens: frozenset,
                     qualifiers: frozenset) -> tuple[str, str, str]:
    """Apply the rule-3 transformations using the vocab-based splitter.

    Returns (output_subset, output_parallel, case_label). The case label is
    one of "A", "A'", "B", "B'", "C", "C'", "D", "D'" matching the plan's
    decision table — used for --debug output.
    """
    insert, parallel = split_subset_parallel(subset or '', tokens, qualifiers)
    has_insert = bool(insert)
    has_parallel = bool(parallel)

    attrs = [a.strip().upper() for a in (attributes or '').split(',') if a.strip()]
    has_rc = 'RC' in attrs

    if has_rc:
        if has_insert and has_parallel:
            output_subset, output_parallel, case = f"{insert} Rookie", parallel, "A"
        elif has_insert and not has_parallel:
            output_subset, output_parallel, case = f"{insert} Rookie", "", "C"
        elif not has_insert and has_parallel:
            output_subset, output_parallel, case = "Rookie", subset or "", "B"
        else:
            output_subset, output_parallel, case = "Rookie", "", "D"
    else:
        if has_insert and has_parallel:
            output_subset, output_parallel, case = insert, parallel, "A'"
        elif has_insert and not has_parallel:
            output_subset, output_parallel, case = insert, "", "C'"
        elif not has_insert and has_parallel:
            output_subset, output_parallel, case = "Base", parallel, "B'"
        else:
            output_subset, output_parallel, case = "Base", "", "D'"

    # Insert suffix: if subset isn't "Base" and the splitter found an insert,
    # mark it as part of a named-series with a trailing "Insert".
    if output_subset != "Base" and has_insert:
        output_subset = f"{output_subset} Insert"

    return output_subset, output_parallel, case


def transform_row(row: dict, tokens: frozenset, qualifiers: frozenset,
                  debug_sink=None) -> dict:
    matched = (row.get('matched') or '').strip().lower()
    if matched and matched != 'yes':
        return {
            'set': '', 'player': '', 'subset': '',
            'card number': '', 'parallel': '',
        }

    set_name = transform_set(
        row.get('set', '') or '',
        row.get('year', '') or '',
        row.get('brand', '') or '',
    )
    raw_subset = row.get('subset', '') or ''
    raw_attrs = row.get('attributes', '') or ''
    out_subset, out_parallel, case = transform_subset(
        raw_subset, raw_attrs, tokens, qualifiers
    )
    card_number = (row.get('card_number') or '').strip()
    card_number_display = f'#{card_number}' if card_number else ''

    if debug_sink is not None:
        rc_flag = 'RC' if 'RC' in [a.strip().upper() for a in raw_attrs.split(',')] else '--'
        debug_sink.write(
            f"[{case:>2}] {rc_flag} | in={raw_subset!r:<55} -> "
            f"subset={out_subset!r}, parallel={out_parallel!r}\n"
        )

    return {
        'set': set_name,
        'player': row.get('player', '') or '',
        'subset': out_subset,
        'card number': card_number_display,
        'parallel': out_parallel,
    }


def transform_csv(in_path: Path, out_path: Path, tokens: frozenset,
                  qualifiers: frozenset, debug: bool = False) -> int:
    debug_sink = sys.stderr if debug else None
    with in_path.open('r', encoding='utf-8', newline='') as f_in:
        reader = csv.DictReader(f_in)
        rows = [transform_row(row, tokens, qualifiers, debug_sink) for row in reader]

    fieldnames = ['set', 'player', 'subset', 'card number', 'parallel']
    with out_path.open('w', encoding='utf-8', newline='') as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if a != '--debug']
    debug = '--debug' in argv[1:]

    if not args:
        print("Usage: python transform_cards.py <input.csv> [output.csv] [--debug]",
              file=sys.stderr)
        return 1

    in_path = Path(args[0])
    if not in_path.exists():
        print(f"Input file not found: {in_path}", file=sys.stderr)
        return 1

    if len(args) > 1:
        out_path = Path(args[1])
    else:
        out_path = in_path.with_name(f"{in_path.stem}_transformed.csv")

    tokens, qualifiers = load_parallel_vocab(VOCAB_PATH)
    n = transform_csv(in_path, out_path, tokens, qualifiers, debug=debug)
    print(f"Wrote {n} rows to {out_path}")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
