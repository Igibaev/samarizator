import json
from dataclasses import asdict

import httpx
import pytest

from samarizator.knowledge import export
from samarizator.summary import (
    ChatClient,
    SummaryFormatError,
    SummaryTooLong,
    checked_complete,
    parse_model_summary,
    summarize,
)


def item(ref, text="Решение"):
    return dict(kind="point", text=text, evidence=[ref], owner=None, due=None)


def test_invalid_model_json_is_retried_without_changing_endpoint(meeting):
    _, _, settings = meeting
    requests = []

    def handler(request):
        requests.append(request)
        content = (
            "broken JSON"
            if len(requests) == 1
            else json.dumps(dict(overview="Итог", items=[item(1)], topics=[]))
        )
        return httpx.Response(
            200, json={"choices": [{"finish_reason": "stop", "message": {"content": content}}]}
        )

    client = ChatClient(settings, transport=httpx.MockTransport(handler), key="test-key")
    assert checked_complete(client, "private input", {1})["items"][0]["evidence"] == [1]
    assert len(requests) == 2
    assert all(r.url.host == "company.example" for r in requests)
    assert json.loads(requests[1].content)["messages"][-1]["content"].startswith("private input")


def test_common_corporate_model_schema_variations_are_repaired_without_inventing_evidence():
    content = """Ответ модели:\n```json
    {"abstract":"Итог", "key_points":[{"type":"решение", "content":"Бюджет принят",
    "segment_ids":["7", {"id":8}], "status":"согласовано"}], "themes":["Бюджет"]}
    ```"""
    result = parse_model_summary(content, {7, 8})
    assert result["overview"] == "Итог"
    assert result["items"] == [
        dict(
            type="решение",
            content="Бюджет принят",
            segment_ids=["7", {"id": 8}],
            status="agreed",
            kind="decision",
            text="Бюджет принят",
            evidence=[7, 8],
            owner=None,
            due=None,
        )
    ]
    assert result["topics"] == ["Бюджет"]


def test_schema_repair_never_fabricates_missing_evidence():
    content = json.dumps({"overview": "Итог", "items": [{"text": "Факт"}], "topics": []})
    with pytest.raises(SummaryFormatError, match="несуществующий"):
        parse_model_summary(content, {1})


def test_truncated_block_splits_and_reuses_successful_subblocks(meeting):
    store, mid, settings = meeting
    store.save_chunk(
        mid, 0, [dict(start=i, end=i + 1, speaker="A", text="Существенная мысль " + str(i)) for i in range(4)]
    )

    class Client:
        def __init__(self):
            self.maps = 0

        def complete(self, prompt, allowed):
            from samarizator.summary_prompts import MAP_PROMPT, REVIEW_PROMPT

            if prompt.startswith(REVIEW_PROMPT):
                draft = json.loads(prompt[len(REVIEW_PROMPT):])["draft"]
                return dict(draft, items=[dict(point, draft_ids=[i]) for i, point in enumerate(draft["items"])])
            if prompt.startswith(MAP_PROMPT):
                self.maps += 1
                ids = {r["id"] for r in json.loads(prompt[len(MAP_PROMPT):])["source"]["segments"]}
                if len(ids) > 2:
                    raise SummaryTooLong("обрезала")
                return dict(overview="Подробно", items=[item(i) for i in sorted(ids)], topics=[])
            return dict(overview="Кратко", items=[item(min(allowed))], topics=[])

    client = Client()
    result = summarize(store, mid, settings, client=client)
    assert len(result["detailed"]["items"]) == 4
    assert len(result["brief"]["items"]) == 1 and client.maps == 3
    summarize(store, mid, settings, client=client)
    assert client.maps == 3


def test_old_speaker_flags_are_migrated_without_changing_text(meeting):
    store, mid, _ = meeting
    store.save_chunk(
        mid,
        0,
        [
            dict(
                start=0,
                end=1,
                speaker="Не определён",
                text="Original",
                uncertain=1,
                review="говорящий, граница фрагмента",
            ),
            dict(
                start=2,
                end=3,
                speaker="Иван",
                text="Speaker-only warning",
                uncertain=1,
                review="говорящий",
            ),
        ],
    )
    from samarizator.store import Store

    rows = Store(store.path).segments(mid)
    assert rows[0]["text"] == "Original"
    assert rows[0]["review"] == "граница фрагмента" and rows[0]["uncertain"]
    assert rows[1]["text"] == "Speaker-only warning"
    assert rows[1]["review"] == "" and not rows[1]["uncertain"]


def test_existing_error_visible_after_reopen_and_rerun_is_a_new_record(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    w = Window()
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(mid, 0, [dict(start=0, end=1, speaker="Не определён", text="Original")])
    w.store.save_checkpoint(mid, "asr_complete", 0, True)
    w.store.update(mid, status="error", error="API модели: HTTP 401")
    w.mid = mid
    w.load_detail()
    assert "401" in w.error_detail.text() and w.copy_error_button.isEnabled()
    assert not w.transcribe.isEnabled() and w.rerun_button.isEnabled()
    assert "Собеседник" not in w.info.text()
    w.settings.glossary = "new terms"
    calls = []
    monkeypatch.setattr(w, "start", calls.append)
    w.rerun_transcription()
    assert w.mid != mid and calls == ["transcribe"]
    assert json.loads(w.store.meeting(w.mid)["settings"]) == asdict(w.settings)
    assert w.store.segments(mid)[0]["text"] == "Original"
    assert w.store.checkpoint(w.mid, "asr_complete", 0) is None
    w.timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()


def test_permanent_bad_json_is_bounded(meeting):
    _, _, settings = meeting
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "invalid"}, "finish_reason": "stop"}]}
        )

    client = ChatClient(settings, transport=httpx.MockTransport(handler), key="test")
    with pytest.raises(ValueError, match="JSON"):
        checked_complete(client, "input", {1})
    assert len(calls) == 3


def test_api_length_signal_is_preserved_for_adaptive_split(meeting):
    _, _, settings = meeting
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": '{"over'}, "finish_reason": "length"}]}
        )

    client = ChatClient(settings, transport=httpx.MockTransport(handler), key="test")
    with pytest.raises(SummaryTooLong):
        checked_complete(client, "input", {1})
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [SummaryFormatError, SummaryTooLong])
def test_bad_final_compression_preserves_detailed_summary_and_returns_safe_brief(
    meeting, failure
):
    store, mid, settings = meeting
    store.save_chunk(mid, 0, [dict(start=0, end=1, speaker="Речь", text="Бюджет согласован")])

    class Client:
        def complete(self, prompt, allowed):
            if "Подготовь подробную" in prompt:
                return dict(overview="Обсудили бюджет", items=[item(min(allowed))], topics=["Бюджет"])
            raise failure("Некорректный или обрезанный пункт сводки.")

    result = summarize(store, mid, settings, client=Client())
    assert result["detailed"]["items"][0]["text"] == "Решение"
    assert result["brief"]["items"][0]["evidence"]
    assert "Финальное сжатие" in result["brief"]["generation_warning"]
    store.update(mid, summary=json.dumps(result))
    note = export(store, mid, settings).read_text()
    assert "> Финальное сжатие" in note and "## Подробная сводка" in note
