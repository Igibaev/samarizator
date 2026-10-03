#!/bin/bash
# Builds a self-contained Samarizator.app and a .dmg for the current Mac architecture.
#
# Inside the app: Python + Qt (PyInstaller), static FFmpeg/ffprobe, whisper-cli and
# llama-server (Metal), the ScreenCaptureKit helper, Whisper small + Silero VAD models.
# The user downloads the .dmg, drags the app to Applications and runs it: no Homebrew,
# no Python, no terminal. Only the summary model (2.5–19 GB) is downloaded from inside
# the app on first use, unless SAMARIZATOR_BUNDLE_LLM embeds it.
#
# Needs: macOS 13+, Xcode Command Line Tools, cmake, git, curl, uv.
#   brew install cmake uv   (only on the build machine)
#
# Environment:
#   SAMARIZATOR_SIGN_IDENTITY  "Developer ID Application: …" (default "-" = ad-hoc)
#   APPLE_ID, APPLE_TEAM_ID, APPLE_APP_PASSWORD  notarize when all are set
#   SAMARIZATOR_BUNDLE_LLM     preset key (qwen3-4b, gemma3-12b, qwen3-30b-a3b) to embed
#   SAMARIZATOR_BUILD_DIR      work folder (default build/macos), caches third-party builds
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
ARCH="$(uname -m)"
MIN_MACOS=13.0
FFMPEG_TAG=n7.1.1
WHISPER_TAG=v1.9.4
LLAMA_TAG=b11377
WHISPER_MODEL_URL=https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin
VAD_MODEL_URL=https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin
WORK="${SAMARIZATOR_BUILD_DIR:-$ROOT/build/macos}"
SRC="$WORK/src"
OUT="$WORK/out-$ARCH"
IDENTITY="${SAMARIZATOR_SIGN_IDENTITY:--}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo local)"
JOBS="$(sysctl -n hw.ncpu)"
ENTITLEMENTS="$ROOT/packaging/macos/entitlements.plist"
export MACOSX_DEPLOYMENT_TARGET="$MIN_MACOS"

say() { printf '\n==> %s\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "Не найден $1. $2"; exit 1; }; }

[[ "$(uname -s)" == "Darwin" ]] || { echo "Сборка .app возможна только на macOS."; exit 1; }
need swiftc "Установите Xcode Command Line Tools: xcode-select --install"
need cmake "brew install cmake"
need git "xcode-select --install"
need uv "brew install uv"
mkdir -p "$SRC" "$OUT/bin" "$OUT/models"

fetch() { # repo tag folder
  if [[ ! -d "$SRC/$3" ]]; then
    git clone --quiet --depth 1 --branch "$2" "https://github.com/$1.git" "$SRC/$3"
  fi
}

# Not tuned to the build machine: Apple clang's arm64 default (apple-m1) runs on every
# Apple Silicon Mac, and Metal does the heavy lifting for the summary model anyway.
CPU_FLAGS=(-DGGML_NATIVE=OFF)
FFMPEG_ASM=()
[[ "$ARCH" == "x86_64" ]] && ! command -v nasm >/dev/null 2>&1 && FFMPEG_ASM=(--disable-x86asm)

say "FFmpeg $FFMPEG_TAG (статическая сборка, только системные фреймворки)"
if [[ ! -x "$OUT/bin/ffmpeg" || ! -x "$OUT/bin/ffprobe" ]]; then
  fetch FFmpeg/FFmpeg "$FFMPEG_TAG" ffmpeg
  (
    cd "$SRC/ffmpeg"
    make distclean >/dev/null 2>&1 || true
    ./configure --prefix="$WORK/ffmpeg-$ARCH" ${FFMPEG_ASM[@]+"${FFMPEG_ASM[@]}"} \
      --enable-static --disable-shared --disable-doc --disable-debug \
      --disable-ffplay --disable-network --disable-autodetect \
      --enable-avfoundation --enable-audiotoolbox --enable-zlib \
      --extra-cflags="-mmacosx-version-min=$MIN_MACOS" --extra-ldflags="-mmacosx-version-min=$MIN_MACOS"
    make -j"$JOBS"
    cp ffmpeg ffprobe "$OUT/bin/"
  )
fi

cmake_tool() { # source-folder build-name target extra-flags...
  local source="$1" name="$2" target="$3"
  shift 3
  cmake -S "$SRC/$source" -B "$WORK/$name-$ARCH" -DCMAKE_BUILD_TYPE=Release \
    -DBUILD_SHARED_LIBS=OFF -DGGML_METAL=ON -DGGML_METAL_EMBED_LIBRARY=ON \
    -DCMAKE_OSX_DEPLOYMENT_TARGET="$MIN_MACOS" -DCMAKE_OSX_ARCHITECTURES="$ARCH" \
    "${CPU_FLAGS[@]}" "$@"
  cmake --build "$WORK/$name-$ARCH" -j"$JOBS" --target "$target"
  cp "$WORK/$name-$ARCH/bin/$target" "$OUT/bin/"
}

say "whisper.cpp $WHISPER_TAG"
if [[ ! -x "$OUT/bin/whisper-cli" ]]; then
  fetch ggml-org/whisper.cpp "$WHISPER_TAG" whisper.cpp
  cmake_tool whisper.cpp whisper whisper-cli -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_SERVER=OFF
fi

say "llama.cpp $LLAMA_TAG"
if [[ ! -x "$OUT/bin/llama-server" ]]; then
  fetch ggml-org/llama.cpp "$LLAMA_TAG" llama.cpp
  cmake_tool llama.cpp llama llama-server -DLLAMA_CURL=OFF -DLLAMA_OPENSSL=OFF \
    -DLLAMA_BUILD_TESTS=OFF -DLLAMA_BUILD_EXAMPLES=OFF -DLLAMA_USE_PREBUILT_UI=OFF
fi

say "Helper системного звука (ScreenCaptureKit)"
swiftc -swift-version 5 -O -target "$ARCH-apple-macos$MIN_MACOS" \
  -o "$OUT/bin/samarizator-system-audio" native/macos-capture/main.swift

for binary in "$OUT"/bin/*; do
  # Static means: nothing outside /usr/lib and /System. A Homebrew path here would
  # break on every Mac without that exact Homebrew installation.
  if otool -L "$binary" | tail -n +2 | grep -vqE '^\s+(/usr/lib/|/System/)'; then
    echo "$binary ссылается на библиотеки вне системы:"
    otool -L "$binary"
    exit 1
  fi
done

say "Модели распознавания"
download() { # url target; resumes an interrupted .part
  [[ -s "$2" ]] && return 0
  curl -fL --retry 5 -C - -o "$2.part" "$1"
  mv -f "$2.part" "$2"
}
download "$WHISPER_MODEL_URL" "$OUT/models/ggml-small-q5_1.bin"
download "$VAD_MODEL_URL" "$OUT/models/ggml-silero-v6.2.0.bin"

say "Python и Qt (PyInstaller)"
VENV="$WORK/venv-$ARCH"
[[ -x "$VENV/bin/python" ]] || uv venv --python 3.12 "$VENV"
uv pip install --python "$VENV/bin/python" --quiet "$ROOT" "pyinstaller>=6.10,<7"
if [[ -n "${SAMARIZATOR_BUNDLE_LLM:-}" ]]; then
  say "Модель сводок $SAMARIZATOR_BUNDLE_LLM внутрь приложения"
  LLM_URL="$("$VENV/bin/python" -c "from samarizator.local_llm import PRESETS as P; print(P['$SAMARIZATOR_BUNDLE_LLM'].url)")"
  LLM_FILE="$("$VENV/bin/python" -c "from samarizator.local_llm import PRESETS as P; print(P['$SAMARIZATOR_BUNDLE_LLM'].file)")"
  download "$LLM_URL" "$OUT/models/$LLM_FILE"
fi
ICONSET="$WORK/Samarizator.iconset"
rm -rf "$ICONSET"
QT_QPA_PLATFORM=offscreen "$VENV/bin/python" packaging/macos/make_icon.py "$ICONSET"
iconutil -c icns "$ICONSET" -o "$WORK/Samarizator.icns"
rm -rf "$WORK/dist" "$WORK/pyinstaller"
SAMARIZATOR_VERSION="$VERSION" SAMARIZATOR_ICON="$WORK/Samarizator.icns" \
  "$VENV/bin/pyinstaller" --noconfirm --clean --log-level WARN \
  --distpath "$WORK/dist" --workpath "$WORK/pyinstaller" packaging/macos/samarizator.spec

APP="$WORK/dist/Samarizator.app"
RES="$APP/Contents/Resources"
mkdir -p "$RES/bin" "$RES/models"
cp "$OUT"/bin/* "$RES/bin/"
cp "$OUT"/models/* "$RES/models/"
chmod 755 "$RES"/bin/*
echo "$VERSION · $COMMIT" > "$RES/build.txt"

say "Подпись ($IDENTITY)"
for binary in "$RES"/bin/*; do
  codesign --force --options runtime --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$binary"
done
codesign --force --deep --options runtime --entitlements "$ENTITLEMENTS" --sign "$IDENTITY" "$APP"
codesign --verify --deep --strict "$APP"

say "Самопроверка приложения"
"$APP/Contents/MacOS/Samarizator" --run-module samarizator.selfcheck

notarize() {
  [[ -n "${APPLE_ID:-}" && -n "${APPLE_TEAM_ID:-}" && -n "${APPLE_APP_PASSWORD:-}" && "$IDENTITY" != "-" ]] || return 0
  say "Нотаризация $(basename "$1")"
  xcrun notarytool submit "$1" --apple-id "$APPLE_ID" --team-id "$APPLE_TEAM_ID" \
    --password "$APPLE_APP_PASSWORD" --wait
  xcrun stapler staple "$2"
}
ZIP="$WORK/Samarizator-notarize.zip"
rm -f "$ZIP"
ditto -c -k --keepParent "$APP" "$ZIP"
notarize "$ZIP" "$APP"

say "Образ диска"
STAGE="$WORK/dmg"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp packaging/macos/README-dmg.txt "$STAGE/Как установить.txt"
SUFFIX="${SAMARIZATOR_BUNDLE_LLM:+-with-$SAMARIZATOR_BUNDLE_LLM}"
DMG="$ROOT/dist/Samarizator-$VERSION-$ARCH$SUFFIX.dmg"
mkdir -p "$ROOT/dist"
rm -f "$DMG"
hdiutil create -volname "Samarizator" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
[[ "$IDENTITY" == "-" ]] || codesign --force --sign "$IDENTITY" "$DMG"
notarize "$DMG" "$DMG"
say "Готово: $DMG ($(du -h "$DMG" | cut -f1))"
