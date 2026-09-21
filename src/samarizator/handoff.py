"""Hand agreed action items from a finished summary to the notch companion.

The companion is the macOS app in focus-companion/. The two programs never talk
over the network: this module drops one JSON file per meeting into the
companion's own Application Support folder, and the companion watches that
folder. A file is written only when the folder already exists — that is, the
companion has been launched at least once on this Mac — so a machine without it
never gets stray files. (companion.py next door is the headless CLI the
companion drives; this module is the opposite direction.)

Everything here is optional enrichment: a failure of any step must never fail
the summary phase that calls it (see safe_handoff).
"""

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .character_prompts import (
    CHARACTER,
    EMOTIONS,
    IDENTITY,
    MAX_LINE_CHARS,
    MAX_TASK_CHARS,
    output_layer,
    task_layer,
)
from .summary import ChatClient, summary_views

# Matches TaskPersistence.bundleIdentifier in the Swift app; the folder is created
# by the app on first launch, which is exactly the signal we rely on.
BUNDLE_ID = "dev.samarizator.focuscompanion"
FORMAT_VERSION = 1
MAX_PROPOSALS = 5
# Stable ids across re-summaries of the same meeting: the companion overwrites the
# file, and a proposal the person already dismissed should not come back renamed.
_NAMESPACE = uuid.UUID("6f8d2c0a-7c9b-4b62-9e8e-2b1f4d3c5a71")

# Phrases the character is never allowed to say (see CHARACTER-BIBLE.md, «Границы»).
# A model line containing any of them is discarded for the deterministic fallback.
FORBIDDEN = (
    "опять",
    "снова не",
    "ты должен",
    "ты должна",
    "давно пора",
    "сколько можно",
    "почему не",
    "не уходи",
    "ты забыл",
    "ты забыла",
    "провал",
    "просрочен",
    "просрочил",
)


def home() -> Path:
    override = os.environ.get("FOCUS_COMPANION_HOME")
    if override:
        return Path(override)
    return Path.home() / "Library/Application Support" / BUNDLE_ID


def is_installed() -> bool:
    return home().is_dir()


def active_companion_tasks() -> list[str]:
    """Titles of the tasks currently in the companion's three slots (its own tasks.json).

    Reads both the current schema (`title` + `status`) and the one written by
    earlier phases (`text` + `isDone`), like the app's own decoder does.
    """
    try:
        rows = json.loads((home() / "tasks.json").read_text())
    except (OSError, ValueError):
        return []
    if not isinstance(rows, list):
        return []
    titles = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = row.get("title") if isinstance(row.get("title"), str) else row.get("text")
        if not isinstance(title, str) or not title.strip():
            continue
        active = row["status"] == "active" if "status" in row else not row.get("isDone")
        if active:
            titles.append(title)
    return titles


def candidate_actions(summary):
    """Agreed action items with their final status, deduplicated, at most MAX_PROPOSALS.

    The reconciled list wins when it exists: it already folds later revisions and
    cancellations. Without it (optional reconciliation can fail) the full ledger is
    used, never the 9-item brief — the brief drops small tasks by design.
    """
    _, detailed = summary_views(summary)
    pool = detailed.get("resolved") or detailed["items"]
    seen, picked = set(), []
    for item in pool:
        if item.get("kind") != "action" or item.get("status") != "agreed":
            continue
        text = item["text"].strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        picked.append(item)
        if len(picked) == MAX_PROPOSALS:
            break
    return picked


def proposal_id(mid, item):
    return str(uuid.uuid5(_NAMESPACE, f"{mid}:{item['text'].casefold()}"))


def plain_task_text(item):
    text = item["text"].strip()
    if item.get("owner"):
        text = f"{item['owner'].strip()}: {text}"
    return _clip(text, MAX_TASK_CHARS)


def _clip(text, limit):
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def mood_layer(now, free_slots, count):
    hour = now.hour
    if hour >= 23 or hour < 6:
        mood, why = "тихий, чуть сонный", "поздно, человеку скоро бы отдыхать"
    elif free_slots == 0:
        mood, why = "спокойный и заботливый", "все три слота у человека заняты, новое подождёт"
    elif count == 1:
        mood, why = "любопытный", "со встречи пришла всего одна задача"
    else:
        mood, why = "любопытный, немного игривый", "со встречи пришло несколько задач"
    return f"НАСТРОЕНИЕ СЕЙЧАС\n{mood.capitalize()}. Причина: {why}. Свободных слотов: {free_slots} из 3."


def memory_layer(title, active):
    lines = ["ЧТО ТЫ ПОМНИШЬ", f"Встреча называлась «{title}»."]
    if active:
        lines.append("У человека сейчас в работе: " + "; ".join(f"«{t}»" for t in active) + ".")
    else:
        lines.append("Список задач у человека сейчас пуст.")
    return "\n".join(lines)


def build_messages(title, actions, mid, active, now):
    """Identity → character → mood → memory → task → format, then the data."""
    system = "\n\n".join(
        [
            IDENTITY,
            CHARACTER,
            mood_layer(now, max(0, 3 - len(active)), len(actions)),
            memory_layer(title, active),
            task_layer(),
            output_layer(),
        ]
    )
    payload = [
        dict(id=proposal_id(mid, item), text=item["text"], owner=item.get("owner"), due=item.get("due"))
        for item in actions
    ]
    user = "Поручения (JSON):\n" + json.dumps(payload, ensure_ascii=False)
    return [dict(role="system", content=system), dict(role="user", content=user)]


class LineFormatError(ValueError):
    """The model answered, but not with a usable character line."""


def parse_reply(content, expected_ids):
    """Validate the character's JSON; any doubt raises and the caller falls back."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[-1].strip() == "```":
            content = "\n".join(lines[1:-1]).strip()
    start = content.find("{")
    if start < 0:
        raise LineFormatError("Реплика не в JSON.")
    try:
        obj, _ = json.JSONDecoder().raw_decode(content[start:])
    except json.JSONDecodeError:
        raise LineFormatError("Реплика не в JSON.") from None
    if not isinstance(obj, dict) or not isinstance(obj.get("text"), str):
        raise LineFormatError("В ответе нет текста реплики.")
    text = re.sub(r"\s+", " ", obj["text"]).strip()
    if not text or len(text) > MAX_LINE_CHARS:
        raise LineFormatError("Реплика пустая или длиннее лимита.")
    lowered = text.casefold()
    if any(phrase in lowered for phrase in FORBIDDEN):
        raise LineFormatError("Реплика нарушает границы характера.")
    emotion = obj.get("emotion") if obj.get("emotion") in EMOTIONS else "curious"
    try:
        intensity = min(1.0, max(0.0, float(obj.get("intensity", 0.5))))
    except (TypeError, ValueError):
        intensity = 0.5
    tasks = {}
    for row in obj.get("tasks") or []:
        if not isinstance(row, dict) or row.get("id") not in expected_ids:
            continue
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            continue
        if any(phrase in row["text"].casefold() for phrase in FORBIDDEN):
            continue
        tasks[row["id"]] = _clip(row["text"], MAX_TASK_CHARS)
    return dict(text=text, emotion=emotion, intensity=intensity, tasks=tasks)


def fallback_line(mid, count, free_slots):
    """Deterministic line when the model is unavailable or answered out of character."""
    if free_slots == 0:
        return dict(
            text="Со встречи кое-что принёс, но у тебя и так три. Подождёт в записях.",
            emotion="calm",
            intensity=0.4,
        )
    variants = (
        ["Со встречи принёс одно дело. Забирай, если твоё.", "После встречи осталось одно дело. Твоё?"]
        if count == 1
        else [
            f"Принёс {count} со встречи. Возьми что нужно, остальное отпущу.",
            f"Со встречи вышло {count} дела. Разбери, когда будет минутка.",
        ]
    )
    index = int(hashlib.sha256(mid.encode()).hexdigest(), 16) % len(variants)
    return dict(text=variants[index], emotion="curious", intensity=0.5)


def _timestamp(now):
    # JSONDecoder(.iso8601) on the Swift side accepts neither fractional seconds
    # nor "+00:00"; it wants exactly this shape.
    return now.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def handoff(store, mid, settings, client=None, now=None):
    """Write inbox/<mid>.json for the companion. Returns the path, or None when skipped."""
    if not getattr(settings, "companion_handoff", True) or not is_installed():
        return None
    meeting = store.meeting(mid)
    if not meeting.get("summary"):
        return None
    actions = candidate_actions(json.loads(meeting["summary"]))
    active = active_companion_tasks()
    taken = {t.casefold() for t in active}
    actions = [item for item in actions if item["text"].strip().casefold() not in taken]
    if not actions:
        return None

    now = now or datetime.now(timezone.utc).astimezone()
    title = meeting["title"]
    ids = {proposal_id(mid, item) for item in actions}
    free_slots = max(0, 3 - len(active))
    line = None
    try:
        client = client or ChatClient(settings)
        content = client.raw_complete(
            build_messages(title, actions, mid, active, now), temperature=0.7, max_tokens=600
        )
        line = parse_reply(content, ids)
    except (ValueError, RuntimeError, OSError):
        # Includes LineFormatError and every provider failure: the line is a nicety,
        # the tasks still reach the companion with their source wording.
        line = None
    rewritten = line.pop("tasks") if line else {}
    line = line or fallback_line(mid, len(actions), free_slots)

    proposals = [
        dict(
            id=proposal_id(mid, item),
            text=rewritten.get(proposal_id(mid, item)) or plain_task_text(item),
            sourceText=item["text"],
            owner=item.get("owner"),
            due=item.get("due"),
        )
        for item in actions
    ]
    document = dict(
        version=FORMAT_VERSION,
        meetingId=mid,
        title=title,
        createdAt=_timestamp(now),
        note=meeting.get("note"),
        line=line,
        proposals=proposals,
    )
    inbox = home() / "inbox"
    inbox.mkdir(exist_ok=True)
    path = inbox / f"{mid}.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=False, indent=2))
    # rename is atomic: the watcher on the other side never sees a half-written file.
    os.replace(tmp, path)
    return path


def safe_handoff(store, mid, settings, progress=lambda *_: None):
    """The summary phase must finish even if the handoff cannot."""
    try:
        return handoff(store, mid, settings)
    except Exception as exc:  # noqa: BLE001 - any failure here is non-fatal by design
        progress(f"ИИ-компаньон: дела не переданы ({type(exc).__name__}).")
        return None
