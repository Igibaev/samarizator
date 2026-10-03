"""`Samarizator --run-module samarizator.selfcheck`: is everything the app needs inside it?

Used by the build after packaging, and useful for a user who reports that something
"does not start": it prints which tools and models were found and whether they run.
"""

import subprocess
import sys

from .bundle import bundled_model, resources, tool

TOOLS = [
    ("ffmpeg", ["-hide_banner", "-version"]),
    ("ffprobe", ["-hide_banner", "-version"]),
    ("whisper-cli", ["--help"]),
    ("llama-server", ["--version"]),
    ("samarizator-system-audio", ["probe"]),
]
MODELS = ["ggml-small-q5_1.bin", "ggml-silero-v6.2.0.bin"]


def run(strict=True):
    failures = []
    print(f"Ресурсы: {resources() or 'не приложение — используется PATH'}")
    for name, args in TOOLS:
        path = tool(name)
        try:
            result = subprocess.run([path, *args], capture_output=True, text=True, timeout=30)
            # whisper-cli --help exits non-zero on some versions; the helper's probe may
            # report a missing permission. Starting at all is what matters here.
            print(f"✓ {name}: {path} (код {result.returncode})")
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"✗ {name}: {path} — {exc}")
            failures.append(name)
    from .model_bundle import shipped

    manifest = shipped()
    names = [manifest[role] for role in ("whisper", "vad", "llm") if manifest[role]] if manifest else MODELS
    for name in names:
        path = bundled_model(name)
        print(f"{'✓' if path else '✗'} модель {name}: {path or 'нет в приложении'}")
        if not path:
            failures.append(name)
    try:
        from PySide6.QtWidgets import QApplication  # noqa: F401

        print("✓ Qt")
    except ImportError as exc:
        print(f"✗ Qt: {exc}")
        failures.append("Qt")
    try:
        from .privacy import classify

        if not classify("Ирине"):
            raise ValueError("словарь не распознал имя")
        print("✓ словарь для обезличивания")
    except (ImportError, OSError, ValueError) as exc:
        print(f"✗ словарь для обезличивания: {exc}")
        failures.append("pymorphy3")
    if failures:
        print("Не хватает: " + ", ".join(failures))
    return 1 if failures and strict else 0


if __name__ == "__main__":
    sys.exit(run())
