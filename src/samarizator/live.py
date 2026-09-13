"""Bounded microphone and system-audio capture for the live-mode milestone on macOS."""

import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path

from .config import data_dir

MICROPHONE = "microphone"
SYSTEM = "system"
BOTH = "both"
LIVE_SOURCES = (MICROPHONE, SYSTEM, BOTH)

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
    ):
        if not capture_supported():
            raise LiveCaptureError("Live-запись пока поддерживается только на macOS.")
        if source not in LIVE_SOURCES:
            raise LiveCaptureError("Неизвестный источник live-записи.")
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise LiveCaptureError("FFmpeg не найден. Запустите ./start.sh для установки.")
        self.folder = Path(folder or data_dir() / "recordings")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        self.source = source
        self.inputs = self._inputs(device_index, microphone_device, system_device)
        self.device_index = self.inputs[0][1]
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        token = uuid.uuid4().hex[:8]
        self.path = self.folder / f"live-{stamp}-{token}.wav"
        self.partial = self.folder / f"live-{stamp}-{token}.partial.wav"
        self.log = self.folder / f"live-{stamp}-{token}.log"
        self.process = None
        self.started = None
        self._log_handle = None
        self._popen = popen_factory or subprocess.Popen

    def _inputs(self, device_index, microphone_device, system_device):
        """Return [(kind, index, name)] in channel order: microphone first, system second."""
        if self.source == MICROPHONE and device_index is not None:
            return [(MICROPHONE, int(device_index), "")]
        devices = audio_devices(self.ffmpeg)
        kinds = [MICROPHONE] if self.source == MICROPHONE else (
            [SYSTEM] if self.source == SYSTEM else [MICROPHONE, SYSTEM]
        )
        wanted = {MICROPHONE: microphone_device, SYSTEM: system_device}
        resolved = []
        for kind in kinds:
            index, name = resolve_device(devices, wanted[kind], kind)
            resolved.append((kind, index, name))
        if len({index for _, index, _ in resolved}) < len(resolved):
            raise LiveCaptureError(
                "Микрофон и системный звук указывают на одно устройство. Выберите разные входы, "
                "иначе дорожки будут одинаковыми."
            )
        return resolved

    @property
    def tracks(self):
        """Channel layout of the produced WAV, in order."""
        return tuple(kind for kind, _, _ in self.inputs)

    @property
    def recording(self):
        return self.process is not None and self.process.poll() is None

    @property
    def elapsed(self):
        return max(0.0, time.monotonic() - self.started) if self.started is not None else 0.0

    def _args(self):
        args = [self.ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-y"]
        for _, index, _ in self.inputs:
            args += ["-thread_queue_size", "512", "-f", "avfoundation", "-i", f":{index}"]
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
        args += ["-ar", "16000", "-c:a", "pcm_s16le", str(self.partial)]
        return args

    def start(self):
        if self.process is not None:
            raise LiveCaptureError("Live-запись уже запущена.")
        env = os.environ.copy()
        env.pop("SAMARIZATOR_API_KEY", None)
        self._log_handle = self.log.open("wb")
        try:
            self.process = self._popen(
                self._args(),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._log_handle,
                env=env,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._close_log()
            raise LiveCaptureError(
                f"Не удалось запустить захват ({SOURCE_LABELS[self.source]}) через FFmpeg."
            ) from exc
        self.started = time.monotonic()
        return self.path

    def stop(self, timeout=10):
        if self.process is None:
            raise LiveCaptureError("Live-запись не запущена.")
        proc = self.process
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
        self._close_log()
        if proc.returncode != 0:
            self.partial.unlink(missing_ok=True)
            raise LiveCaptureError(self._failure_message())
        if not self.partial.is_file() or self.partial.stat().st_size <= 1024:
            self.partial.unlink(missing_ok=True)
            raise LiveCaptureError(self._silence_message())
        self.partial.replace(self.path)
        self.log.unlink(missing_ok=True)
        return self.path

    def _close_log(self):
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _silence_message(self):
        if self.source == MICROPHONE:
            return "Микрофон не записал звук. Проверьте выбранный вход и разрешение macOS."
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
