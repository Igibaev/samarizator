"""Headless-интерфейс для ИИ-компаньона.

Компаньон — отдельное приложение на Swift, которое запускает эти команды
процессом и читает NDJSON из stdout. Поэтому здесь проверяется именно
контракт: какие события выходят, в каком порядке и что происходит, когда
запускать нечего.
"""

import json
import signal
import subprocess
import sys

import pytest

from samarizator import companion
from samarizator.config import Settings


def events(capsys):
    """Разбирает NDJSON, который команда написала в stdout."""
    out = capsys.readouterr().out.strip()
    return [json.loads(line) for line in out.splitlines() if line]


def run(argv, capsys):
    code = companion.main(argv)
    return code, events(capsys)


def run_record(capsys, source=None):
    args = companion.build_parser().parse_args(["record"] + (["--source", source] if source else []))
    code = companion.command_record(args, stop=already_stopped())
    return code, events(capsys)


# --- status -----------------------------------------------------------------


def test_status_reports_what_is_actually_available(meeting, capsys, monkeypatch, tmp_path):
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: Settings()))
    code, out = run(["status"], capsys)

    assert code == 0
    assert len(out) == 1
    status = out[0]
    assert status["event"] == "status"
    # Модели Whisper в пустой настройке нет — и притворяться, что есть, нельзя.
    assert status["whisperModelReady"] is False
    assert status["summaryBaseURL"] == ""
    assert "databasePath" in status


def test_status_marks_whisper_ready_only_when_the_file_exists(capsys, monkeypatch, tmp_path):
    model = tmp_path / "ggml-small.bin"
    model.write_bytes(b"model")
    monkeypatch.setattr(
        companion.Settings, "load", staticmethod(lambda: Settings(whisper_model=str(model)))
    )
    code, out = run(["status"], capsys)

    assert code == 0
    assert out[0]["whisperModelReady"] is True
    assert out[0]["whisperModelPath"] == str(model)


# --- summarize --------------------------------------------------------------


def test_summarize_refuses_when_there_is_no_recognised_text(meeting, capsys, monkeypatch):
    store, mid, settings = meeting
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))

    code, out = run(["summarize", mid], capsys)

    assert code == 1
    assert out[-1]["event"] == "error"
    assert out[-1]["stage"] == "summary"
    assert "текста" in out[-1]["message"]


def test_summarize_refuses_when_the_api_is_not_configured(meeting, capsys, monkeypatch):
    store, mid, _ = meeting
    store.save_chunk(mid, 0, [dict(start=0.0, end=1.0, speaker="", text="привет")])
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: Settings()))

    code, out = run(["summarize", mid], capsys)

    assert code == 1
    assert out[-1]["event"] == "error"
    # Сообщение приходит от штатной проверки настроек, а не придумывается здесь.
    assert out[-1]["message"]


def test_summarize_runs_the_worker_and_reports_both_ends(meeting, capsys, monkeypatch):
    store, mid, settings = meeting
    store.save_chunk(mid, 0, [dict(start=0.0, end=1.0, speaker="", text="привет")])
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))

    seen = {}

    def fake_supervise(args, budget_gb, **kwargs):
        seen["args"] = args
        seen["budget"] = budget_gb
        return 0

    monkeypatch.setattr("samarizator.process.supervise", fake_supervise)

    code, out = run(["summarize", mid], capsys)

    assert code == 0
    assert [e["event"] for e in out] == ["summarizing", "summarized"]
    assert seen["args"][1:] == ["-m", "samarizator.worker", "summary", mid]
    assert seen["budget"] == settings.memory_gb


def test_summary_failure_is_reported_without_inventing_a_reason(meeting, capsys, monkeypatch):
    store, mid, settings = meeting
    store.save_chunk(mid, 0, [dict(start=0.0, end=1.0, speaker="", text="привет")])
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))
    monkeypatch.setattr("samarizator.process.supervise", lambda *a, **k: 1)

    code, out = run(["summarize", mid], capsys)

    assert code == 1
    assert out[-1]["event"] == "error"
    assert out[-1]["stage"] == "summary"


def test_memory_budget_stop_is_passed_through_as_its_own_message(meeting, capsys, monkeypatch):
    from samarizator.process import BudgetExceeded

    store, mid, settings = meeting
    store.save_chunk(mid, 0, [dict(start=0.0, end=1.0, speaker="", text="привет")])
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))

    def exceeded(*_args, **_kwargs):
        raise BudgetExceeded("Обработка остановлена у границы бюджета памяти.")

    monkeypatch.setattr("samarizator.process.supervise", exceeded)

    code, out = run(["summarize", mid], capsys)

    assert code == 1
    assert "бюджета памяти" in out[-1]["message"]


# --- transcribe -------------------------------------------------------------


def test_transcribe_reports_start_and_finish(meeting, capsys, monkeypatch):
    _, mid, settings = meeting
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))
    monkeypatch.setattr("samarizator.process.supervise", lambda *a, **k: 0)

    code, out = run(["transcribe", mid], capsys)

    assert code == 0
    assert [e["event"] for e in out] == ["transcribing", "transcribed"]


# --- record -----------------------------------------------------------------


class FakeRecorder:
    """Подменяет захват звука: настоящий требует macOS, FFmpeg и микрофон."""

    def __init__(self, folder, **_kwargs):
        self.source = "both"
        self.partial = folder / "live-2026-01-01_10-00-00-abcdef12.partial.wav"
        self.partial.write_bytes(b"partial")
        self.final = folder / "live-2026-01-01_10-00-00-abcdef12.wav"
        self.started = False
        self.stop_error = None

    def start(self):
        self.started = True

    def stop(self, timeout=10):
        if self.stop_error:
            raise self.stop_error
        self.final.write_bytes(b"final")
        return self.final


@pytest.fixture
def fake_capture(monkeypatch, tmp_path):
    """Записывает вместо реального захвата и воркера — остаётся только порядок действий."""
    folder = tmp_path / "recordings"
    folder.mkdir()
    recorder = FakeRecorder(folder)
    monkeypatch.setattr("samarizator.live.LiveRecorder", lambda **kwargs: recorder)
    monkeypatch.setattr("samarizator.live.recording_title", lambda source, stem: "Live тест")
    monkeypatch.setattr("samarizator.process.supervise", lambda *a, **k: 0)
    monkeypatch.setattr(companion, "_finish_catchup", lambda store, mid, process: None)
    monkeypatch.setattr(
        companion.subprocess, "Popen", lambda *a, **k: None
    )
    return recorder


def already_stopped():
    """Остановка, запрошенная заранее: запись завершится сразу после старта.

    Настоящий SIGINT внутри процесса pytest поднимать нельзя — он убил бы сам
    запуск тестов. То, что сигнал действительно приводит к остановке,
    проверяется отдельно, в `test_signal_handler_requests_stop_and_is_removed_afterwards`.
    """
    stop = companion.StopRequest()
    stop.request()
    return stop


def test_record_creates_the_meeting_before_capture_ends(meeting, capsys, monkeypatch, fake_capture):
    _, _, settings = meeting
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))
    code, out = run_record(capsys, source="both")

    assert code == 0
    names = [e["event"] for e in out]
    # Запись существует в базе с первой секунды: распознавание идёт параллельно.
    assert names == ["recording", "stopped", "transcribing", "transcribed"]
    assert out[0]["mid"] == out[-1]["mid"]


def test_record_moves_the_source_to_the_finished_file(meeting, capsys, monkeypatch, fake_capture):
    from samarizator.store import Store

    _, _, settings = meeting
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))
    code, out = run_record(capsys)
    assert code == 0

    mid = out[0]["mid"]
    row = Store().meeting(mid)
    assert row["source"] == str(fake_capture.final)
    assert row["title"] == "Live тест"


def test_empty_recording_is_discarded_instead_of_left_as_a_stub(
    meeting, capsys, monkeypatch, fake_capture
):
    from samarizator.store import Store

    _, _, settings = meeting
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))
    fake_capture.stop_error = ValueError("Запись пуста.")

    code, out = run_record(capsys)

    assert code == 1
    assert out[-1]["event"] == "discarded"
    with pytest.raises(ValueError):
        Store().meeting(out[-1]["mid"])


def test_capture_failure_is_reported_and_nothing_is_created(meeting, capsys, monkeypatch):
    from samarizator.live import LiveCaptureError

    _, _, settings = meeting
    monkeypatch.setattr(companion.Settings, "load", staticmethod(lambda: settings))

    def refuse(**_kwargs):
        raise LiveCaptureError("FFmpeg не найден.")

    monkeypatch.setattr("samarizator.live.LiveRecorder", refuse)

    code, out = run_record(capsys)

    assert code == 1
    assert out[-1]["event"] == "error"
    assert out[-1]["stage"] == "record"
    assert "FFmpeg" in out[-1]["message"]


# --- контракт процесса ------------------------------------------------------


def test_module_runs_as_a_subprocess_and_prints_one_json_object_per_line(tmp_path):
    """Компаньон запускает именно так: `python -m samarizator.companion`."""
    result = subprocess.run(
        [sys.executable, "-m", "samarizator.companion", "status"],
        capture_output=True,
        text=True,
        env={
            **dict(PATH="/usr/bin:/bin", HOME=str(tmp_path)),
            "SAMARIZATOR_HOME": str(tmp_path / "home"),
            "PYTHONPATH": "src",
        },
        cwd=str(__import__("pathlib").Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "status"


def test_signal_handler_requests_stop_and_is_removed_afterwards():
    """Остановка записи — это SIGINT/SIGTERM процессу, и только на время записи.

    Обработчик обязан сниматься: процесс, в котором он остался бы, повёл себя
    при следующем Ctrl+C не так, как ожидает его владелец.
    """
    before = {number: signal.getsignal(number) for number in companion._STOP_SIGNALS}

    with companion.StopRequest() as stop:
        assert stop.requested is False
        for number in companion._STOP_SIGNALS:
            assert signal.getsignal(number) is not before[number]
        # Вызываем обработчик напрямую: настоящий сигнал убил бы pytest.
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        assert stop.requested is True
        stop.wait()

    for number in companion._STOP_SIGNALS:
        assert signal.getsignal(number) is before[number]


def test_unknown_command_fails_loudly():
    with pytest.raises(SystemExit):
        companion.main(["nonsense"])
