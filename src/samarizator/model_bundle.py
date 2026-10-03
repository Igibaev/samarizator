"""Models shipped inside the app: taken from the repository, downloaded only as a fallback.

`models/bundle.json` in the repository says which models the app carries:

    {"whisper": "ggml-large-v3-turbo.bin",
     "vad": "ggml-silero-v6.2.0.bin",
     "llm": "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
     "files": {"Qwen3-4B-Instruct-2507-Q4_K_M.gguf": {"sha256": "…", "url": "…"}}}

The build looks for each file in `models/` (Git LFS), then for its parts
(`name.part-000`, …: Git LFS limits one file to 2 GB on the free plan), and downloads
it only when neither is there. A copy of the manifest goes into the app, so the first
launch selects exactly these models. Whatever is missing at runtime is offered for
download inside the app.

    python -m samarizator.model_bundle provide --source models --target <dir>
    python -m samarizator.model_bundle split models/<big file>
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from .bundle import resources

ROLES = ("whisper", "vad", "llm")
PART_SIZE = 1900 * 1024**2  # under the 2 GB per-file limit of Git LFS on the free plan
DEFAULTS = {"whisper": "ggml-small-q5_1.bin", "vad": "ggml-silero-v6.2.0.bin", "llm": ""}


def known_urls():
    """Download addresses of every model the app knows by file name."""
    from .local_llm import PRESETS
    from .setup_models import MODELS, QUALITY_MODELS, WHISPER_PRESETS

    urls = dict(MODELS)
    urls.update(QUALITY_MODELS)
    urls.update({preset.file: preset.url for preset in PRESETS.values()})
    urls.update({preset.file: preset.url for preset in WHISPER_PRESETS.values()})
    return urls


def load(path):
    path = Path(path)
    data = json.loads(path.read_text()) if path.is_file() else {}
    manifest = {role: str(data.get(role, DEFAULTS[role]) or "") for role in ROLES}
    manifest["files"] = {k: dict(v) for k, v in (data.get("files") or {}).items()}
    for name in [manifest[role] for role in ROLES if manifest[role]]:
        if Path(name).name != name:
            raise ValueError(f"В bundle.json указано имя файла, а не путь: {name}")
    return manifest


def shipped():
    """The manifest copied into the app, or None on a checkout."""
    root = resources()
    if root is None or not (root / "models" / "bundle.json").is_file():
        return None
    return load(root / "models" / "bundle.json")


def is_lfs_pointer(path):
    """A checkout without `git lfs pull` holds 130-byte pointer files, not models."""
    try:
        if path.stat().st_size > 1024:
            return False
        return path.read_bytes().startswith(b"version https://git-lfs")
    except OSError:
        return False


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(8 * 1024**2):
            h.update(chunk)
    return h.hexdigest()


def parts(source, name):
    return sorted(Path(source).glob(name + ".part-*"))


def provide(name, source, target, meta=None, log=print, downloader=None):
    """Put `name` into `target`: repository copy → joined parts → download. Returns how."""
    meta = meta or {}
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    final = target / name
    expected = meta.get("sha256", "").lower()
    if final.is_file() and (not expected or digest(final) == expected):
        return "cached"
    local = source / name
    pieces = parts(source, name)
    if local.is_file() and not is_lfs_pointer(local):
        shutil.copyfile(local, final)
        how = "repository"
    elif pieces and not any(is_lfs_pointer(piece) for piece in pieces):
        with final.open("wb") as out:
            for piece in pieces:
                with piece.open("rb") as chunk:
                    shutil.copyfileobj(chunk, out, 16 * 1024**2)
        how = f"repository, {len(pieces)} parts"
    else:
        url = meta.get("url") or known_urls().get(name)
        if not url:
            raise ValueError(f"{name}: нет ни в models/, ни адреса для скачивания (files.{name}.url).")
        if local.is_file() or pieces:
            log(f"{name}: в репозитории только указатели Git LFS — выполните `git lfs pull`. Скачиваю.")
        else:
            log(f"{name}: нет в models/, скачиваю.")
        if downloader is None:
            from .local_llm import download as downloader
        downloader(url, final)
        how = "download"
    if expected and digest(final) != expected:
        final.unlink()
        raise ValueError(f"{name}: контрольная сумма не совпала с bundle.json.")
    return how


def provide_all(manifest_path, source, target, extra_llm="", log=print, downloader=None):
    manifest = load(manifest_path)
    if extra_llm:
        manifest["llm"] = extra_llm
    for role in ROLES:
        name = manifest[role]
        if not name:
            continue
        how = provide(name, source, target, manifest["files"].get(name), log=log, downloader=downloader)
        log(f"{role}: {name} — {how}")
    shipped_manifest = {role: manifest[role] for role in ROLES}
    (Path(target) / "bundle.json").write_text(json.dumps(shipped_manifest, ensure_ascii=False, indent=2))
    return manifest


def split(path, manifest_path=None, keep=False, size=PART_SIZE):
    """Cut a large model into Git LFS-sized parts and record its checksum."""
    path = Path(path)
    whole = digest(path)
    written = []
    with path.open("rb") as f:
        index = 0
        while chunk := f.read(size):
            piece = path.with_name(f"{path.name}.part-{index:03}")
            piece.write_bytes(chunk)
            written.append(piece)
            index += 1
    manifest_path = Path(manifest_path or path.parent / "bundle.json")
    data = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
    data.setdefault("files", {}).setdefault(path.name, {})["sha256"] = whole
    manifest_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    if not keep:
        path.unlink()
    return written


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)
    provide_cmd = commands.add_parser("provide", help="положить модели в папку сборки")
    provide_cmd.add_argument("--source", default="models")
    provide_cmd.add_argument("--target", required=True)
    provide_cmd.add_argument("--manifest", default=None)
    provide_cmd.add_argument(
        "--llm", default="", help="файл или ключ пресета модели сводок поверх bundle.json"
    )
    split_cmd = commands.add_parser("split", help="разрезать большую модель для Git LFS")
    split_cmd.add_argument("file")
    split_cmd.add_argument("--keep", action="store_true", help="не удалять исходный файл")
    args = parser.parse_args(argv)
    try:
        if args.command == "provide":
            from .local_llm import PRESETS

            llm = PRESETS[args.llm].file if args.llm in PRESETS else args.llm
            manifest = args.manifest or Path(args.source) / "bundle.json"
            provide_all(manifest, args.source, args.target, extra_llm=llm)
        else:
            for piece in split(args.file, keep=args.keep):
                print(piece)
    except (OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
