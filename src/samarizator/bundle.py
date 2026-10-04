"""Where tools and bundled models live: inside Samarizator.app or on a developer checkout.

The portable app carries FFmpeg, whisper-cli, llama-server, the ScreenCaptureKit helper
and the small Whisper/VAD models in `Contents/Resources`. A checkout started with
`./start.sh` finds the same tools on PATH (Homebrew) and models in Application Support.
"""

import os
import shutil
import sys
from pathlib import Path


def frozen():
    return bool(getattr(sys, "frozen", False))


def resources():
    """`Contents/Resources` of the app bundle, or None on a checkout.

    SAMARIZATOR_RESOURCES overrides the location, so tests and a hand-assembled
    folder behave exactly like the bundle.
    """
    override = os.environ.get("SAMARIZATOR_RESOURCES")
    if override:
        return Path(override)
    if not frozen():
        return None
    executable = Path(sys.executable).resolve()
    # PyInstaller BUNDLE: Contents/MacOS/Samarizator next to Contents/Resources.
    candidates = [executable.parent.parent / "Resources", Path(getattr(sys, "_MEIPASS", executable.parent))]
    return next((path for path in candidates if (path / "bin").is_dir()), candidates[0])


def tool(name):
    """Absolute path to a bundled tool, falling back to PATH; the bare name if neither exists.

    The bare name keeps the error from the subprocess call that follows, which already
    explains what is missing.
    """
    root = resources()
    if root is not None:
        candidate = root / "bin" / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return shutil.which(name) or name


def has_tool(name):
    return Path(tool(name)).is_absolute()


def bundled_model(name):
    root = resources()
    if root is None:
        return None
    path = root / "models" / name
    return path if path.is_file() else None


def python_command(module, *args):
    """Command line that runs `python -m module` both on a checkout and in the frozen app."""
    if frozen():
        return [sys.executable, "--run-module", module, *map(str, args)]
    return [sys.executable, "-m", module, *map(str, args)]
