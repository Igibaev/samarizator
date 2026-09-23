"""The companion keeps two notes in the Obsidian vault: a day note and the actions list.

`Дни/2026-09-23.md` — what happened that day: meetings with their agreed actions,
what sat in the focus slots, what was done and what was let go.
`Поручения.md` — every agreed action from every meeting and where it stands now.

Both are rebuilt from facts that already exist — the Samarizator database and the
companion's own tasks.json — so running this twice gives the same text and nothing
has to be replayed. The day note belongs to the person: only the part between the
`samarizator:` markers is rewritten, everything written above or below it stays.
`Поручения.md` is generated whole and says so at the top.

Like the handoff, this is optional enrichment: a failure never fails the summary
(see safe_refresh).
"""

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from . import handoff
from .knowledge import atomic_text, plain, readable
from .summary import summary_views

DAYS_FOLDER = "Дни"
ACTIONS_NOTE = "Поручения.md"

MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
WEEKDAYS = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")

MEETINGS_BLOCK = "meetings"
FOCUS_BLOCK = "focus"


# --- facts --------------------------------------------------------------------


def _parse_time(value, tz):
    """ISO timestamps from Python ("+00:00") and Swift ("Z") alike, in the local zone."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tz)


def companion_tasks(tz):
    """Every task in the companion's tasks.json, with its dates parsed. Empty when absent."""
    try:
        rows = json.loads((handoff.home() / "tasks.json").read_text())
    except (OSError, ValueError):
        return []
    tasks = []
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict):
            continue
        title = row.get("title") if isinstance(row.get("title"), str) else row.get("text")
        if not isinstance(title, str) or not title.strip():
            continue
        status = row.get("status") or ("completed" if row.get("isDone") else "active")
        tasks.append(
            dict(
                title=title.strip(),
                status=status,
                started=_parse_time(row.get("startedAt"), tz),
                expires=_parse_time(row.get("expiresAt"), tz),
                completed=_parse_time(row.get("completedAt"), tz),
                expired=_parse_time(row.get("expiredAt"), tz),
                meeting=row.get("meetingId") if isinstance(row.get("meetingId"), str) else None,
                source=row.get("sourceText") if isinstance(row.get("sourceText"), str) else None,
            )
        )
    return tasks


def agreed_actions(summary):
    """All agreed actions of one meeting, deduplicated — no cap, unlike the handoff."""
    _, detailed = summary_views(summary)
    pool = detailed.get("resolved") or detailed["items"]
    seen, picked = set(), []
    for item in pool:
        if item.get("kind") != "action" or item.get("status") != "agreed":
            continue
        text = item["text"].strip()
        if text and text.casefold() not in seen:
            seen.add(text.casefold())
            picked.append(item)
    return picked


def checked_in_note(note_path):
    """Action texts ticked `- [x]` in the meeting note: a tick in Obsidian counts as done."""
    try:
        text = Path(note_path).read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return set()
    done = set()
    for line in text.splitlines():
        match = re.match(r"\s*- \[[xX]\] (.+)", line)
        if match:
            done.add(match.group(1).casefold())
    return done


def meetings_with_actions(store, tz):
    """Summarized meetings, newest first, each with its agreed actions and local time."""
    found = []
    for row in store.meetings():
        if not row.get("summary"):
            continue
        try:
            actions = agreed_actions(json.loads(row["summary"]))
        except (ValueError, KeyError, TypeError):
            continue
        created = _parse_time(row.get("created"), tz)
        if created is None:
            continue
        found.append(dict(row, actions=actions, when=created))
    found.sort(key=lambda m: m["when"], reverse=True)
    return found


def action_state(meeting, item, tasks, ticked):
    """`focus`, `done` or `open` for one agreed action.

    Done wins: ticked in the meeting note, or completed in the companion. A task the
    companion took from this meeting is matched by meeting id and source wording —
    titles are rewritten by the character, so the title alone proves nothing.
    """
    key = item["text"].strip().casefold()
    if any(line.startswith(plain(item["text"]).casefold()) for line in ticked):
        return "done"
    linked = [
        t for t in tasks if t["meeting"] == meeting["id"] and (t["source"] or "").strip().casefold() == key
    ]
    if any(t["status"] == "completed" for t in linked):
        return "done"
    if any(t["status"] == "active" for t in linked):
        return "focus"
    return "open"


# --- rendering ----------------------------------------------------------------


def _link(vault, note, label):
    """Wiki link to a note inside the vault; plain text when it lives elsewhere."""
    label = plain(label)
    if not note:
        return label
    try:
        relative = Path(note).resolve().relative_to(vault.resolve()).with_suffix("")
    except (ValueError, OSError):
        return label
    return f"[[{relative.as_posix()}|{label}]]"


def _action_text(item):
    text = plain(item["text"])
    extra = [plain(item["owner"])] if item.get("owner") else []
    if item.get("due"):
        extra.append(f"срок: {plain(item['due'])}")
    return text + (" — " + ", ".join(extra) if extra else "")


def _duration(seconds):
    minutes = max(0, int(seconds or 0)) // 60
    return f"{minutes // 60} ч {minutes % 60} мин" if minutes >= 60 else f"{minutes} мин"


def _on(moment, day):
    return moment is not None and moment.date() == day


def meetings_section(vault, meetings, day):
    todays = sorted((m for m in meetings if m["when"].date() == day), key=lambda m: m["when"])
    lines = ["## Встречи", ""]
    if not todays:
        return lines + ["Встреч со сводкой в этот день нет.", ""]
    for meeting in todays:
        title = _link(vault, meeting.get("note"), readable(meeting["title"]))
        count = len(meeting["actions"])
        tail = f" · поручений: {count}" if count else ""
        lines.append(f"- {meeting['when']:%H:%M} {title} · {_duration(meeting.get('duration'))}{tail}")
        lines += [f"    - {_action_text(item)}" for item in meeting["actions"]]
    return lines + [""]


def focus_section(vault, tasks, meetings, day, today):
    by_id = {m["id"]: m for m in meetings}

    def source(task):
        meeting = by_id.get(task["meeting"])
        if not meeting:
            return ""
        return " · со встречи " + _link(vault, meeting.get("note"), readable(meeting["title"]))

    lines = ["## Фокус", ""]
    if day == today:
        active = [t for t in tasks if t["status"] == "active"]
        if active:
            lines += ["В работе сейчас:", ""]
            for task in active:
                due = (
                    f" (до {task['expires']:%H:%M})"
                    if task["expires"] and task["expires"].date() == day
                    else ""
                )
                lines.append(f"- {plain(task['title'])}{due}{source(task)}")
            lines.append("")
        else:
            lines += ["Слоты фокуса свободны.", ""]

    done = sorted(
        (t for t in tasks if t["status"] == "completed" and _on(t["completed"], day)),
        key=lambda t: t["completed"],
    )
    let_go = sorted(
        (
            t
            for t in tasks
            if (t["status"] == "expired" and _on(t["expired"], day))
            or (t["status"] == "archived" and _on(t["completed"], day))
        ),
        key=lambda t: t["expired"] or t["completed"],
    )
    if done:
        lines += ["Сделано:", ""]
        lines += [f"- {t['completed']:%H:%M} {plain(t['title'])}{source(t)}" for t in done]
        lines.append("")
    if let_go:
        # Wording from the character bible: letting go without guilt, no «провал».
        lines += ["Отпущено:", ""]
        for t in let_go:
            how = "срок вышел" if t["status"] == "expired" else "убрано"
            lines.append(f"- {(t['expired'] or t['completed']):%H:%M} {plain(t['title'])} — {how}")
        lines.append("")
    if day != today and not done and not let_go:
        lines += ["Отметок фокуса за этот день нет.", ""]
    return lines


def _markers(name):
    return (
        f"<!-- samarizator:{name} — этот блок обновляет ИИ-компаньон, пишите выше или ниже -->",
        f"<!-- /samarizator:{name} -->",
    )


def replace_block(text, name, body):
    """Swap the text between one pair of markers; append the block when it is absent."""
    start, end = _markers(name)
    block = "\n".join([start, *body, end])
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.S)
    if pattern.search(text):
        return pattern.sub(lambda _: block, text, count=1)
    return text.rstrip("\n") + "\n\n" + block + "\n"


def day_title(day):
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}, {WEEKDAYS[day.weekday()]}"


def new_day_note(day):
    return "\n".join(
        [
            "---",
            f"date: {day.isoformat()}",
            "tags: [samarizator, day]",
            "---",
            "",
            f"# {day_title(day)}",
            "",
            "## Мои заметки",
            "",
            "",
        ]
    )


def day_path(vault, day):
    return vault / DAYS_FOLDER / f"{day.isoformat()}.md"


def write_day(vault, store, day, now, tasks=None, meetings=None):
    tz = now.tzinfo
    tasks = companion_tasks(tz) if tasks is None else tasks
    meetings = meetings_with_actions(store, tz) if meetings is None else meetings
    folder = vault / DAYS_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    path = day_path(vault, day)
    if path.is_symlink():
        raise ValueError("Заметка дня не должна быть символической ссылкой.")
    exists = path.exists()
    text = path.read_text(encoding="utf-8") if exists else new_day_note(day)
    text = replace_block(text, MEETINGS_BLOCK, meetings_section(vault, meetings, day))
    # A past day keeps the focus record it was written with: tasks.json forgets old
    # history (100 entries), so rebuilding it later could only lose lines.
    if day == now.date() or not exists or _markers(FOCUS_BLOCK)[0] not in text:
        text = replace_block(text, FOCUS_BLOCK, focus_section(vault, tasks, meetings, day, now.date()))
    if not exists or path.read_text(encoding="utf-8") != text:
        atomic_text(path, text)
    return path


ACTIONS_HEADER = """---
tags: [samarizator, actions]
---

# Поручения

> Заметку целиком пересобирает Samarizator после каждой сводки и каждого изменения
> в фокусе компаньона — правки здесь не сохранятся. Отмечайте выполнение галочкой
> в заметке встречи или кнопкой в компаньоне: оба способа учитываются.
"""


def actions_note(vault, meetings, tasks):
    groups = dict(focus=[], open=[], done=[])
    for meeting in meetings:
        if not meeting["actions"]:
            continue
        ticked = checked_in_note(meeting.get("note"))
        link = _link(vault, meeting.get("note"), f"{readable(meeting['title'])}, {meeting['when']:%d.%m}")
        for item in meeting["actions"]:
            state = action_state(meeting, item, tasks, ticked)
            groups[state].append(f"- {_action_text(item)} · {link}")
    lines = [ACTIONS_HEADER]
    for key, label, empty in (
        ("focus", "В фокусе сейчас", "Ни одно поручение сейчас не в фокусе."),
        ("open", "Ждут", "Открытых поручений нет."),
        ("done", "Сделано", "Пока ничего не отмечено."),
    ):
        lines += [f"## {label}", ""] + (groups[key] or [empty]) + [""]
    return "\n".join(lines)


def write_actions(vault, meetings, tasks):
    path = vault / ACTIONS_NOTE
    if path.is_symlink():
        raise ValueError("Заметка поручений не должна быть символической ссылкой.")
    text = actions_note(vault, meetings, tasks)
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        atomic_text(path, text)
    return path


# --- entry points -------------------------------------------------------------


def refresh(store, settings, days=(), now=None):
    """Rewrite today's note, the notes of `days` and the actions list. Returns the paths."""
    now = now or datetime.now(timezone.utc).astimezone()
    if not settings.vault:
        return []
    vault = Path(settings.vault).expanduser()
    # Same as the export: the vault folder appears on first use.
    vault.mkdir(parents=True, exist_ok=True)
    tasks = companion_tasks(now.tzinfo)
    meetings = meetings_with_actions(store, now.tzinfo)
    wanted = sorted({now.date(), *days})
    paths = [write_day(vault, store, day, now, tasks, meetings) for day in wanted]
    paths.append(write_actions(vault, meetings, tasks))
    return paths


def meeting_day(store, mid, tz=None):
    created = _parse_time(store.meeting(mid).get("created"), tz or datetime.now().astimezone().tzinfo)
    return created.date() if created else None


def safe_refresh(store, mid, settings, progress=lambda *_: None):
    """After a summary: the meeting's day note and the actions list. Never raises."""
    if not getattr(settings, "companion_journal", True):
        return []
    try:
        day = meeting_day(store, mid)
        return refresh(store, settings, days=[day] if day else [])
    except Exception as exc:  # noqa: BLE001 - the summary must not fail because of the journal
        progress(f"Дневник: заметка дня не обновлена ({type(exc).__name__}).")
        return []


def parse_day(value):
    return date.fromisoformat(value)
