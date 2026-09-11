import json
import math
import struct
import wave

import pytest

from samarizator.audio_quality import diagnose, nearest_pause, plan_chunks
from samarizator.config import Settings
from samarizator.knowledge import export
from samarizator.media import dedup_seam, fingerprint, parse_whisper, whisper
from samarizator.summary import reconcile_decisions, summarize, validate_summary
from samarizator.worker import retry_uncertain, transcribe


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


def test_segments_uncertain_only_filters_and_paginates_flagged_rows(meeting):
    store, mid, settings = meeting
    store.save_chunk(
        mid,
        0,
        [
            dict(start=0, end=1, speaker="A", text="clear one", uncertain=0, review=""),
            dict(start=1, end=2, speaker="A", text="flagged one", uncertain=1, review="граница"),
            dict(start=2, end=3, speaker="A", text="clear two", uncertain=0, review=""),
            dict(start=3, end=4, speaker="A", text="flagged two", uncertain=1, review="граница"),
        ],
    )
    flagged = store.segments(mid, uncertain_only=True)
    assert [r["text"] for r in flagged] == ["flagged one", "flagged two"]
    assert store.segments(mid, offset=1, limit=1, uncertain_only=True)[0]["text"] == "flagged two"
    assert len(store.segments(mid)) == 4  # unfiltered call is unaffected


def test_dedup_seam_trims_exact_repeats_and_leaves_the_rest_untouched():
    trimmed, overlap = dedup_seam("The report is due Friday.", "due Friday afternoon")
    assert overlap == 2 and trimmed == "afternoon"
    trimmed, overlap = dedup_seam("Alice will prepare it.", "Bob is responsible.")
    assert overlap == 0 and trimmed == "Bob is responsible."
    trimmed, overlap = dedup_seam("Same words here", "same words here")
    assert overlap == 3 and trimmed == ""


def test_seam_preserves_equal_words_far_apart(meeting, tmp_path, monkeypatch):
    store, mid, settings = meeting
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    settings.whisper_model = str(model)
    settings.pause_boundaries = True
    store.save_checkpoint(mid, "asr-plan", 0, [[0, 28], [28, 60]])
    monkeypatch.setattr("samarizator.worker.probe", lambda *a: (60, 1))
    monkeypatch.setattr("samarizator.worker.plan_chunks", lambda *a: pytest.fail("must reuse plan"))
    monkeypatch.setattr("samarizator.worker.extract", lambda source, target, *a, **kw: pcm(target, seconds=1))
    calls = []

    def asr(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return dict(
                transcription=[dict(offsets={"from": 0, "to": 3000}, text="The report is due Friday.")]
            )
        # Overlapping padded audio decoded independently produced the same lead-in words.
        return dict(
            transcription=[dict(offsets={"from": 2000, "to": 5000}, text="Friday afternoon we will meet.")]
        )

    monkeypatch.setattr("samarizator.worker.whisper", asr)
    transcribe(store, mid, settings, tmp_path)
    rows = store.segments(mid)
    assert [r["text"] for r in rows] == ["The report is due Friday.", "Friday afternoon we will meet."]
    assert "возможный повтор" not in rows[1]["review"]


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
    monkeypatch.setattr("samarizator.worker.extract", lambda source, target, *a, **kw: pcm(target, seconds=1))
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


def test_retry_pass_only_touches_flagged_segments_and_needs_acceptance(meeting, tmp_path, monkeypatch):
    store, mid, settings = meeting
    model = tmp_path / "model.bin"
    model.write_bytes(b"model")
    settings.whisper_model = str(model)
    store.update(mid, duration=30)
    store.save_checkpoint(mid, "source", 0, fingerprint(store.meeting(mid)["source"]))
    store.save_chunk(
        mid,
        0,
        [
            dict(start=0, end=2, speaker="A", text="clear", uncertain=0, review=""),
            dict(
                start=5, end=7, speaker="A", text="unsure", uncertain=1, review="низкая уверенность Whisper"
            ),
        ],
    )
    rows = store.segments(mid)
    clear_id, flagged_id = rows[0]["id"], rows[1]["id"]
    monkeypatch.setattr("samarizator.worker.extract", lambda source, target, *a, **kw: pcm(target, seconds=1))
    calls = []

    def asr(*a, **kw):
        calls.append(1)
        return dict(transcription=[dict(offsets={"from": 2000, "to": 4000}, text="corrected")])

    monkeypatch.setattr("samarizator.worker.whisper", asr)
    retry_uncertain(store, mid, settings, tmp_path)
    assert len(calls) == 1  # the clean segment is never re-sent to Whisper
    rows = {r["id"]: r for r in store.segments(mid)}
    assert rows[clear_id]["text"] == "clear" and not rows[clear_id]["retry_text"]
    assert rows[flagged_id]["text"] == "unsure"  # original text untouched until accepted
    assert rows[flagged_id]["retry_text"] == "corrected"

    with pytest.raises(ValueError):
        store.accept_retry(mid, clear_id)  # nothing to accept there
    store.accept_retry(mid, flagged_id)
    accepted = {r["id"]: r for r in store.segments(mid)}[flagged_id]
    assert accepted["text"] == "corrected"
    assert accepted["retry_text"] == "" and accepted["retry_done"]
    assert accepted["review"] == "низкая уверенность Whisper"
    store.undo_retry(mid, flagged_id)
    assert store.segments(mid)[1]["text"] == "unsure"


def test_reconcile_decisions_only_sends_decisions_and_actions_to_the_model():
    ledger = [
        dict(kind="point", text="Бюджет обсуждён", evidence=[1], owner=None, due=None, status="unspecified"),
        dict(kind="decision", text="Релиз в пятницу", evidence=[2], owner=None, due=None, status="agreed"),
    ]

    class Fake:
        def __init__(self):
            self.prompts = []

        def complete(self, prompt, allowed):
            self.prompts.append((prompt, allowed))
            return dict(overview="", topics=[], items=[ledger[1]])

    fake = Fake()
    resolved = reconcile_decisions(fake, ledger, 4000)
    assert len(fake.prompts) == 1
    assert fake.prompts[0][1] == {2}  # the "point" item's evidence never reaches the model
    assert resolved == [ledger[1]]


def test_reconcile_decisions_skips_the_model_with_nothing_to_reconcile():
    class Fake:
        def complete(self, *a):
            pytest.fail("must not call the model when there are no decisions/actions")

    ledger = [dict(kind="point", text="x", evidence=[1], owner=None, due=None, status="unspecified")]
    assert reconcile_decisions(Fake(), ledger, 4000) == []


def test_summarize_reconciles_decisions_without_touching_the_full_ledger(meeting):
    store, mid, settings = meeting
    store.save_chunk(
        mid,
        0,
        [
            dict(start=0, end=1, speaker="A", text="Решили выпустить в пятницу."),
            dict(start=1, end=2, speaker="A", text="Выпуск отменили."),
        ],
    )
    ids = [r["id"] for r in store.segments(mid)]
    agreed = dict(
        kind="decision", text="Выпуск в пятницу", evidence=[ids[0]], owner=None, due=None, status="agreed"
    )
    cancelled = dict(
        kind="decision", text="Выпуск отменён", evidence=[ids[1]], owner=None, due=None, status="cancelled"
    )
    final = dict(
        kind="decision",
        text="Выпуск отменён (итог)",
        evidence=ids,
        owner=None,
        due=None,
        status="cancelled",
    )

    class Fake:
        def complete(self, prompt, allowed):
            if "ИТОГОВЫЙ список" in prompt:  # the reconciliation pass
                return dict(overview="", topics=[], items=[final])
            return dict(overview="Итог", topics=[], items=[agreed, cancelled])  # map and brief reduce

    result = summarize(store, mid, settings, client=Fake())
    assert result["detailed"]["items"] == [agreed, cancelled]  # full history is never shortened
    assert result["detailed"]["resolved"] == [final]

    store.update(mid, summary=json.dumps(result))
    content = export(store, mid, settings).read_text()
    assert "Собеседник" not in content
    assert "## Итог по решениям" in content
    assert "Выпуск отменён (итог)" in content
    assert "Выпуск в пятницу" in content  # the superseded entry is still in "Подробная сводка"


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
