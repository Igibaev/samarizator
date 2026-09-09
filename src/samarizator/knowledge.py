import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path


def stamp(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def plain(value):
    return re.sub(r"[\[\]<>#*`|\\]", "", str(value)).replace("\n", " ").replace("\r", " ")


def topic_name(topic):
    normalized = " ".join(topic.casefold().split())
    name = re.sub(r"[^\w\s-]", "", normalized).strip()[:70] or "тема"
    return f"{name}-{hashlib.sha256(normalized.encode()).hexdigest()[:8]}"


def atomic_text(path, text):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def export(store, mid, settings):
    meeting = store.meeting(mid)
    summary = json.loads(meeting["summary"])
    vault = Path(settings.vault).expanduser().resolve()
    for folder in ["Meetings", "Transcripts", "Topics"]:
        target = vault / folder
        target.mkdir(parents=True, exist_ok=True)
        if target.resolve().parent != vault:
            raise ValueError("Папка заметок не должна быть ссылкой за пределы хранилища.")
    # Preserve manually changed exports: publish a fresh pair, never overwrite edits.
    previous = store.checkpoint(mid, "export", 0)
    suffix = mid
    if previous:
        for rel, digest in previous["files"].items():
            file = vault / rel
            if file.exists() and hashlib.sha256(file.read_bytes()).hexdigest() != digest:
                suffix += "-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
                break
    transcript = vault / "Transcripts" / f"{suffix}.md"
    note = vault / "Meetings" / f"{suffix}.md"
    # A new vault should not accidentally overwrite an unrelated existing export.
    for file in [transcript, note]:
        if file.is_symlink():
            raise ValueError("Файл экспорта не должен быть символической ссылкой.")
    tmp = transcript.with_suffix(".tmp")
    refs = {}
    with tmp.open("w", encoding="utf-8") as f:
        f.write(f"# Расшифровка: {plain(meeting['title'])}\n\n")
        for row in store.iter_segments(mid):
            refs[row["id"]] = stamp(row["start"])
            warning = " · проверить говорящего / границу" if row["uncertain"] else ""
            f.write(
                f"**{stamp(row['start'])} — {stamp(row['end'])} · "
                f"{plain(row['speaker'])}{warning}**\n\n{plain(row['text'])} ^s{row['id']}\n\n"
            )
    tmp.replace(transcript)
    topics = sorted(set(summary["topics"]))
    topic_links = []
    for topic in topics:
        name = topic_name(topic)
        topic_links.append(f"[[Topics/{name}|{plain(topic)}]]")
        target = vault / "Topics" / f"{name}.md"
        if not target.exists():
            atomic_text(
                target, f"# {plain(topic)}\n\nСвязанные встречи доступны в обратных ссылках Obsidian.\n"
            )
    lines = [
        "---",
        f"title: {json.dumps(meeting['title'], ensure_ascii=False)}",
        f"date: {meeting['created']}",
        f"meeting_id: {mid}",
        "tags: [samarizator, meeting]",
        "---",
        "",
        f"# {plain(meeting['title'])}",
        "",
        "> Сводка ИИ: проверьте важные решения, сроки и назначение говорящих по записи.",
        "",
        f"Длительность: {stamp(meeting['duration'])}",
        "",
        "## Главное",
        "",
        plain(summary["overview"]),
        "",
        " ".join(topic_links),
        "",
    ]
    labels = dict(
        point="Основные темы", decision="Решения", action="Задачи", risk="Риски", question="Открытые вопросы"
    )

    def item_line(item):
        links = " ".join(
            f"[[Transcripts/{suffix}#^s{ref}|{refs[ref]}]]" for ref in item["evidence"] if ref in refs
        )
        owner = f" Ответственный: {plain(item['owner'])}." if item.get("owner") else ""
        due = f" Срок: {plain(item['due'])}." if item.get("due") else ""
        prefix = "- [ ]" if item["kind"] == "action" else "-"
        return f"{prefix} {plain(item['text'])}{owner}{due} {links}"

    for kind, label in labels.items():
        items = [item for item in summary["items"] if item["kind"] == kind]
        lines += [f"## {label}", ""]
        lines += [item_line(item) for item in items] or ["Не зафиксировано."]
        lines.append("")
    lines += [
        "## Подробный реестр пунктов",
        "",
        "Пункты каждого исходного блока до финального сокращения. Возможны повторы.",
        "",
    ]
    lines += [item_line(item) for item in summary["ledger"]]
    lines += ["", f"[[Transcripts/{suffix}|Полная расшифровка с собеседниками]]", ""]
    atomic_text(note, "\n".join(lines))
    hashes = {
        str(f.relative_to(vault)): hashlib.sha256(f.read_bytes()).hexdigest() for f in [note, transcript]
    }
    store.save_checkpoint(mid, "export", 0, dict(files=hashes))
    store.update(mid, note=str(note))
    return note
