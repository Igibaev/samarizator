"""Two lines a day: a greeting when the person first sits down, a recap in the evening.

This is where the companion shows that it remembers: the morning line may mention
what was done yesterday and what is waiting from meetings, the evening one what
got done today. The facts come from the same sources as the day note (journal.py)
— the Samarizator database and the companion's tasks.json — nothing is stored
for this on the side.

The wording comes from the summary model when it is configured, through the same
character layers and the same tone filter as the meeting handoff; otherwise, or
when the model answers out of character, a deterministic template speaks. The
evening line is positive only: what did not happen is never mentioned.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone

from . import journal
from .character_prompts import (
    CHARACTER,
    IDENTITY,
    rhythm_output_layer,
    rhythm_task_layer,
)
from .handoff import parse_reply
from .summary import ChatClient

KINDS = ("morning", "evening")
MAX_TITLES = 3


def facts(store, now):
    """Everything a line may rely on, as plain numbers and a few titles."""
    tz = now.tzinfo
    today = now.date()
    yesterday = today - timedelta(days=1)
    tasks = journal.companion_tasks(tz)
    meetings = journal.meetings_with_actions(store, tz)

    waiting = []
    for meeting in meetings:
        ticked = journal.checked_in_note(meeting.get("note"))
        for item in meeting["actions"]:
            if journal.action_state(meeting, item, tasks, ticked) == "open":
                waiting.append(item["text"].strip())

    def done_on(day):
        return [
            t for t in tasks if t["status"] == "completed" and t["completed"] and t["completed"].date() == day
        ]

    done_today = done_on(today)
    return dict(
        hour=now.hour,
        active=[t["title"] for t in tasks if t["status"] == "active"],
        waiting_count=len(waiting),
        waiting=waiting[:MAX_TITLES],
        done_today=len(done_today),
        done_today_titles=[t["title"] for t in done_today][:MAX_TITLES],
        done_today_from_meetings=sum(1 for t in done_today if t["meeting"]),
        done_yesterday=len(done_on(yesterday)),
        meetings_today=sum(1 for m in meetings if m["when"].date() == today),
    )


def _plural(count, one, few, many):
    if 11 <= count % 100 <= 14:
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(count % 10, many)


def _pick(variants, seed):
    return variants[int(hashlib.sha256(seed.encode()).hexdigest(), 16) % len(variants)]


def fallback(kind, f, seed):
    """The template line. Same day, same line: rerunning does not reshuffle it."""
    if kind == "morning":
        hello = "Доброе утро" if f["hour"] < 12 else "Привет"
        slots = 3 - len(f["active"])
        if f["active"] and slots == 0:
            return dict(
                text=f"{hello}. Три дела уже ждут в фокусе — начнём с первого?", emotion="calm", intensity=0.3
            )
        if f["waiting_count"]:
            n = f["waiting_count"]
            word = _plural(n, "поручение", "поручения", "поручений")
            return dict(
                text=_pick(
                    [
                        f"{hello}. Со встреч ждут {n} {word} — глянешь, что взять?",
                        f"{hello}. У меня тут {n} {word} со встреч, возьми одно, если хочется.",
                    ],
                    seed,
                ),
                emotion="curious",
                intensity=0.5,
            )
        if f["done_yesterday"]:
            n = f["done_yesterday"]
            return dict(
                text=f"{hello}. Вчера {n} {_plural(n, 'дело закрыто', 'дела закрыто', 'дел закрыто')}. Что сегодня главное?",
                emotion="happy",
                intensity=0.4,
            )
        return dict(
            text=f"{hello}. Сегодня пока пусто — можно выбрать одно главное.", emotion="calm", intensity=0.3
        )

    n = f["done_today"]
    if n:
        extra = (
            f", из них со встреч — {f['done_today_from_meetings']}" if f["done_today_from_meetings"] else ""
        )
        return dict(
            text=f"За сегодня сделано {n}{extra}. Хороший день.",
            emotion="happy",
            intensity=0.6,
        )
    if f["meetings_today"]:
        m = f["meetings_today"]
        return dict(
            text=f"Сегодня {m} {_plural(m, 'встреча', 'встречи', 'встреч')}, всё записал в заметку дня. Отдыхай.",
            emotion="calm",
            intensity=0.3,
        )
    return dict(text="День закрываю. Завтра начнём спокойно.", emotion="calm", intensity=0.3)


def facts_layer(kind, f):
    lines = ["ФАКТЫ (JSON)"]
    shown = dict(f)
    if kind == "evening":
        # Nothing about what is still open: the evening line must not become a reproach.
        for key in ("waiting", "waiting_count", "active"):
            shown.pop(key, None)
    lines.append(json.dumps(shown, ensure_ascii=False))
    return "\n".join(lines)


def build_messages(kind, f):
    system = "\n\n".join([IDENTITY, CHARACTER, rhythm_task_layer(kind), rhythm_output_layer()])
    return [dict(role="system", content=system), dict(role="user", content=facts_layer(kind, f))]


def line(store, settings, kind, now=None, client=None):
    """The line to show, with where it came from: `model` or `template`."""
    if kind not in KINDS:
        raise ValueError("Реплика бывает утренней или вечерней.")
    now = now or datetime.now(timezone.utc).astimezone()
    f = facts(store, now)
    seed = f"{kind}:{now.date().isoformat()}"
    try:
        client = client or ChatClient(settings)
        content = client.raw_complete(build_messages(kind, f), temperature=0.7, max_tokens=300)
        spoken = parse_reply(content, set())
        spoken.pop("tasks", None)
        return dict(spoken, source="model")
    except Exception:  # noqa: BLE001 - keyring, network, format: any failure means the template
        # Not configured, provider failure or out of character: the template speaks.
        return dict(fallback(kind, f, seed), source="template")
