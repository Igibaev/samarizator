import json
from dataclasses import asdict

import httpx
import pytest

from samarizator.summary import ChatClient, SummaryTooLong, checked_complete, summarize


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


def test_truncated_block_splits_and_reuses_successful_subblocks(meeting):
    store, mid, settings = meeting
    store.save_chunk(
        mid, 0, [dict(start=i, end=i + 1, speaker="A", text="Существенная мысль " + str(i)) for i in range(4)]
    )

    class Client:
        def __init__(self):
            self.maps = 0

        def complete(self, prompt, allowed):
            if "Подготовь подробную" in prompt:
                self.maps += 1
                if len(allowed) > 2:
                    raise SummaryTooLong("обрезала")
                return dict(overview="Подробно", items=[item(i) for i in sorted(allowed)], topics=[])
            return dict(overview="Кратко", items=[item(min(allowed))], topics=[])

    client = Client()
    result = summarize(store, mid, settings, client=client)
    assert len(result["detailed"]["items"]) == 4
    assert len(result["brief"]["items"]) == 1 and client.maps == 3
    summarize(store, mid, settings, client=client)
    assert client.maps == 3


def test_repair_speakers_keeps_text_manual_names_and_non_speaker_warnings(meeting):
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
            dict(start=2, end=3, speaker="Иван", text="Manually named", uncertain=0),
        ],
    )
    assert store.assign_unknown_speakers(mid, [dict(start=0, end=5, speaker="Собеседник 1")]) == 1
    rows = store.segments(mid)
    assert rows[0]["text"] == "Original" and rows[0]["speaker"] == "Собеседник 1"
    assert rows[0]["review"] == "граница фрагмента" and rows[0]["uncertain"]
    assert rows[1]["speaker"] == "Иван" and rows[1]["text"] == "Manually named"


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
    assert "Не определены 1" in w.info.text()
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
