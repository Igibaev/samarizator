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


def test_a_record_left_recording_by_an_older_version_can_continue(meeting):
    # Versions before allowed-by-security had live recording; their database may keep the status.
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
