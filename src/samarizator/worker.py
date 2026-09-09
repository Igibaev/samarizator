import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .config import Settings, data_dir
from .media import extract, fingerprint, parse_whisper, probe, whisper
from .process import run_command
from .store import Store


def status(store, mid, message):
    store.update(mid, error=message)


def transcribe(store, mid, settings, work):
    meeting = store.meeting(mid)
    source = Path(meeting["source"])
    if not source.is_file():
        raise ValueError("Исходный файл перемещён. Верните его по прежнему пути или импортируйте заново.")
    settings.validate()
    if not Path(settings.whisper_model).is_file():
        raise ValueError("Модель Whisper не найдена. Запустите ./start.sh или выберите .bin в настройках.")
    if Path(settings.whisper_model).stat().st_size * 2.5 + 600 * 1024**2 > settings.memory_gb * 1024**3 * 0.8:
        raise ValueError(
            "Выбранная модель слишком велика для бюджета. Выберите small-q5_1/base или увеличьте бюджет."
        )
    status(store, mid, "Проверка исходного файла…")
    digest = fingerprint(source)
    old = store.checkpoint(mid, "source", 0)
    if old and old != digest:
        raise ValueError("Исходный файл изменился. Импортируйте его как новую запись.")
    store.save_checkpoint(mid, "source", 0, digest)
    duration, channels = probe(source, work)
    store.update(mid, duration=duration, channels=channels)
    if settings.diarization == "channels" and channels != 2:
        raise ValueError("Режиму отдельных каналов нужна стереозапись: один участник слева, другой справа.")
    turns = []
    if settings.diarization == "local":
        turns = store.checkpoint(mid, "diarization", 0)
        if turns is None:
            for path in [settings.segmentation_model, settings.embedding_model]:
                if not Path(path).is_file():
                    raise ValueError(
                        "Модели собеседников не найдены. Запустите ./start.sh "
                        "или выберите режим отдельных каналов / ручной."
                    )
            # ONNX clustering covers the complete recording, keeping IDs stable across ASR chunks.
            # Conservative input/copy estimate; runtime watchdog remains authoritative.
            if duration * 16000 * 4 * 4 + 800 * 1024**2 > settings.memory_gb * 1024**3 * 0.8:
                raise ValueError(
                    "Для автоматического определения собеседников этой длинной записи "
                    "нужен больший бюджет. Увеличьте память или выберите ручной режим."
                )
            status(store, mid, "Локальное определение собеседников…")
            wav = work / "diarization.wav"
            extract(source, wav, work)
            target = work / "turns.json"
            run_command(
                [
                    sys.executable,
                    "-m",
                    "samarizator.diarize",
                    str(wav),
                    settings.segmentation_model,
                    settings.embedding_model,
                    str(settings.speakers),
                    str(settings.threads),
                    str(target),
                ],
                work / "diarization.log",
                timeout=24 * 3600,
            )
            turns = json.loads(target.read_text())
            store.save_checkpoint(mid, "diarization", 0, turns)
            wav.unlink()
    count = math.ceil(duration / settings.chunk_seconds)
    for index in range(count):
        if store.checkpoint(mid, "asr", index):
            continue
        lower = index * settings.chunk_seconds
        upper = min(duration, lower + settings.chunk_seconds)
        start = max(0, lower - 2)
        length = min(duration, upper + 2) - start
        status(store, mid, f"Whisper: фрагмент {index + 1}/{count}")
        rows = []
        for channel in [0, 1] if settings.diarization == "channels" else [None]:
            wav = work / "chunk.wav"
            extract(source, wav, work, start, length, channel)
            result = whisper(wav, settings.whisper_model, settings.language, settings.threads, work)
            rows += parse_whisper(result, start, lower, upper, turns or [], channel)
            wav.unlink()
        store.save_chunk(mid, index, sorted(rows, key=lambda r: r["start"]))
    if not store.segments(mid, limit=1):
        raise ValueError("Whisper не обнаружил речь. Проверьте аудиодорожку и язык.")
    store.save_checkpoint(mid, "asr_complete", 0, True)
    store.update(mid, status="review", error=None)


def main():
    phase, mid = sys.argv[1:3]
    store = Store()
    settings = Settings(**json.loads(store.meeting(mid)["settings"]))
    os.environ["SAMARIZATOR_THREADS"] = str(settings.threads)
    work_root = data_dir() / "work"
    work_root.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=work_root, prefix=mid + "-") as temp:
            if phase == "transcribe":
                transcribe(store, mid, settings, Path(temp))
            elif phase == "summary":
                from .knowledge import export
                from .summary import summarize

                result = summarize(store, mid, settings, lambda msg: status(store, mid, msg))
                store.update(mid, summary=json.dumps(result, ensure_ascii=False))
                export(store, mid, settings)
                store.update(mid, status="done", error=None)
            elif phase == "export":
                from .knowledge import export

                export(store, mid, settings)
                store.update(mid, status="done", error=None)
            else:
                raise ValueError("Неизвестная операция.")
    except Exception as exc:
        # Controlled validation errors only; third-party errors can include request headers or text.
        safe = (
            str(exc)
            if isinstance(exc, (ValueError, RuntimeError))
            else f"Ошибка {type(exc).__name__}. Проверьте настройки и зависимости."
        )
        store.update(mid, status="error", error=safe[:1000])
        return 1
    return 0


def cleanup():
    root = data_dir() / "work"
    if root.exists():
        for folder in root.iterdir():
            if folder.is_dir() and not folder.is_symlink():
                shutil.rmtree(folder)


if __name__ == "__main__":
    sys.exit(main())
