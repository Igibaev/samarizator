"""Apple ScreenCaptureKit helper: system audio without a third-party driver.

The helper is a small Swift binary built from `native/macos-capture`. It writes raw
float32 mono PCM to its stdout, which `live.LiveRecorder` hands to FFmpeg as one more
input, so system audio joins the microphone on a single timeline.

macOS gates it behind the screen-recording permission, granted per host application:
launched from `start.sh` the prompt names Terminal, not Samarizator.
"""

import json
import os
import platform
import shutil
import subprocess
import time
from pathlib import Path

from .config import data_dir

SOURCE = Path(__file__).resolve().parents[2] / "native/macos-capture/main.swift"
SAMPLE_RATE = 48000
SAMPLE_FORMAT = "f32le"
CHANNELS = 1
MINIMUM_MACOS = 13

PERMISSION_HELP = (
    "macOS не дала разрешение на запись экрана и системного звука. Откройте Системные "
    "настройки → Конфиденциальность и безопасность → Запись экрана и системного звука, "
    "включите Terminal (или Samarizator) и перезапустите приложение: разрешение действует "
    "со следующего запуска. Если пункт недоступен или управляется профилем MDM, запрет "
    "поставлен политикой компании — обход приложение не выполняет, обратитесь к IT."
)
BUILD_HELP = (
    "Helper системного звука не собран. Запустите ./start.sh на macOS — он соберёт его "
    "через Xcode Command Line Tools (xcode-select --install). Пока helper не собран, "
    "доступны микрофон и системный звук через устройство петли."
)


class HelperError(RuntimeError):
    pass


def binary_path():
    return data_dir() / "bin/samarizator-system-audio"


def macos_version():
    try:
        return tuple(int(part) for part in platform.mac_ver()[0].split(".")[:2])
    except ValueError:
        return ()


def supported_platform():
    """ScreenCaptureKit audio needs macOS 13+; SAMARIZATOR_DEV keeps the tests runnable."""
    if os.environ.get("SAMARIZATOR_DEV") == "1":
        return True
    version = macos_version()
    return platform.system() == "Darwin" and bool(version) and version[0] >= MINIMUM_MACOS


def build(source=None, target=None):
    """Compile the helper. Returns its path; raises HelperError with a fixable message."""
    source = Path(source or SOURCE)
    target = Path(target or binary_path())
    if not source.is_file():
        raise HelperError("Исходник helper'а не найден: переустановите проект из репозитория.")
    swiftc = shutil.which("swiftc")
    if not swiftc:
        raise HelperError(
            "Не найден swiftc. Установите Xcode Command Line Tools: xcode-select --install."
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".partial")
    try:
        result = subprocess.run(
            [
                swiftc,
                "-swift-version",
                "5",
                "-O",
                # Pin the deployment target so the ScreenCaptureKit availability checks
                # are explicit, instead of following whichever macOS builds the helper.
                "-target",
                f"{platform.machine()}-apple-macos{MINIMUM_MACOS}.0",
                "-o",
                str(partial),
                str(source),
            ],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise HelperError("Не удалось запустить swiftc для сборки helper'а.") from exc
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        detail = (result.stderr or "").strip().splitlines()[-3:]
        raise HelperError("swiftc не собрал helper: " + " / ".join(detail))
    partial.chmod(0o700)
    partial.replace(target)
    return target


def status(binary=None, runner=subprocess.run):
    """Report what the helper can do right now, without starting a capture."""
    if not supported_platform():
        return dict(
            available=False,
            reason="platform",
            message=f"Штатный захват требует macOS {MINIMUM_MACOS} или новее.",
        )
    binary = Path(binary or binary_path())
    if not binary.is_file():
        return dict(available=False, reason="not-built", message=BUILD_HELP)
    try:
        result = runner(
            # Short: this runs while the settings window is opening.
            [str(binary), "probe"], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return dict(available=False, reason="probe-failed", message="Helper системного звука не отвечает.")
    try:
        payload = json.loads(result.stdout or "{}")
    except json.JSONDecodeError:
        return dict(available=False, reason="probe-failed", message="Helper вернул неожиданный ответ.")
    if payload.get("permission") != "granted":
        return dict(available=False, reason="permission", message=PERMISSION_HELP, probe=payload)
    if not payload.get("supported"):
        detail = payload.get("reason") or "macOS не отдала ни одного дисплея для захвата."
        return dict(available=False, reason="unsupported", message=detail, probe=payload)
    return dict(available=True, reason="", message="Штатный захват macOS готов.", probe=payload)


def failure_message(returncode, log_text):
    """Translate a helper exit into something the user can act on."""
    error = next((e for e in reversed(events(log_text)) if e.get("event") == "error"), None)
    code = (error or {}).get("code")
    if code == "permission" or returncode == 2:
        return PERMISSION_HELP
    if code == "no-display":
        return "macOS не вернула дисплей для захвата. Проверьте, что экран не заблокирован."
    if error:
        return f"Штатный захват системного звука прервался: {error.get('message', 'причина не указана')}."
    if returncode not in (0, None):
        return "Штатный захват системного звука завершился с ошибкой. Подробности в журнале записи."
    return ""


def capture_stats(log_text):
    """What the helper reported about the audio it actually received."""
    stats = dict(started=False, buffers=0, bytes=0, frames=0, peak=0.0)
    for payload in events(log_text):
        if payload.get("event") == "started":
            stats["started"] = True
        progress = payload.get("progress") if payload.get("event") == "error" else payload
        if payload.get("event") in {"progress", "stopped", "error"} and isinstance(progress, dict):
            for key in ("buffers", "bytes", "frames"):
                stats[key] = max(stats[key], int(progress.get(key) or 0))
            stats["peak"] = max(stats["peak"], float(progress.get("peak") or 0))
    return stats


def silence_diagnosis(log_text):
    """Explain an empty recording from the helper's own counters."""
    stats = capture_stats(log_text)
    if not stats["started"]:
        return (
            "Helper системного звука не сообщил о старте захвата. Проверьте разрешение "
            "и запустите диагностику: .venv/bin/python -m samarizator.screencapture check"
        )
    if stats["bytes"] == 0:
        return (
            "ScreenCaptureKit не отдал ни одного аудиобуфера, хотя захват стартовал и "
            "разрешение есть. Это не про громкость источника: буферы приходят даже в тишине. "
            "Проверьте версию macOS и соберите диагностику: "
            ".venv/bin/python -m samarizator.screencapture check"
        )
    return (
        f"Helper получил {stats['bytes'] // 1024} КиБ звука, но в файл ничего не попало — "
        "значит потерялось между helper'ом и FFmpeg. Пришлите журнал записи."
    )


def check(seconds=5, binary=None):
    """Run the helper alone and report what ScreenCaptureKit gives, bypassing FFmpeg."""
    binary = Path(binary or binary_path())
    report = status(binary)
    print(f"Состояние: {report['message']}")
    if not report["available"]:
        return 1
    print(f"Проверяю {seconds} с. Включите любой звук — музыку, видео, звонок.")
    log = data_dir() / "system-audio-check.log"
    with log.open("wb") as errors:
        proc = subprocess.Popen(
            [str(binary), "capture"], stdout=subprocess.PIPE, stderr=errors, start_new_session=True
        )
        received = 0
        deadline = time.monotonic() + seconds
        try:
            while time.monotonic() < deadline:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                received += len(chunk)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            proc.stdout.close()
    detail = log.read_text(errors="replace")
    stats = capture_stats(detail)
    seconds_of_audio = received / (SAMPLE_RATE * 4)
    print(f"Прочитано из helper'а: {received} байт ≈ {seconds_of_audio:.1f} с звука.")
    print(
        f"Сам helper насчитал: буферов {stats['buffers']}, байт {stats['bytes']}, "
        f"пиковый уровень {stats['peak']:.3f}."
    )
    if stats["buffers"] and stats["peak"] == 0:
        print("Буферы приходят, но в них цифровая тишина: звук не попадает в захват macOS.")
    print(f"Код возврата: {proc.returncode}. Журнал: {log}")
    if received == 0:
        print("Аудиобуферы не приходят. Пришлите журнал целиком — по нему видно причину.")
        return 1
    print("ScreenCaptureKit отдаёт звук: захват работает.")
    return 0


def events(log_text):
    parsed = []
    for line in (log_text or "").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            parsed.append(payload)
    return parsed


def main():
    """Build the helper from start.sh. Never fatal: the loopback path stays available."""
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "check":
        return check()
    if not supported_platform():
        print(f"Штатный захват системного звука требует macOS {MINIMUM_MACOS}+, пропускаю сборку.")
        return 0
    try:
        target = build()
    except HelperError as exc:
        print(f"Helper системного звука не собран: {exc}")
        return 0
    report = status(target)
    print(f"Helper системного звука готов: {target}")
    if not report["available"] and report["reason"] == "permission":
        print(PERMISSION_HELP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
