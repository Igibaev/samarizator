#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
# uv versions before and after the option rename; certificate verification stays enabled.
export UV_SYSTEM_CERTS="${UV_SYSTEM_CERTS:-true}"
export UV_NATIVE_TLS="${UV_NATIVE_TLS:-true}"
if [[ "$(uname -s)" != "Darwin" && "${SAMARIZATOR_DEV:-0}" != "1" ]]; then
  echo 'Этот установщик предназначен для macOS. Для разработки: SAMARIZATOR_DEV=1 ./start.sh'
  exit 1
fi
if [[ "$(uname -s)" == "Darwin" ]]; then
  if ! command -v brew >/dev/null 2>&1; then
    echo 'Сначала установите Homebrew: https://brew.sh (потребуются права на установку).'
    echo 'Затем повторите ./start.sh'
    exit 1
  fi
  packages=()
  command -v uv >/dev/null 2>&1 || packages+=(uv)
  command -v ffmpeg >/dev/null 2>&1 || packages+=(ffmpeg)
  command -v whisper-cli >/dev/null 2>&1 || packages+=(whisper-cpp)
  if [[ ${#packages[@]} -gt 0 ]]; then brew install "${packages[@]}"; fi
fi
uv sync --frozen --python 3.12
if [[ "${SAMARIZATOR_SKIP_DOWNLOAD:-0}" != "1" ]]; then
  .venv/bin/python -m samarizator.setup_models "$@"
elif [[ $# -gt 0 ]]; then
  echo 'Уберите SAMARIZATOR_SKIP_DOWNLOAD, чтобы применить --quality или --large-v3.'
  exit 1
fi
exec .venv/bin/python -m samarizator.app
