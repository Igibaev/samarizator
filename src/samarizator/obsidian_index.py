"""Incremental, read-only indexing of a selected Obsidian vault."""

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, time, timezone
from pathlib import Path

MAX_NOTE_BYTES = 1_000_000
MAX_CHUNK_CHARS = 6_000
TASK = re.compile(r"^\s*[-*]\s+\[([ xX])\]\s+(.+?)\s*$")
DUE = re.compile(
    r"(?:📅\s*|\bdue\s*(?:::|:)\s*|@due\()(?P<date>\d{4}-\d{2}-\d{2})\)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SyncResult:
    scanned: int
    updated: int
    skipped: int
    tasks: int


def _within(root, path):
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, RuntimeError, ValueError):
        return False


def _title(lines, fallback):
    for line in lines:
        if match := re.match(r"^#\s+(.+?)\s*$", line):
            return match.group(1).strip()[:300]
    return fallback


def _due_at(text):
    match = DUE.search(text)
    if not match:
        return None
    try:
        date = datetime.strptime(match.group("date"), "%Y-%m-%d").date()
    except ValueError:
        return None
    # A date-only reminder becomes due at the start of that local day.  Store it
    # with the local offset so comparisons remain unambiguous after restart.
    return datetime.combine(date, time.min).astimezone(timezone.utc).isoformat()


def task_from_text(text):
    raw = str(text).strip()
    return DUE.sub("", raw).strip(" ·-"), _due_at(raw)


def parse_note(relative_path, text):
    """Return (title, chunks, checkbox tasks) without executing note content."""
    lines = text.splitlines()
    title = _title(lines, Path(relative_path).stem)
    chunks = []
    heading = title
    buffer = []

    def flush():
        body = "\n".join(buffer).strip()
        while body:
            chunks.append((heading, body[:MAX_CHUNK_CHARS]))
            body = body[MAX_CHUNK_CHARS:]

    tasks = []
    occurrences = {}
    for number, line in enumerate(lines, 1):
        if match := re.match(r"^(#{1,6})\s+(.+?)\s*$", line):
            flush()
            heading = match.group(2).strip()[:300]
            buffer = []
        else:
            buffer.append(line)
        if match := TASK.match(line):
            raw = match.group(2).strip()
            clean, due_at = task_from_text(raw)
            if not clean:
                continue
            key_text = " ".join(clean.casefold().split())
            ordinal = occurrences.get(key_text, 0)
            occurrences[key_text] = ordinal + 1
            origin = f"{relative_path}\0{key_text}\0{ordinal}"
            tasks.append(
                dict(
                    title=clean[:500],
                    line=number,
                    origin_key=hashlib.sha256(origin.encode()).hexdigest(),
                    due_at=due_at,
                    status="done" if match.group(1).strip() else "open",
                )
            )
    flush()
    return title, chunks or [(title, "")], tasks


def sync_vault(vault, store):
    root = Path(vault).expanduser()
    if not root.exists() or not root.is_dir():
        raise ValueError("Папка Obsidian не найдена. Укажите её в настройках Samarizator.")
    root = root.resolve()
    store.prepare_vault(root)
    scanned = updated = skipped = task_count = 0
    existing = []
    for path in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in path.relative_to(root).parts):
            continue
        if path.is_symlink() or not _within(root, path):
            skipped += 1
            continue
        scanned += 1
        stat = path.stat()
        relative = path.relative_to(root).as_posix()
        existing.append(relative)
        if stat.st_size > MAX_NOTE_BYTES:
            store.remove_note(relative)
            skipped += 1
            continue
        previous = store.note_signature(relative)
        if previous and previous[:2] == (stat.st_mtime_ns, stat.st_size):
            continue
        try:
            raw = path.read_bytes()
            text = raw.decode("utf-8")
        except (OSError, UnicodeDecodeError):
            store.remove_note(relative)
            skipped += 1
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if previous and previous[2] == digest:
            continue
        title, chunks, tasks = parse_note(relative, text)
        store.sync_note(relative, stat.st_mtime_ns, stat.st_size, digest, title, chunks, tasks)
        updated += 1
        task_count += len(tasks)
    store.remove_missing_notes(existing)
    return SyncResult(scanned=scanned, updated=updated, skipped=skipped, tasks=task_count)


def context_for(store, question, limit=6):
    """Bound retrieved note text before it reaches the local model."""
    results = store.search_notes(question, limit=limit)
    blocks = []
    total = 0
    for row in results:
        block = f"SOURCE: {row['path']}\nHEADING: {row['heading']}\n{row['body'].strip()}"
        if total + len(block) > 24_000:
            block = block[: max(0, 24_000 - total)]
        if block:
            blocks.append(block)
            total += len(block)
        if total >= 24_000:
            break
    return "\n\n---\n\n".join(blocks)
