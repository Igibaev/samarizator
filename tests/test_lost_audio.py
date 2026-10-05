"""A record whose audio is gone mid-recognition: find the file or summarise what was recognised."""

import pytest

SPEECH = [
    dict(start=0, end=4, speaker="Речь", text="Бюджет утвердили."),
    dict(start=90, end=95, speaker="Речь", text="Договор пришлём до пятницы."),
]


def test_finishing_at_the_recognised_part(meeting):
    store, mid, settings = meeting
    with pytest.raises(ValueError, match="Нет распознанного текста"):
        store.finish_partial(mid)
    store.save_chunk(mid, 0, SPEECH)
    store.update(mid, status="error", error="Исходный файл перемещён.")
    assert store.finish_partial(mid) == 95
    assert store.checkpoint(mid, "asr_complete", 0) and store.checkpoint(mid, "asr-partial", 0) == 95
    record = store.meeting(mid)
    assert record["status"] == "review" and record["error"] is None


def test_a_crash_while_recording_leaves_a_record_that_can_continue(meeting):
    store, mid, settings = meeting
    store.update(mid, status="recording")
    store.recover()
    assert store.meeting(mid)["status"] == "interrupted"


@pytest.fixture
def lost(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window
    from samarizator.media import fingerprint

    app = QApplication.instance() or QApplication([])
    w = Window()
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"audio of the meeting")
    mid = w.store.create(source, w.settings)
    w.store.save_checkpoint(mid, "source", 0, fingerprint(source))
    w.store.save_chunk(mid, 0, SPEECH)
    moved = tmp_path / "elsewhere.wav"
    source.replace(moved)
    w.store.update(mid, status="error", error="Исходный файл перемещён. Верните его по прежнему пути.")
    w.mid = mid
    w.refresh_list()
    w.load_detail()
    w.show()
    app.processEvents()
    yield w, mid, moved, app
    w.timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()


def test_the_banner_offers_both_ways_out(lost):
    w, mid, moved, app = lost
    assert w.error_box.isVisibleTo(w)
    # The page explains and offers both steps; in the transcript the banner does.
    assert w.stack.currentWidget() is w.empty and "Звук записи не найден" in w.empty.title.text()
    assert "00:01:35" in w.empty.body.text() and not w.locate_button.isVisibleTo(w)
    w.set_view("transcript")
    app.processEvents()
    assert w.locate_button.isVisibleTo(w) and w.partial_button.isVisibleTo(w)
    assert w.partial_button.isEnabled() and not w.transcribe.isVisibleTo(w)
    assert not w.summarize.isVisibleTo(w)


def test_summary_by_the_recognised_part(lost, monkeypatch):
    from samarizator.app import Window

    w, mid, moved, app = lost
    monkeypatch.setattr(Window, "confirm_partial", lambda self, end: False)
    assert w.finish_partial() is False and not w.store.checkpoint(mid, "asr_complete", 0)
    monkeypatch.setattr(Window, "confirm_partial", lambda self, end: True)
    assert w.finish_partial() is True
    app.processEvents()
    assert w.summarize.isVisibleTo(w) and w.summarize.isEnabled()
    assert not w.error_box.isVisibleTo(w) and not w.partial_button.isVisibleTo(w)
    assert "00:01:35" in w.progress.text()


def test_the_moved_file_is_found_and_recognition_continues(lost, tmp_path, monkeypatch):
    from samarizator.app import Window

    w, mid, moved, app = lost
    phases = []
    monkeypatch.setattr(w, "start", phases.append)
    warnings = []
    monkeypatch.setattr("samarizator.app.QMessageBox.warning", lambda *args: warnings.append(args[1]))
    other = tmp_path / "other.wav"
    other.write_bytes(b"a different recording")
    monkeypatch.setattr(Window, "choose_source", lambda self: str(other))
    assert w.locate_source() is False and warnings == ["Другой файл"] and phases == []
    monkeypatch.setattr(Window, "choose_source", lambda self: str(moved))
    assert w.locate_source() is True and phases == ["transcribe"]
    assert w.store.meeting(mid)["source"] == str(moved.resolve())


def test_a_failed_live_recording_keeps_its_audio(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window
    from samarizator.live import LiveCaptureError

    app = QApplication.instance() or QApplication([])
    recorded = tmp_path / "live.wav"
    partial = tmp_path / "live.partial.wav"
    saved = []

    class Recorder:
        recording = True
        elapsed = 3
        source = "system"
        tracks = ("system",)
        inputs = ()

        def __init__(self, **kwargs):
            self.partial = partial

        def start(self):
            partial.write_bytes(b"RIFF" + b"\0" * 2048)
            return recorded

        def stop(self):
            self.recording = False
            if saved:
                partial.replace(recorded)
                raise LiveCaptureError("Захват прервался.\nЗвук сохранён.", saved=recorded)
            partial.unlink()
            raise LiveCaptureError("Захват прервался.")

    monkeypatch.setattr("samarizator.app.LiveRecorder", Recorder)
    warnings = []
    monkeypatch.setattr("samarizator.app.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    w = Window()
    phases = []
    monkeypatch.setattr(w, "start", phases.append)
    monkeypatch.setattr(w, "start_catchup", lambda: None)

    saved.append(True)
    w.toggle_live()
    mid = w.mid
    w.toggle_live()
    assert w.store.meeting(mid)["source"] == str(recorded) and phases == ["transcribe"]
    assert warnings and "Звук сохранён" in warnings[0]

    # No audio at all, but catch-up recognised the beginning: the text stays, honestly marked.
    saved.clear()
    phases.clear()
    w.toggle_live()
    mid = w.mid
    w.store.save_chunk(mid, 0, SPEECH)
    w.toggle_live()
    record = w.store.meeting(mid)
    assert phases == [] and record["status"] == "interrupted" and "звук не сохранился" in record["error"]
    w.mid = mid
    w.set_view("transcript")
    w.load_detail()
    w.controls()
    assert w.partial_button.isVisibleTo(w)
    assert w.source_lost(record)
    w.timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()
