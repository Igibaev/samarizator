#!/bin/bash
# Собирает FocusCompanion через SwiftPM, упаковывает результат в .app-бандл
# вручную (Xcode-проекта у нас нет) и запускает.
#
# ВНИМАНИЕ: скрипт требует Swift-тулчейн и macOS SDK — то есть исполняется
# только на macOS. В Linux-контейнере, где писался этот код, он не запускался
# и не проверялся.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

APP_NAME="FocusCompanion"
BUILD_CONFIG="release"
APP_BUNDLE="$SCRIPT_DIR/$APP_NAME.app"
CONTENTS_DIR="$APP_BUNDLE/Contents"
MACOS_DIR="$CONTENTS_DIR/MacOS"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

echo "==> Сборка $APP_NAME ($BUILD_CONFIG)"
swift build -c "$BUILD_CONFIG"

BIN_PATH="$(swift build -c "$BUILD_CONFIG" --show-bin-path)/$APP_NAME"

if [ ! -f "$BIN_PATH" ]; then
    echo "Бинарник не найден по пути: $BIN_PATH" >&2
    exit 1
fi

echo "==> Упаковка в $APP_NAME.app"
rm -rf "$APP_BUNDLE"
mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

cp "$BIN_PATH" "$MACOS_DIR/$APP_NAME"
cp "$SCRIPT_DIR/Resources/Info.plist" "$CONTENTS_DIR/Info.plist"

echo "==> Запуск"
open "$APP_BUNDLE"

echo "Готово. Приложение живёт в меню-баре (иконка-кружок) — там же пункт «Выход»."
