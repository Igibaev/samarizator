"""Bounded PCM analysis: no denoising, no full-recording arrays, no extra model."""

import math
import sys
import wave
from array import array

from .media import extract


def frames(path):
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
            raise ValueError("Анализу нужен mono PCM16 16 kHz.")
        while raw := wav.readframes(320):
            samples = array("h", raw)
            if sys.byteorder != "little":
                samples.byteswap()
            yield samples


def diagnose(path):
    total = clipped = 0
    square = 0
    peak = 0
    for samples in frames(path):
        total += len(samples)
        square += sum(s * s for s in samples)
        clipped += sum(abs(s) >= 32700 for s in samples)
        peak = max(peak, max(map(abs, samples), default=0))
    rms = math.sqrt(square / max(1, total)) / 32768
    dbfs = 20 * math.log10(max(rms, 1e-8))
    warnings = []
    if peak == 0:
        warnings.append("нет звукового сигнала")
    elif dbfs < -38:
        warnings.append("тихий сигнал или много пауз")
    if clipped / max(1, total) > 0.001:
        warnings.append("возможное искажение: отсечение пиков")
    return dict(rms_dbfs=round(dbfs, 1), clipped_fraction=clipped / max(1, total), warnings=warnings)


def nearest_pause(path, target, radius=6):
    """Return a pause midpoint in local seconds, or the unchanged target.

    Energy is only used to choose a cut, never to discard quiet speech.
    """
    quiet_start = None
    pauses = []
    end = 0.0
    for i, samples in enumerate(frames(path)):
        start, end = i * 0.02, (i + 1) * 0.02
        quiet = sum(s * s for s in samples) / len(samples) < (32768 * 10 ** (-38 / 20)) ** 2
        if quiet and quiet_start is None:
            quiet_start = start
        if not quiet and quiet_start is not None:
            if start - quiet_start >= 0.35:
                pauses.append((quiet_start, start))
            quiet_start = None
    if quiet_start is not None and end - quiet_start >= 0.35:
        pauses.append((quiet_start, end))
    # Stay within the pause, keeping at least 150 ms on either side of the cut.
    candidates = [max(a + 0.15, min(target, b - 0.15)) for a, b in pauses]
    candidates = [c for c in candidates if abs(c - target) <= radius]
    return min(candidates, key=lambda c: abs(c - target), default=target)


def plan_chunks(source, duration, seconds, work, pauses=False):
    bounds = [0.0]
    # Anchor to original targets, so shifts cannot accumulate over long recordings.
    for index in range(1, math.ceil(duration / seconds)):
        target = index * seconds
        cut = target
        if pauses:
            offset = max(0, target - 6)
            wav = work / "boundary.wav"
            extract(source, wav, work, offset, min(duration, target + 6) - offset)
            cut = offset + nearest_pause(wav, target - offset)
            wav.unlink()
        if not pauses or bounds[-1] + 15 <= cut <= duration - 5:
            bounds.append(cut)
    bounds.append(duration)
    return list(zip(bounds, bounds[1:]))
