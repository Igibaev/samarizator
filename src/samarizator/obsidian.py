"""Find the vault Obsidian actually knows about, instead of guessing its name.

`obsidian://open?vault=<name>` only works when Obsidian has a vault registered under
exactly that name, and `?path=<absolute path>` only when that exact path matches a
registered vault — on macOS it often does not, because iCloud's "Desktop & Documents"
sync turns ~/Documents into a symlink. Obsidian keeps its own list of vaults, so the
reliable route is to read it and address the vault by id.
"""

import json
import os
from pathlib import Path
from urllib.parse import quote

CONFIG = Path.home() / "Library/Application Support/obsidian/obsidian.json"


def config_path():
    return Path(os.environ.get("SAMARIZATOR_OBSIDIAN_CONFIG") or CONFIG)


def real(path):
    """Compare paths the way the filesystem sees them, symlinks resolved."""
    try:
        return Path(path).expanduser().resolve()
    except (OSError, RuntimeError):
        return Path(path).expanduser()


def vaults(path=None):
    """Registered vaults as (id, path) pairs. Empty when Obsidian has never run."""
    path = Path(path) if path else config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    found = []
    for vault_id, entry in (data.get("vaults") or {}).items():
        location = (entry or {}).get("path")
        if isinstance(location, str) and location:
            found.append((vault_id, Path(location)))
    return found


def vault_for(note, path=None):
    """The registered vault containing `note`, deepest first, or None.

    The deepest match matters: a user can have both ~/Documents and
    ~/Documents/Samarizator registered, and the note belongs to the inner one.
    """
    target = real(note)
    matches = []
    for vault_id, location in vaults(path):
        root = real(location)
        if target == root or root in target.parents:
            matches.append((len(root.parts), vault_id, root))
    if not matches:
        return None
    _, vault_id, root = max(matches)
    return vault_id, root


def note_url(note, path=None):
    """An obsidian:// URL Obsidian can resolve, or None when no vault holds the note."""
    found = vault_for(note, path)
    if not found:
        return None
    vault_id, root = found
    relative = real(note).relative_to(root).with_suffix("")
    return (
        f"obsidian://open?vault={quote(vault_id, safe='')}"
        f"&file={quote(relative.as_posix(), safe='')}"
    )
