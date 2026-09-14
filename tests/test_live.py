import shutil
import time

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
    # The file has to be readable while it grows, for catch-up recognition.
    assert recorder.process.args[recorder.process.args.index("-flush_packets") + 1] == "1"
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


def test_live_button_records_then_hands_over_to_recognition(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    recorded = tmp_path / "live-2026-09-12_14-30-00-12345678.wav"
    partial = tmp_path / "live-2026-09-12_14-30-00-12345678.partial.wav"

    class Recorder:
        recording = True
        elapsed = 3
        source = "microphone"
        tracks = ("microphone",)
        inputs = ()

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.partial = partial

        def start(self):
            partial.write_bytes(b"RIFF" + b"\0" * 2048)
            return recorded

        def stop(self):
            partial.replace(recorded)
            self.recording = False
            return recorded

    monkeypatch.setattr("samarizator.app.LiveRecorder", Recorder)
    window = Window()
    phases, catchups = [], []
    monkeypatch.setattr(window, "start", phases.append)
    monkeypatch.setattr(window, "start_catchup", lambda: catchups.append(window.mid))

    window.toggle_live()
    # Recognition needs a meeting from the first second, pointed at the growing file.
    assert window.mid and catchups == [window.mid]
    assert window.store.meeting(window.mid)["source"] == str(partial)
    assert window.store.meeting(window.mid)["status"] == "recording"
    assert "остановить" in window.live_button.text().lower()

    window.toggle_live()
    assert phases == ["transcribe"]
    assert window.store.meeting(window.mid)["source"] == str(recorded)
    assert window.store.checkpoint(window.mid, "live-stopped", 0)
    assert "начать" in window.live_button.text().lower()
    window.timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_stop_waits_for_catchup_before_the_closing_pass(tmp_path, monkeypatch):
    """The closing pass must not start while catch-up still holds a Whisper process."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    recorded = tmp_path / "live-2026-09-12_15-00-00-abcdef12.wav"
    partial = tmp_path / "live-2026-09-12_15-00-00-abcdef12.partial.wav"

    class Recorder:
        recording = True
        elapsed = 1
        source = "microphone"
        tracks = ("microphone",)
        inputs = ()

        def __init__(self, **kwargs):
            self.partial = partial

        def start(self):
            partial.write_bytes(b"RIFF" + b"\0" * 2048)
            return recorded

        def stop(self):
            partial.replace(recorded)
            self.recording = False
            return recorded

    class FakeJob:
        phase = "catchup"

        def deleteLater(self):
            pass

    monkeypatch.setattr("samarizator.app.LiveRecorder", Recorder)
    window = Window()
    phases = []
    monkeypatch.setattr(window, "start", phases.append)
    monkeypatch.setattr(window, "start_catchup", lambda: None)
    window.toggle_live()
    window.job = FakeJob()
    window.active_id = window.mid

    window.toggle_live()
    assert phases == []  # nothing started yet: catch-up is still running
    assert window.pending_phase == "transcribe"

    window.job_finished()
    assert phases == ["transcribe"] and window.pending_phase == ""
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
    # Both inputs are downmixed the way `-ac 1` does on the single-source path: taking
    # channel 0 alone recorded the noise floor of a stereo input instead of the voice.
    assert graph.count("aformat=channel_layouts=mono") == 2
    assert "pan=mono|c0=c0" not in graph
    assert graph.count("aresample=async=1:first_pts=0") == 2
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


def test_check_gives_up_at_the_deadline_when_no_audio_arrives(tmp_path, monkeypatch, capsys):
    """A helper that starts but sends nothing must not block the diagnosis."""
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path))
    helper = tmp_path / "mute-helper"
    helper.write_text(
        "#!/usr/bin/env python3\n"
        "import sys, time\n"
        'sys.stderr.write(\'{"event": "started"}\\n\'); sys.stderr.flush()\n'
        "time.sleep(60)\n"
    )
    helper.chmod(0o755)
    monkeypatch.setattr(
        screencapture, "status", lambda b=None: dict(available=True, reason="", message="ok")
    )
    started = time.monotonic()
    assert screencapture.check(seconds=1, binary=helper) == 1
    assert time.monotonic() - started < 15
    assert "Аудиобуферы не приходят" in capsys.readouterr().out


def stereo_fixture(path, left, right):
    """Write a two-channel PCM16 file: `left`/`right` amplitudes, 0 meaning silence."""
    import math
    import struct
    import wave

    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, 16000, 0, "NONE", "not compressed"))
        frames = bytearray()
        for index in range(16000 * 2):
            tone = math.sin(2 * math.pi * 440 * index / 16000)
            frames += struct.pack("<hh", int(left * tone * 20000), int(right * tone * 20000))
        wav.writeframes(bytes(frames))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_describe_tracks_names_the_silent_source(tmp_path):
    from samarizator.live import describe_tracks

    recording = tmp_path / "live.wav"
    stereo_fixture(recording, left=0, right=1)
    report, silent = describe_tracks(recording, ("microphone", "system"), ["USB Audio", ""])
    assert silent == ["микрофон (USB Audio)"]
    # The device name is in the report, so a wrong input is visible, not guessed at.
    assert "канал 1 · микрофон (USB Audio)" in report and "тишина" in report
    assert "канал 2 · системный звук" in report and "пик" in report


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_describe_tracks_reports_both_when_sound_is_present(tmp_path):
    from samarizator.live import describe_tracks

    recording = tmp_path / "live.wav"
    stereo_fixture(recording, left=1, right=1)
    _, silent = describe_tracks(recording, ("microphone", "system"))
    assert silent == []


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_check_recording_exit_code_flags_a_dead_track(tmp_path, capsys):
    from samarizator.live import check_recording

    recording = tmp_path / "live.wav"
    stereo_fixture(recording, left=0, right=1)
    assert check_recording(recording) == 1
    assert "Пустые дорожки" in capsys.readouterr().out
    stereo_fixture(recording, left=1, right=1)
    assert check_recording(recording) == 0


def test_mix_profiles_exist_for_ab_testing_on_a_real_mac(tmp_path, monkeypatch):
    """The shipped default is one of several graphs; the rest stay available to compare."""
    from samarizator.live import MIX_PROFILES

    monkeypatch.setenv("SAMARIZATOR_DEV", "1")
    binary = available_helper(monkeypatch, tmp_path)
    stub_devices(monkeypatch)
    for name, chain in MIX_PROFILES.items():
        recorder = LiveRecorder(
            folder=tmp_path,
            ffmpeg="/usr/bin/ffmpeg",
            popen_factory=native_popen(),
            source="both",
            helper=binary,
            mix=name,
        )
        graph = recorder._args(7)[recorder._args(7).index("-filter_complex") + 1]
        assert graph.count(chain) == 2

    with pytest.raises(LiveCaptureError, match="профиль сведения"):
        LiveRecorder(folder=tmp_path, ffmpeg="/usr/bin/ffmpeg", source="both", mix="выдумка")


def test_live_log_is_kept_when_ffmpeg_warned(tmp_path, monkeypatch):
    """Dropped packets are warnings: keep them, they explain a crackling recording."""
    monkeypatch.setenv("SAMARIZATOR_DEV", "1")

    class WarningProcess(SuccessfulProcess):
        def __init__(self, args, **kwargs):
            super().__init__(args, **kwargs)
            kwargs["stderr"].write(b"Thread message queue blocking; consider raising\n")
            kwargs["stderr"].flush()

    recorder = LiveRecorder(
        folder=tmp_path, ffmpeg="/usr/bin/ffmpeg", device_index=0, popen_factory=WarningProcess
    )
    recorder.start()
    recorder.stop()
    assert "Thread message queue blocking" in recorder.log.read_text()


def pumping_fixture(path, duck_db=12.0, seconds=12):
    """Two tracks where the microphone dips exactly while the far side speaks."""
    import math
    import struct
    import wave

    rate = 16000
    with wave.open(str(path), "wb") as wav:
        wav.setparams((2, 2, rate, 0, "NONE", "not compressed"))
        frames = bytearray()
        for index in range(rate * seconds):
            moment = index / rate
            far_side = (int(moment) // 3) % 2 == 1
            gain = 0.25 * (10 ** (-duck_db / 20)) if far_side else 0.25
            mic = int(gain * 32767 * math.sin(2 * math.pi * 180 * moment))
            system = int((0.3 if far_side else 0.0005) * 32767 * math.sin(2 * math.pi * 300 * moment))
            frames += struct.pack("<hh", mic, system)
        wav.writeframes(bytes(frames))


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_level_profile_and_ducking_detect_outside_gain_control(tmp_path):
    from samarizator.audio_quality import ducking, level_profile

    recording = tmp_path / "pumping.wav"
    pumping_fixture(recording)
    profile = level_profile(recording, ("microphone", "system"), tmp_path, seconds=12)

    assert [track["track"] for track in profile] == ["microphone", "system"]
    assert len(profile[0]["levels"]) >= 11
    verdict = ducking(profile)
    assert verdict["share"] > 0.9  # the microphone is turned down whenever the far side talks


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
def test_steady_microphone_is_not_reported_as_ducking(tmp_path):
    from samarizator.audio_quality import ducking, level_profile

    recording = tmp_path / "steady.wav"
    pumping_fixture(recording, duck_db=0.0)
    verdict = ducking(level_profile(recording, ("microphone", "system"), tmp_path, seconds=12))
    assert verdict["share"] == 0


def test_ducking_needs_two_tracks_and_enough_speech():
    from samarizator.audio_quality import ducking

    assert ducking([dict(track="microphone", channel=0, levels=[-20, -20])]) is None
    quiet = [
        dict(track="microphone", channel=0, levels=[-20] * 4),
        dict(track="system", channel=1, levels=[-70] * 4),
    ]
    assert ducking(quiet) is None  # nobody spoke on the far side: nothing to judge
