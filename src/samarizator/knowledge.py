import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .summary import summary_views

STATUS_LABELS = dict(
    proposed="предложено", agreed="согласовано", cancelled="отменено", disputed="оспаривается"
)
LABELS = dict(
    point="Основные темы", decision="Решения", action="Задачи", risk="Риски", question="Открытые вопросы"
)


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def plain(value):
    return re.sub(r"[\[\]<>#*`|\\]", "", str(value)).replace("\n", " ").replace("\r", " ")


# A topic deserves a note of its own only once it comes back: a note per one-off
# phrase fills the vault with orphans and turns the graph into noise.
RECURRING_TOPIC = 2

UUID_IN_TITLE = re.compile(r"[\[(]?\b[0-9a-f]{8}(?:-?[0-9a-f]{4}){3}-?[0-9a-f]{12}\b[\])]?", re.I)
HEX_IN_TITLE = re.compile(r"[\[(]?\b[0-9a-f]{32}\b[\])]?", re.I)
# Characters a file name cannot hold, plus the ones that would break a wiki link.
UNSAFE_NAME = re.compile(r'[\\/:*?"<>|#^\[\]]')


def readable(title):
    """Titles come from file names: strip the identifiers and underscores they carry."""
    text = HEX_IN_TITLE.sub(" ", UUID_IN_TITLE.sub(" ", str(title)))
    return " ".join(text.replace("_", " ").split()) or "Встреча"


def topic_key(topic):
    return " ".join(str(topic).casefold().split())


def topic_name(topic):
    """Note name for a topic: no hash, so the same topic from two meetings is one note."""
    name = " ".join(UNSAFE_NAME.sub(" ", " ".join(str(topic).split())).split())
    return name[:70].strip(" .") or "тема"


def topic_counts(store):
    """How many meetings mention each topic, counted from the summaries themselves."""
    from collections import Counter

    counts = Counter()
    for row in store.meetings():
        if not row.get("summary"):
            continue
        try:
            brief, detailed = summary_views(json.loads(row["summary"]))
        except (ValueError, KeyError, TypeError):
            continue
        for topic in set(brief["topics"]) | set(detailed["topics"]):
            counts[topic_key(topic)] += 1
    return counts


def file_base(meeting):
    """`2026-09-10 1455 Планёрка отдела продаж` — sorts by date, reads like a name."""
    try:
        when = datetime.fromisoformat(meeting["created"]).astimezone()
    except (ValueError, TypeError):
        when = datetime.now(timezone.utc).astimezone()
    name = " ".join(UNSAFE_NAME.sub(" ", readable(meeting["title"])).split())[:60].strip(" .")
    return f"{when:%Y-%m-%d %H%M} {name}".strip()


def owns(note, mid):
    try:
        return f"meeting_id: {mid}" in note.read_text(encoding="utf-8")[:1000]
    except OSError:
        return False


def free_base(vault, base, mid):
    """First name not taken by another meeting; `mid` may reuse its own files."""
    for attempt in range(1, 50):
        candidate = base if attempt == 1 else f"{base} ({attempt})"
        note = vault / "Meetings" / f"{candidate}.md"
        transcript = vault / "Transcripts" / f"{candidate}.md"
        if (mid and owns(note, mid)) or not (note.exists() or transcript.exists()):
            return candidate
    return f"{base} {mid[:8]}" if mid else base


def atomic_text(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


INDEX_NAME = "Встречи — индекс.md"
INDEX_TEXT = """---
tags: [samarizator, index]
---

# Встречи — индекс

Заметка создана Samarizator один раз. Правьте её свободно: приложение больше её не трогает.

Каждая встреча несёт свойства `date`, `duration`, `topics`, `meeting_id`, поэтому списки
ниже не нужно вести руками — они собираются запросом.

## Все встречи

```dataview
TABLE date AS "Дата", duration AS "Длительность", topics AS "Темы"
FROM #meeting
SORT date DESC
```

## Незакрытые задачи

```dataview
TASK
FROM #meeting
WHERE !completed
SORT file.name DESC
```

Блоки выше работают с плагином Dataview (Настройки → Сторонние плагины). Без него
пользуйтесь встроенным поиском — эти запросы работают всегда:

- `tag:#meeting` — все встречи;
- `["topics"]` содержит слово — встречи по теме;
- `path:Meetings/2026-09` — встречи за месяц;
- `task-todo:""` — незакрытые задачи.

Темы, которые встречались минимум в двух встречах, лежат в папке `Topics` отдельными
заметками; разовые остаются только в свойстве `topics`. Так граф показывает то, что
повторяется, а не каждую случайную фразу.

Расшифровки в папке `Transcripts` — вспомогательные. Их удобно исключить из графа:
Настройки графа → Фильтры → `-path:Transcripts`.
"""


def write_index(vault):
    """Create the index once; never overwrite, it is the user's note afterwards."""
    index = vault / INDEX_NAME
    if not index.exists():
        atomic_text(index, INDEX_TEXT)


def export(store, mid, settings):
    meeting = store.meeting(mid)
    summary = json.loads(meeting["summary"])
    brief, detailed = summary_views(summary)
    vault = Path(settings.vault).expanduser().resolve()
    for folder in ["Meetings", "Transcripts", "Topics"]:
        target = vault / folder
        target.mkdir(parents=True, exist_ok=True)
        if target.resolve().parent != vault:
            raise ValueError("Папка заметок не должна быть ссылкой за пределы хранилища.")
    # Preserve manually changed exports: publish a fresh pair, never overwrite edits.
    previous = store.checkpoint(mid, "export", 0) or {}
    edited = any(
        (vault / rel).exists() and hashlib.sha256((vault / rel).read_bytes()).hexdigest() != digest
        for rel, digest in (previous.get("files") or {}).items()
    )
    suffix = previous.get("base")
    if edited or not suffix:
        # An unedited re-export keeps its files; anything else takes the next free name.
        suffix = free_base(vault, file_base(meeting), "" if edited else mid)
    transcript = vault / "Transcripts" / f"{suffix}.md"
    note = vault / "Meetings" / f"{suffix}.md"
    # A new vault should not accidentally overwrite an unrelated existing export.
    for file in [transcript, note]:
        if file.is_symlink():
            raise ValueError("Файл экспорта не должен быть символической ссылкой.")
    # Anchors only where the summary points: one per quoted reply instead of one per
    # line keeps the backlink panel usable and the transcript out of the way.
    cited = {
        ref
        for view in (brief, detailed)
        for item in list(view["items"]) + list(detailed.get("resolved") or [])
        for ref in item["evidence"]
    }
    tmp = transcript.with_suffix(".tmp")
    refs = {}
    with tmp.open("w", encoding="utf-8") as f:
        f.write(f"# Расшифровка: {plain(readable(meeting['title']))}\n\n")
        for row in store.iter_segments(mid):
            refs[row["id"]] = stamp(row["start"])
            warning = (
                " · проверить: " + plain(row.get("review") or "текст / границу") if row["uncertain"] else ""
            )
            anchor = f" ^s{row['id']}" if row["id"] in cited else ""
            f.write(
                f"**{stamp(row['start'])} — {stamp(row['end'])}{warning}**\n\n"
                f"{plain(row['text'])}{anchor}\n\n"
            )
    tmp.replace(transcript)
    topics = sorted(set(brief["topics"]) | set(detailed["topics"]))
    counts = topic_counts(store)
    topic_links = []
    for topic in topics:
        if counts.get(topic_key(topic), 0) < RECURRING_TOPIC:
            topic_links.append(plain(topic))  # one-off: kept in properties, no orphan note
            continue
        name = topic_name(topic)
        topic_links.append(f"[[Topics/{name}|{plain(topic)}]]")
        target = vault / "Topics" / f"{name}.md"
        if not target.exists():
            atomic_text(
                target,
                f"# {plain(topic)}\n\ntags: #samarizator/topic\n\n"
                "Встречи по этой теме — в обратных ссылках Obsidian (панель справа).\n",
            )
    title = readable(meeting["title"])
    lines = [
        "---",
        f"title: {json.dumps(title, ensure_ascii=False)}",
        f"date: {meeting['created']}",
        f"duration: {stamp(meeting['duration'])}",
        f"meeting_id: {mid}",
        # Properties, not folders: one index note can query every meeting by topic.
        "topics: [" + ", ".join(json.dumps(plain(t), ensure_ascii=False) for t in topics) + "]",
        "tags: [samarizator, meeting]",
        "---",
        "",
        f"# {plain(title)}",
        "",
        "> Сводка ИИ: проверьте важные решения и сроки по исходной записи.",
        "",
        f"Длительность: {stamp(meeting['duration'])}",
        "",
        " · ".join(topic_links),
        "",
    ]

    def item_line(item, tasks=False):
        links = " ".join(
            f"[[Transcripts/{suffix}#^s{ref}|{refs[ref]}]]" for ref in item["evidence"] if ref in refs
        )
        owner = f" Ответственный: {plain(item['owner'])}." if item.get("owner") else ""
        due = f" Срок: {plain(item['due'])}." if item.get("due") else ""
        state = STATUS_LABELS.get(item.get("status"), "")
        state = f" [{state}]" if state else ""
        prefix = "- [ ]" if tasks and item["kind"] == "action" and item.get("status") == "agreed" else "-"
        return f"{prefix} {plain(item['text'])}{state}{owner}{due} {links}"

    sections = [("Кратко · тезисы", brief, True), ("Подробная сводка", detailed, False)]
    if resolved := detailed.get("resolved"):
        sections.append(
            (
                "Итог по решениям",
                dict(
                    overview=detailed.get("resolution_warning")
                    or "Финальный статус с учётом более поздних правок и отмен.",
                    items=resolved,
                ),
                not bool(detailed.get("resolution_warning")),
            )
        )
    if detailed.get("resolution_warning") and not detailed.get("resolved"):
        lines += ["## Проверка решений", "", plain(detailed["resolution_warning"]), ""]
    for title, view, tasks in sections:
        lines += [f"## {title}", "", plain(view["overview"]), ""]
        for field in ("quality_warning", "generation_warning"):
            if view.get(field):
                lines += [f"> {plain(view[field])}", ""]
        if title == "Подробная сводка":
            lines += ["Сохранены пункты всех блоков; возможны повторы и последующие изменения решений.", ""]
        for kind, label in LABELS.items():
            items = [item for item in view["items"] if item["kind"] == kind]
            if items:
                lines += [f"### {label}", ""] + [item_line(item, tasks=tasks) for item in items] + [""]
    lines += ["", f"[[Transcripts/{suffix}|Полная расшифровка]]", ""]
    atomic_text(note, "\n".join(lines))
    write_index(vault)
    hashes = {
        str(f.relative_to(vault)): hashlib.sha256(f.read_bytes()).hexdigest() for f in [note, transcript]
    }
    store.save_checkpoint(mid, "export", 0, dict(files=hashes, base=suffix))
    store.update(mid, note=str(note))
    return note


def summary_text(view, refs):
    lines = [view["overview"], ""]
    for kind, label in LABELS.items():
        items = [item for item in view["items"] if item["kind"] == kind]
        if not items:
            continue
        lines += [label.upper()]
        for item in items:
            state = STATUS_LABELS.get(item.get("status"), "")
            lines.append(
                "• "
                + item["text"]
                + (f" [{state}]" if state else "")
                + (f" — {item['owner']}" if item.get("owner") else "")
                + (f" · {item['due']}" if item.get("due") else "")
                + "  ["
                + ", ".join(refs.get(r, str(r)) for r in item["evidence"])
                + "]"
            )
        lines += [""]
    return "\n".join(lines)
