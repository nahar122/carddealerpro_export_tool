// makezip packages the universal Mac binary plus a README into a .zip
// with Unix mode 0755 on the binary, so macOS preserves the executable
// bit when the recipient extracts it. PowerShell's Compress-Archive
// cannot do this -- it always strips Unix perms.
//
// Usage:
//   go run ./tools/makezip -bin path/to/universal -name CardCSVTransformer -o out.zip
package main

import (
	"archive/zip"
	"flag"
	"fmt"
	"os"
)

const readme = `Card CSV Transformer
====================

To run:
1. Double-click "CardCSVTransformer" in Finder.
2. macOS will warn that the developer cannot be verified -- click Cancel.
3. Right-click "CardCSVTransformer" -> Open -> Open (this approves it).
4. A new browser tab opens with an upload page.
5. Choose your CSV, click "Transform & download", save the result.
6. Click "Quit" on the page when finished.

If step 3 doesn't work, open Terminal and run:
    xattr -dr com.apple.quarantine ~/Downloads/CardCSVTransformer
    chmod +x ~/Downloads/CardCSVTransformer
Then double-click again.
`

func main() {
	binPath := flag.String("bin", "", "path to the universal Mach-O binary to package")
	name := flag.String("name", "CardCSVTransformer", "filename inside the zip")
	outPath := flag.String("o", "", "output .zip path")
	flag.Parse()

	if *binPath == "" || *outPath == "" {
		flag.Usage()
		os.Exit(2)
	}

	binData, err := os.ReadFile(*binPath)
	if err != nil {
		fatal("read binary: %v", err)
	}

	out, err := os.Create(*outPath)
	if err != nil {
		fatal("create zip: %v", err)
	}
	defer out.Close()

	zw := zip.NewWriter(out)

	// Binary entry with 0755 perms.
	binHeader := &zip.FileHeader{Name: *name, Method: zip.Deflate}
	binHeader.SetMode(0o755)
	bw, err := zw.CreateHeader(binHeader)
	if err != nil {
		fatal("create binary entry: %v", err)
	}
	if _, err := bw.Write(binData); err != nil {
		fatal("write binary: %v", err)
	}

	// README entry with 0644 perms.
	readmeHeader := &zip.FileHeader{Name: "README.txt", Method: zip.Deflate}
	readmeHeader.SetMode(0o644)
	rw, err := zw.CreateHeader(readmeHeader)
	if err != nil {
		fatal("create readme entry: %v", err)
	}
	if _, err := rw.Write([]byte(readme)); err != nil {
		fatal("write readme: %v", err)
	}

	if err := zw.Close(); err != nil {
		fatal("close zip: %v", err)
	}

	info, err := os.Stat(*outPath)
	if err == nil {
		fmt.Printf("wrote %s (%d bytes)\n", *outPath, info.Size())
	}
}

func fatal(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "makezip: "+format+"\n", a...)
	os.Exit(1)
}
