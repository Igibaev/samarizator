"""The notes algorithm: compact lines in, short lines out, cached check, one-shot assembly."""

import json
import re

import httpx
import pytest

from samarizator import summary_notes as notes
from samarizator.summary import SummaryFormatError, summarize
from samarizator.summary_prompts import ASSEMBLE_PROMPT, CHECK_PROMPT, NOTES_PROMPT, SYSTEM


def primary_ids(prompt):
    body = prompt.split("Основной фрагмент:\n", 1)[1]
    body = body.split("\nПосле основного фрагмента", 1)[0].split("\n\nЗАДАНИЕ", 1)[0]
    return [int(n) for n in re.findall(r"^\[(\d+)\??\]", body, re.M)]


class Notes:
    """A model that writes notes in the line format and records every request."""

    parallel = 1

    def __init__(self, check="ВСЁ ВЕРНО", assembly=None, notes=None):
        self.requests = []
        self.check = check
        self.assembly = assembly
        self.notes = notes

    def complete_text(self, system, prompt, max_tokens, minimum=1024, continuation=False, grammar=None):
        self.requests.append(dict(system=system, prompt=prompt, grammar=grammar))
        if prompt.endswith(NOTES_PROMPT) or "\n\nЗАДАНИЕ\nСоставь подробный конспект" in prompt:
            ids = primary_ids(prompt)
            if self.notes:
                return self.notes(ids, prompt), False
            lines = [f"ТЕМА: Блок {ids[0]}", "ТЕМЫ: бюджет; сроки", f"ОБЗОР: Обсуждали блок {ids[0]}."]
            lines.append(f"- Решение [{ids[0]}] Бюджет блока {ids[0]} — 17 млн | статус: согласовано")
            lines.append(
                f"- Задача [{ids[-1]}] Подготовить отчёт по блоку {ids[0]} | отв: Анна | срок: к пятнице"
            )
            return "\n".join(lines) + "\n", False
        if CHECK_PROMPT in prompt:
            answer = self.check(prompt) if callable(self.check) else self.check
            return answer + "\n", False
        if prompt.endswith(ASSEMBLE_PROMPT):
            if self.assembly:
                return self.assembly(prompt), False
            refs = re.findall(r"^\d+\. \S+ \[(\d+)", prompt, re.M)
            lines = ["ОБЗОР: Встреча о бюджете и сроках.", "ТЕМЫ: бюджет; сроки", "ТЕЗИСЫ"]
            lines.append(f"- Решение [{refs[0]}] Бюджет — 17 млн | статус: согласовано")
            lines += ["ПЕРЕСМОТРЫ", "НЕТ"]
            return "\n".join(lines) + "\n", False
        raise AssertionError("unexpected request: " + prompt[-200:])


def rows(count, length=900):
    return [
        dict(
            start=i * 30,
            end=i * 30 + 25,
            speaker="Речь",
            text=f"Реплика {i}. " + "Обсуждаем план. " * (length // 16),
            uncertain=0,
        )
        for i in range(count)
    ]


@pytest.fixture
def notes_meeting(meeting):
    store, mid, settings = meeting
    settings.summary_algorithm = "notes"
    settings.input_chars = 6000
    return store, mid, settings


# -- format ------------------------------------------------------------------------------------


def test_a_point_line_is_read_back_exactly():
    item = dict(
        kind="decision",
        text="Бюджет — 17 млн",
        evidence=[12, 14],
        owner="Анна",
        due="к пятнице",
        status="agreed",
    )
    line = notes.item_line(item)
    assert line == "Решение [12, 14] Бюджет — 17 млн | отв: Анна | срок: к пятнице | статус: согласовано"
    assert notes.parse_item(line) == item
    plain = notes.parse_item("- Тезис [3] -5 градусов ночью")
    assert plain == dict(
        kind="point", text="-5 градусов ночью", evidence=[3], owner=None, due=None, status="unspecified"
    )
    assert notes.parse_item("2. Вопрос [7, 8] Кто платит?")["kind"] == "question"
    for bad in ["Бюджет 17 млн", "Тезис [12?] текст", "Мысль [3] текст", "Тезис [3] "]:
        with pytest.raises(SummaryFormatError):
            notes.parse_item(bad)


def test_notes_and_corrections_are_parsed():
    answer = (
        "ТЕМА: Бюджет квартала\nТЕМЫ: бюджет; сроки; бюджет\nОБЗОР: Обсудили бюджет.\n"
        "- Решение [1, 2] Бюджет 17 млн | статус: согласовано\n- Риск [3] Поставщик может задержать\n"
    )
    parsed = notes.parse_notes(answer)
    assert parsed["title"] == "Бюджет квартала" and parsed["topics"] == ["бюджет", "сроки"]
    assert [i["kind"] for i in parsed["items"]] == ["decision", "risk"]
    assert (
        notes.parse_notes("ТЕМА: Тишина\nТЕМЫ: связь\nОБЗОР: Проверка связи.\nПУНКТОВ НЕТ\n")["items"] == []
    )
    draft = dict(parsed, items=parsed["items"] + [dict(parsed["items"][0], text="Дубль бюджета")])
    fixed, receipt = notes.apply_check(
        "Исправить 1, 3: Решение [1, 2] Бюджет 17 млн на второй квартал | статус: согласовано\n"
        "Добавить: Задача [3] Найти второго поставщика | отв: Олег\n",
        draft,
        {1, 2, 3},
        {1, 2, 3},
    )
    assert [i["text"] for i in fixed["items"]] == [
        "Бюджет 17 млн на второй квартал",
        "Поставщик может задержать",
        "Найти второго поставщика",
    ]
    assert receipt["edited"] == 1 and receipt["added"] == 1
    same, _ = notes.apply_check("ВСЁ ВЕРНО\n", draft, {1, 2, 3}, {1, 2, 3})
    assert same["items"] == draft["items"]
    for bad in [
        "Исправить 9: Тезис [1] x",
        "Удалить 1:",
        "Удалить 1: дубль\nИсправить 1: Тезис [1] x",
        "Что-то ещё",
    ]:
        with pytest.raises(SummaryFormatError):
            notes.apply_check(bad, draft, {1, 2, 3}, {1, 2, 3})
    with pytest.raises(SummaryFormatError, match="слишком много"):
        notes.apply_check("Удалить 1, 2, 3: всё выдумано", draft, {1, 2, 3}, {1, 2, 3})


def test_the_transcript_is_compact_and_blocks_hold_more_speech():
    from samarizator.summary import contextual_blocks

    meeting_rows = [dict(r, id=i + 1) for i, r in enumerate(rows(200, 200))]
    planned = notes.planned_blocks(meeting_rows, 12000)
    classic = list(contextual_blocks(meeting_rows, 12000))
    assert len(planned) < len(classic)
    block, before, after = planned[1]
    text = notes.transcript_text(block, before, after)
    assert text.startswith("РАСШИФРОВКА\nДо основного фрагмента")
    assert f"[{block[0]['id']}] Реплика" in text and '"id"' not in text
    assert all(len(notes.row_line(r)) <= 1200 for r in before + after)
    uncertain = notes.row_line(dict(id=5, text="Сумма   пятьдесят", uncertain=1))
    assert uncertain == "[5?] Сумма пятьдесят"


# -- pipeline ----------------------------------------------------------------------------------


def test_notes_pipeline_writes_once_and_checks_against_the_cached_transcript(notes_meeting):
    store, mid, settings = notes_meeting
    store.save_chunk(mid, 0, rows(12))
    model = Notes()
    progress = []
    result = summarize(store, mid, settings, client=model, progress=progress.append)
    kinds = [
        "notes"
        if r["prompt"].endswith(NOTES_PROMPT)
        else "check"
        if CHECK_PROMPT in r["prompt"]
        else "assemble"
        for r in model.requests
    ]
    blocks = kinds.count("notes")
    assert blocks >= 2 and kinds.count("check") == blocks and kinds.count("assemble") == 1
    # The check begins with exactly the transcript the notes request read, and the same system.
    for first, second in zip(model.requests[::2], model.requests[1::2]):
        transcript = first["prompt"][: -len(NOTES_PROMPT)]
        assert second["prompt"].startswith(transcript) and first["system"] == second["system"]
        assert first["grammar"] == notes.NOTES_GRAMMAR and second["grammar"] == notes.CHECK_GRAMMAR
    assert all("СВЯЗЬ С ИСТОЧНИКОМ" in r["system"] and "JSON" not in r["system"] for r in model.requests)
    assert result["algorithm"] == "notes"
    detailed = result["detailed"]
    assert len(detailed["items"]) == 2 * blocks and detailed["topics"] == ["бюджет", "сроки"]
    assert detailed["items"][1] == dict(
        kind="action",
        text=detailed["items"][1]["text"],
        evidence=detailed["items"][1]["evidence"],
        owner="Анна",
        due="к пятнице",
        status="unspecified",
    )
    assert [p["title"] for p in detailed["parts"]][0].startswith("Блок")
    assert result["brief"]["items"][0]["text"] == "Бюджет — 17 млн" and result["topics"] == [
        "бюджет",
        "сроки",
    ]
    # Without revisions every decision and task keeps its status as written.
    assert len(detailed["resolved"]) == 2 * blocks
    assert set(result["timings"]) >= {"blocks", "assembly", "final", "total"}
    assert any(line.startswith("Сводка готова за") for line in progress)
    # Everything is kept per meeting: a repeated run asks nothing.
    again = Notes()
    summarize(store, mid, settings, client=again)
    assert again.requests == []


def test_assembly_folds_revised_decisions_into_their_last_state(notes_meeting):
    store, mid, settings = notes_meeting
    store.save_chunk(mid, 0, rows(12))

    def assembly(prompt):
        numbers = re.findall(r"^(\d+)\. Решение \[(\d+)\]", prompt, re.M)
        (first, ref1), (second, ref2) = numbers[0], numbers[1]
        return (
            "ОБЗОР: Бюджет пересмотрели.\nТЕМЫ: бюджет\nТЕЗИСЫ\n"
            f"- Решение [{ref1}, {ref2}] Бюджет — 20 млн вместо 17 | статус: согласовано\n"
            f"ПЕРЕСМОТРЫ\n- {first}, {second} → Решение [{ref1}, {ref2}] Бюджет — 20 млн вместо 17 | статус: согласовано\n"
        )

    result = summarize(store, mid, settings, client=Notes(assembly=assembly))
    resolved = result["detailed"]["resolved"]
    assert resolved[0]["text"] == "Бюджет — 20 млн вместо 17"
    assert sum("20 млн" in item["text"] for item in resolved) == 1
    # The full history stays in the detailed summary.
    assert len(result["detailed"]["items"]) == len(resolved) + 1


def test_check_corrections_replace_points_and_a_bad_check_keeps_the_draft(notes_meeting):
    store, mid, settings = notes_meeting
    store.save_chunk(mid, 0, rows(4))

    def check(prompt):
        ids = primary_ids(prompt)
        return f"Исправить 1: Решение [{ids[0]}] Бюджет — 18 млн | статус: предложено\nДобавить: Риск [{ids[0]}] Курс валют"

    result = summarize(store, mid, settings, client=Notes(check=check))
    items = result["detailed"]["items"]
    assert items[0]["text"] == "Бюджет — 18 млн" and items[0]["status"] == "proposed"
    assert items[-1]["kind"] == "risk"
    store.reset_summary(mid)
    # A check that cites a line outside the transcript is asked again, then given up on.
    broken = Notes(check="Добавить: Тезис [99999] Выдумка")
    result = summarize(store, mid, settings, client=broken)
    blocks = sum(r["prompt"].endswith(NOTES_PROMPT) for r in broken.requests)
    assert sum(CHECK_PROMPT in r["prompt"] for r in broken.requests) == 3 * blocks
    retry = [r["prompt"] for r in broken.requests if CHECK_PROMPT in r["prompt"]][1]
    assert "Предыдущий ответ не прошёл проверку" in retry.rsplit(CHECK_PROMPT, 1)[1]
    assert "Повторная проверка части сводки не завершена" in result["detailed"]["quality_warning"]


def test_unusable_notes_split_the_block_and_finally_keep_the_source(notes_meeting):
    store, mid, settings = notes_meeting
    store.save_chunk(mid, 0, rows(2, 300))
    model = Notes(notes=lambda ids, prompt: "ТЕМА: x\nТЕМЫ: x\nОБЗОР: x\n- Тезис [99999] Выдумка\n")
    progress = []
    result = summarize(store, mid, settings, client=model, progress=progress.append)
    assert any("Делю блок" in line for line in progress)
    assert any("сохранён исходный текст" in line for line in progress)
    assert "Исходные реплики сохранены" in result["detailed"]["quality_warning"]
    assert all(item["text"].startswith("Реплика") for item in result["detailed"]["items"])


def test_a_register_too_long_for_one_request_is_reduced_by_levels(notes_meeting, monkeypatch):
    from samarizator import local_llm
    from samarizator.summary import BRIEF_PROMPT

    store, mid, settings = notes_meeting
    store.save_chunk(mid, 0, rows(6))
    monkeypatch.setattr(local_llm, "final_input_chars", lambda settings: 50)

    class Classic(Notes):
        def complete(self, prompt, allowed):
            self.requests.append(dict(prompt=prompt, system="", grammar=None))
            group = json.loads(prompt[prompt.index("\n[") + 1 :]) if prompt.startswith(BRIEF_PROMPT) else []
            if prompt.startswith(BRIEF_PROMPT):
                return dict(overview="Кратко.", topics=["бюджет"], items=group[:1])
            return dict(overview="", topics=[], items=json.loads(prompt[prompt.index("\n[") + 1 :]))

    model = Classic()
    progress = []
    result = summarize(store, mid, settings, client=model, progress=progress.append)
    assert not any(r["prompt"].endswith(ASSEMBLE_PROMPT) for r in model.requests)
    assert any("по уровням" in line for line in progress)
    assert result["brief"]["overview"] == "Кратко." and len(result["brief"]["items"]) == 1


def test_assembly_answers_are_checked(notes_meeting):
    ledger = [
        dict(kind="decision", text="A", evidence=[1], owner=None, due=None, status="agreed"),
        dict(kind="point", text="B", evidence=[2], owner=None, due=None, status="unspecified"),
    ]
    good = "ОБЗОР: О.\nТЕМЫ: т\nТЕЗИСЫ\n- Тезис [1, 2] Итог\nПЕРЕСМОТРЫ\nНЕТ\n"
    brief, revisions = notes.parse_assembly(good, ledger)
    assert brief["items"][0]["evidence"] == [1, 2] and revisions == []
    for bad in [
        good.replace("[1, 2]", "[7]"),  # a line nobody said
        good.replace("НЕТ", "- 2 → Тезис [2] B"),  # a point is not a decision
        good.replace("ОБЗОР: О.\n", ""),
        "ОБЗОР: О.\nТЕМЫ: т\nТЕЗИСЫ\n" + "- Тезис [1] x\n" * 10 + "ПЕРЕСМОТРЫ\nНЕТ\n",
    ]:
        with pytest.raises(SummaryFormatError):
            notes.parse_assembly(bad, ledger)


def test_local_client_sends_the_grammar_with_text_requests():
    from samarizator.config import Settings
    from samarizator.summary import LocalClient

    seen = []

    def handler(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json=dict(tokens=[1] * 10))
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json=dict(choices=[dict(message=dict(content="ВСЁ ВЕРНО\n"), finish_reason="stop")])
        )

    client = LocalClient(Settings(), "http://127.0.0.1:9", transport=httpx.MockTransport(handler))
    text, truncated = client.complete_text("S", "P", 100, 50, grammar=notes.CHECK_GRAMMAR)
    assert text == "ВСЁ ВЕРНО" and not truncated and seen[0]["grammar"] == notes.CHECK_GRAMMAR
    client.complete_text("S", "P", 100, 50)
    assert "grammar" not in seen[1]


def test_algorithm_setting_is_validated_and_defaults_to_notes():
    from samarizator.config import Settings

    assert Settings().summary_algorithm == "notes"
    assert Settings.from_dict({"version": 2}).summary_algorithm == "notes"
    with pytest.raises(ValueError, match="Алгоритм"):
        Settings(summary_algorithm="fastest").validate()


def test_the_classic_prompts_are_untouched():
    import hashlib

    from samarizator.summary_prompts import MAP_PROMPT, REVIEW_PROMPT

    digest = hashlib.sha256((SYSTEM + MAP_PROMPT + REVIEW_PROMPT).encode()).hexdigest()
    assert digest == "9ec5eaf69cbc97d77aeaf64d8a519e6c5bd2abd702a14a05ed6e5e40825f7fb8"


def test_settings_offer_both_algorithms(qapp, tmp_path, monkeypatch):
    from samarizator.config import Settings
    from samarizator.settings_dialog import SettingsDialog

    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    dialog = SettingsDialog(Settings())
    choice = dialog.fields["summary_algorithm"]
    assert choice.currentData() == "notes"
    choice.setCurrentIndex(choice.findData("classic"))
    assert dialog.value(choice) == "classic"
    dialog.deleteLater()
