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
        self.requests.append(dict(system=system, prompt=prompt, grammar=grammar, limit=max_tokens))
        if prompt.endswith(NOTES_PROMPT) or "\n\nЗАДАНИЕ\nСоставь подробный конспект" in prompt:
            ids = primary_ids(prompt)
            if self.notes:
                return self.notes(ids, prompt), False
            lines = [f"ТЕМА: Блок {ids[0]}", "ТЕМЫ: бюджет; сроки", f"ОБЗОР: Обсуждали блок {ids[0]}."]
            lines.append(f"- Решение [{ids[0]}] Бюджет блока {ids[0]} — 17 млн | статус: согласовано")
            lines.append(
                f"- Задача [{ids[-1]}] Подготовить отчёт по блоку {ids[0]} | отв: Анна | срок: к пятнице"
            )
            return "\n".join(lines) + "\nКОНЕЦ\n", False
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


def test_answers_stop_at_end_of_turn_markers_and_lose_special_tokens():
    """GigaChat 3 ends a turn with <|message_sep|>, which llama.cpp does not count as the end."""
    from samarizator import local_llm
    from samarizator.config import Settings
    from samarizator.summary import END_MARKERS, LocalClient

    seen = []

    def handler(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json=dict(tokens=[1] * 10))
        seen.append(json.loads(request.content))
        content = "# Протокол\nТекст.<|message_sep|></s>"
        return httpx.Response(
            200, json=dict(choices=[dict(message=dict(content=content), finish_reason="stop")])
        )

    client = LocalClient(Settings(), "http://127.0.0.1:9", transport=httpx.MockTransport(handler))
    text, truncated = client.complete_text("S", "P", 100, 50)
    assert text == "# Протокол\nТекст." and not truncated
    assert seen[0]["stop"] == END_MARKERS and "<|message_sep|>" in END_MARKERS
    client.complete_text("S", "P", 100, 50, grammar=notes.CHECK_GRAMMAR)
    assert "stop" not in seen[1]  # a grammar answer ends by its grammar
    args = local_llm.server_args(Settings(llm_model="/m.gguf"), 8080, "k", binary="llama-server")
    assert "--special" in args


def test_every_answer_has_a_limit_proportional_to_its_material(notes_meeting):
    store, mid, settings = notes_meeting
    settings.max_output_tokens = 16000
    store.save_chunk(mid, 0, rows(6))

    model = Notes()
    summarize(store, mid, settings, client=model)
    limits = {
        (
            "notes"
            if r["prompt"].endswith(NOTES_PROMPT)
            else "check"
            if CHECK_PROMPT in r["prompt"]
            else "assemble"
        ): r["limit"]
        for r in model.requests
    }
    assert limits["notes"] < 3000 and limits["check"] < 1500 and limits["assemble"] == notes.ASSEMBLE_TOKENS


def test_a_looping_answer_keeps_its_points_instead_of_splitting_the_block(notes_meeting):
    store, mid, settings = notes_meeting
    store.save_chunk(mid, 0, rows(3, 300))

    class Looping(Notes):
        def complete_text(self, system, prompt, max_tokens, minimum=1024, continuation=False, grammar=None):
            if prompt.endswith(NOTES_PROMPT):
                self.requests.append(dict(system=system, prompt=prompt, grammar=grammar, limit=max_tokens))
                ids = primary_ids(prompt)
                head = (
                    f"ТЕМА: Бюджет\nТЕМЫ: бюджет\nОБЗОР: Обсудили бюджет.\n- Тезис [{ids[0]}] Новый пункт\n"
                )
                loop = f"- Решение [{ids[0]}] Бюджет 17 млн\n- Задача [{ids[-1]}] Отчёт\n" * 6
                return head + loop + "- Решение [", True  # cut off by the limit mid-line
            return super().complete_text(system, prompt, max_tokens, minimum, continuation, grammar)

    progress = []
    result = summarize(store, mid, settings, client=Looping(), progress=progress.append)
    assert not any("Делю блок" in line for line in progress)
    assert [i["text"] for i in result["detailed"]["items"]][:3] == ["Новый пункт", "Бюджет 17 млн", "Отчёт"]
    # A long answer that does not repeat itself is still split as before.
    assert notes.looped("ТЕМА: x\n- Тезис [1] a\n- Тезис [1] b\n- Тезис [1] c\n- Тез") is None


# -- time budget -------------------------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Timed(Notes):
    """A model whose every answer takes a known time and reports it like llama-server."""

    def __init__(self, clock, notes_seconds=60, check_seconds=30, speed=50.0, **kw):
        super().__init__(**kw)
        self.clock = clock
        self.notes_seconds, self.check_seconds, self.speed = notes_seconds, check_seconds, speed

    def complete_text(self, system, prompt, max_tokens, minimum=1024, continuation=False, grammar=None):
        answer = super().complete_text(system, prompt, max_tokens, minimum, continuation, grammar)
        seconds = self.check_seconds if CHECK_PROMPT in prompt else self.notes_seconds
        self.clock.now += seconds
        if getattr(self, "observe", None):
            tokens = round(seconds * self.speed)
            self.observe(dict(prompt_n=3000, prompt_ms=3000, predicted_n=tokens, predicted_ms=seconds * 1000))
        return answer


def test_budget_measures_the_model_from_the_servers_timings():
    budget = notes.Budget(600, parallel=2)
    assert budget.predict_speed() == notes.Budget.DEFAULT_PREDICT_SPEED
    budget.observe(dict(prompt_n=4000, prompt_ms=4000, predicted_n=500, predicted_ms=10000))
    budget.observe("not timings")
    assert budget.prompt_speed() == 1000 and budget.predict_speed() == 50 and budget.measured()
    assert budget.cost(1000, 100) == 1 + 2
    report = budget.report()
    assert report["limit"] == 600 and report["predict_speed"] == 50 and report["checks_skipped"] == 0
    assert notes.Budget().left() is None and notes.Budget().pressure(10, 2048) == 0


def test_budget_gives_up_checks_then_detail_as_time_runs_out(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(notes, "clock", clock)
    budget = notes.Budget(300, parallel=1)
    budget.observe(dict(prompt_n=3000, prompt_ms=3000, predicted_n=3000, predicted_ms=60000))  # 50 tok/s
    assert budget.pressure(6, 2048) == 0  # nothing measured per block yet
    budget.notes_done(60, 1500)
    budget.check_done(30)
    budget.block_finished()
    clock.now += 90
    # Five blocks left at 90 s each do not fit into the 210 s left; at 60 s each they do not either.
    assert budget.pressure(6, 2048) == 2
    assert budget.eta(6, 2048).startswith(" · осталось ≈ ")
    # With more time, only the checks go.
    relaxed = notes.Budget(500, parallel=1)
    relaxed.started = clock.now - 90
    relaxed.observe(dict(prompt_n=3000, prompt_ms=3000, predicted_n=3000, predicted_ms=60000))
    relaxed.notes_done(60, 1500)
    relaxed.check_done(30)
    relaxed.block_finished()
    assert relaxed.pressure(6, 2048) == 1
    # Two slots halve the waves.
    wide = notes.Budget(500, parallel=2)
    wide.started = clock.now - 90
    wide.observe(dict(prompt_n=3000, prompt_ms=3000, predicted_n=3000, predicted_ms=60000))
    wide.notes_done(60, 1500)
    wide.check_done(30)
    wide.block_finished()
    assert wide.pressure(6, 2048) == 0
    # The final text gets what is left — the whole room while there is time, never less
    # than the floor once the time is spent.
    tokens, continuations = budget.final_plan(4096, 6)
    assert tokens == 4096 and continuations == 1
    clock.now += 170  # 40 s left: room for about 1500 tokens after reading the register
    tokens, _ = budget.final_plan(4096, 6)
    assert notes.FINAL_FLOOR_TOKENS < tokens < 4096
    clock.now += 100
    assert budget.final_plan(4096, 6) == (notes.FINAL_FLOOR_TOKENS, 1)
    assert notes.Budget(0).final_plan(4096, 6) == (4096, None)


def test_a_summary_short_on_time_skips_checks_and_says_so(notes_meeting, monkeypatch):
    store, mid, settings = notes_meeting
    settings.summary_minutes = 4  # 240 s for 6 blocks of 60 + 30 s: only the notes fit
    settings.input_chars = 4000
    store.save_chunk(mid, 0, rows(12))
    clock = Clock()
    monkeypatch.setattr(notes, "clock", clock)
    model = Timed(clock)
    progress = []
    result = summarize(store, mid, settings, client=model, progress=progress.append)
    blocks = sum(
        r["prompt"].endswith(NOTES_PROMPT) or notes.TERSE_NOTE in r["prompt"] for r in model.requests
    )
    checks = sum(CHECK_PROMPT in r["prompt"] for r in model.requests)
    assert blocks >= 4 and 0 < checks < blocks
    budget = result["budget"]
    assert budget["checks_skipped"] == blocks - checks and budget["limit"] == 240
    assert budget["predict_speed"] == 50 and budget["prompt_speed"] == 1000
    assert budget["final_tokens"] == notes.FINAL_FLOOR_TOKENS  # the time is already spent
    assert any("осталось ≈" in line for line in progress) and any(
        "Скорость модели" in line for line in progress
    )
    assert model.expected is not None  # requests get timeouts from the measured speed
    # Every block still has its notes; the detailed summary is complete.
    assert len(result["detailed"]["items"]) == 2 * blocks


def test_when_even_the_notes_do_not_fit_they_are_asked_terse_and_a_cut_answer_is_kept(
    notes_meeting, monkeypatch
):
    store, mid, settings = notes_meeting
    settings.summary_minutes = 2
    settings.input_chars = 4000
    store.save_chunk(mid, 0, rows(12))
    clock = Clock()
    monkeypatch.setattr(notes, "clock", clock)

    class Cut(Timed):
        def complete_text(self, system, prompt, max_tokens, minimum=1024, continuation=False, grammar=None):
            text, truncated = super().complete_text(
                system, prompt, max_tokens, minimum, continuation, grammar
            )
            if notes.TERSE_NOTE in prompt:
                return text.replace("КОНЕЦ\n", "") + "- Решение [", True  # cut off by the limit
            return text, truncated

    model = Cut(clock, notes_seconds=90)
    result = summarize(store, mid, settings, client=model)
    terse = [r for r in model.requests if notes.TERSE_NOTE in r["prompt"]]
    assert terse and all(r["limit"] <= notes.notes_tokens(settings, [], True) + 4000 // 6 for r in terse)
    budget = result["budget"]
    assert budget["terse_blocks"] == len(terse) and budget["cut_blocks"] == len(terse)
    assert budget["checks_skipped"] >= len(terse)
    # No block was split because of the cut: the complete lines were kept.
    assert all("- Решение [" not in r["prompt"] for r in model.requests)
    assert len(result["detailed"]["items"]) >= 2 * len(terse)


def test_short_recordings_are_cut_into_enough_blocks_for_every_slot():
    few = [dict(id=i, start=i, uncertain=0, text="Реплика о бюджете. " * 10) for i in range(40)]  # ~8k chars
    assert len(notes.planned_blocks(few, 12000, slots=1)) == 2
    assert len(notes.planned_blocks(few, 12000, slots=4)) == 4  # 2 500 characters at least
    assert notes.block_chars(few, 12000, slots=4) == notes.MIN_BLOCK_CHARS
    tiny = few[:4]
    assert len(notes.planned_blocks(tiny, 12000, slots=4)) == 1  # never finer than MIN_BLOCK_CHARS
    many = [dict(id=i, start=i, uncertain=0, text="Реплика о бюджете. " * 10) for i in range(600)]
    assert notes.block_chars(many, 12000, slots=4) == 6000  # long recordings keep full blocks


def test_final_text_room_follows_the_format():
    from samarizator import local_llm
    from samarizator.config import Settings
    from samarizator.summary_prompts import FINAL_FORMATS

    assert local_llm.final_output_tokens(Settings(final_format="executive")) == 2048
    assert local_llm.final_output_tokens(Settings(final_format="protocol")) == 4096
    same = Settings(final_format="protocol", final_prompt=FINAL_FORMATS["protocol"][1])
    assert local_llm.final_output_tokens(same) == 4096
    custom = Settings(final_format="protocol", final_prompt="Свой шаблон")
    assert local_llm.final_output_tokens(custom) == local_llm.FINAL_OUTPUT_TOKENS


def test_requests_that_take_far_longer_than_expected_are_given_up_not_retried():
    from samarizator.config import Settings
    from samarizator.summary import STUCK_FACTOR, STUCK_GRACE, LocalClient, SummaryTooLong

    calls = []

    def handler(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json=dict(tokens=[1] * 100))
        calls.append(request.extensions.get("timeout"))
        raise httpx.ReadTimeout("slow")

    client = LocalClient(Settings(), "http://127.0.0.1:9", transport=httpx.MockTransport(handler))
    client.expected = lambda prompt_tokens, max_tokens: 10.0
    with pytest.raises(SummaryTooLong):
        client.complete_text("S", "P", 100, 50)
    assert len(calls) == 1 and calls[0]["read"] == STUCK_GRACE + STUCK_FACTOR * 10
    seen = []
    plain = LocalClient(Settings(), "http://127.0.0.1:9", transport=httpx.MockTransport(handler))
    plain.observe = seen.append

    def timed(request):
        if request.url.path == "/tokenize":
            return httpx.Response(200, json=dict(tokens=[1] * 100))
        return httpx.Response(
            200,
            json=dict(
                choices=[dict(message=dict(content="Ок"), finish_reason="stop")],
                timings=dict(prompt_n=5, prompt_ms=10, predicted_n=2, predicted_ms=40),
            ),
        )

    plain.client = httpx.Client(transport=httpx.MockTransport(timed))
    plain.complete_text("S", "P", 100, 50)
    assert seen == [dict(prompt_n=5, prompt_ms=10, predicted_n=2, predicted_ms=40)]


def test_progress_screen_knows_the_assembly_stage_and_the_summarys_own_estimate():
    from samarizator import progress

    assert progress.summary_stage("Сборка сводки: 80 пунктов за один заход…") == "brief"
    assert progress.summary_stage("Сводка готова за 4 мин 10 с (разбор 3 мин 2 с)") == "final"
    line = "Разбор записи: блок 3 из 8 · 2 одновременно · осталось ≈ 4 мин"
    assert progress.summary(line)["detail"] == "3 из 8"
    assert progress.remaining(0.3, 100, line) == "осталось около 4 мин"
    assert progress.remaining(0.3, 100, line.replace("4 мин", "меньше минуты")) == "осталось меньше минуты"
    assert progress.remaining(0.5, 100) == "осталось около 2 мин"


def test_time_limit_setting_is_offered_and_validated(qapp, tmp_path, monkeypatch):
    from samarizator.config import Settings
    from samarizator.settings_dialog import SettingsDialog

    assert Settings().summary_minutes == 10
    with pytest.raises(ValueError, match="Время на сводку"):
        Settings(summary_minutes=500).validate()
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    dialog = SettingsDialog(Settings(summary_minutes=7))
    choice = dialog.fields["summary_minutes"]
    assert dialog.value(choice) == 7  # an unusual stored value is kept
    choice.setCurrentIndex(choice.findData(0))
    assert dialog.value(choice) == 0
    dialog.deleteLater()
