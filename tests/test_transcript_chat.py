"""Questions about the transcript itself: whole when it fits, the matching lines when not."""

from samarizator import qa
from samarizator.config import Settings


def rows(texts, uncertain=()):
    return [
        dict(id=100 + i, start=i * 30.0, end=i * 30.0 + 25, text=text, uncertain=int(i in uncertain))
        for i, text in enumerate(texts)
    ]


MEETING = rows(
    [
        "Добрый день, начинаем.",
        "Бюджет на второй квартал — семнадцать миллионов.",
        "А если поставщик задержит поставку?",
        "Тогда сдвигаем запуск на декабрь.",
        "Эээ.",
        "Отчёт подготовит Ирина к пятнице.",
    ],
    uncertain={5},
)


class Client:
    def __init__(self, *answers, n_ctx=20480):
        self.answers = list(answers)
        self.prompts = []
        self.n_ctx = n_ctx

    def complete_json(self, system, prompt, max_tokens):
        self.prompts.append((system, prompt))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def test_lines_are_numbered_timed_and_tidied():
    lines = qa.transcript_lines(MEETING)
    texts = [line[1] for line in lines]
    assert texts[0] == "[1] 00:00:00 Добрый день, начинаем."
    assert texts[1].startswith("[2] 00:00:30 Бюджет")
    # Hesitation-only rows disappear; numbering stays continuous and stable.
    assert len(lines) == 5 and texts[4] == "[5] 00:02:30 (?) Отчёт подготовит Ирина к пятнице."
    assert [line[2]["id"] for line in lines] == [100, 101, 102, 103, 105]


def test_whole_transcript_answers_with_line_references_grouped_into_sources():
    client = Client(
        dict(found=True, answer="Сдвигают запуск на декабрь, если поставщик задержит [3][4].", refs=[3, 4]),
        dict(found=True, answer="Бюджет — 17 млн [2].", refs=[2]),
    )
    first = qa.ask_transcript(client, MEETING, "Что будет при задержке поставки?", title="Планёрка")
    assert first["found"] and first["source"] == "transcript" and not first["partial"]
    assert first["sources"] == [
        dict(
            n=3,
            text="А если поставщик задержит поставку? … Тогда сдвигаем запуск на декабрь.",
            evidence=[102, 103],
        )
    ]
    system, prompt = client.prompts[0]
    assert "СТРОГО по её расшифровке" in system and "Говорящие не подписаны" in system
    assert prompt.startswith("РАСШИФРОВКА записи «Планёрка»:\n[1] 00:00:00")
    second = qa.ask_transcript(client, MEETING, "Какой бюджет?", [first], title="Планёрка")
    assert second["sources"][0]["evidence"] == [101]
    # The transcript is the same beginning of both requests: the server reads it only once.
    head = prompt.split("ПРЕДЫДУЩИЕ ВОПРОСЫ")[0]
    assert client.prompts[1][1].startswith(head)


def test_answers_without_valid_references_are_never_shown():
    for answer in [
        dict(found=True, answer="Бюджет 17 млн.", refs=[]),
        dict(found=True, answer="Бюджет 17 млн [9].", refs=[9]),
        dict(found=True, answer="Бюджет [2] и [42].", refs=[2]),
        "не JSON",
    ]:
        result = qa.ask_transcript(Client(answer, answer, answer), MEETING, "Бюджет?")
        assert result == dict(
            q="Бюджет?",
            source="transcript",
            partial=False,
            a=qa.TRANSCRIPT_NOT_FOUND,
            found=False,
            sources=[],
        )
    refused = qa.ask_transcript(Client(dict(found=False, answer="", refs=[])), MEETING, "Как погода?")
    assert not refused["found"] and refused["a"] == qa.TRANSCRIPT_NOT_FOUND


def test_a_long_transcript_is_searched_and_only_shown_lines_can_be_cited():
    texts = [f"Обсуждаем рекламу, вариант {i}." for i in range(400)]
    texts[250] = "Поставщик картона поднял цену на девять процентов."
    texts[100] = "Поставщик обещал скидку."
    long = rows(texts)
    client = Client(dict(found=True, answer="На 9% [251].", refs=[251]), n_ctx=8192)
    result = qa.ask_transcript(client, long, "Насколько поставщик поднял цену?")
    prompt = client.prompts[0][1]
    assert result["found"] and result["partial"] and result["sources"][0]["evidence"] == [350]
    assert "фрагменты, найденные по словам вопроса" in prompt
    # The match and its neighbours, with gaps marked; not the whole recording.
    assert "[249]" in prompt and "[253]" in prompt and "[400]" not in prompt.split("ВОПРОС:")[0]
    assert "\n…\n" in prompt and "[101]" in prompt and "[200]" not in prompt
    unseen = dict(found=True, answer="Реклама [5].", refs=[5])
    hidden = Client(unseen, unseen, unseen, n_ctx=8192)
    assert not qa.ask_transcript(hidden, long, "Насколько поставщик поднял цену?")["found"]


def test_a_request_too_long_for_the_model_is_retried_with_fewer_lines():
    from samarizator.summary import SummaryTooLong

    texts = [f"Реплика номер {i} про бюджет." for i in range(300)]
    client = Client(SummaryTooLong("context"), dict(found=True, answer="Да [1].", refs=[1]), n_ctx=20480)
    result = qa.ask_transcript(client, rows(texts), "Что про бюджет?")
    assert result["found"] and len(client.prompts) == 2
    assert len(client.prompts[1][1]) < len(client.prompts[0][1]) and result["partial"]


def test_the_question_window_grows_only_as_far_as_memory_allows(tmp_path):
    from samarizator import local_llm

    model = tmp_path / "m.gguf"
    with model.open("wb") as f:
        f.truncate(6 * 1024**3)
    settings = Settings(llm_model=str(model))
    base = local_llm.context_tokens(settings)
    assert local_llm.chat_context_tokens(settings, 1000, total_gb=64) == base
    assert local_llm.chat_context_tokens(settings, 50000, total_gb=64) == 50176
    assert local_llm.chat_context_tokens(settings, 10**6, total_gb=64) == local_llm.CHAT_MAX_CONTEXT
    small = local_llm.chat_context_tokens(settings, 50000, total_gb=12)
    assert base <= small < 50000


def test_chat_engine_restarts_with_a_larger_window_only_when_needed(monkeypatch, tmp_path, qapp):
    from samarizator import chat, local_llm

    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    started = []

    class Server:
        def __init__(self, settings, work, progress, slots=None, ctx_size=None):
            self.ctx_size, self.url, self.key = ctx_size, "http://127.0.0.1:9", "k"
            started.append(ctx_size)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(local_llm, "LlamaServer", Server)
    monkeypatch.setattr(local_llm, "check_fits", lambda settings: None)
    settings = Settings(llm_model=str(tmp_path / "m.gguf"))
    monkeypatch.setattr(Settings, "validate", lambda self, llm=False: None)
    engine = chat.ChatEngine()
    with engine.lock:
        engine.client(settings, print).close()
        engine.client(settings, print, 8192).close()  # smaller than the summary window: reused
        assert len(started) == 1
        client = engine.client(settings, print, 40960)
        assert started[-1] == 40960 and client.n_ctx == 40960
        client.close()
        engine.client(settings, print).close()  # a summary question fits the larger window
    assert len(started) == 2
    engine.read = 7
    engine.stop()
    assert engine.read is None


def test_panel_asks_by_summary_or_transcript(qapp):
    from samarizator.chat import SUGGESTIONS, SUMMARY, TRANSCRIPT, ChatPanel

    panel = ChatPanel(lambda mid, ids: [])
    asked = []
    panel.ask.connect(lambda text, mode: asked.append((text, mode)))
    panel.show_meeting(1, {}, [])
    assert panel.mode == SUMMARY
    panel.field.setText("Что решили?")
    panel.send()
    panel.source.select(TRANSCRIPT, emit=True)
    assert "по расшифровке" in panel.note.text()
    panel.field.setText("Какие числа звучали?")
    panel.send()
    assert asked == [("Что решили?", SUMMARY), ("Какие числа звучали?", TRANSCRIPT)]
    # Without a summary only the transcript can be asked; its suggestions are offered.
    panel.source.select(SUMMARY)
    panel.show_meeting(2, {}, [], has_summary=False)
    assert panel.mode == TRANSCRIPT and not panel.source.buttons[SUMMARY].isEnabled()
    texts = [b.text() for b in panel.findChildren(type(panel.send_button)) if b.objectName() == "suggestion"]
    assert texts == SUGGESTIONS[TRANSCRIPT]
    panel.add_answer(dict(a="Бюджет [2].", found=True, source=TRANSCRIPT, partial=True, sources=[]))
    from PySide6.QtWidgets import QLabel

    captions = [label.text() for label in panel.findChildren(QLabel)]
    assert any("модель читала найденные по словам фрагменты" in text for text in captions)
    panel.deleteLater()
