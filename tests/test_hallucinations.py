"""Phrases Whisper invents on silence: flagged when recognised, removed at once on confirmation."""

import json

import pytest

from samarizator.hallucinations import REASON, hallucinated, is_hallucination
from samarizator.media import parse_whisper, whisper
from samarizator.speech import tidy_rows

INVENTED = [
    "Субтитры создавал DimaTorzok",
    "Субтитры делал DimaTorzok.",
    "Редактор субтитров А.Семкин Корректор А.Егорова",
    "Корректор А.Егорова",
    "Субтитры сделаны сообществом Amara.org",
    "Продолжение следует...",
    "Спасибо за просмотр!",
    "Подписывайтесь на канал",
    "[музыка]",
    "(аплодисменты)",
    "♪ ♪",
]
REAL = [
    "Спасибо за внимание, коллеги.",
    "Продолжение следует обсуждать на следующей неделе с юристами.",
    "Я посмотрел субтитры к ролику, там ошибки в терминах.",
    "Корректор нужен для отчёта, иначе цифры поплывут.",
    "Спасибо за просмотр презентации, теперь к бюджету: нам нужно ещё два человека.",
    "Субтитры сделали плохо, надо переделать.",
    "Субтитры делал подрядчик, я с ним поговорю.",
    "Редактор субтитров сказал, что успеет к пятнице.",
    "и",
]


@pytest.mark.parametrize("text", INVENTED)
def test_known_phrases_are_recognised(text):
    assert is_hallucination(text)


@pytest.mark.parametrize("text", REAL)
def test_real_speech_is_left_alone(text):
    assert not is_hallucination(text)


def whisper_row(start, end, text):
    return dict(offsets={"from": start * 1000, "to": end * 1000}, text=text, tokens=[])


def test_recognised_replies_are_flagged_for_review():
    payload = {
        "transcription": [
            whisper_row(10, 14, "Договор пришлём до пятницы."),
            whisper_row(20, 24, "Субтитры создавал DimaTorzok"),
        ]
    }
    real, invented = parse_whisper(payload, 0, 0, 90)
    assert not real["uncertain"] and real["review"] == ""
    assert invented["uncertain"] and invented["review"] == REASON


def test_whisper_suppresses_non_speech_and_drops_likely_silence(tmp_path, monkeypatch):
    calls = []

    def run(args, *a, **kw):
        calls.append(args)
        (tmp_path / "whisper-result.json").write_text('{"transcription": []}')

    monkeypatch.setattr("samarizator.media.tool", lambda _: "/opt/whisper-cli")
    monkeypatch.setattr("samarizator.media.run_command", run)
    whisper("audio.wav", "asr.bin", "ru", 8, tmp_path)
    assert "--suppress-nst" in calls[0]
    assert calls[0][calls[0].index("--no-speech-thold") + 1] == "0.5"


def test_the_summary_model_never_reads_them():
    rows = [dict(id=1, text="Субтитры создавал DimaTorzok"), dict(id=2, text="Бюджет утвердили.")]
    assert [row["id"] for row in tidy_rows(rows)] == [2]
    # With cleaning off the model reads the transcript exactly as recognised.
    assert [row["id"] for row in tidy_rows(rows, enabled=False)] == [1, 2]


SPEECH = [
    dict(start=0, end=4, speaker="Речь", text="Бюджет утвердили."),
    dict(start=88, end=90, speaker="Речь", text="Субтитры создавал DimaTorzok"),
    dict(start=92, end=96, speaker="Речь", text="Договор пришлём до пятницы."),
    dict(start=178, end=180, speaker="Речь", text="Редактор субтитров А.Семкин Корректор А.Егорова"),
]


def test_deleting_replies_resets_the_summary_built_on_them(meeting):
    store, mid, settings = meeting
    store.save_chunk(mid, 0, SPEECH)
    store.update(mid, summary=json.dumps(dict(overview="x")), status="done")
    store.save_checkpoint(mid, "summary-notes-map", 0, {"x": 1})
    junk = hallucinated(store.iter_segments(mid))
    assert [row["start"] for row in junk] == [88, 178]
    assert store.delete_segments(mid, [row["id"] for row in junk]) == 2
    assert [row["text"] for row in store.iter_segments(mid)] == [
        "Бюджет утвердили.",
        "Договор пришлём до пятницы.",
    ]
    meeting_row = store.meeting(mid)
    assert meeting_row["summary"] is None and meeting_row["status"] == "review"
    assert not store.summary_progress(mid)
    assert store.delete_segments(mid, []) == 0
    # A paused transcription stays resumable.
    store.update(mid, status="interrupted")
    store.delete_segments(mid, [store.segments(mid)[0]["id"]])
    assert store.meeting(mid)["status"] == "interrupted"


def test_the_window_offers_to_delete_them_and_asks_first(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    w = Window()
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"fixture")
    mid = w.store.create(source, w.settings)
    # Recognised before the filter existed: nothing is flagged, the banner still finds them.
    w.store.save_chunk(mid, 0, SPEECH)
    w.store.save_checkpoint(mid, "asr_complete", 0, True)
    w.store.update(mid, status="review")
    w.mid = mid
    w.refresh_list()
    w.set_view("transcript")
    w.load_detail()
    w.show()
    app.processEvents()
    assert w.junk_banner.isVisibleTo(w) and "Найдено 2 фразы" in w.junk_banner.title.text()
    assert w.clean_button.isEnabled()
    assert w.table.item(1, 2).text() == "● вероятно, выдумка Whisper" and w.table.item(0, 2).text() == ""
    asked = []

    def decline(rows, has_summary):
        asked.append([row["text"] for row in rows])
        return False

    monkeypatch.setattr(Window, "confirm_cleanup", lambda self, rows, has_summary: decline(rows, has_summary))
    assert w.clean_hallucinations() is False
    assert asked == [["Субтитры создавал DimaTorzok", "Редактор субтитров А.Семкин Корректор А.Егорова"]]
    assert w.store.segment_counts(mid)[0] == 4
    monkeypatch.setattr(Window, "confirm_cleanup", lambda self, rows, has_summary: True)
    assert w.clean_hallucinations() is True
    app.processEvents()
    assert w.store.segment_counts(mid)[0] == 2 and not w.junk_banner.isVisibleTo(w)
    assert "Удалено 2 выдуманные фразы" in w.progress.text()
    w.close()
