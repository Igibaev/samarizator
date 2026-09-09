"""Download public model weights only; never upload audio or read corporate keys."""

import hashlib
import json
import os
import tarfile
import urllib.request

from .config import data_dir

BASE = "https://github.com/k2-fsa/sherpa-onnx/releases/download/"
MODELS = {
    "ggml-small-q5_1.bin": "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q5_1.bin",
    "segmentation.tar.bz2": BASE
    + "speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2",
    "embedding.onnx": BASE
    + "speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx",
}


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
    try:
        with urllib.request.urlopen(url, timeout=60) as response, part.open("wb") as f:
            while chunk := response.read(1024 * 1024):
                f.write(chunk)
        if part.stat().st_size < 1024:
            raise ValueError("Вместо модели получен слишком маленький файл.")
        part.replace(path)
    finally:
        part.unlink(missing_ok=True)


def main():
    folder = data_dir() / "models"
    folder.mkdir(exist_ok=True)
    strict = os.environ.get("SAMARIZATOR_STRICT") == "1"
    for name, url in MODELS.items():
        if strict and name != "ggml-small-q5_1.bin":
            continue
        if name.startswith("segmentation") and (folder / "segmentation.onnx").exists():
            continue
        download(url, folder / name)
        if name.endswith(".tar.bz2"):
            # Extract only the model bytes, never arbitrary archive paths/symlinks.
            with tarfile.open(folder / name, "r:bz2") as archive:
                member = next(
                    m for m in archive.getmembers() if m.name.endswith("/model.onnx") and m.isfile()
                )
                stream = archive.extractfile(member)
                temp = folder / "segmentation.onnx.part"
                with temp.open("wb") as out:
                    while chunk := stream.read(1024 * 1024):
                        out.write(chunk)
                temp.replace(folder / "segmentation.onnx")
            (folder / name).unlink()
    manifest = {
        p.name: dict(sha256=digest(p), bytes=p.stat().st_size)
        for p in folder.iterdir()
        if p.suffix in {".bin", ".onnx"}
    }
    (folder / "download-manifest.json").write_text(json.dumps(manifest, indent=2))
    print("Модели готовы. Во время распознавания интернет не используется.")


if __name__ == "__main__":
    main()
