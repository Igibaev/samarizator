import json
from datetime import datetime, timezone

import httpx

from samarizator import handoff as companion
from samarizator.handoff import (
    build_messages,
    candidate_actions,
    fallback_line,
    handoff,
    parse_reply,
    proposal_id,
    safe_handoff,
)
from samarizator.summary import ChatClient


def action(text, status="agreed", owner=None, due=None, kind="action"):
    return dict(kind=kind, text=text, evidence=[1], owner=owner, due=due, status=status)


def summary(items, resolved=None):
    brief = dict(overview="Итог", items=items[:2], topics=[])
    detailed = dict(overview="Подробно", items=items, topics=[], resolved=resolved or [])
    return dict(**brief, brief=brief, detailed=detailed, ledger=items, blocks=1, version=2)


def reply(text="Принёс два дела со встречи. Забирай, что твоё.", tasks=(), **extra):
    body = dict(text=text, emotion="curious", intensity=0.6, tasks=list(tasks), **extra)
    return httpx.Response(
        200, json={"choices": [{"finish_reason": "stop", "message": {"content": json.dumps(body)}}]}
    )


def client(settings, handler):
    return ChatClient(settings, transport=httpx.MockTransport(handler), key="test")


def install_companion(tmp_path, monkeypatch, tasks=None):
    home = tmp_path / "companion"
    home.mkdir()
    monkeypatch.setenv("FOCUS_COMPANION_HOME", str(home))
    if tasks is not None:
        (home / "tasks.json").write_text(json.dumps(tasks, ensure_ascii=False))
    return home


def stored(meeting, items, resolved=None):
    store, mid, settings = meeting
    store.update(mid, summary=json.dumps(summary(items, resolved), ensure_ascii=False), note="/n.md")
    return store, mid, settings


# --- what becomes a proposal -------------------------------------------------


def test_only_agreed_actions_from_the_reconciled_list_are_proposed():
    ledger = [
        action("Отправить смету"),
        action("Обсудить бюджет", status="proposed"),
        action("Риск срыва", kind="risk"),
        action("Отменённая задача", status="cancelled"),
    ]
    resolved = [action("Отправить смету"), action("Согласовать сроки"), action("отправить смету")]
    picked = candidate_actions(summary(ledger, resolved))
    assert [item["text"] for item in picked] == ["Отправить смету", "Согласовать сроки"]


def test_ledger_is_the_fallback_when_reconciliation_failed_and_the_list_is_capped():
    ledger = [action(f"Задача {i}") for i in range(9)] + [action("Не согласовано", status="proposed")]
    picked = candidate_actions(summary(ledger))
    assert len(picked) == companion.MAX_PROPOSALS
    assert all(item["status"] == "agreed" for item in picked)


def test_proposal_ids_are_stable_across_resummaries():
    first = proposal_id("m1", action("Позвонить Ивану"))
    assert first == proposal_id("m1", action("позвонить ивану"))
    assert first != proposal_id("m2", action("Позвонить Ивану"))


# --- the character's reply ----------------------------------------------------


def test_reply_is_validated_and_only_known_task_ids_are_rewritten():
    parsed = parse_reply(
        "```json\n"
        + json.dumps(
            dict(
                text="  Принёс   парочку.  ",
                emotion="happy",
                intensity="0.9",
                animation="dance",
                tasks=[dict(id="a", text="Отправить смету"), dict(id="zzz", text="Выдумка")],
            )
        )
        + "\n```",
        {"a", "b"},
    )
    assert parsed == dict(
        text="Принёс парочку.",
        emotion="happy",
        intensity=0.9,
        tasks={"a": "Отправить смету"},
    )


def test_lines_that_break_the_character_boundaries_are_rejected():
    for text in ["Ты опять ничего не сделал.", "Давно пора закрыть смету", "Не уходи, я скучаю"]:
        try:
            parse_reply(json.dumps(dict(text=text)), set())
        except companion.LineFormatError:
            continue
        raise AssertionError(text)


def test_fallback_line_is_deterministic_and_respects_full_slots():
    assert fallback_line("m", 1, 3) == fallback_line("m", 1, 3)
    assert "три" in fallback_line("m", 2, 0)["text"]
    assert "2" in fallback_line("m", 2, 1)["text"]


def test_prompt_layers_follow_the_bible_order_and_carry_memory():
    now = datetime(2026, 9, 21, 23, 30, tzinfo=timezone.utc)
    messages = build_messages("Планёрка", [action("Смета")], "m", ["Купить молоко"], now)
    system = messages[0]["content"]
    order = [
        system.index(marker)
        for marker in ["Ты — ИИ-компаньон", "ХАРАКТЕР", "НАСТРОЕНИЕ", "ЧТО ТЫ ПОМНИШЬ", "ЗАДАЧА", "ФОРМАТ"]
    ]
    assert order == sorted(order)
    assert "Купить молоко" in system and "Планёрка" in system and "сонный" in system
    assert json.loads(messages[1]["content"].split("\n", 1)[1])[0]["text"] == "Смета"


# --- writing the inbox --------------------------------------------------------


def test_nothing_is_written_when_the_companion_was_never_launched(meeting, tmp_path, monkeypatch):
    monkeypatch.setenv("FOCUS_COMPANION_HOME", str(tmp_path / "missing"))
    store, mid, settings = stored(meeting, [action("Смета")])
    calls = []
    assert handoff(store, mid, settings, client=client(settings, lambda r: calls.append(r))) is None
    assert not (tmp_path / "missing").exists() and calls == []


def test_setting_off_skips_the_handoff(meeting, tmp_path, monkeypatch):
    home = install_companion(tmp_path, monkeypatch)
    store, mid, settings = stored(meeting, [action("Смета")])
    settings.companion_handoff = False
    assert handoff(store, mid, settings, client=client(settings, lambda r: reply())) is None
    assert not (home / "inbox").exists()


def test_handoff_writes_one_atomic_file_the_swift_decoder_can_read(meeting, tmp_path, monkeypatch):
    home = install_companion(tmp_path, monkeypatch, tasks=[])
    store, mid, settings = stored(meeting, [action("Отправить смету Ивану", owner="Оля", due="к пятнице")])
    pid = proposal_id(mid, action("Отправить смету Ивану"))
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return reply(tasks=[dict(id=pid, text="Отправить смету Ивану к пятнице")])

    now = datetime(2026, 9, 21, 12, 0, 0, 123456, tzinfo=timezone.utc)
    path = handoff(store, mid, settings, client=client(settings, handler), now=now)
    assert path == home / "inbox" / f"{mid}.json"
    assert sorted(p.name for p in (home / "inbox").iterdir()) == [f"{mid}.json"]
    document = json.loads(path.read_text())
    assert document["version"] == 1 and document["meetingId"] == mid and document["note"] == "/n.md"
    assert document["createdAt"] == "2026-09-21T12:00:00Z"
    assert document["line"] == dict(
        text="Принёс два дела со встречи. Забирай, что твоё.", emotion="curious", intensity=0.6
    )
    assert document["proposals"] == [
        dict(
            id=pid,
            text="Отправить смету Ивану к пятнице",
            sourceText="Отправить смету Ивану",
            owner="Оля",
            due="к пятнице",
        )
    ]
    body = requests[0]
    assert body["temperature"] == 0.7 and body["messages"][0]["role"] == "system"
    assert "Отправить смету Ивану" in body["messages"][1]["content"]


def test_model_failure_still_delivers_tasks_with_source_wording(meeting, tmp_path, monkeypatch):
    home = install_companion(tmp_path, monkeypatch)
    store, mid, settings = stored(meeting, [action("Смета", owner="Оля"), action("Созвон")])
    path = handoff(store, mid, settings, client=client(settings, lambda r: httpx.Response(500)))
    document = json.loads(path.read_text())
    assert document["line"] == fallback_line(mid, 2, 3)
    assert [p["text"] for p in document["proposals"]] == ["Оля: Смета", "Созвон"]
    assert (home / "inbox" / f"{mid}.json").exists()


def test_out_of_character_reply_falls_back_but_keeps_valid_task_rewrites(meeting, tmp_path, monkeypatch):
    install_companion(tmp_path, monkeypatch)
    store, mid, settings = stored(meeting, [action("Смета")])
    path = handoff(
        store, mid, settings, client=client(settings, lambda r: reply(text="Ты опять забыл смету"))
    )
    document = json.loads(path.read_text())
    assert document["line"]["text"] == fallback_line(mid, 1, 3)["text"]
    assert document["proposals"][0]["text"] == "Смета"


def test_tasks_already_in_the_companion_are_not_proposed_again(meeting, tmp_path, monkeypatch):
    home = install_companion(
        tmp_path,
        monkeypatch,
        tasks=[
            # Текущая схема компаньона (title + status) и схема прошлых фаз (text + isDone).
            dict(id="1", title="Смета", status="active", startedAt="2026-09-21T10:00:00Z"),
            dict(id="2", text="Созвон", createdAt="2026-09-21T10:00:00Z", isDone=True),
            dict(id="3", title="Убранное", status="archived"),
        ],
    )
    store, mid, settings = stored(meeting, [action("смета"), action("Созвон")])
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return reply()

    path = handoff(store, mid, settings, client=client(settings, handler))
    document = json.loads(path.read_text())
    assert [p["text"] for p in document["proposals"]] == ["Созвон"]
    assert "«Смета»" in calls[0]["messages"][0]["content"]

    store.update(mid, summary=json.dumps(summary([action("Смета")]), ensure_ascii=False))
    assert handoff(store, mid, settings, client=client(settings, handler)) is None
    assert len(calls) == 1 and len(list((home / "inbox").iterdir())) == 1


def test_safe_handoff_never_raises(meeting, tmp_path, monkeypatch):
    install_companion(tmp_path, monkeypatch)
    store, mid, settings = stored(meeting, [action("Смета")])
    monkeypatch.setattr(companion, "candidate_actions", lambda *_: 1 / 0)
    notes = []
    assert safe_handoff(store, mid, settings, notes.append) is None
    assert notes == ["ИИ-компаньон: дела не переданы (ZeroDivisionError)."]
