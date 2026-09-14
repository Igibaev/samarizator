#!/bin/bash
# Compares the ways two live tracks can be mixed, on your own devices.
# Speak into the microphone the whole time and keep some system sound playing.
set -uo pipefail
cd "$(dirname "$0")/.."

SECONDS_PER_RUN="${SECONDS_PER_RUN:-10}"
OUT="$(mktemp -d)"

echo "=== Устройства ==="
ffmpeg -hide_banner -f avfoundation -list_devices true -i "" 2>&1 | sed -n '/audio devices/,$p'

echo
echo "Каждый прогон — $SECONDS_PER_RUN с. ГОВОРИТЕ В МИКРОФОН и держите звук из приложений."
echo

run() {
  local name="$1" source="$2" mix="$3"
  echo "--- $name (источник: $source, сведение: $mix) ---"
  .venv/bin/python -m samarizator.live record \
    --seconds "$SECONDS_PER_RUN" --mix "$mix" --source "$source" --folder "$OUT" 2>&1 |
    sed "s|^|    |"
}

# The microphone alone is the reference: this is how it sounds without any mixing.
run "эталон · только микрофон" microphone default
run "текущий по умолчанию"     both default
run "жёсткая синхронизация"    both hard-sync
run "без выравнивания"         both no-resample
run "старое поведение"         both legacy-pan

echo
echo "=== Послушайте и сравните ==="
for wav in "$OUT"/*.wav; do
  echo "  afplay \"$wav\""
done
echo
echo "Каналы по отдельности (1 — микрофон, 2 — системный звук):"
echo "  ffmpeg -i ЗАПИСЬ.wav -af \"pan=mono|c0=c0\" /tmp/mic.wav && afplay /tmp/mic.wav"
