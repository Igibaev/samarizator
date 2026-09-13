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
