// Package transform ports the Python card-CSV transformer to Go.
//
// Mirrors transform_cards.py: load a vocab of "tokens" and "qualifiers",
// then rewrite each row's set, subset, parallel, and card-number columns
// per the documented rules. Output schema is fixed:
//
//	set, player, subset, card number, parallel
package transform

import (
	"encoding/csv"
	"io"
	"regexp"
	"sort"
	"strconv"
	"strings"
)

type stringSet = map[string]struct{}

func newSet(items ...string) stringSet {
	s := make(stringSet, len(items))
	for _, it := range items {
		s[it] = struct{}{}
	}
	return s
}

// Fallback vocab used when LoadVocab is given an empty/missing reader.
// The embedded parallel_vocab.txt is the source of truth at runtime.
var defaultTokens = newSet(
	"red", "blue", "green", "yellow", "orange", "purple", "pink", "black",
	"white", "gold", "silver", "bronze", "aqua", "teal", "fuchsia", "emerald",
	"refractor", "refractors", "prizm", "prizms", "mosaic", "lava", "hyper",
	"velocity", "wave", "raywave", "foil", "ice", "mirror", "pandora", "holo",
	"burnt umber", "cherry blossoms", "light blue", "dark blue",
)

var defaultQualifiers = newSet(
	"die", "cut", "numbered", "shock", "border", "stripes", "&", "and", "of",
	"the", "sp", "ssp", "sps", "to",
)

// Brands that get "Panini" inserted between the year and the brand
// (rule 2a) when year >= 2010 and the set name doesn't already contain it.
var paniniBrands = newSet(
	"panini", "score", "donruss", "optic", "select", "prizm", "mosaic",
	"hoops", "contenders", "absolute", "phoenix", "prestige", "chronicles",
	"rookies & stars", "totally certified", "court kings", "certified",
	"national treasures", "immaculate", "spectra", "origins", "obsidian",
	"black", "flux", "encased", "limited", "threads", "playoff", "xr",
	"plates & patches", "pinnacle", "crown royale", "one", "elite",
	"stars & stripes", "clearly donruss",
)

// Brands that get "Topps" inserted between the year and the brand (rule 2c).
var toppsBrands = newSet(
	"topps", "bowman", "finest", "heritage", "pristine", "gypsy queen",
	"allen & ginter", "stadium club", "chrome", "archives", "big league",
	"bowman chrome", "bowman's best", "update", "tier one",
	"definitive collection", "dynasty", "triple threads", "diamond icons",
	"inception", "museum collection", "five star", "tribute",
)

var (
	yearPattern       = regexp.MustCompile(`^(\d{4}(?:-\d{2})?)\s+`)
	universityPattern = regexp.MustCompile(`\bUniversity\b`)
	hoopsPattern      = regexp.MustCompile(`\bHoops\b`)
	finestPattern     = regexp.MustCompile(`\bFinest\b`)
)

// LoadVocab parses parallel_vocab.txt-formatted content from r and returns
// the (tokens, qualifiers) sets. Sections are introduced with [tokens] /
// [qualifiers] headers; '#' starts a comment; one term per line; multi-word
// entries on a single line. All terms are lowercased.
//
// If r is nil or yields no terms, the built-in defaults are returned.
func LoadVocab(r io.Reader) (tokens, qualifiers stringSet) {
	tokens = make(stringSet)
	qualifiers = make(stringSet)
	if r == nil {
		return defaultTokens, defaultQualifiers
	}
	data, err := io.ReadAll(r)
	if err != nil || len(data) == 0 {
		return defaultTokens, defaultQualifiers
	}

	var section stringSet
	for _, raw := range strings.Split(string(data), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		low := strings.ToLower(line)
		switch low {
		case "[tokens]":
			section = tokens
			continue
		case "[qualifiers]":
			section = qualifiers
			continue
		}
		if section == nil {
			continue
		}
		section[low] = struct{}{}
	}

	if len(tokens) == 0 && len(qualifiers) == 0 {
		return defaultTokens, defaultQualifiers
	}
	return tokens, qualifiers
}

// TransformSet applies the rule-2 set-name transformations.
func TransformSet(setName, yearStr, brand string) string {
	if setName == "" {
		return setName
	}

	// 2e: "University" -> "U" anywhere it appears.
	setName = universityPattern.ReplaceAllString(setName, "U")

	// 2a-iii: any set containing "Hoops" should read "NBA Hoops" — replace
	// only the first occurrence (mirroring Python re.sub count=1).
	if !strings.Contains(setName, "NBA Hoops") {
		if loc := hoopsPattern.FindStringIndex(setName); loc != nil {
			setName = setName[:loc[0]] + "NBA Hoops" + setName[loc[1]:]
		}
	}

	hasPanini := strings.Contains(setName, "Panini")
	hasTopps := strings.Contains(setName, "Topps")
	hasFleer := strings.Contains(setName, "Fleer")
	hasFinest := finestPattern.MatchString(setName)
	hasNBAHoops := strings.Contains(setName, "NBA Hoops")

	year, yearOK := parseYear(yearStr)
	brandLower := strings.ToLower(strings.TrimSpace(brand))

	prefix := ""

	switch {
	// 2d: brand "Fleer" must produce "Fleer" in the set name.
	case brandLower == "fleer" && !hasFleer:
		prefix = "Fleer"
	// 2b: any "Finest" set needs "Topps" between the year and "Finest".
	case hasFinest && !hasTopps:
		prefix = "Topps"
	// 2a-iii companion: Hoops sets need the "Panini" prefix as well.
	case hasNBAHoops && !hasPanini:
		prefix = "Panini"
	case yearOK && year >= 2010:
		// 2a / 2c: brand-family decides which prefix to add.
		if _, ok := paniniBrands[brandLower]; ok && !hasPanini {
			prefix = "Panini"
		} else if _, ok := toppsBrands[brandLower]; ok && !hasTopps {
			prefix = "Topps"
		}
	}

	if prefix != "" && yearPattern.MatchString(setName) {
		setName = yearPattern.ReplaceAllString(setName, "${1} "+prefix+" ")
	}

	return setName
}

func parseYear(s string) (int, bool) {
	if s == "" {
		return 0, false
	}
	n, err := strconv.Atoi(strings.TrimSpace(s))
	if err != nil {
		return 0, false
	}
	return n, true
}

type logicalToken struct {
	original string
	lower    string
}

// tokenizeSubset splits subset into logical tokens, greedily merging
// multi-word vocab entries (longest-first) so e.g. "cherry blossoms"
// becomes a single token rather than two.
func tokenizeSubset(subset string, tokens, qualifiers stringSet) []logicalToken {
	raw := strings.Fields(subset)
	if len(raw) == 0 {
		return nil
	}

	var multi []string
	for entry := range tokens {
		if strings.Contains(entry, " ") {
			multi = append(multi, entry)
		}
	}
	for entry := range qualifiers {
		if strings.Contains(entry, " ") {
			multi = append(multi, entry)
		}
	}
	sort.Slice(multi, func(i, j int) bool {
		return len(strings.Fields(multi[i])) > len(strings.Fields(multi[j]))
	})

	maxN := 1
	for _, m := range multi {
		if w := len(strings.Fields(m)); w > maxN {
			maxN = w
		}
	}

	logical := make([]logicalToken, 0, len(raw))
	i := 0
	for i < len(raw) {
		matched := false
		// Mirror Python: range(min(maxN, len(raw)-i), 1, -1) — only n>=2.
		nMax := maxN
		if rem := len(raw) - i; rem < nMax {
			nMax = rem
		}
		for n := nMax; n >= 2; n-- {
			chunk := strings.Join(raw[i:i+n], " ")
			chunkLow := strings.ToLower(chunk)
			if _, inT := tokens[chunkLow]; inT {
				logical = append(logical, logicalToken{chunk, chunkLow})
				i += n
				matched = true
				break
			}
			if _, inQ := qualifiers[chunkLow]; inQ {
				logical = append(logical, logicalToken{chunk, chunkLow})
				i += n
				matched = true
				break
			}
		}
		if !matched {
			logical = append(logical, logicalToken{raw[i], strings.ToLower(raw[i])})
			i++
		}
	}
	return logical
}

// SplitSubsetParallel splits a subset string into (insert, parallel) using
// the vocab. Algorithm: find the leftmost index whose tail tokens are all
// in tokens|qualifiers AND contain at least one tokens member.
func SplitSubsetParallel(subset string, tokens, qualifiers stringSet) (insert, parallel string) {
	if strings.TrimSpace(subset) == "" {
		return "", ""
	}
	logical := tokenizeSubset(subset, tokens, qualifiers)
	if len(logical) == 0 {
		return "", ""
	}

	for idx := 0; idx < len(logical); idx++ {
		tail := logical[idx:]
		allVocab := true
		hasToken := false
		for _, t := range tail {
			_, inT := tokens[t.lower]
			_, inQ := qualifiers[t.lower]
			if !inT && !inQ {
				allVocab = false
				break
			}
			if inT {
				hasToken = true
			}
		}
		if allVocab && hasToken {
			prefixOriginals := make([]string, idx)
			for i := 0; i < idx; i++ {
				prefixOriginals[i] = logical[i].original
			}
			suffixOriginals := make([]string, len(tail))
			for i, t := range tail {
				suffixOriginals[i] = t.original
			}
			return strings.Join(prefixOriginals, " "), strings.Join(suffixOriginals, " ")
		}
	}

	allOriginals := make([]string, len(logical))
	for i, t := range logical {
		allOriginals[i] = t.original
	}
	return strings.Join(allOriginals, " "), ""
}

// TransformSubset applies the rule-3 transformations using the
// vocab-based splitter. Returns (outSubset, outParallel, caseLabel).
func TransformSubset(subset, attributes string, tokens, qualifiers stringSet) (outSubset, outParallel, caseLabel string) {
	insert, parallel := SplitSubsetParallel(subset, tokens, qualifiers)
	hasInsert := insert != ""
	hasParallel := parallel != ""

	hasRC := false
	for _, a := range strings.Split(attributes, ",") {
		if strings.ToUpper(strings.TrimSpace(a)) == "RC" {
			hasRC = true
			break
		}
	}

	if hasRC {
		switch {
		case hasInsert && hasParallel:
			outSubset, outParallel, caseLabel = insert+" Rookie", parallel, "A"
		case hasInsert && !hasParallel:
			outSubset, outParallel, caseLabel = insert+" Rookie", "", "C"
		case !hasInsert && hasParallel:
			outSubset, outParallel, caseLabel = "Rookie", subset, "B"
		default:
			outSubset, outParallel, caseLabel = "Rookie", "", "D"
		}
	} else {
		switch {
		case hasInsert && hasParallel:
			outSubset, outParallel, caseLabel = insert, parallel, "A'"
		case hasInsert && !hasParallel:
			outSubset, outParallel, caseLabel = insert, "", "C'"
		case !hasInsert && hasParallel:
			outSubset, outParallel, caseLabel = "Base", parallel, "B'"
		default:
			outSubset, outParallel, caseLabel = "Base", "", "D'"
		}
	}

	if outSubset != "Base" && hasInsert {
		outSubset = outSubset + " Insert"
	}

	return outSubset, outParallel, caseLabel
}

// TransformRow runs the transformation for one input row. The row map's
// keys are the input CSV's column headers (lowercased: matched, year,
// brand, set, subset, attributes, card_number, player). Returns a map
// keyed by the output schema.
func TransformRow(row map[string]string, tokens, qualifiers stringSet) map[string]string {
	matched := strings.ToLower(strings.TrimSpace(row["matched"]))
	if matched != "" && matched != "yes" {
		return map[string]string{
			"set": "", "player": "", "subset": "",
			"card number": "", "parallel": "",
		}
	}

	setName := TransformSet(row["set"], row["year"], row["brand"])
	outSubset, outParallel, _ := TransformSubset(row["subset"], row["attributes"], tokens, qualifiers)

	cardNumber := strings.TrimSpace(row["card_number"])
	cardNumberDisplay := ""
	if cardNumber != "" {
		cardNumberDisplay = "#" + cardNumber
	}

	return map[string]string{
		"set":         setName,
		"player":      row["player"],
		"subset":      outSubset,
		"card number": cardNumberDisplay,
		"parallel":    outParallel,
	}
}

// OutputColumns is the fixed output column order. Note "card number"
// has a space — it's a display label, not a snake_case identifier.
var OutputColumns = []string{"set", "player", "subset", "card number", "parallel"}

// TransformCSV reads CSV from in, transforms each row, and writes the
// 5-column output to out. Returns the number of data rows processed.
// Output uses CRLF line endings to match the existing Python output.
func TransformCSV(in io.Reader, out io.Writer, tokens, qualifiers stringSet) (int, error) {
	r := csv.NewReader(in)
	r.FieldsPerRecord = -1 // tolerate ragged rows; Python's DictReader does too

	header, err := r.Read()
	if err != nil {
		return 0, err
	}
	colIdx := make(map[string]int, len(header))
	for i, name := range header {
		colIdx[name] = i
	}

	w := csv.NewWriter(out)
	w.UseCRLF = true
	if err := w.Write(OutputColumns); err != nil {
		return 0, err
	}

	count := 0
	for {
		record, err := r.Read()
		if err == io.EOF {
			break
		}
		if err != nil {
			w.Flush()
			return count, err
		}

		rowMap := make(map[string]string, len(header))
		for name, idx := range colIdx {
			if idx < len(record) {
				rowMap[name] = record[idx]
			}
		}

		out := TransformRow(rowMap, tokens, qualifiers)
		row := make([]string, len(OutputColumns))
		for i, col := range OutputColumns {
			row[i] = out[col]
		}
		if err := w.Write(row); err != nil {
			return count, err
		}
		count++
	}

	w.Flush()
	return count, w.Error()
}
