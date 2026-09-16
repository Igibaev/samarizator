"""Headless-интерфейс Samarizator для ИИ-компаньона.

Зачем отдельный модуль: у приложения есть только GUI на PySide6, а компаньону
нужно запускать запись, распознавание и сводку без окна. Вся логика уже
headless — `live.LiveRecorder` и `worker` не тянут Qt, — не хватало только
входа, который можно вызвать из другого процесса.

Протокол: одна JSON-строка на событие в stdout, по строке на событие
(NDJSON). Компаньон читает их по мере поступления и показывает СВОЁ
состояние по факту, а не по таймеру.

    python -m samarizator.companion status
    python -m samarizator.companion record --source both
    python -m samarizator.companion transcribe <mid>
    python -m samarizator.companion summarize <mid>

`record` держит запись, пока идёт сам: остановка — это SIGINT (или SIGTERM)
процессу. Так тому, кто его запустил, не нужны ни pid-файлы, ни отдельная
команда остановки: владение процессом и есть владение записью.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from .config import Settings, data_dir
from .store import Store

# Причины завершения записи, которые не являются ошибкой.
_STOP_SIGNALS = (signal.SIGINT, signal.SIGTERM)


def emit(event, **fields):
    """Одно событие протокола. Пишем сразу: компаньон читает по мере поступления."""
    payload = {"event": event}
    payload.update(fields)
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _worker_command(phase, mid):
    return [sys.executable, "-m", "samarizator.worker", phase, mid]


def _run_worker(phase, mid, budget_gb):
    """Запускает фазу обработки под тем же надзором за памятью, что и GUI."""
    from .process import BudgetExceeded, supervise

    try:
        code = supervise(_worker_command(phase, mid), budget_gb)
    except BudgetExceeded as exc:
        return str(exc)
    except InterruptedError as exc:
        return str(exc)
    except RuntimeError as exc:
        return str(exc)
    if code != 0:
        # Настоящая причина уже записана воркером в meetings.error —
        # придумывать свою поверх неё нельзя.
        return None if code is None else "worker-error"
    return None


# --- status -----------------------------------------------------------------


def command_status(_args):
    """Что доступно прямо сейчас. Компаньон рисует по этому свои состояния."""
    from . import live

    settings = Settings.load()
    database = data_dir() / "meetings.sqlite3"
    whisper = Path(settings.whisper_model) if settings.whisper_model else None

    emit(
        "status",
        databasePath=str(database),
        databaseExists=database.exists(),
        captureSupported=live.capture_supported(),
        # Без модели Whisper распознавать нечем, и делать вид, что можно, нельзя.
        whisperModelPath=str(whisper) if whisper else "",
        whisperModelReady=bool(whisper and whisper.is_file()),
        # Адрес модели для сводки; пустой — API не настроен.
        summaryBaseURL=settings.base_url,
        summaryModel=settings.model,
        liveSource=settings.live_source,
        memoryBudgetGb=settings.memory_gb,
    )
    return 0


# --- record -----------------------------------------------------------------


class StopRequest:
    """Ожидание сигнала остановки записи.

    Вынесено из `command_record` по двум причинам. Первая — обработчики
    сигналов надо СНИМАТЬ: процесс, в котором остался наш обработчик, при
    следующем Ctrl+C повёл бы себя не так, как ожидает его владелец. Вторая —
    так остановку можно вызвать напрямую, не поднимая настоящий SIGINT внутри
    чужого процесса.
    """

    def __init__(self):
        self._event = threading.Event()
        self._previous = {}

    def __enter__(self):
        for number in _STOP_SIGNALS:
            self._previous[number] = signal.getsignal(number)
            signal.signal(number, self._handle)
        return self

    def __exit__(self, *_exc):
        for number, handler in self._previous.items():
            signal.signal(number, handler)
        self._previous.clear()
        return False

    def _handle(self, _signum, _frame):
        self._event.set()

    def request(self):
        """Остановить запись. То же, что делает сигнал, но без сигнала."""
        self._event.set()

    def wait(self):
        self._event.wait()

    @property
    def requested(self):
        return self._event.is_set()


def command_record(args, stop=None):
    """Запись до сигнала остановки, затем закрывающее распознавание.

    Повторяет порядок действий GUI: запись существует в базе с первой секунды,
    чтобы распознавание шло параллельно, а источник записи переезжает на
    готовый файл, когда захват остановлен.
    """
    from .live import LiveCaptureError, LiveRecorder, recording_title

    settings = Settings.load()
    store = Store()

    try:
        recorder = LiveRecorder(
            source=args.source or settings.live_source,
            microphone_device=settings.live_microphone_device,
            system_device=settings.live_system_device,
            system_backend=settings.live_system_backend,
            mix=settings.live_mix,
        )
        recorder.start()
    except LiveCaptureError as exc:
        emit("error", stage="record", message=str(exc))
        return 1

    mid = store.create(recorder.partial, settings)
    store.update(
        mid,
        title=recording_title(recorder.source, recorder.partial.stem),
        status="recording",
    )
    emit("recording", mid=mid, source=recorder.source, path=str(recorder.partial))

    # Распознавание идёт ПО ХОДУ записи — отдельным процессом, как в GUI.
    catchup = subprocess.Popen(
        _worker_command("catchup", mid),
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if stop is None:
        with StopRequest() as waiter:
            waiter.wait()
    else:
        stop.wait()

    try:
        path = recorder.stop()
    except (LiveCaptureError, OSError, ValueError) as exc:
        _finish_catchup(store, mid, catchup)
        # Пустая запись без единого распознанного фрагмента не оставляет ничего.
        if not store.segments(mid, limit=1):
            store.delete(mid)
            emit("discarded", mid=mid, message=str(exc))
        else:
            emit("error", stage="record", mid=mid, message=str(exc))
        return 1

    store.update(mid, source=str(path), status="transcribing")
    emit("stopped", mid=mid, path=str(path))
    _finish_catchup(store, mid, catchup)

    emit("transcribing", mid=mid)
    failure = _run_worker("transcribe", mid, settings.memory_gb)
    if failure:
        emit("error", stage="transcribe", mid=mid, message=failure)
        return 1
    emit("transcribed", mid=mid)
    return 0


def _finish_catchup(store, mid, process):
    """Сообщает параллельному распознаванию, что запись окончена, и ждёт хвост."""
    store.save_checkpoint(mid, "live-stopped", 0, True)
    if process is None:
        return
    try:
        process.wait(timeout=600)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


# --- transcribe / summarize -------------------------------------------------


def command_transcribe(args):
    settings = Settings.load()
    emit("transcribing", mid=args.mid)
    failure = _run_worker("transcribe", args.mid, settings.memory_gb)
    if failure:
        emit("error", stage="transcribe", mid=args.mid, message=failure)
        return 1
    emit("transcribed", mid=args.mid)
    return 0


def command_summarize(args):
    settings = Settings.load()
    store = Store()

    # Материала нет — честно об этом говорим, а не запускаем пустую обработку.
    if not store.segments(args.mid, limit=1):
        emit("error", stage="summary", mid=args.mid, message="У записи ещё нет распознанного текста.")
        return 1
    try:
        settings.validate(api=True)
    except ValueError as exc:
        emit("error", stage="summary", mid=args.mid, message=str(exc))
        return 1

    emit("summarizing", mid=args.mid)
    failure = _run_worker("summary", args.mid, settings.memory_gb)
    if failure:
        emit("error", stage="summary", mid=args.mid, message=failure)
        return 1
    emit("summarized", mid=args.mid)
    return 0


# --- CLI --------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(prog="samarizator.companion", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="что доступно прямо сейчас")

    record = commands.add_parser("record", help="запись до сигнала остановки")
    record.add_argument("--source", choices=("microphone", "system", "both"), default=None)

    transcribe = commands.add_parser("transcribe", help="распознать запись")
    transcribe.add_argument("mid")

    summarize = commands.add_parser("summarize", help="сделать сводку")
    summarize.add_argument("mid")

    return parser


HANDLERS = {
    "status": command_status,
    "record": command_record,
    "transcribe": command_transcribe,
    "summarize": command_summarize,
}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return HANDLERS[args.command](args)
    except KeyboardInterrupt:
        emit("cancelled", stage=args.command)
        return 1
    except Exception as exc:
        # Наружу отдаём только контролируемые сообщения: текст чужой ошибки
        # может содержать заголовки запроса или фрагменты транскрипта.
        safe = (
            str(exc)
            if isinstance(exc, (ValueError, RuntimeError))
            else f"Ошибка {type(exc).__name__}."
        )
        emit("error", stage=args.command, message=safe[:1000])
        return 1


if __name__ == "__main__":
    sys.exit(main())
