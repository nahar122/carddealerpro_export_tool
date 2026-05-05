# Cross-compiles the Card CSV Transformer for macOS from Windows and
# stitches the per-arch binaries into a Mach-O universal (fat) binary
# that runs natively on any Mac.
#
# Output:
#   dist/CardCSVTransformer-mac-arm64       (Apple Silicon thin binary)
#   dist/CardCSVTransformer-mac-amd64       (Intel thin binary)
#   dist/CardCSVTransformer-mac-universal   (Universal -- ship this one)
#
# CGO_ENABLED=0 is load-bearing: it guarantees a static, pure-Go binary so
# Windows->Mac cross-compile works without a C toolchain.
#
# After building, send dist/CardCSVTransformer-mac-universal to the
# recipient. On first launch macOS will block the unsigned binary; the
# recipient must either right-click -> Open and confirm, or run:
#   xattr -dr com.apple.quarantine /path/to/CardCSVTransformer-mac-universal

$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force -Path dist | Out-Null

$env:CGO_ENABLED = "0"

Write-Host "Building darwin/arm64 (Apple Silicon)..."
$env:GOOS = "darwin"
$env:GOARCH = "arm64"
go build -trimpath -ldflags "-s -w" -o dist/CardCSVTransformer-mac-arm64 .
if (-not $?) { exit 1 }

Write-Host "Building darwin/amd64 (Intel + Rosetta)..."
$env:GOOS = "darwin"
$env:GOARCH = "amd64"
go build -trimpath -ldflags "-s -w" -o dist/CardCSVTransformer-mac-amd64 .
if (-not $?) { exit 1 }

Remove-Item Env:GOOS, Env:GOARCH, Env:CGO_ENABLED

Write-Host "Stitching universal (fat) binary..."
go run ./tools/makefat `
    -arm64 dist/CardCSVTransformer-mac-arm64 `
    -amd64 dist/CardCSVTransformer-mac-amd64 `
    -o     dist/CardCSVTransformer-mac-universal
if (-not $?) { exit 1 }

Write-Host "Packaging zip with executable perms..."
go run ./tools/makezip `
    -bin  dist/CardCSVTransformer-mac-universal `
    -name CardCSVTransformer `
    -o    dist/CardCSVTransformer-mac.zip
if (-not $?) { exit 1 }

Write-Host ""
Write-Host "Done. Ship dist/CardCSVTransformer-mac.zip -- recipient extracts and runs."
Get-ChildItem dist | Format-Table Name, Length, LastWriteTime
