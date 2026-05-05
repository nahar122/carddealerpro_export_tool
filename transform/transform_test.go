package transform

import (
	"bytes"
	_ "embed"
	"testing"
)

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
