// makefat stitches two thin Mach-O binaries (arm64 + amd64) into a single
// Mach-O fat (universal) binary that runs natively on any Mac. Equivalent
// to `lipo -create -output <out> <arm64> <amd64>` but pure Go, so it works
// from Windows.
//
// Usage:
//   go run ./tools/makefat -arm64 path/to/arm64 -amd64 path/to/amd64 -o path/to/universal
//
// The fat-binary format is documented in <mach-o/fat.h>. Header values are
// big-endian on disk regardless of host byte order. macOS expects each slice
// to be aligned to 2^align bytes within the file; we use align=14 (16 KB),
// matching what modern `lipo` produces.
package main

import (
	"encoding/binary"
	"flag"
	"fmt"
	"io"
	"os"
)

const (
	fatMagic = 0xCAFEBABE

	cpuTypeARM64  = 0x0100000C
	cpuTypeX86_64 = 0x01000007

	cpuSubtypeARM64All  = 0x00000000
	cpuSubtypeX86_64All = 0x00000003

	sliceAlign     = 14 // 2^14 = 16 KB, matches modern lipo
	sliceAlignSize = 1 << sliceAlign
)

type slice struct {
	cputype    uint32
	cpusubtype uint32
	data       []byte
	offset     uint32
}

func main() {
	arm64Path := flag.String("arm64", "", "path to arm64 thin binary")
	amd64Path := flag.String("amd64", "", "path to amd64 thin binary")
	outPath := flag.String("o", "", "output path for universal binary")
	flag.Parse()

	if *arm64Path == "" || *amd64Path == "" || *outPath == "" {
		flag.Usage()
		os.Exit(2)
	}

	arm64Data, err := os.ReadFile(*arm64Path)
	if err != nil {
		fatal("read arm64: %v", err)
	}
	amd64Data, err := os.ReadFile(*amd64Path)
	if err != nil {
		fatal("read amd64: %v", err)
	}

	slices := []slice{
		{cputype: cpuTypeARM64, cpusubtype: cpuSubtypeARM64All, data: arm64Data},
		{cputype: cpuTypeX86_64, cpusubtype: cpuSubtypeX86_64All, data: amd64Data},
	}

	// Header: 8 bytes (magic + nfat_arch) + 20 bytes per arch entry.
	headerSize := uint32(8 + 20*len(slices))
	cursor := alignUp(headerSize, sliceAlignSize)
	for i := range slices {
		slices[i].offset = cursor
		cursor = alignUp(cursor+uint32(len(slices[i].data)), sliceAlignSize)
	}

	out, err := os.Create(*outPath)
	if err != nil {
		fatal("create output: %v", err)
	}
	defer out.Close()

	// fat_header
	if err := binary.Write(out, binary.BigEndian, uint32(fatMagic)); err != nil {
		fatal("write magic: %v", err)
	}
	if err := binary.Write(out, binary.BigEndian, uint32(len(slices))); err != nil {
		fatal("write nfat_arch: %v", err)
	}
	// fat_arch table
	for _, s := range slices {
		fields := []uint32{s.cputype, s.cpusubtype, s.offset, uint32(len(s.data)), uint32(sliceAlign)}
		for _, v := range fields {
			if err := binary.Write(out, binary.BigEndian, v); err != nil {
				fatal("write fat_arch: %v", err)
			}
		}
	}
	// Pad + slice data
	for _, s := range slices {
		pos, _ := out.Seek(0, io.SeekCurrent)
		if pad := int64(s.offset) - pos; pad > 0 {
			if _, err := out.Write(make([]byte, pad)); err != nil {
				fatal("write padding: %v", err)
			}
		}
		if _, err := out.Write(s.data); err != nil {
			fatal("write slice: %v", err)
		}
	}

	totalSize, _ := out.Seek(0, io.SeekCurrent)
	fmt.Printf("wrote %s (%d bytes, %d slices)\n", *outPath, totalSize, len(slices))
}

func alignUp(n, align uint32) uint32 {
	return (n + align - 1) &^ (align - 1)
}

func fatal(format string, a ...any) {
	fmt.Fprintf(os.Stderr, "makefat: "+format+"\n", a...)
	os.Exit(1)
}
