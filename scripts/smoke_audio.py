"""Real models + FFmpeg integration. Does not contact a summarization API.

Run manually on Mac (no CI): `uv run python scripts/smoke_audio.py path/to.wav`.
Logs and intermediate files are kept under ./smoke-logs/<timestamp>/ instead of
a temp dir that vanishes on error/timeout, so a failed run can be inspected.
"""

import sys
import time
from pathlib import Path

from samarizator.audio_quality import plan_chunks
from samarizator.config import Settings
from samarizator.media import probe
from samarizator.store import Store
from samarizator.worker import transcribe


class SmokeStore(Store):
    def update(self, mid, **values):
        super().update(mid, **values)
        if values.get("error"):
            print(values["error"], flush=True)


def main():
    source = Path(sys.argv[1])
    settings = Settings.load()
    settings.language = "en"
    settings.memory_gb = 4
    settings.chunk_seconds = 30

    folder = Path("smoke-logs") / str(int(time.time()))
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Logs and intermediate files kept at {folder} (not deleted on failure).")

    duration, channels = probe(source, folder)
    print(f"ffprobe: duration={duration:.1f}s channels={channels}")
    # This smoke test asserts a chunk boundary was exercised; verify that is even
    # possible before paying for ASR, instead of discovering it after a long run.
    needed = settings.chunk_seconds * 2 + 20
    if duration < needed:
        sys.exit(
            f"Fixture is only {duration:.1f}s; need at least {needed:.0f}s to reach a second "
            f"chunk (chunk_seconds={settings.chunk_seconds}). Use a longer recording."
        )
    plan = plan_chunks(source, duration, settings.chunk_seconds, folder, settings.pause_boundaries)
    print(f"Chunk boundary plan: {[(round(a, 1), round(b, 1)) for a, b in plan]}")
    if len(plan) < 2:
        sys.exit("Boundary plan collapsed to one fragment despite fixture length; fix before running ASR.")

    store = SmokeStore(folder / "smoke.sqlite3")
    mid = store.create(str(source), settings)
    transcribe(store, mid, settings, folder)
    rows = store.segments(mid)
    assert rows, "No speech recognized"
    assert store.checkpoint(mid, "asr_complete", 0)
    assert settings.vad and settings.pause_boundaries, "Quality profile must be enabled in this smoke test"
    assert len(store.checkpoint(mid, "asr-plan", 0)) > 1, "Need audio across a chunk boundary"
    assert store.checkpoint(mid, "audio-quality", 0)
    assert all(0 <= r["start"] < r["end"] <= store.meeting(mid)["duration"] + 1 for r in rows)
    print(f"Real audio smoke passed: {len(rows)} segments")
    # Keep transcript out of logs.


if __name__ == "__main__":
    main()
