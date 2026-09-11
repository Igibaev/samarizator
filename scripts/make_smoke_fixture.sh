#!/usr/bin/env bash
# Build a deterministic macOS `say` fixture long enough to cross a chunk boundary,
# instead of hoping a fixed phrase list is long enough (it previously was not:
# a single "say -r 100 <text>" run produced under 30s of audio, so
# scripts/smoke_audio.py always saw one chunk and never exercised a boundary).
#
# Usage: scripts/make_smoke_fixture.sh [output.wav] [min_seconds]
set -euo pipefail

out="${1:-/tmp/samarizator-smoke.wav}"
min_seconds="${2:-150}"

phrase='The project deadline is Friday. Alice will prepare the report. We need to review the budget before the next meeting. The team will meet on Monday to discuss the results. We agreed to postpone the release until the tests pass. Bob is responsible for checking the requirements. We have two open questions about the budget and the timeline. Please keep the original recording for review. The next meeting is scheduled for Wednesday.'

aiff="$(mktemp -t samarizator-smoke).aiff"
trap 'rm -f "$aiff"' EXIT

text="$phrase"
duration=0
attempt=1
# Keep doubling the script until ffprobe confirms we cleared the target duration;
# verify with ffprobe, never assume a fixed phrase list is long enough.
while :; do
  say -v Samantha -r 100 -o "$aiff" "$text"
  duration=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$aiff")
  echo "attempt $attempt: ${duration}s (target >= ${min_seconds}s)" >&2
  awk -v d="$duration" -v m="$min_seconds" 'BEGIN{exit !(d>=m)}' && break
  attempt=$((attempt + 1))
  if [ "$attempt" -gt 6 ]; then
    echo "Could not reach ${min_seconds}s after $attempt attempts (last: ${duration}s)." >&2
    exit 1
  fi
  text="$text $phrase"
done

ffmpeg -nostdin -v error -y -i "$aiff" -ac 1 -ar 16000 -c:a pcm_s16le "$out"
echo "Wrote $out (${duration}s, verified via ffprobe)."
