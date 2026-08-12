package transform

import (
	"bytes"
	_ "embed"
	"testing"
)

func TestTransformSetCanonicalFamilies(t *testing.T) {
	tests := []struct {
		name, input, year, brand, want string
	}{
		{"Ultra", "1996-97 Ultra", "1996", "Fleer", "1996-97 Fleer Ultra"},
		{"existing Fleer Ultra", "1996-97 fleer ultra", "1996", "Fleer", "1996-97 Fleer Ultra"},
		{"Flair", "1994-95 Flair", "1994", "Fleer", "1994-95 Fleer Flair"},
		{"Flair Showcase spaced", "1996-97 Flair Showcase", "1996", "Fleer", "1996-97 Fleer Flair Showcase"},
		{"FlairShowcase joined", "1996-97 flairshowcase", "1996", "", "1996-97 Fleer Flair Showcase"},
		{"SP", "1996-97 SP", "1996", "Upper Deck", "1996-97 Upper Deck SP"},
		{"SP suffix", "2005-06 SP Authentic", "2005", "Upper Deck", "2005-06 Upper Deck SP Authentic"},
		{"existing Upper Deck SP", "1996-97 Upper Deck SP", "1996", "Upper Deck", "1996-97 Upper Deck SP"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := TransformSet(tt.input, tt.year, tt.brand); got != tt.want {
				t.Fatalf("TransformSet() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestRepairSetYear(t *testing.T) {
	tests := []struct {
		name, setName, year, title, want string
	}{
		{"season from title", "36923 Ultra", "2001", "2001-02 Fleer Ultra #1", "2001-02 Ultra"},
		{"season from year column", "38504 SP", "2005-06", "", "2005-06 SP"},
		{"four digit year is valid", "2006 Ultra", "2006", "", "2006 Ultra"},
		{"no trustworthy year", "36923 Ultra", "unknown", "", "36923 Ultra"},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := RepairSetYear(tt.setName, tt.year, tt.title); got != tt.want {
				t.Fatalf("RepairSetYear() = %q, want %q", got, tt.want)
			}
		})
	}
}

func TestTransformRowRepairsCorruptYearBeforeSetRules(t *testing.T) {
	row := map[string]string{
		"matched": "Yes", "year": "2006", "brand": "Fleer",
		"set": "36923 Ultra", "title": "2001-02 Fleer Ultra #12",
		"card_number": "12", "player": "Example Player",
	}
	got := TransformRow(row, defaultTokens, defaultQualifiers)
	if got["set"] != "2001-02 Fleer Ultra" {
		t.Fatalf("set = %q, want %q", got["set"], "2001-02 Fleer Ultra")
	}
}

//go:embed testdata/parallel_vocab.txt
var testVocab []byte

//go:embed testdata/batch-1022110-export.csv
var testInput []byte

//go:embed testdata/batch-1022110-export_transformed.csv
var testExpected []byte

// TestGoldenTransform runs the real sample input through TransformCSV
// and compares byte-for-byte against the Python-produced fixture.
func TestGoldenTransform(t *testing.T) {
	tokens, qualifiers := LoadVocab(bytes.NewReader(testVocab))
	if len(tokens) == 0 {
		t.Fatal("no tokens loaded from embedded vocab")
	}

	var out bytes.Buffer
	n, err := TransformCSV(bytes.NewReader(testInput), &out, tokens, qualifiers)
	if err != nil {
		t.Fatalf("TransformCSV: %v", err)
	}
	if n == 0 {
		t.Fatal("zero rows transformed")
	}

	got := out.Bytes()
	if bytes.Equal(got, testExpected) {
		return
	}

	// Report first divergence with a few lines of context.
	gotLines := bytes.Split(got, []byte("\r\n"))
	wantLines := bytes.Split(testExpected, []byte("\r\n"))
	maxLen := len(gotLines)
	if len(wantLines) > maxLen {
		maxLen = len(wantLines)
	}
	for i := 0; i < maxLen; i++ {
		var g, w []byte
		if i < len(gotLines) {
			g = gotLines[i]
		}
		if i < len(wantLines) {
			w = wantLines[i]
		}
		if !bytes.Equal(g, w) {
			t.Fatalf("line %d differs:\n  got:  %q\n  want: %q", i+1, g, w)
		}
	}
	t.Fatalf("output differs but per-line scan found no diff (got %d bytes, want %d bytes)", len(got), len(testExpected))
}
