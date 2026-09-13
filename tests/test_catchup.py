"""Catch-up recognition during a live recording.

The point of these tests is that audio recognised while recording is planned and
cut exactly like audio recognised afterwards: the quality of the final transcript
must not depend on when the recognition happened.
"""

import math
import shutil
import struct
import subprocess
import threading
import wave
from pathlib import Path

import pytest

from samarizator.audio_quality import cut_points, plan_chunks
from samarizator.worker import catchup, transcribe

pytestmark = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")


def fake_whisper(work_dir):
    """Whisper stand-in: reports one phrase per chunk, named by its own audio length."""

    def run(wav, *args, **kwargs):
        with wave.open(str(wav)) as f:
            seconds = f.getnframes() / f.getframerate()
        middle = int(seconds * 500)  # place the phrase mid-window, clear of the padding
        return dict(
            transcription=[
                dict(offsets={"from": middle, "to": middle + 1000}, text=f"фрагмент {seconds:.1f}с"),
            ]
        )

    return run


def growing_wav(path, seconds, rate=16000):
    """Write a WAV the way the recorder does: flushed as it goes, header finalised at close."""
    return subprocess.Popen(
        [
            # readrate writes faster than real time while still growing the file
            # incrementally, so the test observes real FFmpeg flushing without waiting.
            "ffmpeg", "-v", "error", "-y", "-readrate", "10",
            "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate={rate}:duration={seconds}",
            "-ac", "1", "-ar", str(rate), "-c:a", "pcm_s16le", "-flush_packets", "1", str(path),
        ],
        stdin=subprocess.DEVNULL,
    )


def settled_wav(path, seconds, rate=16000):
    with wave.open(str(path), "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        frames = bytearray()
        for index in range(int(rate * seconds)):
            frames += struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * index / rate)))
        wav.writeframes(bytes(frames))


def test_catchup_recognises_chunks_while_the_file_is_still_growing(meeting, tmp_path, monkeypatch):
    store, mid, settings = meeting
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    settings.whisper_model = str(model)
    settings.chunk_seconds = 30
    source = Path(store.meeting(mid)["source"])
    monkeypatch.setattr("samarizator.worker.whisper", fake_whisper(tmp_path))

    recorder = growing_wav(source, seconds=75)
    try:
        # The marker appears once the recording process is done, exactly as the window does it.
        def watch():
            recorder.wait()
            store.save_checkpoint(mid, "live-stopped", 0, True)

        threading.Thread(target=watch, daemon=True).start()
        catchup(store, mid, settings, tmp_path, poll=0.5)
    finally:
        recorder.poll() is None and recorder.kill()

    plan = store.checkpoint(mid, "asr-plan", 0)
    # Two settled cuts at 30 and 60 seconds; the tail is deliberately left to the closing pass.
    assert [round(upper) for _, upper in plan] == [30, 60]
    assert len(store.segments(mid)) == len(plan)
    assert not store.checkpoint(mid, "asr_complete", 0)


def test_closing_pass_keeps_catchup_boundaries_and_finishes_the_tail(meeting, tmp_path, monkeypatch):
    store, mid, settings = meeting
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    settings.whisper_model = str(model)
    settings.chunk_seconds = 30
    source = Path(store.meeting(mid)["source"])
    settled_wav(source, seconds=75)
    monkeypatch.setattr("samarizator.worker.whisper", fake_whisper(tmp_path))

    # Catch-up already handled the first two chunks of this recording.
    store.save_checkpoint(mid, "asr-plan", 0, [[0, 30], [30, 60]])
    store.save_chunk(mid, 0, [dict(start=1, end=2, speaker="A", text="первый", uncertain=False)])
    store.save_chunk(mid, 1, [dict(start=31, end=32, speaker="A", text="второй", uncertain=False)])

    transcribe(store, mid, settings, tmp_path)

    plan = store.checkpoint(mid, "asr-plan", 0)
    assert [(round(a), round(b)) for a, b in plan] == [(0, 30), (30, 60), (60, 75)]
    texts = [row["text"] for row in store.segments(mid)]
    # The two finished chunks are kept as they were; only the tail is new.
    assert texts[:2] == ["первый", "второй"]
    assert len(texts) == 3
    assert store.checkpoint(mid, "asr_complete", 0)


def test_catchup_plan_matches_the_plan_of_a_finished_recording(tmp_path):
    """Same boundaries whether planned live or in one go: identical chunks, identical quality."""
    source = tmp_path / "recording.wav"
    settled_wav(source, seconds=150)
    whole = plan_chunks(source, 150, 30, tmp_path, pauses=True)

    bounds = [0.0]
    for available in [40, 50, 75, 100, 130, 150]:
        bounds = [0.0, *cut_points(source, available, 30, tmp_path, True, bounds=bounds, growing=True)]
    live_then_closed = [0.0, *cut_points(source, 150, 30, tmp_path, True, bounds=bounds), 150]

    # Live planning settles every cut except the last, which only the closing pass can place.
    assert [round(b, 3) for b in bounds[1:]] == [round(upper, 3) for _, upper in whole[:-1]]
    assert [(round(a, 3), round(b, 3)) for a, b in zip(live_then_closed, live_then_closed[1:])] == [
        (round(a, 3), round(b, 3)) for a, b in whole
    ]
