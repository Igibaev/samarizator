"""Every request is measured against the model's context: nothing is cut off silently."""

import json

import httpx
import pytest

from samarizator.config import Settings
from samarizator.summary import (
    CONTINUE_PROMPT,
    LocalClient,
    SummaryTooLong,
    final_document,
    join_continuation,
    summarize,
)
from samarizator.summary_prompts import BRIEF_PROMPT, MAP_PROMPT, REDUCE_PROMPT, REVIEW_PROMPT


def client_for(handler, chars_per_token=3, **values):
    """LocalClient against a fake llama-server with /tokenize and the chat endpoint."""
    chats = []

    def route(req):
        body = json.loads(req.content)
        if req.url.path == "/tokenize":
            if chars_per_token is None:
                return httpx.Response(404)
            return httpx.Response(200, json={"tokens": list(range(len(body["content"]) // chars_per_token))})
        chats.append(body)
        finish, content = handler(body, len(chats))
        return httpx.Response(
            200, json={"choices": [{"finish_reason": finish, "message": {"content": content}}]}
        )

    client = LocalClient(
        Settings(**values), "http://127.0.0.1:9", key="k", transport=httpx.MockTransport(route)
    )
    return client, chats


def test_answer_limit_is_what_is_left_of_the_context_window():
    client, chats = client_for(lambda body, n: ("stop", "Готово"))
    client.n_ctx = 6000
    prompt = "слово " * 2400  # 14 400 characters → 4 800 tokens by the fake tokenizer
    text, truncated = client.complete_text("система", prompt, 4000, minimum=500)
    assert text == "Готово" and not truncated
    room = chats[0]["max_tokens"]
    assert room < 4000 and room + 4800 <= 6000


def test_a_request_that_cannot_fit_is_refused_before_it_is_sent():
    client, chats = client_for(lambda body, n: ("stop", "{}"))
    client.n_ctx = 2000
    with pytest.raises(SummaryTooLong):
        client.complete("x" * 9000, {1})
    assert chats == []  # split by the caller instead of an answer cut off mid-way


def test_without_a_tokenizer_the_estimate_errs_on_the_safe_side():
    client, _ = client_for(lambda body, n: ("stop", "{}"), chars_per_token=None)
    # 1.5 characters per token: more tokens than any real tokenizer gives for this text.
    assert client.count_tokens("x" * 300) == 200
    assert client.tokenizer is False


@pytest.mark.real_final
def test_a_cut_off_final_text_is_continued_from_where_it_stopped():
    first = "# Протокол\n\n## Решения\n1. Бюджет 17 млн — согласо"
    rest = "вано.\n2. Запуск в ноябре.\n"

    def handler(body, n):
        return ("length", first) if n == 1 else ("stop", rest)

    client, chats = client_for(handler)
    summary = dict(
        brief=dict(overview="Обзор", items=[], topics=[]),
        detailed=dict(overview="", items=[dict(kind="decision", text="Бюджет", evidence=[1])], topics=[]),
    )
    progress = []
    final = final_document(client, Settings(), summary, {1: 5.0}, progress.append)
    assert final["text"] == first + rest.rstrip()
    assert final["continued"] == 1 and "обрывается" not in final["warning"]
    follow = chats[1]["messages"][-1]["content"]
    assert follow.endswith(CONTINUE_PROMPT.format(tail=first)[-200:])
    assert "Бюджет" in follow  # the continuation still sees the material
    assert any("продолжение" in line for line in progress)


@pytest.mark.real_final
def test_final_text_falls_back_to_a_shorter_register_when_the_full_one_does_not_fit():
    client, chats = client_for(lambda body, n: ("stop", "# Текст"))
    long_items = [dict(kind="point", text="Подробность " * 40, evidence=[i]) for i in range(1, 80)]
    summary = dict(
        brief=dict(overview="Обзор", items=[dict(kind="decision", text="Главное", evidence=[1])], topics=[]),
        detailed=dict(overview="", items=long_items, topics=[]),
    )
    client.n_ctx = 4500  # the full register (~38k characters) cannot fit, the brief can
    final = final_document(client, Settings(), summary, {}, lambda *_: None)
    assert final["text"] == "# Текст" and final["source"] == "brief"
    assert "Подробность" not in chats[0]["messages"][-1]["content"]


def test_continuation_drops_a_repeated_line():
    text = "Строка один\nДлинная вторая строка, которая оборвалась"
    assert join_continuation(text, "Длинная вторая строка, которая оборвалась на середине.") == (
        text + " на середине."
    )
    assert join_continuation("Абв\n", "\nГде") == "Абв\nГде"


def test_reduction_that_does_not_fit_is_split_instead_of_falling_back(meeting):
    store, mid, settings = meeting
    settings.input_chars = 4000
    store.save_chunk(
        mid,
        0,
        [
            dict(start=i * 10, end=i * 10 + 3, speaker="Речь", text=f"{i}" * 2500, uncertain=False)
            for i in range(4)
        ],
    )

    class Fake:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt, allowed):
            self.prompts.append(prompt)
            if prompt.startswith(REVIEW_PROMPT):
                draft = json.loads(prompt[len(REVIEW_PROMPT) :])["draft"]
                return dict(draft, items=[dict(item, draft_ids=[i]) for i, item in enumerate(draft["items"])])
            if prompt.startswith(BRIEF_PROMPT) and len(allowed) > 2:
                raise SummaryTooLong("Запрос не поместился в контекст модели.")
            ids = allowed
            if prompt.startswith(MAP_PROMPT):
                ids = {r["id"] for r in json.loads(prompt[len(MAP_PROMPT) :])["source"]["segments"]}
            return dict(
                overview="Обзор.",
                topics=["Тема"],
                items=[dict(kind="point", text=f"Пункт {sorted(ids)}", evidence=sorted(ids)[:1])],
            )

    fake = Fake()
    result = summarize(store, mid, settings, client=fake)
    assert not result["brief"].get("generation_warning")
    assert any(p.startswith(REDUCE_PROMPT) for p in fake.prompts)


def test_summary_can_be_redone_from_scratch_and_resumed(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from samarizator.app import Window

    w = Window()
    source = tmp_path / "a.wav"
    source.write_bytes(b"x")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(mid, 0, [dict(start=1, end=2, speaker="Речь", text="Бюджет согласован")])
    w.store.save_checkpoint(mid, "asr_complete", 0, True)
    w.store.save_checkpoint(mid, "summary-map", 0, dict(digest="old"))
    w.store.update(mid, status="interrupted")
    w.mid = mid
    w.refresh_list()
    w.show_page()
    # An unfinished summary offers both ways out.
    assert w.stack.currentWidget() is w.empty
    texts = " ".join(b.text() for b in w.empty.findChildren(type(w.ask_button)) if b.isVisibleTo(w.empty))
    assert "Продолжить сводку" in texts and "Сделать заново" in texts

    started = []
    monkeypatch.setattr(w, "start", started.append)
    monkeypatch.setattr(w, "confirm_redo", lambda meeting: False)
    w.redo_summary()
    assert started == [] and w.store.summary_progress(mid)
    monkeypatch.setattr(w, "confirm_redo", lambda meeting: True)
    w.store.update(mid, summary=json.dumps(dict(overview="", items=[], topics=[])), status="done")
    w.redo_summary()
    assert started == ["summary"]
    assert not w.store.summary_progress(mid)  # every intermediate step is recomputed
    assert w.store.meeting(mid)["summary"]  # the old summary stays until the new one is ready
    w.timer.stop()
    w.close()
    w.deleteLater()
    qapp.processEvents()
