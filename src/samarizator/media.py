import hashlib
import json
import math
import re
import shutil
from pathlib import Path

from .process import run_command

_TOKEN = re.compile(r"\S+")


def fingerprint(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        while block := f.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def probe(path, work):
    out = work / "probe.json"
    run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=channels,duration:format=duration",
            "-of",
            "json",
            str(path),
        ],
        out,
        60,
    )
    info = json.loads(out.read_text())
    streams = info.get("streams", [])
    if not streams:
        raise ValueError("В файле нет аудиодорожки.")
    duration = float(info.get("format", {}).get("duration") or streams[0].get("duration") or 0)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Не удалось определить длительность записи.")
    return duration, int(streams[0]["channels"])


def extract(source, target, work, start=0, duration=None, channel=None):
    args = [
        "ffmpeg",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-threads",
        "1",
        "-ss",
        str(start),
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
    ]
    if duration is not None:
        args += ["-t", str(duration)]
    if channel is not None:
        args += ["-af", f"pan=mono|c0=c{channel}"]
    args += ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-threads", "1", str(target)]
    run_command(args, work / "ffmpeg.log")


def assign_speaker(start, end, turns):
    scores = {}
    for turn in turns:
        if turn["start"] >= end:
            break
        overlap = max(0, min(end, turn["end"]) - max(start, turn["start"]))
        if overlap:
            speaker = turn["speaker"]
            scores[speaker] = scores.get(speaker, 0) + overlap
    if not scores:
        return "Не определён", True
    ordered = sorted(scores.items(), key=lambda s: s[1], reverse=True)
    uncertain = ordered[0][1] < (end - start) * 0.5 or (
        len(ordered) > 1 and ordered[1][1] > ordered[0][1] * 0.3
    )
    if len(ordered) > 1 and ordered[1][1] > ordered[0][1] * 0.7:
        return " / ".join(s[0] for s in ordered[:2]), True
    return ordered[0][0], uncertain


def parse_whisper(payload, offset, lower, upper, turns=(), channel=None):
    """Select overlap by segment midpoint. Boundaries are reviewable, never silently truncated."""
    output = []
    if not isinstance(payload.get("transcription"), list):
        raise ValueError("Неподдерживаемый JSON Whisper: отсутствует transcription.")
    for row in payload["transcription"]:
        start = offset + float(row["offsets"]["from"]) / 1000
        end = offset + float(row["offsets"]["to"]) / 1000
        text = row["text"].strip()
        if not text or end <= start or not lower <= (start + end) / 2 < upper:
            continue
        if channel is not None:
            speaker, uncertain = f"Канал {channel + 1}", False
        else:
            speaker, uncertain = assign_speaker(start, end, turns)
        # Explicitly flag chunk seams for review; alignment is not sample exact.
        seam = (lower > 0 and start < lower + 1) or end > upper - 1
        reasons = []
        if uncertain:
            reasons.append("говорящий")
        if seam:
            reasons.append("граница фрагмента")
        # Token scores are decoder signals, not calibrated word accuracy probabilities.
        scores = [
            t["p"]
            for t in row.get("tokens", [])
            if isinstance(t.get("p"), (int, float))
            and t.get("text", "").strip()
            and not t["text"].startswith("[_")
        ]
        if scores and (sum(scores) / len(scores) < 0.55 or sum(p < 0.2 for p in scores) >= 2):
            reasons.append("низкая уверенность Whisper")
        output.append(
            dict(
                start=max(0, start),
                end=end,
                text=text,
                speaker=speaker,
                uncertain=bool(reasons),
                review=", ".join(reasons),
            )
        )
    return output


def dedup_seam(prev_text, text, max_words=12):
    """Trim a leading run of words in `text` that exactly repeats the tail of
    `prev_text`, for two adjacent chunks whose padded audio overlapped at a seam.

    Comparison is case/punctuation-insensitive; only an exact matching run is
    removed, never guessed or reworded. Returns (trimmed_text, words_removed);
    an empty trimmed_text means the whole segment was a repeat of the previous
    chunk's tail and should be dropped, not kept as an empty row.
    """
    prev_tokens = _TOKEN.findall(prev_text)[-max_words:]
    matches = list(_TOKEN.finditer(text))
    norm = [re.sub(r"^\W+|\W+$", "", m.group()).lower() for m in matches[:max_words]]
    prev_norm = [re.sub(r"^\W+|\W+$", "", t).lower() for t in prev_tokens]
    overlap = 0
    for n in range(min(len(prev_norm), len(norm)), 0, -1):
        if prev_norm[-n:] == norm[:n] and all(prev_norm[-n:]):
            overlap = n
            break
    if not overlap:
        return text, 0
    if overlap == len(matches):
        return "", overlap
    return text[matches[overlap].start() :].lstrip(), overlap


def whisper(wav, model, language, threads, work, gpu=False, *, vad_model="", glossary="", beam_size=5):
    binary = shutil.which("whisper-cli")
    if not binary:
        raise ValueError("whisper-cli не найден. Запустите ./start.sh для установки.")
    prefix = work / "whisper-result"
    result = prefix.with_suffix(".json")
    result.unlink(missing_ok=True)
    args = [binary, "-m", str(model), "-f", str(wav), "-l", language, "-t", str(threads)]
    if not gpu:
        # Metal allocations stay outside the RSS the watchdog can see.
        args.append("-ng")
    args += ["-ojf", "-of", str(prefix), "-ml", "80", "-sow", "-bs", str(beam_size)]
    if glossary.strip():
        # Hints only; do not carry unreviewed ASR text into the next chunk.
        args += ["--prompt", glossary.strip()[:800]]
    if vad_model:
        args += [
            "--vad",
            "--vad-model",
            str(vad_model),
            "--vad-threshold",
            "0.4",
            "--vad-min-speech-duration-ms",
            "150",
            "--vad-min-silence-duration-ms",
            "500",
            "--vad-speech-pad-ms",
            "250",
        ]
    run_command(args, work / "whisper.log")
    return json.loads(result.read_text())
