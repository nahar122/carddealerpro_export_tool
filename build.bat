@echo off
REM Build the standalone Windows .exe for the Card CSV Transformer.
REM
REM Prerequisites (one-time setup on the build machine):
REM     python -m pip install pyinstaller tkinterdnd2
REM
REM After this script finishes, hand `dist\CardCSVTransformer.exe` to the
REM recipient. They double-click it; no Python install needed.
REM
REM Note: PyInstaller-built .exes occasionally trigger Windows Defender
REM false positives on first launch. If that happens, click "More info"
REM and "Run anyway" — or sign the binary if you have a code-signing cert.

setlocal

echo Building CardCSVTransformer.exe ...

python -m PyInstaller ^
  --onefile ^
  --windowed ^
  --noconfirm ^
  --name CardCSVTransformer ^
  --add-data "parallel_vocab.txt;." ^
  --collect-all tkinterdnd2 ^
  app.py

if errorlevel 1 (
    echo.
    echo BUILD FAILED.
    exit /b 1
)

echo.
echo Done. Output: dist\CardCSVTransformer.exe
endlocal
