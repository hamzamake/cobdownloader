#!/bin/bash
# Builds dist-mac/Cob Downloader.app and wraps it in a drag-to-Applications
# disk image:  installer/Cob Downloader (Apple Silicon).dmg   on an M-series Mac
#              installer/Cob Downloader (Intel).dmg           on an Intel Mac
#
# Must run on a Mac - PyInstaller cannot cross-compile, and the result only
# runs on the CPU type it was built on.  No Mac?  .github/workflows/build-mac.yml
# runs this on GitHub's macOS runners and builds both.
#
# Run it with:  bash build-mac.sh
# Needs Python 3 with tkinter (the python.org installer has it; Homebrew needs
# `brew install python-tk`).  Everything else is fetched into build/.
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$PWD"

# keep in step with vendor/README.txt, which pins the Windows qjs.exe
QJS_VERSION=v0.17.0

case "$(uname -m)" in
    arm64)  ARCH=arm64;  FF_ARCH=arm64; LABEL="Apple Silicon" ;;
    x86_64) ARCH=x86_64; FF_ARCH=amd64; LABEL="Intel" ;;
    *) echo "Unsupported CPU: $(uname -m)"; exit 1 ;;
esac

APP_VERSION=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' cob_downloader.py)
[ -n "$APP_VERSION" ] || { echo "Could not read APP_VERSION"; exit 1; }

PY=${PYTHON:-python3}
if ! "$PY" -c "import tkinter; tkinter.Tcl()" 2>/dev/null; then
    echo "$PY has no working tkinter."
    echo "Install Python from https://www.python.org/downloads/macos/"
    echo "or, with Homebrew:  brew install python-tk"
    exit 1
fi

# ---- bundled binaries -------------------------------------------------------
# Kept out of vendor/ (which is committed) because they are per-architecture
# downloads, not sources.  Delete build/mac-vendor-* to refetch.
VENDOR="$ROOT/build/mac-vendor-$ARCH"
mkdir -p "$VENDOR"

if [ ! -x "$VENDOR/qjs" ]; then
    echo "Fetching QuickJS $QJS_VERSION ($ARCH)..."
    curl -fL --retry 3 -o "$VENDOR/qjs" \
        "https://github.com/quickjs-ng/quickjs/releases/download/$QJS_VERSION/qjs-darwin-$ARCH"
    chmod +x "$VENDOR/qjs"
fi

# BtbN (the Windows self-update source) ships no macOS builds and
# evermeet.cx is Intel-only; Martin Riedl publishes static builds for
# both architectures
for tool in ffmpeg ffprobe; do
    if [ ! -x "$VENDOR/$tool" ]; then
        echo "Fetching $tool ($FF_ARCH)..."
        curl -fL --retry 3 -o "$VENDOR/$tool.zip" \
            "https://ffmpeg.martin-riedl.de/redirect/latest/macos/$FF_ARCH/release/$tool.zip"
        unzip -o -j -q "$VENDOR/$tool.zip" "$tool" -d "$VENDOR"
        rm -f "$VENDOR/$tool.zip"
        chmod +x "$VENDOR/$tool"
    fi
done
"$VENDOR/ffmpeg" -hide_banner -version | head -1

# ---- python environment -----------------------------------------------------
VENV="$ROOT/build/venv-mac-$ARCH"
if [ ! -x "$VENV/bin/python" ]; then
    "$PY" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install -q --upgrade pip
# pillow lets PyInstaller turn icon.ico into the .icns a Mac app needs
"$VENV/bin/python" -m pip install -q --upgrade -r requirements.txt pyinstaller pillow

# ---- the .app ---------------------------------------------------------------
# dist-mac/ and build/mac/ rather than dist/ and the repo root: a windowed
# onedir build also writes a plain "Cob Downloader" folder, which would land on
# top of the Windows build, and the spec would overwrite the Windows one.
# Paths are absolute because PyInstaller resolves them against --specpath.
"$VENV/bin/python" -m PyInstaller --noconfirm --clean --windowed \
    --name "Cob Downloader" \
    --osx-bundle-identifier com.cobdownloader.app \
    --icon "$ROOT/icon.ico" \
    --add-data "$ROOT/icon.ico:." \
    --add-binary "$VENDOR/ffmpeg:." \
    --add-binary "$VENDOR/ffprobe:." \
    --add-binary "$VENDOR/qjs:." \
    --add-data "$ROOT/vendor/LICENSE-quickjs-ng.txt:." \
    --collect-submodules yt_dlp \
    --distpath "$ROOT/dist-mac" \
    --workpath "$ROOT/build/mac-$ARCH" \
    --specpath "$ROOT/build/mac-$ARCH" \
    cob_downloader.py

APP="$ROOT/dist-mac/Cob Downloader.app"
PLIST="$APP/Contents/Info.plist"
plutil -replace CFBundleShortVersionString -string "$APP_VERSION" "$PLIST"
plutil -replace CFBundleVersion -string "$APP_VERSION" "$PLIST"
# editing Info.plist breaks PyInstaller's ad-hoc signature, and Apple Silicon
# refuses to launch anything unsigned, so sign again
codesign --force --deep --sign - "$APP"
codesign --verify --deep "$APP"

# ---- smoke test -------------------------------------------------------------
# Only the bundled pieces fail the build.  The YouTube line is informational:
# CI runners sit on datacenter IPs that YouTube often refuses outright.
echo
echo "Running the packaged self check..."
REPORT="$HOME/Library/Application Support/CobDownloader/check-report.txt"
rm -f "$REPORT"
"$APP/Contents/MacOS/Cob Downloader" --check --quiet || true
grep -q "quickjs" "$REPORT" || { echo "Bundled qjs was not found"; exit 1; }
grep -q "^ffmpeg runs    : ffmpeg version" "$REPORT" \
    || { echo "Bundled ffmpeg does not run"; exit 1; }

# ---- the .dmg ---------------------------------------------------------------
STAGE="$ROOT/build/dmg-$ARCH"
DMG="$ROOT/installer/Cob Downloader ($LABEL).dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE" "$ROOT/installer"
ditto "$APP" "$STAGE/Cob Downloader.app"
ln -s /Applications "$STAGE/Applications"
rm -f "$DMG"
hdiutil create -quiet -volname "Cob Downloader" -srcfolder "$STAGE" \
    -fs HFS+ -format UDZO "$DMG"
rm -rf "$STAGE"

echo
echo "Built $DMG"
