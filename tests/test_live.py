import pytest

from samarizator import screencapture
from samarizator.live import (
    NO_LOOPBACK_DEVICE,
    LiveCaptureError,
    LiveRecorder,
    parse_avfoundation_audio_devices,
    resolve_device,
    system_audio_devices,
)


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
        source = "microphone"
        tracks = ("microphone",)

        def __init__(self, **kwargs):
            self.kwargs = kwargs

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
    window.settings.live_source = "both"
    window.settings.live_system_device = "BlackHole 2ch"
    window.toggle_live()
    assert window.live_recorder.kwargs == dict(
        source="both",
        microphone_device="",
        system_device="BlackHole 2ch",
        system_backend="screencapturekit",
    )
    assert "остановить" in window.live_button.text().lower()
    window.toggle_live()
    assert phases == ["transcribe"]
    assert window.mid and window.store.meeting(window.mid)["source"] == str(recorded)
    assert "начать" in window.live_button.text().lower()
    window.timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


DEVICES = [(0, "MacBook Pro Microphone"), (1, "BlackHole 2ch"), (2, "USB Audio")]


def stub_devices(monkeypatch, devices=DEVICES):
    monkeypatch.setattr("samarizator.live.audio_devices", lambda ffmpeg: list(devices))


def test_system_audio_devices_match_loopback_drivers_only():
    assert system_audio_devices(DEVICES) == [(1, "BlackHole 2ch")]
    assert system_audio_devices([(0, "Aggregate Device"), (1, "Loopback Audio")]) == [
        (0, "Aggregate Device"),
        (1, "Loopback Audio"),
    ]


def test_resolve_device_prefers_saved_name_over_index():
    assert resolve_device(DEVICES, "USB Audio", "microphone") == (2, "USB Audio")
    assert resolve_device(DEVICES, "", "system") == (1, "BlackHole 2ch")
    assert resolve_device(DEVICES, "", "microphone") == (0, "MacBook Pro Microphone")


def test_resolve_device_reports_missing_name_with_available_inputs():
    with pytest.raises(LiveCaptureError, match="USB Headset"):
        resolve_device(DEVICES, "USB Headset", "microphone")


def test_missing_loopback_device_explains_setup_without_offering_a_bypass():
    with pytest.raises(LiveCaptureError) as error:
        resolve_device([(0, "MacBook Pro Microphone")], "", "system")
    assert str(error.value) == NO_LOOPBACK_DEVICE
    assert "обход" in NO_LOOPBACK_DEVICE.casefold()


def test_system_only_capture_records_the_loopback_input(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    stub_devices(monkeypatch)
    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        popen_factory=SuccessfulProcess,
        source="system",
        system_backend="device",
    )
    recorder.start()
    args = recorder.process.args
    assert args.count("-i") == 1 and args[args.index("-i") + 1] == ":1"
    assert recorder.tracks == ("system",)
    assert recorder.stop().is_file()


def test_dual_capture_merges_microphone_and_system_into_two_channels(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    stub_devices(monkeypatch)
    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        popen_factory=SuccessfulProcess,
        source="both",
        system_backend="device",
        microphone_device="USB Audio",
        system_device="BlackHole 2ch",
    )
    recorder.start()
    args = recorder.process.args
    assert [args[i + 1] for i, a in enumerate(args) if a == "-i"] == [":2", ":1"]
    graph = args[args.index("-filter_complex") + 1]
    assert graph.endswith("[t0][t1]amerge=inputs=2[live]")
    assert graph.count("aresample=async=1000:first_pts=0") == 2
    assert args[args.index("-ac") + 1] == "2"
    assert recorder.tracks == ("microphone", "system")
    assert recorder.stop().is_file()


def test_dual_capture_rejects_the_same_device_on_both_tracks(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    stub_devices(monkeypatch)
    with pytest.raises(LiveCaptureError, match="одно устройство"):
        LiveRecorder(
            folder=tmp_path,
            ffmpeg="/usr/bin/ffmpeg",
            popen_factory=SuccessfulProcess,
            source="both",
            system_backend="device",
            microphone_device="BlackHole 2ch",
            system_device="BlackHole 2ch",
        )


def test_empty_system_recording_points_at_the_output_device(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    stub_devices(monkeypatch)

    class SilentProcess(SuccessfulProcess):
        def __init__(self, args, **kwargs):
            super().__init__(args, **kwargs)
            with open(self.target, "wb") as output:
                output.write(b"RIFF")

    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        popen_factory=SilentProcess,
        source="system",
        system_backend="device",
    )
    recorder.start()
    with pytest.raises(LiveCaptureError, match="выходом звука"):
        recorder.stop()


def test_settings_dialog_exposes_live_source_and_devices(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    from PySide6.QtWidgets import QApplication

    from samarizator.app import SettingsDialog
    from samarizator.config import Settings

    monkeypatch.setattr("samarizator.app.audio_devices", lambda ffmpeg: list(DEVICES))
    app = QApplication.instance() or QApplication([])
    settings = Settings(live_source="system", live_system_device="BlackHole 2ch")
    dialog = SettingsDialog(settings)
    assert dialog.fields["live_source"].currentData() == "system"
    assert dialog.fields["live_system_device"].currentData() == "BlackHole 2ch"
    # Only loopback inputs may be offered as the system source.
    system_box = dialog.fields["live_system_device"]
    assert [system_box.itemData(i) for i in range(system_box.count())] == ["", "BlackHole 2ch"]
    microphone_box = dialog.fields["live_microphone_device"]
    assert [microphone_box.itemData(i) for i in range(microphone_box.count())] == [
        "",
        "MacBook Pro Microphone",
        "BlackHole 2ch",
        "USB Audio",
    ]
    dialog.deleteLater()
    app.processEvents()


def test_settings_reject_an_unknown_live_source():
    from samarizator.config import Settings

    with pytest.raises(ValueError, match="Источник live-записи"):
        Settings(live_source="speakers").validate()


class HelperProcess:
    """Stands in for the ScreenCaptureKit helper: JSON events on stderr, PCM on the pipe."""

    def __init__(self, args, events=b'{"event": "started"}\n', code=None, **kwargs):
        self.args = args
        self.stdout_fd = kwargs.get("stdout")
        self.returncode = code
        self._code = code
        kwargs["stderr"].write(events)
        kwargs["stderr"].flush()

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = 0 if self._code is None else self._code

    def wait(self, timeout=None):
        self.returncode = 0 if self._code is None else self._code
        return self.returncode

    def kill(self):
        self.returncode = -9


def native_popen(helper_events=b'{"event": "started"}\n', helper_code=None, seen=None):
    """Dispatch by executable: FFmpeg writes the WAV, the helper only reports events."""

    def factory(args, **kwargs):
        if seen is not None:
            seen.append((args, kwargs))
        if args[0].endswith("system-audio"):
            return HelperProcess(args, events=helper_events, code=helper_code, **kwargs)
        return SuccessfulProcess(args, **kwargs)

    return factory


def available_helper(monkeypatch, tmp_path):
    binary = tmp_path / "samarizator-system-audio"
    binary.write_bytes(b"#!/bin/sh\n")
    monkeypatch.setattr(
        screencapture, "status", lambda b=None: dict(available=True, reason="", message="ok")
    )
    return binary


def test_native_capture_feeds_screencapturekit_audio_into_ffmpeg(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    binary = available_helper(monkeypatch, tmp_path)
    seen = []
    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        popen_factory=native_popen(seen=seen),
        source="system",
        helper=binary,
    )
    assert recorder.native_capture and recorder.tracks == ("system",)
    recorder.start()
    (ffmpeg_args, ffmpeg_kwargs), (helper_args, helper_kwargs) = seen
    read_fd = ffmpeg_kwargs["pass_fds"][0]
    assert ffmpeg_args[ffmpeg_args.index("-i") + 1] == f"pipe:{read_fd}"
    assert "avfoundation" not in ffmpeg_args
    # The declared format must match what the helper writes, byte for byte.
    assert ffmpeg_args[ffmpeg_args.index("-f") + 1] == screencapture.SAMPLE_FORMAT
    assert ffmpeg_args[ffmpeg_args.index("-ar") + 1] == str(screencapture.SAMPLE_RATE)
    assert helper_args == [str(binary), "capture"]
    assert helper_kwargs["stdout"] != read_fd  # the helper holds the writing end
    assert recorder.stop().is_file()


def test_native_and_microphone_share_one_stereo_timeline(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    binary = available_helper(monkeypatch, tmp_path)
    stub_devices(monkeypatch)
    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        popen_factory=native_popen(),
        source="both",
        helper=binary,
    )
    recorder.start()
    args = recorder.process.args
    inputs = [args[i + 1] for i, a in enumerate(args) if a == "-i"]
    assert inputs[0] == ":0" and inputs[1].startswith("pipe:")
    assert recorder.tracks == ("microphone", "system")
    assert args[args.index("-filter_complex") + 1].endswith("[t0][t1]amerge=inputs=2[live]")
    assert recorder.stop().is_file()


def test_native_capture_refuses_to_start_without_the_permission(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    monkeypatch.setattr(
        screencapture,
        "status",
        lambda b=None: dict(available=False, reason="permission", message=screencapture.PERMISSION_HELP),
    )
    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        popen_factory=native_popen(),
        source="system",
        helper=tmp_path / "helper",
    )
    with pytest.raises(LiveCaptureError, match="Запись экрана и системного звука"):
        recorder.start()
    assert recorder.process is None and not list(tmp_path.glob("live-*.wav"))


def test_helper_failure_is_reported_instead_of_a_truncated_recording(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    binary = available_helper(monkeypatch, tmp_path)
    events = b'{"event": "error", "code": "permission", "message": "denied"}\n'
    recorder = LiveRecorder(
        folder=tmp_path,
        ffmpeg="/usr/bin/ffmpeg",
        popen_factory=native_popen(helper_events=events, helper_code=2),
        source="system",
        helper=binary,
    )
    recorder.start()
    with pytest.raises(LiveCaptureError, match="Запись экрана и системного звука"):
        recorder.stop()
    assert not recorder.path.exists() and not recorder.partial.exists()
    # The helper's report is kept: a failed session must stay diagnosable.
    assert "permission" in recorder.helper_log.read_text()


def test_helper_status_reads_the_probe_answer(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    binary = tmp_path / "samarizator-system-audio"
    binary.write_bytes(b"#!/bin/sh\n")

    class Result:
        def __init__(self, stdout):
            self.stdout, self.returncode = stdout, 0

    granted = screencapture.status(
        binary, runner=lambda *a, **k: Result('{"permission": "granted", "supported": true}')
    )
    assert granted["available"]
    denied = screencapture.status(
        binary, runner=lambda *a, **k: Result('{"permission": "denied", "supported": false}')
    )
    assert not denied["available"] and denied["reason"] == "permission"
    broken = screencapture.status(binary, runner=lambda *a, **k: Result("not json"))
    assert not broken["available"] and broken["reason"] == "probe-failed"


def test_missing_helper_binary_explains_how_to_build_it(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    report = screencapture.status(tmp_path / "absent")
    assert not report["available"] and report["reason"] == "not-built"
    assert "start.sh" in report["message"]


def test_helper_exit_without_an_event_still_produces_a_message():
    assert screencapture.failure_message(0, "") == ""
    assert "журнал" in screencapture.failure_message(3, "не json")


def test_empty_native_recording_separates_a_silent_stream_from_a_lost_one(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    binary = available_helper(monkeypatch, tmp_path)

    class SilentFFmpeg(SuccessfulProcess):
        def __init__(self, args, **kwargs):
            super().__init__(args, **kwargs)
            with open(self.target, "wb") as output:
                output.write(b"RIFF")

    def recorder_for(events):
        def factory(args, **kwargs):
            if args[0].endswith("system-audio"):
                return HelperProcess(args, events=events, **kwargs)
            return SilentFFmpeg(args, **kwargs)

        recorder = LiveRecorder(
            folder=tmp_path,
            ffmpeg="/usr/bin/ffmpeg",
            popen_factory=factory,
            source="system",
            helper=binary,
        )
        recorder.start()
        return recorder

    nothing = recorder_for(
        b'{"event": "started"}\n{"event": "stopped", "buffers": 0, "bytes": 0}\n'
    )
    with pytest.raises(LiveCaptureError, match="не отдал ни одного аудиобуфера"):
        nothing.stop()

    lost = recorder_for(
        b'{"event": "started"}\n{"event": "stopped", "buffers": 40, "bytes": 400000}\n'
    )
    with pytest.raises(LiveCaptureError, match="потерялось между helper"):
        lost.stop()


def test_capture_stats_track_the_highest_reported_counters():
    log = (
        '{"event": "started"}\n'
        '{"event": "progress", "buffers": 10, "bytes": 1000, "frames": 250, "peak": 0.4}\n'
        '{"event": "stopped", "buffers": 25, "bytes": 2500, "frames": 625, "peak": 0.9}\n'
    )
    assert screencapture.capture_stats(log) == dict(
        started=True, buffers=25, bytes=2500, frames=625, peak=0.9
    )
    # An error event carries its progress in a nested object.
    failed = '{"event": "error", "code": "pipe-closed", "progress": {"buffers": 3, "bytes": 30}}\n'
    assert screencapture.capture_stats(failed)["bytes"] == 30
