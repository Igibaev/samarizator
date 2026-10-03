"""Download public model weights only; never upload audio or read corporate keys."""

import argparse
import hashlib
import json
import os
import ssl
import urllib.request

import truststore

from .config import Settings, data_dir
from .local_llm import PRESETS as LLM_PRESETS
from .local_llm import download as download_llm
from .local_llm import recommended_preset

MODELS = {
    "ggml-small-q5_1.bin": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin",
}
QUALITY_MODELS = {
    "ggml-silero-v6.2.0.bin": "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v6.2.0.bin",
}
LARGE_MODEL = "ggml-large-v3.bin"


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def download(url, path):
    if path.exists() and path.stat().st_size > 1024:
        return
    part = path.with_suffix(path.suffix + ".part")
    print(f"Скачивание {path.name}…", flush=True)
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    try:
        with urllib.request.urlopen(url, timeout=60, context=context) as response, part.open("wb") as f:
            while chunk := response.read(1024 * 1024):
                f.write(chunk)
        if part.stat().st_size < 1024:
            raise ValueError("Вместо модели получен слишком маленький файл.")
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--quality", action="store_true", help="VAD и профиль M4 Pro / 48 ГБ; текущая модель сохранится"
    )
    parser.add_argument("--large-v3", action="store_true", help="Скачать и выбрать full large-v3 (~3.1 GB)")
    parser.add_argument(
        "--llm",
        choices=["auto", *LLM_PRESETS],
        help="Скачать и выбрать локальную модель сводок; auto — по объёму памяти этого Mac",
    )
    args = parser.parse_args()
    folder = data_dir() / "models"
    folder.mkdir(exist_ok=True)
    strict = os.environ.get("SAMARIZATOR_STRICT") == "1"
    models = dict(MODELS)
    if args.quality and not strict:
        models.update(QUALITY_MODELS)
    if args.large_v3:
        models[LARGE_MODEL] = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/" + LARGE_MODEL
    for name, url in models.items():
        download(url, folder / name)
    manifest = {
        p.name: dict(sha256=digest(p), bytes=p.stat().st_size)
        for p in folder.iterdir()
        if p.suffix == ".bin"
    }
    (folder / "download-manifest.json").write_text(json.dumps(manifest, indent=2))
    llm_path = None
    if args.llm:
        key = recommended_preset() if args.llm == "auto" else args.llm
        preset = LLM_PRESETS[key]
        llm_path = folder / preset.file
        if not llm_path.is_file():
            print(f"Скачивание модели сводок {preset.file} ({preset.size_gb:g} ГБ)…", flush=True)
            download_llm(preset.url, llm_path)
    if args.quality or args.large_v3 or llm_path:
        settings = Settings.load()
        if llm_path:
            settings.llm_model, settings.llm_preset = str(llm_path), key
        if args.quality:
            settings = settings.quality_profile()
            if strict:
                settings.vad = False
        if args.large_v3:
            settings.whisper_model = str(folder / LARGE_MODEL)
            settings.memory_gb = max(16, settings.memory_gb)
        settings.save()
        print("Настройки обновлены. Существующие записи сохраняют параметры продолжения.")
    print("Модели готовы. Во время распознавания и сводок интернет не используется.")


if __name__ == "__main__":
    main()
