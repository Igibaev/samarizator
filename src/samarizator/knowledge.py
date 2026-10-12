"""A record's summary as one Markdown document, saved only where the user chooses."""

import hashlib
import json
import re

from .summary import summary_views

STATUS_LABELS = dict(
    proposed="предложено", agreed="согласовано", cancelled="отменено", disputed="оспаривается"
)
LABELS = dict(
    point="Основные темы", decision="Решения", action="Задачи", risk="Риски", question="Открытые вопросы"
)


def task_key(item):
    """Stable id of a task across re-renders: its text and sources, not its position."""
    raw = item["text"].strip().casefold() + "|" + ",".join(map(str, sorted(item.get("evidence", []))))
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def plain(value):
    return re.sub(r"[\[\]<>#*`|\\]", "", str(value)).replace("\n", " ").replace("\r", " ")


UUID_IN_TITLE = re.compile(r"[\[(]?\b[0-9a-f]{8}(?:-?[0-9a-f]{4}){3}-?[0-9a-f]{12}\b[\])]?", re.I)
HEX_IN_TITLE = re.compile(r"[\[(]?\b[0-9a-f]{32}\b[\])]?", re.I)


def readable(title):
    """Titles come from file names: strip the identifiers and underscores they carry."""
    text = HEX_IN_TITLE.sub(" ", UUID_IN_TITLE.sub(" ", str(title)))
    return " ".join(text.replace("_", " ").split()) or "Встреча"


def summary_markdown(store, mid):
    """Final text, brief and detailed summary; sources are given as times in the recording."""
    meeting = store.meeting(mid)
    summary = json.loads(meeting["summary"])
    brief, detailed = summary_views(summary)
    refs = {row["id"]: stamp(row["start"]) for row in store.iter_segments(mid)}
    topics = sorted(set(brief["topics"]) | set(detailed["topics"]))
    title = readable(meeting["title"])
    lines = [
        f"# {plain(title)}",
        "",
        "> Сводка ИИ: проверьте важные решения и сроки по исходной записи.",
        "",
        f"Длительность: {stamp(meeting['duration'] or 0)}",
        "",
    ]
    if topics:
        lines += ["Темы: " + ", ".join(plain(t) for t in topics), ""]

    def item_line(item, tasks=False):
        times = ", ".join(refs[ref] for ref in item["evidence"] if ref in refs)
        links = f"({times})" if times else ""
        owner = f" Ответственный: {plain(item['owner'])}." if item.get("owner") else ""
        due = f" Срок: {plain(item['due'])}." if item.get("due") else ""
        state = STATUS_LABELS.get(item.get("status"), "")
        state = f" [{state}]" if state else ""
        box = "- [x]" if task_key(item) in done else "- [ ]"
        prefix = box if tasks and item["kind"] == "action" and item.get("status") == "agreed" else "-"
        return f"{prefix} {plain(item['text'])}{state}{owner}{due} {links}".rstrip()

    final = summary.get("final") or {}
    if final.get("text", "").strip():
        # The user's own format comes first: it is the document people read. Its Markdown
        # is kept as written; the generated headings are demoted under the document title.
        lines += [f"## Итоговый текст · {plain(final.get('title', ''))}", ""]
        if final.get("warning"):
            lines += [f"> {plain(final['warning'])}", ""]
        lines += [demote_headings(final["text"].strip()), ""]
    # Tasks ticked off in the app stay ticked in the file.
    done = store.checkpoint(mid, "tasks-done", 0) or {}
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
    return "\n".join(lines).rstrip() + "\n"


def demote_headings(markdown):
    """`# x` → `### x`: the final text sits inside the document under a `##` heading."""
    out, fenced = [], False
    for line in markdown.splitlines():
        if line.lstrip().startswith("```"):
            fenced = not fenced
        if not fenced and re.match(r"#{1,4} ", line):
            line = "##" + line
        out.append(line)
    return "\n".join(out)
