"""Bounded microphone capture for the first live-mode milestone on macOS."""

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


class LiveCaptureError(RuntimeError):
    pass


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


def default_microphone(ffmpeg):
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
        raise LiveCaptureError("Не удалось получить список микрофонов через FFmpeg.") from exc
    devices = parse_avfoundation_audio_devices(result.stderr)
    if not devices:
        raise LiveCaptureError(
            "FFmpeg не нашёл микрофон. Проверьте подключение устройства и разрешение macOS."
        )
    return devices[0]


class LiveRecorder:
    """Record one microphone session to a local WAV without buffering it in RAM."""

    def __init__(self, folder=None, ffmpeg=None, device_index=None, popen_factory=None):
        if platform.system() != "Darwin" and os.environ.get("SAMARIZATOR_DEV") != "1":
            raise LiveCaptureError("Live-запись пока поддерживается только на macOS.")
        self.ffmpeg = ffmpeg or shutil.which("ffmpeg")
        if not self.ffmpeg:
            raise LiveCaptureError("FFmpeg не найден. Запустите ./start.sh для установки.")
        self.folder = Path(folder or data_dir() / "recordings")
        self.folder.mkdir(parents=True, exist_ok=True)
        self.folder.chmod(0o700)
        self.device_index = (
            default_microphone(self.ffmpeg)[0] if device_index is None else int(device_index)
        )
        stamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S")
        token = uuid.uuid4().hex[:8]
        self.path = self.folder / f"live-{stamp}-{token}.wav"
        self.partial = self.folder / f"live-{stamp}-{token}.partial.wav"
        self.log = self.folder / f"live-{stamp}-{token}.log"
        self.process = None
        self.started = None
        self._log_handle = None
        self._popen = popen_factory or subprocess.Popen

    @property
    def recording(self):
        return self.process is not None and self.process.poll() is None

    @property
    def elapsed(self):
        return max(0.0, time.monotonic() - self.started) if self.started is not None else 0.0

    def start(self):
        if self.process is not None:
            raise LiveCaptureError("Live-запись уже запущена.")
        env = os.environ.copy()
        env.pop("SAMARIZATOR_API_KEY", None)
        self._log_handle = self.log.open("wb")
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostats",
            "-y",
            "-thread_queue_size",
            "512",
            "-f",
            "avfoundation",
            "-i",
            f":{self.device_index}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(self.partial),
        ]
        try:
            self.process = self._popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=self._log_handle,
                env=env,
                start_new_session=True,
            )
        except (OSError, ValueError) as exc:
            self._close_log()
            raise LiveCaptureError("Не удалось запустить захват микрофона через FFmpeg.") from exc
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
            raise LiveCaptureError("Микрофон не записал звук. Проверьте выбранный вход и разрешение macOS.")
        self.partial.replace(self.path)
        self.log.unlink(missing_ok=True)
        return self.path

    def _close_log(self):
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None

    def _failure_message(self):
        detail = self.log.read_text(errors="replace")[-1200:] if self.log.is_file() else ""
        self.log.unlink(missing_ok=True)
        lowered = detail.casefold()
        if "not authorized" in lowered or "permission" in lowered:
            return (
                "macOS не дала доступ к микрофону. Откройте Системные настройки → "
                "Конфиденциальность и безопасность → Микрофон и разрешите Terminal или Samarizator."
            )
        return "Не удалось записать микрофон. Проверьте устройство ввода и разрешение macOS."
