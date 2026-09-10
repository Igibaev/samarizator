import json
import math
import struct
import wave

import pytest

from samarizator.audio_quality import diagnose, nearest_pause, plan_chunks
from samarizator.config import Settings
from samarizator.knowledge import export
from samarizator.media import parse_whisper, whisper
from samarizator.summary import summarize, validate_summary
from samarizator.worker import transcribe


def pcm(path, seconds=12, pause=None, amplitude=5000):
    with wave.open(str(path), "wb") as f:
        f.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        for second in range(seconds):
            samples = []
            for i in range(16000):
                t = second + i / 16000
                quiet = pause and pause[0] <= t < pause[1]
                samples.append(0 if quiet else int(amplitude * math.sin(2 * math.pi * 440 * t)))
            f.writeframes(struct.pack("<16000h", *samples))


def test_pause_boundary_does_not_remove_audio(tmp_path):
    path = tmp_path / "pause.wav"
    pcm(path, pause=(7, 8))
    assert 7.15 <= nearest_pause(path, 6) <= 7.85
    pcm(path)
    assert nearest_pause(path, 6) == 6
    # Existing recordings preserve the exact fixed geometry, including a short final chunk.
    assert plan_chunks(path, 61, 30, tmp_path) == [(0, 30), (30, 60), (60, 61)]


def test_audio_diagnostics_distinguish_silence_and_clipping(tmp_path):
    path = tmp_path / "audio.wav"
    pcm(path, seconds=1, amplitude=0)
    assert diagnose(path)["warnings"] == ["нет звукового сигнала"]
    pcm(path, seconds=1, amplitude=32767)
    assert diagnose(path)["clipped_fraction"] > 0.001
    pcm(path, seconds=1)
    assert diagnose(path)["warnings"] == []


def test_profile_keeps_user_models_and_api(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path))
    s = Settings(whisper_model="my-better-model.bin", base_url="https://corp/v1", model="corp")
    q = s.quality_profile()
    assert q.whisper_model == s.whisper_model and q.base_url == s.base_url and q.model == s.model
    assert q.memory_gb == 16 and q.vad and q.pause_boundaries and not q.gpu
    assert s.memory_gb == 4  # profile does not mutate the settings behind an open dialog


def test_low_token_scores_flag_review_without_deleting_text():
    payload = dict(
        transcription=[
            dict(
                offsets={"from": 2000, "to": 4000},
                text="Не утверждено 15 миллионов",
                tokens=[dict(text="Не", p=0.1), dict(text="утверждено", p=0.15)],
            )
        ]
    )
    row = parse_whisper(payload, 0, 0, 30, channel=0)[0]
    assert row["text"] == "Не утверждено 15 миллионов"
    assert row["uncertain"] and "Whisper" in row["review"]


def test_decoder_receives_vad_and_literal_glossary(tmp_path, monkeypatch):
    calls = []

    def run(args, *a, **kw):
        calls.append(args)
        (tmp_path / "whisper-result.json").write_text('{"transcription": []}')

    monkeypatch.setattr("samarizator.media.shutil.which", lambda _: "whisper-cli")
    monkeypatch.setattr("samarizator.media.run_command", run)
    whisper("audio.wav", "asr.bin", "ru", 8, tmp_path, vad_model="vad.bin", glossary="EBITDA, Иванов")
    assert "-ojf" in calls[0] and "--vad" in calls[0]
    assert calls[0][calls[0].index("--prompt") + 1] == "EBITDA, Иванов"


def test_resume_uses_saved_pause_plan_and_never_duplicates(meeting, tmp_path, monkeypatch):
    store, mid, settings = meeting
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    settings.whisper_model = str(model)
    settings.pause_boundaries = True
    store.save_checkpoint(mid, "asr-plan", 0, [[0, 28], [28, 60]])
    store.save_chunk(mid, 0, [dict(start=2, end=4, speaker="A", text="first")])
    monkeypatch.setattr("samarizator.worker.probe", lambda *a: (60, 1))
    monkeypatch.setattr("samarizator.worker.plan_chunks", lambda *a: pytest.fail("must reuse plan"))
    monkeypatch.setattr("samarizator.worker.extract", lambda source, target, *a: pcm(target, seconds=1))
    calls = []

    def asr(*a, **kw):
        calls.append(1)
        return dict(transcription=[dict(offsets={"from": 2500, "to": 4500}, text="second")])

    monkeypatch.setattr("samarizator.worker.whisper", asr)
    transcribe(store, mid, settings, tmp_path)
    transcribe(store, mid, settings, tmp_path)
    rows = store.segments(mid)
    assert len(rows) == 2 and len(calls) == 1
    assert rows[1]["start"] == 28.5
    assert store.checkpoint(mid, "audio-quality", 1)


def test_detailed_summary_preserves_facts_dropped_from_brief_and_cancelled_tasks(meeting):
    store, mid, settings = meeting
    store.save_chunk(mid, 0, [dict(start=0, end=1, speaker="A", text="Релиз отменён. Бюджет 17 млн.")])
    sid = store.segments(mid)[0]["id"]
    cancelled = dict(
        kind="action", text="Релиз отменён", evidence=[sid], owner=None, due=None, status="cancelled"
    )
    budget = dict(kind="point", text="Бюджет 17 млн", evidence=[sid], owner=None, due=None)

    class Fake:
        def complete(self, prompt, allowed):
            detailed = "Подготовь подробную" in prompt
            return dict(
                overview="Итог",
                items=[cancelled, budget] if detailed else [cancelled],
                topics=["Бюджет"] if detailed else [],
            )

    result = summarize(store, mid, settings, client=Fake())
    assert len(result["brief"]["items"]) == 1
    assert len(result["detailed"]["items"]) == 2
    store.update(mid, summary=json.dumps(result))
    content = export(store, mid, settings).read_text()
    assert "## Кратко · тезисы" in content and "## Подробная сводка" in content
    assert "17 млн" in content and "[[Topics/" in content
    assert "[отменено]" in content and "- [ ]" not in content
    cancelled["status"] = "made-up"
    with pytest.raises(ValueError, match="статус"):
        validate_summary(dict(overview="", items=[cancelled], topics=[]), {sid})


def test_memory_monitor_fails_closed(monkeypatch):
    import sys

    from samarizator.process import supervise

    monkeypatch.setattr("samarizator.process.rss_tree", lambda _: 0)
    with pytest.raises(RuntimeError, match="измерить память"):
        supervise([sys.executable, "-c", "import time; time.sleep(30)"], 16)
