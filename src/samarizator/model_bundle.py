"""Models the build puts inside the app. By default only the tiny VAD model.

The user chooses and downloads the recognition and summary models in the app, where
each choice shows its download size and the memory it takes on this Mac. Nothing big
is kept in the repository. `models/bundle.json` says what the build embeds:

    {"whisper": "", "vad": "ggml-silero-v6.2.0.bin", "llm": ""}

An empty name means "do not embed: the app offers the download". For a build that
must work without internet, name a model there (or pass SAMARIZATOR_BUNDLE_LLM): the
build takes the file from `models/` on the build machine (ignored by Git) or downloads
it, and checks `files.<name>.sha256` when it is given. A copy of the manifest goes into
the app, so the first launch selects exactly these models.

    python -m samarizator.model_bundle provide --source models --target <dir>
"""

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

from .bundle import resources

ROLES = ("whisper", "vad", "llm")
# Fallbacks for a checkout without the manifest (development runs).
DEFAULTS = {"whisper": "ggml-small-q5_1.bin", "vad": "ggml-silero-v6.2.0.bin", "llm": ""}


def known_urls():
    """Download addresses of every model the app knows by file name."""
    from .local_llm import DECISION_PRESET, PRESETS
    from .setup_models import MODELS, QUALITY_MODELS, WHISPER_PRESETS

    urls = dict(MODELS)
    urls.update(QUALITY_MODELS)
    urls.update({preset.file: preset.url for preset in PRESETS.values()})
    urls[DECISION_PRESET.file] = DECISION_PRESET.url
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


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        while chunk := f.read(8 * 1024**2):
            h.update(chunk)
    return h.hexdigest()


def provide(name, source, target, meta=None, log=print, downloader=None):
    """Put `name` into `target`: a copy from `source` → download. Returns how."""
    meta = meta or {}
    source, target = Path(source), Path(target)
    target.mkdir(parents=True, exist_ok=True)
    final = target / name
    expected = meta.get("sha256", "").lower()
    if final.is_file() and (not expected or digest(final) == expected):
        return "cached"
    local = source / name
    if local.is_file():
        shutil.copyfile(local, final)
        how = "local copy"
    else:
        url = meta.get("url") or known_urls().get(name)
        if not url:
            raise ValueError(f"{name}: нет ни в models/, ни адреса для скачивания (files.{name}.url).")
        log(f"{name}: скачиваю.")
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
    args = parser.parse_args(argv)
    try:
        from .local_llm import PRESETS

        llm = PRESETS[args.llm].file if args.llm in PRESETS else args.llm
        manifest = args.manifest or Path(args.source) / "bundle.json"
        provide_all(manifest, args.source, args.target, extra_llm=llm)
    except (OSError, ValueError) as exc:
        print(f"Ошибка: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
