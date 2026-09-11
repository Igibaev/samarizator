import json
import os
import sys
import time

import pytest

from samarizator.config import Settings
from samarizator.knowledge import export, topic_name
from samarizator.media import parse_whisper, whisper
from samarizator.process import BudgetExceeded, rss_tree, supervise
from samarizator.summary import blocks, summarize, validate_summary


def segment(text="Решено запустить проект", start=0, speaker="Собеседник 1"):
    return dict(start=start, end=start + 3, speaker=speaker, text=text, uncertain=False)


def result(sid, text="Запустить проект"):
    return dict(
        overview="Обсудили проект.",
        topics=["Проект"],
        items=[dict(kind="decision", text=text, evidence=[sid], owner=None, due=None)],
    )


def test_checkpoint_is_atomic_and_idempotent(meeting):
    store, mid, _ = meeting
    store.save_chunk(mid, 0, [segment()])
    store.save_chunk(mid, 0, [segment("duplicate")])
    assert len(list(store.iter_segments(mid))) == 1
    assert store.checkpoint(mid, "asr", 0) is True


def test_failed_transaction_does_not_checkpoint(meeting):
    store, mid, _ = meeting
    with pytest.raises(KeyError):
        store.save_chunk(mid, 0, [segment(), {}])
    assert not store.segments(mid)
    assert store.checkpoint(mid, "asr", 0) is None


def test_edits_invalidate_summary_and_cache(meeting):
    store, mid, _ = meeting
    store.save_chunk(mid, 0, [segment()])
    store.save_checkpoint(mid, "summary-map", 0, {"cached": True})
    store.update(mid, summary="{}", status="done")
    sid = store.segments(mid)[0]["id"]
    store.edit_segment(mid, sid, "Решение отменили")
    assert store.meeting(mid)["summary"] is None
    assert store.checkpoint(mid, "summary-map", 0) is None
    assert store.segments(mid)[0]["text"] == "Решение отменили"


def test_recovery_retains_transcript(meeting):
    store, mid, _ = meeting
    store.save_chunk(mid, 0, [segment()])
    store.update(mid, status="transcribing")
    store.recover()
    assert store.meeting(mid)["status"] == "interrupted"
    assert store.segments(mid)


def test_search(meeting):
    store, mid, _ = meeting
    store.save_chunk(mid, 0, [segment("бюджет XZ-2026"), segment("следующая реплика", 3)])
    assert store.meetings("XZ-2026")[0]["id"] == mid


def test_delete_removes_meeting_segments_and_checkpoints(meeting):
    store, mid, _ = meeting
    store.save_chunk(mid, 0, [segment()])
    store.save_checkpoint(mid, "source", 0, "digest")
    store.delete(mid)
    with pytest.raises(ValueError):
        store.meeting(mid)
    assert store.segments(mid) == []
    assert store.checkpoint(mid, "source", 0) is None


def test_gpu_setting_controls_the_no_gpu_flag(tmp_path, monkeypatch):
    calls = []

    def fake_run(args, log, *rest, **kwargs):
        calls.append(args)
        (tmp_path / "whisper-result.json").write_text('{"transcription": []}')

    monkeypatch.setattr("samarizator.media.shutil.which", lambda name: "/usr/bin/whisper-cli")
    monkeypatch.setattr("samarizator.media.run_command", fake_run)
    whisper(tmp_path / "chunk.wav", "model.bin", "ru", 4, tmp_path)
    whisper(tmp_path / "chunk.wav", "model.bin", "ru", 4, tmp_path, gpu=True)
    assert "-ng" in calls[0]
    assert "-ng" not in calls[1]


def test_whisper_offsets_channels_and_seams():
    payload = {
        "transcription": [
            dict(offsets={"from": 1500, "to": 3500}, text=" Привет "),
            dict(offsets={"from": 0, "to": 500}, text="context"),
        ]
    }
    rows = parse_whisper(payload, 118, 120, 240, channel=1)
    assert len(rows) == 1
    assert rows[0]["start"] == 119.5
    assert rows[0]["speaker"] == "Речь"  # compatibility column is not displayed or summarized
    assert rows[0]["uncertain"]


def test_all_text_is_in_bounded_blocks():
    rows = [dict(id=1, **segment("а" * 16000)), dict(id=2, **segment("конец"))]
    batch = list(blocks(rows, 4000))
    assert len(batch) > 1
    assert all(sum(len(line) + 1 for _, line in b) <= 4000 for b in batch)
    restored = "".join(json.loads(line)["text"] for b in batch for _, line in b)
    assert restored == "а" * 16000 + "конец"
    assert all("speaker" not in json.loads(line) for b in batch for _, line in b)


def test_legacy_speaker_settings_are_ignored():
    settings = Settings.from_dict(
        {
            "diarization": "local",
            "speakers": 4,
            "segmentation_model": "/old/segmentation.onnx",
            "embedding_model": "/old/embedding.onnx",
            "language": "ru",
        }
    )
    assert settings.language == "ru"
    assert not hasattr(settings, "diarization")
    assert not hasattr(settings, "speakers")


def test_downloader_contains_only_whisper_models():
    from samarizator.setup_models import MODELS, QUALITY_MODELS

    assert set(MODELS) == {"ggml-small-q5_1.bin"}
    assert set(QUALITY_MODELS) == {"ggml-silero-v6.2.0.bin"}


def test_invalid_evidence_and_owner_rejected():
    obj = result(123)
    with pytest.raises(ValueError, match="несуществующий"):
        validate_summary(obj, {1})
    obj["items"][0]["evidence"] = [True]
    with pytest.raises(ValueError):
        validate_summary(obj, {1})
    obj = result(1)
    obj["items"][0]["owner"] = ["invented"]
    with pytest.raises(ValueError):
        validate_summary(obj, {1})


@pytest.mark.parametrize(
    "url", ["http://corp.test/v1", "https://user:secret@corp.test/v1", "", "file:///tmp/x"]
)
def test_base_url_validation(url):
    with pytest.raises(ValueError):
        Settings(base_url=url, model="corp").validate(api=True)


@pytest.mark.parametrize(
    "url", ["https://openrouter.ai/api/v1", "http://localhost:11434/v1", "http://127.0.0.1:1234/v1"]
)
def test_https_and_loopback_http_accepted(url):
    Settings(base_url=url, model="llama").validate(api=True)


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://openrouter.ai/api/v1", "https://openrouter.ai/api/v1/chat/completions"),
        ("https://openrouter.ai/api/v1/", "https://openrouter.ai/api/v1/chat/completions"),
        ("https://corp.test/v1/chat/completions", "https://corp.test/v1/chat/completions"),
    ],
)
def test_chat_url_building(base_url, expected):
    assert Settings(base_url=base_url).chat_url() == expected


def test_old_settings_migrate_and_unknown_fields_ignored():
    old = dict(
        endpoint="https://corp.test/v1/chat/completions",
        auth_header="api-key",
        auth_prefix="",
        ca_file="/tmp/corp.pem",
        model="corp",
    )
    settings = Settings.from_dict(old)
    assert settings.base_url == "https://corp.test/v1"
    # The migrated value must rebuild the exact previous URL so cached summary blocks stay valid.
    assert settings.chat_url() == old["endpoint"]


def test_ledger_preserves_late_topics_and_cached_maps(meeting):
    store, mid, settings = meeting
    settings.input_chars = 4000
    store.save_chunk(mid, 0, [segment("А" * 2500, 0), segment("Б" * 2500, 10), segment("Последняя тема", 20)])

    class Fake:
        def __init__(self):
            self.calls = 0

        def complete(self, prompt, allowed):
            self.calls += 1
            sid = max(allowed)
            obj = result(sid, "Пункт " + str(sid))
            if "ИТОГОВЫЙ список" in prompt:
                obj["items"][0]["evidence"] = sorted(allowed)
            return obj

    fake = Fake()
    summary = summarize(store, mid, settings, client=fake)
    assert len(summary["ledger"]) >= 2
    assert max(r["id"] for r in store.segments(mid)) in {
        x for it in summary["ledger"] for x in it["evidence"]
    }
    first = fake.calls
    summarize(store, mid, settings, client=fake)
    assert fake.calls - first == 1  # only final synthesis; map results resumed


def test_obsidian_evidence_and_manual_edits_preserved(meeting):
    store, mid, settings = meeting
    store.save_chunk(mid, 0, [segment()])
    sid = store.segments(mid)[0]["id"]
    summary = result(sid)
    summary["ledger"] = summary["items"]
    store.update(mid, summary=json.dumps(summary))
    first = export(store, mid, settings)
    assert f"#^s{sid}" in first.read_text()
    assert "[[Topics/" in first.read_text()
    original = first.read_text() + "\nМои ручные заметки\n"
    first.write_text(original)
    second = export(store, mid, settings)
    assert first != second
    assert first.read_text() == original
    assert topic_name("../../Secret [x]") == topic_name("../../secret [x]")
    assert "/" not in topic_name("../../Secret [x]")


def test_memory_watchdog_stops_process_group(tmp_path):
    # Budget deliberately below current process RSS: fail before allocation can run away.
    marker = tmp_path / "must-not-finish"
    code = f"import time; from pathlib import Path; time.sleep(5); Path({str(marker)!r}).touch()"
    start = time.monotonic()
    with pytest.raises(BudgetExceeded):
        supervise([sys.executable, "-c", code], budget_gb=0.000001)
    assert time.monotonic() - start < 3
    assert not marker.exists()


def test_cancellation_and_normal_completion():
    with pytest.raises(InterruptedError):
        supervise([sys.executable, "-c", "import time; time.sleep(30)"], 64, cancelled=lambda: True)
    assert supervise([sys.executable, "-c", "pass"], 64) == 0
    assert rss_tree(os.getpid()) > 0


def test_blocks_bound_json_escaped_control_characters():
    text = "\x00" * 9000 + '\\"' * 1000
    rows = [dict(id=1, **segment(text))]
    batches = list(blocks(rows, 4000))
    assert all(sum(len(line) + 1 for _, line in batch) <= 4000 for batch in batches)
    assert "".join(json.loads(line)["text"] for batch in batches for _, line in batch) == text
