import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from .audio_quality import diagnose, plan_chunks
from .config import Settings, data_dir
from .media import dedup_seam, extract, fingerprint, parse_whisper, probe, whisper
from .process import run_command
from .store import Store


def status(store, mid, message):
    store.update(mid, error=message)


def speaker_turns(store, mid, settings, source, duration, work, force=False):
    turns = None if force else store.checkpoint(mid, "diarization", 0)
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
    if not turns:
        raise ValueError(
            "Локальная модель не обнаружила собеседников. Проверьте аудиодорожку и модели голоса."
        )
    return turns


def transcribe(store, mid, settings, work):
    meeting = store.meeting(mid)
    source = Path(meeting["source"])
    if not source.is_file():
        raise ValueError("Исходный файл перемещён. Верните его по прежнему пути или импортируйте заново.")
    settings.validate()
    if not Path(settings.whisper_model).is_file():
        raise ValueError("Модель Whisper не найдена. Запустите ./start.sh или выберите .bin в настройках.")
    if settings.vad and not Path(settings.vad_model).is_file():
        raise ValueError("Модель VAD не найдена. Запустите ./start.sh --quality или отключите VAD.")
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
        turns = speaker_turns(store, mid, settings, source, duration, work)
    plan = store.checkpoint(mid, "asr-plan", 0)
    if plan is None:
        status(store, mid, "Подбор границ фрагментов по паузам…")
        plan = plan_chunks(source, duration, settings.chunk_seconds, work, settings.pause_boundaries)
        store.save_checkpoint(mid, "asr-plan", 0, plan)
    count = len(plan)
    for index, (lower, upper) in enumerate(plan):
        if store.checkpoint(mid, "asr", index):
            continue
        start = max(0, lower - 2)
        length = min(duration, upper + 2) - start
        status(store, mid, f"Whisper: фрагмент {index + 1}/{count}")
        rows = []
        diagnostics = []
        for channel in [0, 1] if settings.diarization == "channels" else [None]:
            wav = work / "chunk.wav"
            extract(source, wav, work, start, length, channel)
            diagnostics.append(dict(channel=channel, **diagnose(wav)))
            result = whisper(
                wav,
                settings.whisper_model,
                settings.language,
                settings.threads,
                work,
                settings.gpu,
                vad_model=settings.vad_model if settings.vad else "",
                glossary=settings.glossary,
                beam_size=settings.beam_size,
            )
            rows += parse_whisper(result, start, lower, upper, turns or [], channel)
            wav.unlink()
        rows = sorted(rows, key=lambda r: r["start"])
        # Text equality cannot prove that two utterances are the same audio.
        # Preserve every word; only flag plausible overlap for human review.
        if index > 0 and rows and settings.diarization != "channels":
            previous = store.last_segment(mid)
            first = rows[0]
            if (
                previous
                and previous["speaker"] == first["speaker"]
                and previous["end"] > first["start"]
                and first["start"] < lower + 2
                and previous["end"] > lower - 2
            ):
                _, overlap = dedup_seam(previous["text"], first["text"])
                if overlap:
                    reasons = [
                        r for r in [first["review"], "возможный повтор на стыке — сверить по аудио"] if r
                    ]
                    rows[0] = dict(first, uncertain=True, review=", ".join(reasons))
        store.save_checkpoint(mid, "audio-quality", index, diagnostics)
        store.save_chunk(mid, index, rows)
    if not store.segments(mid, limit=1):
        raise ValueError("Whisper не обнаружил речь. Проверьте аудиодорожку и язык.")
    store.save_checkpoint(mid, "asr_complete", 0, True)
    store.update(mid, status="review", error=None)


def retry_uncertain(store, mid, settings, work, limit=20, pad=2.0):
    """Second ASR pass, with edge context, for segments already flagged for review.

    Only stores the alternate text (Store.save_retry); the transcript is not
    changed until the user explicitly accepts it (Store.accept_retry). Bounded
    to `limit` segments per call so a large backlog cannot run unattended.
    """
    meeting = store.meeting(mid)
    source = Path(meeting["source"])
    duration = meeting["duration"]
    settings.validate()
    if not source.is_file():
        raise ValueError("Исходный файл не найден. Верните его по прежнему пути.")
    expected = store.checkpoint(mid, "source", 0)
    if not expected or fingerprint(source) != expected:
        raise ValueError(
            "Исходный файл изменился или его контрольная сумма не сохранена. Импортируйте запись заново."
        )
    if not Path(settings.whisper_model).is_file():
        raise ValueError("Модель Whisper не найдена. Выберите .bin в настройках.")
    if settings.vad and not Path(settings.vad_model).is_file():
        raise ValueError("Модель VAD не найдена.")
    if Path(settings.whisper_model).stat().st_size * 2.5 + 600 * 1024**2 > settings.memory_gb * 1024**3 * 0.8:
        raise ValueError("Выбранная модель слишком велика для бюджета памяти.")
    targets = []
    for row in store.iter_segments(mid):
        if row["uncertain"] and not row["retry_text"] and not row["retry_done"]:
            targets.append(row)
            if len(targets) >= limit:
                break
    if settings.diarization == "channels" and any(r.get("source_channel") not in (0, 1) for r in targets):
        raise ValueError(
            "У старой записи не сохранён исходный аудиоканал. Импортируйте её заново для второго прохода."
        )
    for index, row in enumerate(targets):
        status(store, mid, f"Повторный проход: реплика {index + 1}/{len(targets)}")
        start = max(0, row["start"] - pad)
        length = min(duration, row["end"] + pad) - start
        wav = work / f"retry-{row['id']}.wav"
        extract(source, wav, work, start, length, channel=row.get("source_channel"))
        result = whisper(
            wav,
            settings.whisper_model,
            settings.language,
            settings.threads,
            work,
            settings.gpu,
            vad_model=settings.vad_model if settings.vad else "",
            glossary=settings.glossary,
            beam_size=settings.beam_size,
        )
        wav.unlink()
        # Filtered by the segment's original bounds, same convention as the padded
        # chunk extraction above: context helps the decoder, it does not widen the text.
        segs = parse_whisper(result, start, row["start"], row["end"])
        store.save_retry(mid, row["id"], " ".join(s["text"] for s in segs).strip())
    store.update(mid, status="review", error=None)


def repair_speakers(store, mid, settings, work):
    settings.validate()
    if settings.diarization != "local":
        raise ValueError(
            "В настройках выберите автоматическое локальное разделение собеседников. Для двух раздельных каналов импортируйте запись заново."
        )
    meeting = store.meeting(mid)
    source = Path(meeting["source"])
    if not source.is_file() or fingerprint(source) != store.checkpoint(mid, "source", 0):
        raise ValueError("Исходный файл изменился или недоступен. Импортируйте запись заново.")
    turns = speaker_turns(store, mid, settings, source, meeting["duration"], work, force=True)
    changed = store.assign_unknown_speakers(mid, turns)
    store.update(
        mid,
        status="review",
        error=None
        if changed
        else "Не удалось сопоставить неопределённые реплики с голосами. Проверьте аудио и таймкоды.",
    )


def main():
    phase, mid = sys.argv[1:3]
    store = Store()
    settings = Settings.from_dict(json.loads(store.meeting(mid)["settings"]))
    os.environ["SAMARIZATOR_THREADS"] = str(settings.threads)
    work_root = data_dir() / "work"
    work_root.mkdir(exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(dir=work_root, prefix=mid + "-") as temp:
            if phase == "transcribe":
                transcribe(store, mid, settings, Path(temp))
            elif phase == "speakers":
                repair_speakers(store, mid, settings, Path(temp))
            elif phase == "retry":
                retry_uncertain(store, mid, settings, Path(temp))
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
