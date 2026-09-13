#!/bin/bash
# Collects everything needed to explain why system-audio capture stays empty.
# Run it with sound playing, then send the whole output back.
set -uo pipefail
cd "$(dirname "$0")/.."

SECONDS_PER_RUN="${SECONDS_PER_RUN:-5}"
HOME_DIR="$(.venv/bin/python -c 'from samarizator.config import data_dir; print(data_dir())' 2>/dev/null)"
HELPER="${SAMARIZATOR_HELPER:-${HOME_DIR:-$HOME/Library/Application Support/Samarizator}/bin/samarizator-system-audio}"
OUT="$(mktemp -d)"

echo "=== 1. Система ==="
sw_vers
uname -m
swiftc --version 2>&1 | head -2
echo "git: $(git rev-parse --short HEAD 2>/dev/null || echo '—')"

echo
echo "=== 2. Сборка helper'а ==="
.venv/bin/python -m samarizator.screencapture
ls -l "$HELPER" 2>&1

echo
echo "=== 3. Разрешение (probe) ==="
"$HELPER" probe 2>&1

echo
echo "=== 4. Варианты захвата по $SECONDS_PER_RUN с ==="
echo "ВКЛЮЧИТЕ ЗВУК СЕЙЧАС: видео, музыку — что угодно, погромче."
sleep 2

run_variant() {
  local name="$1"; shift
  "$HELPER" capture --seconds "$SECONDS_PER_RUN" "$@" \
    >"$OUT/$name.pcm" 2>"$OUT/$name.log"
  local code=$?
  local size
  size=$(wc -c <"$OUT/$name.pcm" | tr -d ' ')
  printf '%-22s байт=%-9s код=%-3s %s\n' "$name" "$size" "$code" \
    "$(grep -o '"peak":[^,}]*' "$OUT/$name.log" | tail -1)"
}

run_variant "default"        # как в приложении: без выхода .screen
run_variant "with-video"     --with-video      # регрессия: на 15.6.1 глушит аудио
run_variant "size-2x2"       --size 2x2        # вырожденный кадр
run_variant "size-1080p"     --size 1920x1080  # обычный кадр
run_variant "include-self"   --include-self    # не исключать свой звук

echo
echo "=== 5. Журналы вариантов ==="
for log in "$OUT"/*.log; do
  echo "--- $(basename "$log" .log) ---"
  cat "$log"
done

echo
echo "=== 6. Запись целиком через FFmpeg ==="
.venv/bin/python -m samarizator.screencapture check

echo
echo "=== 7. Файлы для проверки на слух ==="
# Raw PCM converted to WAV here: option spellings for raw input differ between
# FFmpeg builds (ffplay 9 dropped -ac), while a WAV opens in any player.
for pcm in "$OUT"/*.pcm; do
  [ -s "$pcm" ] || continue
  wav="${pcm%.pcm}.wav"
  if ffmpeg -v error -y -f f32le -sample_rate 48000 -ch_layout mono -i "$pcm" "$wav" 2>/dev/null; then
    echo "  afplay $wav"
  else
    echo "  (не удалось преобразовать $pcm; сырой формат: f32le, 48000 Гц, моно)"
  fi
done
