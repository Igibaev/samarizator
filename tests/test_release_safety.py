import json
import shutil
import struct
import wave
from pathlib import Path

import pytest

from samarizator.media import fingerprint
from samarizator.summary import reconcile_decisions, summarize
from samarizator.worker import retry_uncertain, transcribe


@pytest.mark.parametrize("overlap", [False, True])
def test_short_real_replies_are_never_deleted(meeting, tmp_path, monkeypatch, overlap):
    store, mid, s = meeting
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    s.whisper_model = str(model)
    store.save_checkpoint(mid, "asr-plan", 0, [[0, 30], [30, 60]])
    store.save_chunk(
        mid,
        0,
        [dict(start=28 if overlap else 1, end=31 if overlap else 2, speaker="A", text="Да", uncertain=False)],
    )
    monkeypatch.setattr("samarizator.worker.probe", lambda *a: (60, 1))
    monkeypatch.setattr(
        "samarizator.worker.extract", lambda source, target, *a, **kw: target.write_bytes(b"audio")
    )
    monkeypatch.setattr("samarizator.worker.diagnose", lambda *a: {})
    offset = 2000 if overlap else 7000
    monkeypatch.setattr(
        "samarizator.worker.whisper",
        lambda *a, **kw: dict(transcription=[dict(offsets={"from": offset, "to": offset + 1000}, text="Да")]),
    )
    transcribe(store, mid, s, tmp_path)
    rows = store.segments(mid)
    assert [r["text"] for r in rows] == ["Да", "Да"]
    assert ("возможный повтор" in rows[1]["review"]) == overlap


def test_legacy_channel_provenance_survives_for_safe_retry(meeting, tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg required")
    store, mid, s = meeting
    source = Path(store.meeting(mid)["source"])
    with wave.open(str(source), "wb") as wav:
        wav.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
        wav.writeframes(struct.pack("<hh", 1000, 2000) * (16000 * 10))
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    s.whisper_model = str(model)
    store.update(mid, duration=10, channels=2)
    store.save_checkpoint(mid, "source", 0, fingerprint(source))
    store.save_chunk(
        mid,
        0,
        [
            dict(
                start=5,
                end=7,
                speaker="Канал 2",
                source_channel=1,
                text="old",
                uncertain=1,
                review="говорящий",
            )
        ],
    )

    def decoder(wav, *a, **kw):
        with wave.open(str(wav)) as audio:
            values = struct.unpack("<" + "h" * audio.getnframes(), audio.readframes(audio.getnframes()))
            assert set(values) == {2000}  # real FFmpeg must select right, not average both channels
        return dict(transcription=[dict(offsets={"from": 2000, "to": 4000}, text="right")])

    monkeypatch.setattr("samarizator.worker.whisper", decoder)
    retry_uncertain(store, mid, s, tmp_path)
    row = store.segments(mid)[0]
    assert row["retry_text"] == "right" and row["source_channel"] == 1


def test_retry_rejects_changed_source_before_decode(meeting, tmp_path, monkeypatch):
    store, mid, s = meeting
    store.save_checkpoint(mid, "source", 0, "old-hash")
    monkeypatch.setattr("samarizator.worker.whisper", lambda *a, **k: pytest.fail("must not decode"))
    with pytest.raises(ValueError, match="изменился"):
        retry_uncertain(store, mid, s, tmp_path)


def test_accept_undo_and_stale_proposal_guard(meeting):
    store, mid, _ = meeting
    store.save_chunk(
        mid,
        0,
        [dict(start=1, end=2, speaker="A", text="original", uncertain=1, review="граница фрагмента")],
    )
    sid = store.segments(mid)[0]["id"]
    store.save_retry(mid, sid, "proposed")
    with pytest.raises(ValueError, match="изменился"):
        store.accept_retry(mid, sid, expected_text="stale")
    store.accept_retry(mid, sid, expected_text="proposed")
    assert store.segments(mid)[0]["review"] == "граница фрагмента"
    store.undo_retry(mid, sid)
    assert store.segments(mid)[0]["text"] == "original"
    store.accept_retry(mid, sid)
    store.edit_segment(mid, sid, "manually corrected")
    with pytest.raises(ValueError, match="вручную"):
        store.undo_retry(mid, sid)
    assert store.segments(mid)[0]["text"] == "manually corrected"


def test_many_independent_decisions_do_not_require_lossy_reduction():
    items = [
        dict(
            kind="decision",
            text=f"{i}: " + "Подробности " * 25,
            evidence=[i + 1],
            owner=None,
            due=None,
            status="agreed",
        )
        for i in range(25)
    ]

    class Echo:
        def complete(self, prompt, allowed):
            return dict(overview="", topics=[], items=json.loads(prompt[prompt.index("[{") :]))

    warnings = []
    assert reconcile_decisions(Echo(), items, 4000, on_warning=warnings.append) == items
    assert warnings and "Между блоками" in warnings[0]


def test_failed_optional_resolution_keeps_both_summaries_and_export(meeting):
    from samarizator.knowledge import export

    store, mid, s = meeting
    store.save_chunk(mid, 0, [dict(start=1, end=2, speaker="A", text="Решение принято")])
    sid = store.segments(mid)[0]["id"]
    item = dict(kind="decision", text="Решение", evidence=[sid], owner=None, due=None, status="agreed")

    class Fake:
        def complete(self, prompt, allowed):
            if "ИТОГОВЫЙ список" in prompt:
                raise ValueError("API failure")
            return dict(overview="Итог", topics=[], items=[item])

    result = summarize(store, mid, s, client=Fake())
    assert result["brief"]["items"] and result["detailed"]["items"]
    assert result["detailed"]["resolution_warning"] and not result["detailed"]["resolved"]
    store.update(mid, summary=json.dumps(result))
    content = export(store, mid, s).read_text()
    assert "Проверка решений" in content and "Кратко" in content and "Подробная сводка" in content


def test_recovery_includes_retrying(meeting):
    store, mid, _ = meeting
    store.update(mid, status="retrying")
    store.recover()
    assert store.meeting(mid)["status"] == "interrupted"


def test_page_change_cannot_accept_an_invisible_proposal(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    w = Window()
    source = tmp_path / "source.wav"
    source.write_bytes(b"audio")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(
        mid,
        0,
        [dict(start=i * 2, end=i * 2 + 1, speaker="A", text=f"row {i}", uncertain=1) for i in range(201)],
    )
    rows = w.store.segments(mid)
    w.store.save_retry(mid, rows[0]["id"], "first proposal")
    w.store.save_retry(mid, rows[200]["id"], "last proposal")
    w.mid = mid
    w.load_detail()
    w.table.selectRow(0)
    assert "first proposal" in w.retry_label.text()
    w.turn_page(1)
    assert w.table.currentRow() == -1 and not w.accept_retry_button.isEnabled() and not w.retry_label.text()
    w.accept_retry()
    assert w.store.segments(mid, 200, 1)[0]["text"] == "row 200"
    w.table.selectRow(0)
    assert "last proposal" in w.retry_label.text()
    w.accept_retry()
    assert w.store.segments(mid, 200, 1)[0]["text"] == "last proposal"
    w.timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()
