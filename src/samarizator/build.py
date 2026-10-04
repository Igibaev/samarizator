"""Visible build identity, independent of the interpreter's installed package metadata."""

import subprocess
from pathlib import Path


def build_label():
    from .bundle import resources

    # The packaged app has no git checkout: build.sh writes the commit next to its tools.
    root = resources()
    if root is not None and (root / "build.txt").is_file():
        return "Локальные сводки · " + (root / "build.txt").read_text().strip()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        revision = result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        revision = "архив"
    return "Локальные сводки · " + revision
