"""Bounded PCM analysis: no denoising, no full-recording arrays, no extra model."""

import math
import sys
import wave
from array import array
from pathlib import Path

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
    return dict(
        rms_dbfs=round(dbfs, 1),
        peak=peak / 32768,
        clipped_fraction=clipped / max(1, total),
        warnings=warnings,
    )


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


# A cut at `target` is decided from audio in [target - 6, target + 6], and the
# acceptance test below also needs `duration - 5` to be past it. Once this much
# audio exists, the cut is final: planning a prefix yields the same boundaries the
# whole recording would, which is what lets live catch-up and the closing pass agree.
SETTLED_MARGIN = 11


def cut_points(source, duration, seconds, work, pauses=False, bounds=None, growing=False):
    """Anchored boundaries between chunks, without the opening 0 and closing duration.

    `bounds` carries boundaries already decided, so a growing recording keeps the
    ones it settled earlier. `growing` holds back cuts too close to the end of what
    has been recorded so far: they would be decided on incomplete audio.
    """
    bounds = list(bounds or [0.0])
    for index in range(1, math.ceil(duration / seconds)):
        target = index * seconds
        if target <= bounds[-1] or (growing and target + SETTLED_MARGIN > duration):
            continue
        cut = target
        if pauses:
            offset = max(0, target - 6)
            wav = work / "boundary.wav"
            extract(source, wav, work, offset, min(duration, target + 6) - offset)
            cut = offset + nearest_pause(wav, target - offset)
            wav.unlink()
        if not pauses or bounds[-1] + 15 <= cut <= duration - 5:
            bounds.append(cut)
    return bounds[1:]


def plan_chunks(source, duration, seconds, work, pauses=False):
    bounds = [0.0, *cut_points(source, duration, seconds, work, pauses), duration]
    return list(zip(bounds, bounds[1:]))


def track_levels(path, tracks, work, seconds=None):
    """Measure each channel of a live recording separately.

    A merged file hides which source went missing: system audio can be loud while
    the microphone track is digital silence, and the mix still sounds fine.
    `seconds` bounds the work, because this analysis walks samples in Python.
    """
    levels = []
    for channel, name in enumerate(tracks):
        mono = Path(work) / f"track-{channel}.wav"
        extract(path, mono, Path(work), duration=seconds, channel=channel)
        levels.append(dict(track=name, channel=channel, **diagnose(mono)))
        mono.unlink(missing_ok=True)
    return levels


def level_profile(path, tracks, work, seconds=180, step=1.0):
    """Per-second loudness of each track, to see pumping instead of guessing at it.

    Automatic gain control and echo cancellation live outside this app, in macOS mic
    modes and in the meeting client. Their signature is a level that drops in step
    with the other side speaking — visible here, inaudible in a single number.
    """
    profile = []
    for channel, name in enumerate(tracks):
        mono = Path(work) / f"profile-{channel}.wav"
        extract(path, mono, Path(work), duration=seconds, channel=channel)
        window = int(16000 * step)
        levels, block, total = [], 0, 0
        for samples in frames(mono):
            for sample in samples:
                block += sample * sample
                total += 1
                if total >= window:
                    levels.append(20 * math.log10(max(math.sqrt(block / total), 1e-8) / 32768))
                    block, total = 0, 0
        if total:
            levels.append(20 * math.log10(max(math.sqrt(block / total), 1e-8) / 32768))
        mono.unlink(missing_ok=True)
        profile.append(dict(track=name, channel=channel, levels=[round(x, 1) for x in levels]))
    return profile


def ducking(profile, quiet_gap=8.0, loud=-35.0):
    """Share of seconds where one track dips while the other is loud.

    A high share is the fingerprint of gain control reacting to the far side: the
    microphone is turned down exactly while somebody else speaks.
    """
    if len(profile) < 2:
        return None
    mic, system = profile[0]["levels"], profile[1]["levels"]
    pairs = list(zip(mic, system))
    speaking = [(m, s) for m, s in pairs if s > loud]
    if len(speaking) < 5:
        return None
    quiet = [m for m, _ in pairs if m <= loud]
    reference = sorted(m for m, s in pairs if s <= loud)
    if not reference:
        return None
    typical = reference[len(reference) // 2]
    dips = sum(1 for m, _ in speaking if typical - m >= quiet_gap)
    return dict(
        share=dips / len(speaking),
        seconds=len(speaking),
        typical=round(typical, 1),
        quiet=len(quiet),
    )
