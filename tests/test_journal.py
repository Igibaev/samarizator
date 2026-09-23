import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from samarizator import journal
from samarizator.companion import main as companion_main
from samarizator.journal import refresh, replace_block, safe_refresh

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 23, 18, 0, tzinfo=MSK)
TODAY = NOW.date()


def action(text, status="agreed", owner=None, due=None, kind="action"):
    return dict(kind=kind, text=text, evidence=[1], owner=owner, due=due, status=status)


def summary(items):
    brief = dict(overview="Итог", items=items[:2], topics=[])
    detailed = dict(overview="Подробно", items=items, topics=[], resolved=[])
    return dict(**brief, brief=brief, detailed=detailed, ledger=items, blocks=1, version=2)


def summarized(meeting, items, created=NOW - timedelta(hours=3), title="Планёрка", note=True):
    store, mid, settings = meeting
    vault = Path(settings.vault)
    note_path = vault / "Meetings" / f"{title}.md"
    if note:
        note_path.parent.mkdir(parents=True, exist_ok=True)
        note_path.write_text(
            "\n".join(f"- [ ] {item['text']} [согласовано]" for item in items), encoding="utf-8"
        )
    store.update(
        mid,
        summary=json.dumps(summary(items), ensure_ascii=False),
        note=str(note_path) if note else None,
        duration=42 * 60,
    )
    with store.connect() as db:
        db.execute(
            "UPDATE meetings SET created=?, title=? WHERE id=?",
            (created.astimezone(timezone.utc).isoformat(), title, mid),
        )
    return store, mid, settings, note_path


def companion(tmp_path, monkeypatch, tasks):
    home = tmp_path / "companion"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("FOCUS_COMPANION_HOME", str(home))
    (home / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False))
    return home


def utc(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task(title, status="active", meeting=None, source=None, **dates):
    row = dict(
        id="00000000-0000-0000-0000-000000000001",
        title=title,
        note="",
        startedAt=utc(NOW - timedelta(hours=1)),
        expiresAt=utc(NOW + timedelta(minutes=40)),
        status=status,
        order=0,
    )
    row.update({k: utc(v) for k, v in dates.items()})
    if meeting:
        row["meetingId"] = meeting
    if source:
        row["sourceText"] = source
    return row


# --- the day note ---------------------------------------------------------------


def test_day_note_lists_meetings_actions_focus_done_and_let_go(meeting, tmp_path, monkeypatch):
    store, mid, settings, _ = summarized(
        meeting, [action("Отправить смету", owner="Иван", due="пятница"), action("Идея", kind="point")]
    )
    companion(
        tmp_path,
        monkeypatch,
        [
            task("Смета Ивану", meeting=mid, source="Отправить смету"),
            task("Позвонить юристу", status="completed", completedAt=NOW - timedelta(hours=2)),
            task("Разобрать почту", status="expired", expiredAt=NOW - timedelta(hours=1)),
            task("Вчерашнее", status="completed", completedAt=NOW - timedelta(days=1)),
        ],
    )

    paths = refresh(store, settings, now=NOW)

    day = (tmp_path / "vault" / "Дни" / "2026-09-23.md").read_text(encoding="utf-8")
    assert paths[0].name == "2026-09-23.md"
    assert "# 23 сентября 2026, среда" in day
    assert "15:00 [[Meetings/Планёрка|Планёрка]] · 42 мин · поручений: 1" in day
    assert "    - Отправить смету — Иван, срок: пятница" in day
    assert "Идея" not in day  # only agreed actions, not every point
    assert "- Смета Ивану (до 18:40) · со встречи [[Meetings/Планёрка|Планёрка]]" in day
    assert "16:00 Позвонить юристу" in day
    assert "17:00 Разобрать почту — срок вышел" in day
    assert "Вчерашнее" not in day


def test_person_text_around_the_blocks_survives_every_refresh(meeting, tmp_path, monkeypatch):
    store, _, settings, _ = summarized(meeting, [action("Отправить смету")])
    companion(tmp_path, monkeypatch, [])
    path = refresh(store, settings, now=NOW)[0]
    text = path.read_text(encoding="utf-8")
    text = text.replace("## Мои заметки\n", "## Мои заметки\n\nСегодня договорился про отпуск.\n")
    path.write_text(text + "\nПриписка в самом конце.\n", encoding="utf-8")

    companion(tmp_path, monkeypatch, [task("Новое дело")])
    refresh(store, settings, now=NOW)

    again = path.read_text(encoding="utf-8")
    assert "Сегодня договорился про отпуск." in again
    assert again.rstrip().endswith("Приписка в самом конце.")
    assert "- Новое дело" in again
    assert again.count("<!-- samarizator:focus") == 1


def test_a_past_day_keeps_its_focus_record(meeting, tmp_path, monkeypatch):
    yesterday = NOW - timedelta(days=1)
    store, _, settings, _ = summarized(meeting, [action("Отправить смету")], created=yesterday)
    companion(tmp_path, monkeypatch, [task("Сделал вчера", status="completed", completedAt=yesterday)])
    refresh(store, settings, days=[yesterday.date()], now=yesterday)

    # Later tasks.json no longer holds that entry (history is trimmed to 100).
    companion(tmp_path, monkeypatch, [])
    refresh(store, settings, days=[yesterday.date()], now=NOW)

    past = (tmp_path / "vault" / "Дни" / "2026-09-22.md").read_text(encoding="utf-8")
    assert "Сделал вчера" in past
    assert "Отправить смету" in past


def test_refresh_is_idempotent(meeting, tmp_path, monkeypatch):
    store, _, settings, _ = summarized(meeting, [action("Отправить смету")])
    companion(tmp_path, monkeypatch, [task("Дело")])
    first = [p.read_text(encoding="utf-8") for p in refresh(store, settings, now=NOW)]
    second = [p.read_text(encoding="utf-8") for p in refresh(store, settings, now=NOW)]
    assert first == second


def test_a_removed_block_comes_back_at_the_end():
    text = "# День\n\nмой текст\n"
    assert replace_block(text, "focus", ["x"]).startswith("# День\n\nмой текст\n\n<!-- samarizator:focus")


# --- the actions list -------------------------------------------------------------


def test_actions_are_grouped_by_focus_open_and_done(meeting, tmp_path, monkeypatch):
    store, mid, settings, note = summarized(
        meeting,
        [
            action("Отправить смету"),
            action("Согласовать бюджет"),
            action("Позвонить в банк"),
            action("Написать ТЗ"),
        ],
    )
    # A tick in Obsidian counts as done.
    note.write_text(
        note.read_text(encoding="utf-8").replace("- [ ] Позвонить в банк", "- [x] Позвонить в банк"),
        encoding="utf-8",
    )
    companion(
        tmp_path,
        monkeypatch,
        [
            task("Смета", meeting=mid, source="Отправить смету"),
            task("ТЗ", status="completed", meeting=mid, source="Написать ТЗ", completedAt=NOW),
        ],
    )

    refresh(store, settings, now=NOW)
    text = (tmp_path / "vault" / "Поручения.md").read_text(encoding="utf-8")
    focus, rest = text.split("## Ждут")
    waiting, done = rest.split("## Сделано")
    assert "Отправить смету" in focus
    assert "Согласовать бюджет · [[Meetings/Планёрка|Планёрка, 23.09]]" in waiting
    assert "Позвонить в банк" in done and "Написать ТЗ" in done


def test_same_title_from_another_meeting_is_not_confused(meeting, tmp_path, monkeypatch):
    store, _, settings, _ = summarized(meeting, [action("Отправить смету")])
    companion(tmp_path, monkeypatch, [task("Отправить смету", status="completed", completedAt=NOW)])
    refresh(store, settings, now=NOW)
    text = (tmp_path / "vault" / "Поручения.md").read_text(encoding="utf-8")
    assert "Отправить смету" in text.split("## Ждут")[1].split("## Сделано")[0]


# --- wiring -----------------------------------------------------------------------


def test_safe_refresh_never_raises(meeting, monkeypatch):
    store, mid, settings = meeting

    def broken(*_args, **_kwargs):
        raise OSError("disk")

    monkeypatch.setattr(journal, "refresh", broken)
    messages = []
    assert safe_refresh(store, mid, settings, messages.append) == []
    assert messages == ["Дневник: заметка дня не обновлена (OSError)."]


def test_safe_refresh_respects_the_setting(meeting):
    store, mid, settings = meeting
    settings.companion_journal = False
    assert safe_refresh(store, mid, settings) == []


def test_cli_journal_writes_today_and_reports_paths(meeting, tmp_path, monkeypatch, capsys):
    store, _, settings, _ = summarized(meeting, [action("Отправить смету")], created=datetime.now(MSK))
    settings.save()
    companion(tmp_path, monkeypatch, [])
    assert companion_main(["journal"]) == 0
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["event"] == "journal"
    assert any(path.endswith("Поручения.md") for path in event["written"])
