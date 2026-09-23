import json
from datetime import datetime, timedelta, timezone

import httpx

from samarizator import rhythm
from samarizator.character_prompts import MAX_LINE_CHARS
from samarizator.companion import main as companion_main
from samarizator.handoff import FORBIDDEN
from samarizator.summary import ChatClient

MSK = timezone(timedelta(hours=3))
MORNING = datetime(2026, 9, 23, 9, 30, tzinfo=MSK)
EVENING = datetime(2026, 9, 23, 19, 0, tzinfo=MSK)


def action(text):
    return dict(kind="action", text=text, evidence=[1], owner=None, due=None, status="agreed")


def with_actions(meeting, texts, created):
    store, mid, settings = meeting
    items = [action(t) for t in texts]
    brief = dict(overview="Итог", items=items, topics=[])
    detailed = dict(overview="Подробно", items=items, topics=[], resolved=[])
    store.update(
        mid,
        summary=json.dumps(dict(**brief, brief=brief, detailed=detailed, ledger=items, blocks=1, version=2)),
    )
    with store.connect() as db:
        db.execute(
            "UPDATE meetings SET created=? WHERE id=?", (created.astimezone(timezone.utc).isoformat(), mid)
        )
    return store, mid, settings


def tasks_file(tmp_path, monkeypatch, rows):
    home = tmp_path / "companion"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("FOCUS_COMPANION_HOME", str(home))
    (home / "tasks.json").write_text(json.dumps(rows, ensure_ascii=False))


def done(title, when, meeting=None):
    row = dict(
        title=title,
        status="completed",
        startedAt="2026-09-20T08:00:00Z",
        expiresAt="2026-09-20T09:00:00Z",
        completedAt=when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    )
    if meeting:
        row["meetingId"] = meeting
    return row


def no_model(settings):
    settings.base_url = ""
    return settings


def model_says(settings, body):
    def handler(_request):
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]}
        )

    return ChatClient(settings, transport=httpx.MockTransport(handler), key="test")


# --- facts --------------------------------------------------------------------------


def test_facts_remember_yesterday_and_count_waiting(meeting, tmp_path, monkeypatch):
    store, mid, _ = with_actions(
        meeting, ["Отправить смету", "Согласовать бюджет"], MORNING - timedelta(days=1)
    )
    tasks_file(tmp_path, monkeypatch, [done("Звонок", MORNING - timedelta(days=1))])
    f = rhythm.facts(store, MORNING)
    assert f["done_yesterday"] == 1
    assert f["waiting_count"] == 2
    assert f["done_today"] == 0


# --- template lines -------------------------------------------------------------------


def test_morning_template_offers_waiting_actions(meeting, tmp_path, monkeypatch):
    store, _, settings = with_actions(
        meeting, ["Отправить смету", "Согласовать бюджет"], MORNING - timedelta(days=1)
    )
    tasks_file(tmp_path, monkeypatch, [])
    spoken = rhythm.line(store, no_model(settings), "morning", now=MORNING)
    assert spoken["source"] == "template"
    assert spoken["text"].startswith("Доброе утро.")
    assert "2 поручения" in spoken["text"]


def test_morning_template_remembers_yesterday(meeting, tmp_path, monkeypatch):
    store, _, settings = meeting
    tasks_file(
        tmp_path,
        monkeypatch,
        [done("А", MORNING - timedelta(days=1)), done("Б", MORNING - timedelta(days=1))],
    )
    spoken = rhythm.line(store, no_model(settings), "morning", now=MORNING)
    assert "Вчера 2 дела закрыто" in spoken["text"]
    assert spoken["emotion"] == "happy"


def test_evening_template_counts_only_what_got_done(meeting, tmp_path, monkeypatch):
    store, mid, settings = with_actions(
        meeting, ["Отправить смету", "Согласовать бюджет"], EVENING - timedelta(hours=5)
    )
    tasks_file(
        tmp_path,
        monkeypatch,
        [done("Смета", EVENING - timedelta(hours=1), meeting=mid), done("Звонок", EVENING)],
    )
    spoken = rhythm.line(store, no_model(settings), "evening", now=EVENING)
    assert spoken["text"] == "За сегодня сделано 2, из них со встреч — 1. Хороший день."
    assert "ждут" not in spoken["text"].casefold()


def test_evening_without_anything_done_is_still_warm(meeting, tmp_path, monkeypatch):
    store, _, settings = meeting
    tasks_file(tmp_path, monkeypatch, [])
    spoken = rhythm.line(store, no_model(settings), "evening", now=EVENING)
    assert spoken["text"] == "День закрываю. Завтра начнём спокойно."


def test_every_template_fits_the_notice_and_respects_the_bible():
    base = dict(
        hour=9,
        active=[],
        waiting_count=0,
        waiting=[],
        done_today=0,
        done_today_titles=[],
        done_today_from_meetings=0,
        done_yesterday=0,
        meetings_today=0,
    )
    cases = [
        dict(base, active=["a", "b", "c"]),
        dict(base, waiting_count=124),
        dict(base, done_yesterday=11),
        base,
        dict(base, done_today=21, done_today_from_meetings=13),
        dict(base, meetings_today=5),
    ]
    for f in cases:
        for kind in rhythm.KINDS:
            for seed in ("a", "b", "c", "d"):
                text = rhythm.fallback(kind, f, seed)["text"]
                assert len(text) <= MAX_LINE_CHARS, text
                assert not any(phrase in text.casefold() for phrase in FORBIDDEN), text


# --- the model ------------------------------------------------------------------------


def test_model_line_is_used_when_in_character(meeting, tmp_path, monkeypatch):
    store, _, settings = meeting
    tasks_file(tmp_path, monkeypatch, [])
    client = model_says(
        settings, dict(text="Привет, я тут. Начнём с малого?", emotion="curious", intensity=0.4)
    )
    spoken = rhythm.line(store, settings, "morning", now=MORNING, client=client)
    assert spoken == dict(
        text="Привет, я тут. Начнём с малого?", emotion="curious", intensity=0.4, source="model"
    )


def test_model_out_of_character_falls_back_to_template(meeting, tmp_path, monkeypatch):
    store, _, settings = meeting
    tasks_file(tmp_path, monkeypatch, [])
    client = model_says(settings, dict(text="Ты опять ничего не сделал.", emotion="calm", intensity=0.4))
    spoken = rhythm.line(store, settings, "evening", now=EVENING, client=client)
    assert spoken["source"] == "template"


def test_evening_prompt_hides_what_is_still_open(meeting, tmp_path, monkeypatch):
    store, _, _ = with_actions(meeting, ["Отправить смету"], EVENING - timedelta(hours=2))
    tasks_file(tmp_path, monkeypatch, [])
    f = rhythm.facts(store, EVENING)
    evening = rhythm.build_messages("evening", f)[1]["content"]
    morning = rhythm.build_messages("morning", f)[1]["content"]
    assert "Отправить смету" not in evening and "waiting" not in evening and "active" not in evening
    assert "Отправить смету" in morning


def test_cli_rhythm_emits_a_line(meeting, tmp_path, monkeypatch, capsys):
    _, _, settings = meeting
    no_model(settings).save()
    tasks_file(tmp_path, monkeypatch, [])
    assert companion_main(["rhythm", "morning"]) == 0
    event = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert event["event"] == "line" and event["kind"] == "morning" and event["source"] == "template"
    assert event["text"]
