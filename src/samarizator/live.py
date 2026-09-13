"""Bounded microphone and system-audio capture for the live-mode milestone on macOS."""

import os
import platform
import re
import shutil
import subprocess
import time
import uuid
import wave
from collections import namedtuple
from datetime import datetime
from pathlib import Path

from . import screencapture
from .config import data_dir

MICROPHONE = "microphone"
SYSTEM = "system"
BOTH = "both"
LIVE_SOURCES = (MICROPHONE, SYSTEM, BOTH)

# How system audio is obtained: Apple's own capture, or a loopback input device.
NATIVE = "screencapturekit"
DEVICE = "device"
SYSTEM_BACKENDS = (NATIVE, DEVICE)

# `index` is the AVFoundation input number; native capture has none and arrives
# on a pipe from the ScreenCaptureKit helper instead.
Input = namedtuple("Input", "kind index name native")

SOURCE_LABELS = {
    MICROPHONE: "микрофон",
    SYSTEM: "системный звук",
    BOTH: "микрофон и системный звук",
}

# Inputs that can carry the Mac output stream. macOS exposes no built-in one:
# the user installs a loopback driver or builds an aggregate device themselves.
LOOPBACK_HINTS = (
    "blackhole",
    "loopback",
    "soundflower",
    "vb-cable",
    "vb cable",
    "existential audio",
    "aggregate",
    "multi-output",
    "multi output",
    "ishowu",
    "background music",
)

NO_LOOPBACK_DEVICE = (
    "Системный звук не найден. macOS не отдаёт вывод приложений как вход сама по себе: нужно "
    "устройство петли — BlackHole, Loopback или агрегатное устройство в «Настройке Audio-MIDI» — "
    "и его же выбрать выходом для звонка. Если установка драйверов запрещена политикой компании, "
    "согласуйте её с IT: обход ограничения приложение не выполняет."
)


class LiveCaptureError(RuntimeError):
    pass


def capture_supported():
    """AVFoundation exists only on macOS; SAMARIZATOR_DEV keeps the tests runnable."""
    return platform.system() == "Darwin" or os.environ.get("SAMARIZATOR_DEV") == "1"


def parse_avfoundation_audio_devices(output):
    """Return (index, name) pairs from FFmpeg's AVFoundation device listing."""
    audio = False
    devices = []
    for line in output.splitlines():
        if "AVFoundation audio devices:" in line:
            audio = True
            continue
        if not audio:
            continue
        match = re.search(r"\[(\d+)\]\s+(.+?)\s*$", line)
        if match:
            devices.append((int(match.group(1)), match.group(2)))
    return devices


def audio_devices(ffmpeg):
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveCaptureError("Не удалось получить список аудиоустройств через FFmpeg.") from exc
    devices = parse_avfoundation_audio_devices(result.stderr)
    if not devices:
        raise LiveCaptureError(
            "FFmpeg не нашёл аудиовходов. Проверьте подключение устройства и разрешение macOS."
        )
    return devices


def looks_like_loopback(name):
    lowered = name.casefold()
    return any(hint in lowered for hint in LOOPBACK_HINTS)


def system_audio_devices(devices):
    """Inputs that can carry system output. AVFoundation does not mark them, so match by name."""
    return [(index, name) for index, name in devices if looks_like_loopback(name)]


def resolve_device(devices, preferred, kind):
    """Pick an input by its saved name; without one take the first device of that kind.

    Names are stored instead of indexes: AVFoundation renumbers inputs when devices
    are plugged in, so a saved index can silently point at the wrong source.
    """
    if preferred and preferred.strip():
        wanted = preferred.strip().casefold()
        for index, name in devices:
            if name.casefold() == wanted:
                return index, name
        listing = ", ".join(name for _, name in devices) or "список пуст"
        raise LiveCaptureError(
            f"Устройство «{preferred.strip()}» не найдено среди входов macOS. Доступны: {listing}. "
            "Выберите другое в настройках live-записи."
        )
    if kind == SYSTEM:
        found = system_audio_devices(devices)
        if not found:
            raise LiveCaptureError(NO_LOOPBACK_DEVICE)
        return found[0]
    return devices[0]


class LiveRecorder:
    """Record one live session to a local WAV without buffering it in RAM.

    With `source="both"` the microphone becomes channel 0 and the system output
    channel 1 of a single stereo file, so both tracks share one timeline and the
    existing file pipeline (which downmixes to mono) needs no change.
    """

    def __init__(
        self,
        folder=None,
        ffmpeg=None,
        device_index=None,
        popen_factory=None,
        source=MICROPHONE,
        microphone_device="",
        system_device="",
        system_backend=NATIVE,
        helper=None,
    ):
        if not capture_supported():
            raise LiveCaptureError("Live-запись пока поддерживается только на macOS.")
        if source not in LIVE_SOURCES:
            raise LiveCaptureError("Неизвестный источник live-записи.")
        if system_backend not in SYSTEM_BACKENDS:
            raise LiveCaptureError("Неизвестный способ захвата системного звука.")
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise LiveCaptureError("FFmpeg не найден. Запустите ./start.sh для установки.")
        self.folder = Path(folder or data_dir() / "recordings")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        self.source = source
        self.system_backend = system_backend
        self.helper = Path(helper) if helper else screencapture.binary_path()
        self.inputs = self._inputs(device_index, microphone_device, system_device)
        self.device_index = self.inputs[0].index
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        token = uuid.uuid4().hex[:8]
        self.path = self.folder / f"live-{stamp}-{token}.wav"
        self.partial = self.folder / f"live-{stamp}-{token}.partial.wav"
        self.log = self.folder / f"live-{stamp}-{token}.log"
        self.helper_log = self.folder / f"live-{stamp}-{token}.helper.log"
        self.process = None
        self.helper_process = None
        self.started = None
        self._log_handle = None
        self._helper_log_handle = None
        self._popen = popen_factory or subprocess.Popen

    def _inputs(self, device_index, microphone_device, system_device):
        """Inputs in channel order: microphone first, system audio second."""
        if self.source == MICROPHONE and device_index is not None:
            return [Input(MICROPHONE, int(device_index), "", False)]
        kinds = [MICROPHONE] if self.source == MICROPHONE else (
            [SYSTEM] if self.source == SYSTEM else [MICROPHONE, SYSTEM]
        )
        native = self.system_backend == NATIVE
        if kinds == [SYSTEM] and native:
            return [Input(SYSTEM, None, "ScreenCaptureKit", True)]
        devices = audio_devices(self.ffmpeg)
        wanted = {MICROPHONE: microphone_device, SYSTEM: system_device}
        resolved = []
        for kind in kinds:
            if kind == SYSTEM and native:
                resolved.append(Input(SYSTEM, None, "ScreenCaptureKit", True))
                continue
            index, name = resolve_device(devices, wanted[kind], kind)
            resolved.append(Input(kind, index, name, False))
        taken = [entry.index for entry in resolved if entry.index is not None]
        if len(set(taken)) < len(taken):
            raise LiveCaptureError(
                "Микрофон и системный звук указывают на одно устройство. Выберите разные входы, "
                "иначе дорожки будут одинаковыми."
            )
        return resolved

    @property
    def tracks(self):
        """Channel layout of the produced WAV, in order."""
        return tuple(entry.kind for entry in self.inputs)

    @property
    def native_capture(self):
        return any(entry.native for entry in self.inputs)

    @property
    def recording(self):
        return self.process is not None and self.process.poll() is None

    @property
    def elapsed(self):
        return max(0.0, time.monotonic() - self.started) if self.started is not None else 0.0

    def _args(self, pipe_fd=None):
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y"]
        for entry in self.inputs:
            args += ["-thread_queue_size", "512"]
            if entry.native:
                # Raw PCM from the ScreenCaptureKit helper; FFmpeg cannot probe a pipe,
                # so the format is stated explicitly and must match the helper's output.
                args += [
                    "-f",
                    screencapture.SAMPLE_FORMAT,
                    "-ar",
                    str(screencapture.SAMPLE_RATE),
                    "-ac",
                    str(screencapture.CHANNELS),
                    "-i",
                    f"pipe:{pipe_fd}",
                ]
            else:
                args += ["-f", "avfoundation", "-i", f":{entry.index}"]
        args += ["-vn"]
        if len(self.inputs) == 1:
            args += ["-ac", "1"]
        else:
            # Each device runs on its own clock; aresample keeps the two tracks on one
            # timeline instead of letting drift accumulate over a long meeting.
            chains = [
                f"[{position}:a]aresample=async=1000:first_pts=0,pan=mono|c0=c0[t{position}]"
                for position in range(len(self.inputs))
            ]
            merge = "".join(f"[t{position}]" for position in range(len(self.inputs)))
            args += [
                "-filter_complex",
                ";".join(chains) + f";{merge}amerge=inputs={len(self.inputs)}[live]",
                "-map",
                "[live]",
                "-ac",
                str(len(self.inputs)),
            ]
        # flush_packets keeps the growing file readable: without it FFmpeg buffers the
        # WAV and nothing reaches disk until the end, so catch-up would have nothing to do.
        args += ["-ar", "16000", "-c:a", "pcm_s16le", "-flush_packets", "1", str(self.partial)]
        return args

    def start(self):
        if self.process is not None:
            raise LiveCaptureError("Live-запись уже запущена.")
        env = os.environ.copy()
        env.pop("SAMARIZATOR_API_KEY", None)
        if self.native_capture:
            self._check_helper()
        # Created before FFmpeg starts so catch-up recognition has a path to watch
        # from the first second of the meeting.
        self.partial.touch(mode=0o600)
        read_fd, write_fd = os.pipe() if self.native_capture else (None, None)
        self._log_handle = self.log.open("wb")
        try:
            # FFmpeg starts first and blocks on an empty pipe; the helper then fills it.
            self.process = self._popen(
                self._args(read_fd),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._log_handle,
                env=env,
                start_new_session=True,
                **({"pass_fds": (read_fd,)} if read_fd is not None else {}),
            )
        except (OSError, ValueError) as exc:
            self._close_pipe(read_fd, write_fd)
            self._close_log()
            raise LiveCaptureError(
                f"Не удалось запустить захват ({SOURCE_LABELS[self.source]}) через FFmpeg."
            ) from exc
        if read_fd is not None:
            self._close_fd(read_fd)
            try:
                self._start_helper(write_fd, env)
            except LiveCaptureError:
                self._close_fd(write_fd)
                self._stop_ffmpeg()
                self._close_log()
                self.process = None
                self.partial.unlink(missing_ok=True)
                raise
            self._close_fd(write_fd)
        self.started = time.monotonic()
        return self.path

    def _check_helper(self):
        report = screencapture.status(self.helper)
        if not report["available"]:
            raise LiveCaptureError(report["message"])

    def _start_helper(self, write_fd, env):
        self._helper_log_handle = self.helper_log.open("wb")
        try:
            self.helper_process = self._popen(
                [str(self.helper), "capture"],
                stdin=subprocess.DEVNULL,
                stdout=write_fd,
                stderr=self._helper_log_handle,
                env=env,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._close_helper_log()
            raise LiveCaptureError(
                "Не удалось запустить helper системного звука. Пересоберите его через ./start.sh."
            ) from exc

    @staticmethod
    def _close_fd(fd):
        try:
            os.close(fd)
        except OSError:
            pass

    def _close_pipe(self, read_fd, write_fd):
        for fd in (read_fd, write_fd):
            if fd is not None:
                self._close_fd(fd)

    def stop(self, timeout=10):
        if self.process is None:
            raise LiveCaptureError("Live-запись не запущена.")
        # The helper goes first: closing its end of the pipe is what lets FFmpeg
        # finish that input cleanly instead of waiting for more audio.
        helper_problem, helper_detail = self._stop_helper(timeout)
        self._stop_ffmpeg(timeout)
        self._close_log()
        if helper_problem:
            self.partial.unlink(missing_ok=True)
            self.log.unlink(missing_ok=True)
            raise LiveCaptureError(helper_problem)
        if self.process.returncode != 0:
            self.partial.unlink(missing_ok=True)
            raise LiveCaptureError(self._failure_message())
        if not self.partial.is_file() or self.partial.stat().st_size <= 1024:
            self.partial.unlink(missing_ok=True)
            raise LiveCaptureError(self._silence_message(helper_detail))
        self.partial.replace(self.path)
        self.log.unlink(missing_ok=True)
        self.helper_log.unlink(missing_ok=True)  # only a good recording removes the evidence
        return self.path

    def _stop_ffmpeg(self, timeout=10):
        proc = self.process
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.stdin.write(b"q\n")
                proc.stdin.flush()
            except (AttributeError, BrokenPipeError, OSError):
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    def _stop_helper(self, timeout=10):
        """Stop the helper; return (error message, its raw report) for diagnosis."""
        proc = self.helper_process
        if proc is None:
            return "", ""
        if proc.poll() is None:
            proc.terminate()  # SIGTERM: the helper stops the stream and exits 0.
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        self._close_helper_log()
        detail = self.helper_log.read_text(errors="replace") if self.helper_log.is_file() else ""
        return screencapture.failure_message(proc.returncode, detail), detail

    def _close_log(self):
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _close_helper_log(self):
        if self._helper_log_handle is not None:
            self._helper_log_handle.close()
            self._helper_log_handle = None

    def _silence_message(self, helper_detail=""):
        if self.source == MICROPHONE:
            return "Микрофон не записал звук. Проверьте выбранный вход и разрешение macOS."
        if self.native_capture:
            # The helper's own counters say whether ScreenCaptureKit delivered anything,
            # so an empty file is never blamed on the user's volume by guesswork.
            return (
                screencapture.silence_diagnosis(helper_detail)
                + f"\nЖурнал helper'а: {self.helper_log}"
            )
        return (
            "Запись получилась пустой. Проверьте, что устройство петли выбрано выходом звука "
            "в системных настройках, иначе на его вход ничего не поступает."
        )

    def _failure_message(self):
        detail = self.log.read_text(errors="replace")[-1200:] if self.log.is_file() else ""
        self.log.unlink(missing_ok=True)
        lowered = detail.casefold()
        if "not authorized" in lowered or "permission" in lowered:
            # Loopback inputs are ordinary AVFoundation inputs: macOS gates them
            # behind the microphone permission too, not screen recording.
            return (
                "macOS не дала доступ к аудиовходу. Откройте Системные настройки → "
                "Конфиденциальность и безопасность → Микрофон и разрешите Terminal или Samarizator. "
                "Это же разрешение нужно для устройства системного звука."
            )
        return (
            f"Не удалось записать выбранный источник ({SOURCE_LABELS[self.source]}). "
            "Проверьте устройства ввода и разрешения macOS."
        )


TRACK_SAMPLE_SECONDS = 30


def describe_tracks(path, tracks, names=(), seconds=TRACK_SAMPLE_SECONDS):
    """Level report per track of a finished live recording, and which are silent.

    Only the first `seconds` are measured: enough to catch a dead source, bounded
    enough to run right after a long recording without freezing the window.
    """
    from tempfile import TemporaryDirectory

    from .audio_quality import track_levels

    labels = {MICROPHONE: "микрофон", SYSTEM: "системный звук"}
    with TemporaryDirectory() as tmp:
        levels = track_levels(path, tracks, tmp, seconds=seconds)
    lines, silent = [], []
    for level in levels:
        name = labels.get(level["track"], level["track"])
        # Naming the device turns "no microphone sound" into "this input gave nothing".
        device = names[level["channel"]] if level["channel"] < len(names) else ""
        titled = f"{name} ({device})" if device else name
        state = "тишина" if level["peak"] == 0 else f"пик {level['peak']:.3f}"
        lines.append(f"канал {level['channel'] + 1} · {titled}: RMS {level['rms_dbfs']} dBFS, {state}")
        if level["peak"] == 0:
            silent.append(titled)
    return "\n".join(lines), silent


def check_recording(path):
    """CLI: report what actually landed in each channel of a live recording."""
    path = Path(path)
    if not path.is_file():
        print(f"Файл не найден: {path}")
        return 1
    with wave.open(str(path)) as wav:
        channels = wav.getnchannels()
        duration = wav.getnframes() / max(1, wav.getframerate())
    tracks = (MICROPHONE, SYSTEM) if channels == 2 else (MICROPHONE,)
    measured = min(duration, 120)
    print(f"{path.name}: каналов {channels}, длительность {duration:.1f} с")
    print(f"Измерены первые {measured:.0f} с каждой дорожки:")
    report, silent = describe_tracks(path, tracks, seconds=measured)
    print(report)
    if silent:
        print("Пустые дорожки: " + ", ".join(silent))
        return 1
    print("Все дорожки содержат звук.")
    return 0


def main():
    import sys

    if len(sys.argv) < 2:
        print("Использование: python -m samarizator.live <файл-записи.wav>")
        return 2
    return check_recording(sys.argv[1])


if __name__ == "__main__":
    raise SystemExit(main())
