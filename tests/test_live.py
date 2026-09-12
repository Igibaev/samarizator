import pytest

from samarizator.live import LiveCaptureError, LiveRecorder, parse_avfoundation_audio_devices


class Stdin:
    def __init__(self):
        self.data = b""

    def write(self, data):
        self.data += data

    def flush(self):
        pass


class SuccessfulProcess:
    def __init__(self, args, **kwargs):
        self.args = args
        self.stdin = Stdin()
        self.returncode = None
        self.target = args[-1]
        with open(self.target, "wb") as output:
            output.write(b"RIFF" + b"\0" * 2048)

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self.returncode = 0
        return 0

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9


def test_avfoundation_audio_device_parser_ignores_video_devices():
    output = """
[AVFoundation indev @ 0x1] AVFoundation video devices:
[AVFoundation indev @ 0x1] [0] FaceTime HD Camera
[AVFoundation indev @ 0x1] AVFoundation audio devices:
[AVFoundation indev @ 0x1] [0] MacBook Pro Microphone
[AVFoundation indev @ 0x1] [2] USB Audio
"""
    assert parse_avfoundation_audio_devices(output) == [
        (0, "MacBook Pro Microphone"),
        (2, "USB Audio"),
    ]


def test_live_recorder_streams_to_disk_and_finalizes_wav(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        device_index=2,
        popen_factory=SuccessfulProcess,
    )
    expected = recorder.start()
    assert recorder.recording
    assert recorder.process.args[recorder.process.args.index("-i") + 1] == ":2"
    result = recorder.stop()
    assert result == expected and result.is_file()
    assert recorder.process.stdin.data == b"q\n"
    assert not recorder.partial.exists() and not recorder.log.exists()


def test_live_recorder_reports_microphone_permission_error(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")

    class DeniedProcess(SuccessfulProcess):
        def __init__(self, args, **kwargs):
            super().__init__(args, **kwargs)
            kwargs["stderr"].write(b"Device is not authorized to capture")
            kwargs["stderr"].flush()
            self.returncode = 1

    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        device_index=0,
        popen_factory=DeniedProcess,
    )
    recorder.start()
    with pytest.raises(LiveCaptureError, match="Системные настройки"):
        recorder.stop()
    assert not recorder.partial.exists()


def test_live_button_adds_recording_and_starts_transcription(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    recorded = tmp_path / "live-2026-09-12_14-30-00-12345678.wav"

    class Recorder:
        recording = True
        elapsed = 3

        def start(self):
            return recorded

        def stop(self):
            recorded.write_bytes(b"RIFF" + b"\0" * 2048)
            self.recording = False
            return recorded

    monkeypatch.setattr("samarizator.app.LiveRecorder", Recorder)
    window = Window()
    phases = []
    monkeypatch.setattr(window, "start", phases.append)
    window.toggle_live()
    assert "остановить" in window.live_button.text().lower()
    window.toggle_live()
    assert phases == ["transcribe"]
    assert window.mid and window.store.meeting(window.mid)["source"] == str(recorded)
    assert "начать" in window.live_button.text().lower()
    window.timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()
