"""Visible build identity, independent of the interpreter's installed package metadata."""

import subprocess
from pathlib import Path


def build_label():
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
    return "Сводки 2.6 · Live: две дорожки · Companion MVP · " + revision
