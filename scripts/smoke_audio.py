"""Real models + FFmpeg integration. Does not contact a summarization API."""

import sys
import tempfile
from pathlib import Path

from samarizator.config import Settings
from samarizator.store import Store
from samarizator.worker import transcribe


class SmokeStore(Store):
    def update(self, mid, **values):
        super().update(mid, **values)
        if values.get("error"):
            print(values["error"], flush=True)


def main():
    settings = Settings.load()
    settings.language = "en"
    settings.memory_gb = 4
    settings.speakers = 1
    settings.chunk_seconds = 30
    with tempfile.TemporaryDirectory() as folder:
        store = SmokeStore(Path(folder) / "smoke.sqlite3")
        mid = store.create(sys.argv[1], settings)
        transcribe(store, mid, settings, Path(folder))
        rows = store.segments(mid)
        assert rows, "No speech recognized"
        assert any(r["speaker"] != "Не определён" for r in rows), "No diarization result"
        assert store.checkpoint(mid, "asr_complete", 0)
        assert settings.vad and settings.pause_boundaries, (
            "Quality profile must be enabled in this smoke test"
        )
        assert len(store.checkpoint(mid, "asr-plan", 0)) > 1, "Need audio across a chunk boundary"
        assert store.checkpoint(mid, "audio-quality", 0)
        assert all(0 <= r["start"] < r["end"] <= store.meeting(mid)["duration"] + 1 for r in rows)
        print(
            f"Real audio smoke passed: {len(rows)} segments; speakers: {sorted({r['speaker'] for r in rows})}"
        )
        # Keep transcript out of CI logs.


if __name__ == "__main__":
    main()
