"""Export rules that keep the vault usable: readable names, few links, no orphans."""

import json
from pathlib import Path

from samarizator.knowledge import INDEX_NAME, export, file_base, readable, topic_name


def summary(topics, evidence):
    item = dict(kind="point", text="Пункт", evidence=evidence, owner=None, due=None)
    view = dict(overview="Итог", items=[item], topics=list(topics))
    return json.dumps(dict(**view, brief=view, detailed=dict(view, resolved=[])))


def prepared(store, mid, topics=("Бюджет",), lines=3, cited=(0,)):
    rows = [
        dict(start=i * 10, end=i * 10 + 5, speaker="A", text=f"Реплика {i}", uncertain=False)
        for i in range(lines)
    ]
    store.save_chunk(mid, 0, rows)
    ids = [row["id"] for row in store.segments(mid)]
    store.update(mid, summary=summary(topics, [ids[i] for i in cited]))
    return ids


def test_note_name_is_readable_and_sorted_by_date(meeting):
    store, mid, settings = meeting
    store.update(mid, title="Планёрка_отдела [b5640e1a-7932-579f-a3ed-1f84646c9058]")
    prepared(store, mid)
    note = export(store, mid, settings)

    # Date first so the folder sorts chronologically; no identifiers left in the name.
    assert note.name.startswith("20") and note.name.endswith("Планёрка отдела.md")
    assert "b5640e1a" not in note.name
    assert note.parent.name == "Meetings"
    assert (note.parent.parent / "Transcripts" / note.name).is_file()


def test_title_property_is_cleaned_too(meeting):
    store, mid, settings = meeting
    store.update(mid, title="Встреча_в_Телемосте_07_07_26 [7abf6971-55d9-4677-80ac-96b18d5e086d]")
    prepared(store, mid)
    text = export(store, mid, settings).read_text()

    assert 'title: "Встреча в Телемосте 07 07 26"' in text
    assert "7abf6971" not in text.split("meeting_id")[0]


def test_one_off_topic_stays_a_property_while_a_repeated_one_gets_a_note(meeting, tmp_path):
    store, mid, settings = meeting
    prepared(store, mid, topics=("Бюджет", "банка пива"))
    first = export(store, mid, settings)
    vault = Path(settings.vault)

    assert 'topics: ["Бюджет", "банка пива"]' in first.read_text()
    assert not (vault / "Topics").exists() or not list((vault / "Topics").iterdir())

    # A second meeting repeats one topic: only that one becomes a note, and without a hash.
    source = tmp_path / "second.wav"
    source.write_bytes(b"audio")
    other = store.create(source, settings)
    prepared(store, other, topics=("Бюджет",))
    second = export(store, other, settings)

    assert "[[Topics/Бюджет|Бюджет]]" in second.read_text()
    assert (vault / "Topics" / "Бюджет.md").is_file()
    assert [p.name for p in (vault / "Topics").iterdir()] == ["Бюджет.md"]


def test_anchors_are_written_only_for_quoted_replies(meeting):
    store, mid, settings = meeting
    ids = prepared(store, mid, lines=4, cited=(1,))
    note = export(store, mid, settings)
    transcript = (note.parent.parent / "Transcripts" / note.name).read_text()

    # One anchor for the one quoted reply, instead of an anchor on every line.
    assert f"^s{ids[1]}" in transcript
    assert all(f"^s{ids[i]}" not in transcript for i in (0, 2, 3))
    assert f"#^s{ids[1]}" in note.read_text()


def test_unedited_export_keeps_its_files_edited_one_gets_a_fresh_pair(meeting):
    store, mid, settings = meeting
    prepared(store, mid)
    first = export(store, mid, settings)
    again = export(store, mid, settings)
    assert again == first  # nothing was touched, so the same note is refreshed in place

    first.write_text(first.read_text() + "\nРучная правка\n")
    third = export(store, mid, settings)
    assert third != first and "(2)" in third.name
    assert "Ручная правка" in first.read_text()


def test_two_meetings_with_the_same_name_do_not_collide(meeting, tmp_path):
    store, mid, settings = meeting
    prepared(store, mid)
    first = export(store, mid, settings)

    source = tmp_path / "twin.wav"
    source.write_bytes(b"audio")
    twin = store.create(source, settings)
    store.update(twin, title=store.meeting(mid)["title"])
    with store.connect() as db:  # same minute on purpose: that is what makes names collide
        db.execute("UPDATE meetings SET created=? WHERE id=?", (store.meeting(mid)["created"], twin))
    prepared(store, twin)
    second = export(store, twin, settings)

    assert first.name != second.name and second.is_file() and first.is_file()


def test_index_note_is_created_once_and_never_rewritten(meeting):
    store, mid, settings = meeting
    prepared(store, mid)
    export(store, mid, settings)
    index = Path(settings.vault) / INDEX_NAME
    assert index.is_file() and "dataview" in index.read_text()

    index.write_text("# Мой индекс\n")
    export(store, mid, settings)
    assert index.read_text() == "# Мой индекс\n"


def test_helpers_strip_what_cannot_go_into_a_file_name():
    assert readable("Звонок_07_07 [7abf6971-55d9-4677-80ac-96b18d5e086d]") == "Звонок 07 07"
    assert readable("") == "Встреча"
    assert topic_name("Тема/с/путями") == "Тема с путями"
    assert topic_name("...") == "тема"
    assert file_base(dict(created="не дата", title="Без даты")).endswith("Без даты")
