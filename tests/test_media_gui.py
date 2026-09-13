import json
import shutil
import subprocess
import wave

import pytest

from samarizator.media import extract, probe


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_real_ffmpeg_stereo_extraction(tmp_path):
    source = tmp_path / "stereo.wav"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "aevalsrc=0.5*sin(440*2*PI*t)|0:d=3:s=16000",
            str(source),
        ],
        check=True,
    )
    duration, channels = probe(source, tmp_path)
    assert duration == 3
    assert channels == 2
    left, right = tmp_path / "left.wav", tmp_path / "right.wav"
    extract(source, left, tmp_path, start=1, duration=1, channel=0)
    extract(source, right, tmp_path, start=1, duration=1, channel=1)
    with wave.open(str(left)) as f:
        assert f.getnchannels() == 1
        assert f.getnframes() == 16000
        assert any(f.readframes(16000))
    with wave.open(str(right)) as f:
        assert not any(f.readframes(16000))


_qt_app = None


def test_gui_constructs_and_shows_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "app"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import SettingsDialog, Window

    global _qt_app
    _qt_app = QApplication.instance() or QApplication([])
    app = _qt_app
    w = Window()
    source = tmp_path / "demo.wav"
    source.write_bytes(b"demo")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(
        mid,
        0,
        [
            dict(start=0, end=1, speaker="A", text="Тест", uncertain=True),
            dict(start=1, end=2, speaker="A", text="Ясно", uncertain=False),
        ],
    )
    w.mid = mid
    w.refresh_list()
    assert w.table.rowCount() == 2
    assert w.list.count() == 1
    w.uncertain_only.setChecked(True)
    assert w.table.rowCount() == 1
    assert w.table.item(0, 1).text() == "Тест"
    w.uncertain_only.setChecked(False)
    assert w.table.rowCount() == 2
    from PySide6.QtWidgets import QMessageBox

    w.table.setCurrentCell(0, 0)
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)
    monkeypatch.setattr("samarizator.app.shutil.which", lambda name: None)
    w.play_segment()
    assert w.player_proc is None  # no ffplay found, nothing started

    class FakeProc:
        def __init__(self):
            self.stopped = False

        def poll(self):
            return None if not self.stopped else 0

        def terminate(self):
            self.stopped = True

    popen_calls = []
    monkeypatch.setattr("samarizator.app.shutil.which", lambda name: "/usr/bin/ffplay")
    monkeypatch.setattr(
        "samarizator.app.subprocess.Popen", lambda args, **kw: popen_calls.append(args) or FakeProc()
    )
    w.play_segment()
    assert popen_calls and popen_calls[0][0] == "/usr/bin/ffplay"
    assert "-ss" in popen_calls[0] and "-t" in popen_calls[0]
    assert w.player_proc is not None and w.playback_label.text()
    w.stop_playback()
    assert w.player_proc is None and w.playback_label.text() == ""
    sid = w.store.segments(mid)[0]["id"]
    item = dict(kind="point", text="Точная сумма 17 млн", evidence=[sid], owner=None, due=None)
    resolved_item = dict(
        kind="decision", text="Бюджет утверждён (итог)", evidence=[sid], owner=None, due=None, status="agreed"
    )
    brief = dict(overview="Короткий итог", items=[], topics=[])
    detailed = dict(overview="Детали обсуждения", items=[item], topics=["Бюджет"], resolved=[resolved_item])
    w.store.update(mid, summary=json.dumps(dict(**brief, brief=brief, detailed=detailed)))
    w.load_detail()
    assert "Короткий итог" in w.summary.toPlainText()
    assert "17 млн" not in w.summary.toPlainText()
    assert "17 млн" in w.detailed_summary.toPlainText()
    assert "▶" in w.detailed_summary.toPlainText()
    assert "00:00:00" not in w.detailed_summary.toPlainText()
    assert "Бюджет утверждён (итог)" in w.resolved_summary.toPlainText()
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    w.delete_meeting()
    assert w.list.count() == 0
    assert w.table.rowCount() == 0
    assert w.mid is None
    with pytest.raises(ValueError):
        w.store.meeting(mid)
    dialog = SettingsDialog(w.settings)
    assert w.table.columnCount() == 3
    assert w.table.horizontalHeaderItem(1).text() == "Текст"
    assert not hasattr(w, "speakers_button")
    assert not {"diarization", "speakers", "segmentation_model", "embedding_model"} & dialog.fields.keys()
    assert dialog.fields["memory_gb"].value() == 4
    old_model = dialog.fields["whisper_model"].text()
    dialog.quality_profile()
    assert dialog.fields["memory_gb"].value() == 16
    assert dialog.fields["vad"].isChecked()
    assert dialog.fields["whisper_model"].text() == old_model
    dialog.fields["base_url"].setText("https://typed-by-hand.example/v1")
    dialog.pick_provider(dialog.provider.findData("https://openrouter.ai/api/v1"))
    assert dialog.fields["base_url"].text() == "https://openrouter.ai/api/v1"
    dialog.pick_provider(dialog.provider.findData(""))
    assert dialog.fields["base_url"].text() == "https://openrouter.ai/api/v1"
    from PySide6.QtCore import QCoreApplication, QEvent

    w.timer.stop()
    dialog.close()
    dialog.deleteLater()
    w.close()
    w.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()
