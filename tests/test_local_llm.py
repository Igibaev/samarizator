"""Local summary model: download, server lifecycle, budgets and the final text stage."""

import json
import os
import stat
import sys
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from samarizator import local_llm
from samarizator.config import Settings
from samarizator.knowledge import export
from samarizator.summary import final_document, final_material, regenerate_final, summarize
from samarizator.summary_prompts import FINAL_FORMATS

FAKE_SERVER = r'''#!PYTHON
"""Stands in for llama-server: same flags, same endpoints, canned answers."""
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer

args = sys.argv[1:]
opt = lambda name: args[args.index(name) + 1]
port, key = int(opt("--port")), opt("--api-key")
with open(opt("--model"), "rb") as f:
    assert f.read(4) == b"GGUF"
log = open(sys.argv[0] + ".requests", "a")
log.write(json.dumps({"args": args}) + "\n")
log.flush()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def reply(self, code, body):
        data = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        self.reply(200, {"status": "ok"})

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer " + key:
            return self.reply(401, {"error": "key"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/tokenize":
            return self.reply(200, {"tokens": list(range(len(body["content"]) // 3))})
        log.write(json.dumps(body, ensure_ascii=False) + "\n")
        log.flush()
        prompt = body["messages"][-1]["content"]
        if body["messages"][0]["content"].startswith("Ты отвечаешь на вопросы"):
            content = json.dumps({"found": True, "answer": "Бюджет согласован [1].", "refs": [1]}, ensure_ascii=False)
            return self.reply(200, {"choices": [{"finish_reason": "stop", "message": {"content": content}}]})
        if "grammar" not in body:
            content = "# Протокол\n\n## Решения\n1. Бюджет 17 млн — согласовано."
        else:
            import re
            ids = [int(n) for n in re.findall(r'"(?:id|evidence)":\[?(\d+)', prompt)] or [1]
            items = [dict(kind="decision", text="Бюджет 17 млн согласован", evidence=ids[:1],
                          owner=None, due=None, status="agreed", draft_ids=[0])]
            content = json.dumps(dict(overview="Обсуждали бюджет.", items=items, topics=["Бюджет"], removed=[]),
                                 ensure_ascii=False)
        self.reply(200, {"choices": [{"finish_reason": "stop", "message": {"content": content}}]})

HTTPServer(("127.0.0.1", port), Handler).serve_forever()
'''


@pytest.fixture
def fake_server(tmp_path, monkeypatch):
    script = tmp_path / "llama-server"
    script.write_text(FAKE_SERVER.replace("PYTHON", sys.executable))
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr("samarizator.local_llm.tool", lambda name: str(script))
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 2048)
    return script, model


def requests_seen(script):
    path = script.with_name(script.name + ".requests")
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


@pytest.mark.real_final
def test_summary_runs_end_to_end_through_a_local_server(meeting, fake_server):
    store, mid, settings = meeting
    script, model = fake_server
    settings.llm_model = str(model)
    store.save_chunk(mid, 0, [dict(start=5, end=9, speaker="Речь", text="Утвердили бюджет в 17 миллионов.")])
    result = summarize(store, mid, settings, work=store.path.parent / "work-test")
    assert result["detailed"]["items"][0]["text"] == "Бюджет 17 млн согласован"
    assert result["final"]["text"].startswith("# Протокол")
    assert result["final"]["title"] == FINAL_FORMATS["protocol"][0]
    seen = requests_seen(script)
    args = seen[0]["args"]
    assert args[args.index("--host") + 1] == "127.0.0.1"
    slots = local_llm.parallel_slots(settings)
    assert args[args.index("--parallel") + 1] == str(slots)
    assert int(args[args.index("--ctx-size") + 1]) == local_llm.context_tokens(settings) * slots
    assert args[args.index("--cache-type-v") + 1] == "q8_0"
    final = [body for body in seen[1:] if "grammar" not in body]
    assert len(final) == 1
    assert "Бюджет 17 млн согласован" in final[0]["messages"][-1]["content"]
    assert "[00:00:05]" in final[0]["messages"][-1]["content"]
    # The server is gone once the job ends.
    port = int(args[args.index("--port") + 1])
    with pytest.raises(OSError):
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            f"http://127.0.0.1:{port}/health", timeout=1
        )
    store.update(mid, summary=json.dumps(result, ensure_ascii=False))
    note = export(store, mid, settings)
    text = note.read_text(encoding="utf-8")
    assert "## Итоговый текст · Протокол встречи" in text
    assert "\n### Протокол\n" in text and "\n#### Решения\n" in text


@pytest.mark.real_final
def test_regenerating_only_the_final_text_reuses_the_summary(meeting, fake_server):
    store, mid, settings = meeting
    script, model = fake_server
    settings.llm_model = str(model)
    store.save_chunk(mid, 0, [dict(start=0, end=2, speaker="Речь", text="Бюджет.")])
    store.update(mid, summary=json.dumps(summarize(store, mid, settings, work=store.path.parent / "w1")))
    before = len(requests_seen(script))
    settings.final_format = "executive"
    result = regenerate_final(store, mid, settings, work=store.path.parent / "w2")
    after = requests_seen(script)[before:]
    assert [("grammar" in body) for body in after if "args" not in body] == [False]
    assert result["final"]["title"] == "Резюме для руководителя"
    assert "Главное" in after[-1]["messages"][-1]["content"]


def test_server_that_fails_to_load_explains_why(tmp_path):
    script = tmp_path / "llama-server"
    script.write_text(
        f"#!{sys.executable}\nimport sys\nprint('ggml: failed to allocate buffer')\nsys.exit(1)\n"
    )
    script.chmod(0o755)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    settings = Settings(llm_model=str(model))
    with pytest.raises(RuntimeError, match="не хватило памяти"):
        with local_llm.LlamaServer(settings, tmp_path, binary=str(script)):
            pass


def test_context_and_budget_grow_with_block_size(tmp_path):
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF" + b"\0" * 1024)
    small = Settings(llm_model=str(model), input_chars=8000)
    large = Settings(llm_model=str(model), input_chars=24000)
    assert local_llm.context_tokens(small) < local_llm.context_tokens(large) <= 131072
    assert local_llm.context_tokens(small) % 1024 == 0
    assert local_llm.job_budget_gb(large) >= local_llm.job_budget_gb(small) >= small.memory_gb
    with pytest.raises(ValueError, match="памяти"):
        local_llm.check_fits(large, total_gb=1)
    local_llm.check_fits(small, total_gb=64)


def test_recommendation_follows_memory():
    assert local_llm.recommended_preset(48) == "gemma4-26b-a4b"
    assert local_llm.recommended_preset(16) == "gigachat3.1-lightning"
    assert local_llm.recommended_preset(8) == "qwen3-4b"
    assert all(
        p.url.startswith(("https://huggingface.co/", local_llm.HF_REPO)) for p in local_llm.PRESETS.values()
    )
    assert all(p.file.endswith(".gguf") for p in local_llm.PRESETS.values())


class RangeHandler(BaseHTTPRequestHandler):
    payload = b"GGUF" + bytes(range(256)) * 40
    fail_after = None

    def log_message(self, *a):
        pass

    def do_GET(self):
        start = 0
        if rng := self.headers.get("Range"):
            start = int(rng.split("=")[1].rstrip("-"))
        body = self.payload[start:]
        self.send_response(206 if start else 200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if RangeHandler.fail_after and not start:
            self.wfile.write(body[: RangeHandler.fail_after])
            self.wfile.flush()
            self.connection.close()
            return
        self.wfile.write(body)


def test_download_resumes_after_a_dropped_connection(tmp_path, monkeypatch):
    monkeypatch.setattr("samarizator.local_llm.time.sleep", lambda _: None)
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    RangeHandler.fail_after = 3000
    seen = []
    try:
        target = local_llm.download(
            f"http://127.0.0.1:{server.server_port}/m.gguf",
            tmp_path / "m.gguf",
            progress=lambda done, total: seen.append((done, total)),
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({})),
        )
    finally:
        server.shutdown()
        RangeHandler.fail_after = None
    assert target.read_bytes() == RangeHandler.payload
    assert not (tmp_path / "m.gguf.part").exists()
    assert seen[-1][0] == len(RangeHandler.payload)


def test_final_material_takes_the_most_detailed_level_that_fits():
    def item(n):
        return dict(kind="point", text=f"Пункт {n} " + "x" * 200, evidence=[n], status="unspecified")

    summary = dict(
        items=[],
        topics=[],
        brief=dict(overview="Кратко", items=[item(1)], topics=[]),
        detailed=dict(overview="Часть 1. Начало", items=[item(n) for n in range(1, 40)], topics=[]),
        reduce_levels=[[item(n) for n in range(1, 10)]],
    )
    times = {n: n * 60 for n in range(1, 40)}
    name, material = final_material(summary, times, 100000)
    assert name == "full" and "Часть 1. Начало" in material and "[00:39:00]" in material
    name, material = final_material(summary, times, 3000)
    assert name == "level-1" and "Пункт 9" in material and "Пункт 10 " not in material
    name, _ = final_material(summary, times, 500)
    assert name == "brief"


@pytest.mark.real_final
def test_custom_template_and_failures_never_lose_the_summary():
    class Client:
        def __init__(self, error=None):
            self.error, self.calls = error, []

        def complete_text(self, system, prompt, max_tokens, minimum=1024):
            self.calls.append((system, prompt))
            if self.error:
                raise self.error
            return "Готово", False

    summary = dict(
        items=[],
        topics=[],
        brief=dict(overview="Кратко", items=[], topics=[]),
        detailed=dict(overview="", items=[], topics=[]),
    )
    settings = Settings(final_prompt="Только список решений.", summary_instructions="Для юристов.")
    client = Client()
    result = final_document(client, settings, summary, {})
    assert result["title"] == "Свой формат" and result["text"] == "Готово"
    system, prompt = client.calls[0]
    assert system.endswith("Для юристов.") and "Только список решений." in prompt
    failed = final_document(Client(RuntimeError("модель упала")), Settings(), summary, {})
    assert failed["text"] == "" and "не создан" in failed["warning"]


def test_launcher_runs_whitelisted_modules_only(monkeypatch, capsys):
    from samarizator import bundle, launcher

    monkeypatch.setattr(sys, "argv", ["Samarizator", "--run-module", "os"])
    assert launcher.main() == 2
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/Applications/Samarizator.app/Contents/MacOS/Samarizator")
    assert bundle.python_command("samarizator.worker", "summary", "abc") == [
        "/Applications/Samarizator.app/Contents/MacOS/Samarizator",
        "--run-module",
        "samarizator.worker",
        "summary",
        "abc",
    ]


def test_bundled_tools_win_over_path(tmp_path, monkeypatch):
    from samarizator import bundle

    (tmp_path / "bin").mkdir()
    tool = tmp_path / "bin/whisper-cli"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)
    monkeypatch.setenv("SAMARIZATOR_RESOURCES", str(tmp_path))
    assert bundle.tool("whisper-cli") == str(tool)
    monkeypatch.setenv("PATH", "")
    assert bundle.tool("not-a-real-tool") == "not-a-real-tool"
    assert not bundle.has_tool("not-a-real-tool")
    assert os.path.isabs(bundle.tool("whisper-cli"))


def test_downloaded_whisper_model_gets_a_budget_transcription_accepts(tmp_path):
    from samarizator.setup_models import WHISPER_PRESETS, whisper_memory_gb

    model = tmp_path / "ggml-large-v3.bin"
    with model.open("wb") as f:
        f.truncate(int(WHISPER_PRESETS["large-v3"].size_gb * 1024**3))
    budget = whisper_memory_gb(model)
    # Same inequality worker.transcribe() enforces before starting Whisper.
    assert model.stat().st_size * 2.5 + 600 * 1024**2 <= budget * 1024**3 * 0.8
    assert 4 <= budget <= 64


def test_progress_reads_block_numbers_and_estimates_time_left():
    from samarizator import progress

    info = progress.summary("Разбор записи: блок 8 из 22")
    assert info["stage"] == "blocks" and info["detail"] == "8 из 22"
    assert 0.05 < info["fraction"] < 0.8
    review = progress.summary("Проверка блока 8 из 22 по расшифровке…")
    assert review["fraction"] > info["fraction"]
    assert progress.summary("Итоговый текст по выбранному формату…")["stage"] == "final"
    assert progress.summary("Объединение: уровень 1, блок 1/2")["stage"] == "brief"
    assert progress.transcription("Whisper: фрагмент 3/20")["detail"] == "фрагмент 3 из 20"
    steps = progress.summary_steps("Разбор записи: блок 8 из 22", 512, "Протокол встречи")
    assert [state for *_, state in steps] == ["done", "done", "current", "todo", "todo"]
    assert progress.remaining(0.05, 600) == ""
    assert progress.remaining(0.5, 600) == "осталось около 10 мин"
    assert progress.percent("summarizing", "Разбор записи: блок 1 из 4").startswith("Сводка ")


@pytest.mark.real_final
def test_timeline_parts_follow_source_blocks(meeting, fake_server):
    store, mid, settings = meeting
    _, model = fake_server
    settings.llm_model = str(model)
    settings.input_chars = 4000
    store.save_chunk(
        mid,
        0,
        [
            dict(start=i * 30, end=i * 30 + 20, speaker="Речь", text="Фраза про бюджет " * 40)
            for i in range(12)
        ],
    )
    result = summarize(store, mid, settings, work=store.path.parent / "w3")
    parts = result["detailed"]["parts"]
    assert len(parts) > 1
    assert parts[0]["first"] == 0 and parts[-1]["last"] == len(result["detailed"]["items"])
    assert all(a["last"] == b["first"] for a, b in zip(parts, parts[1:]))


def test_ticked_tasks_are_ticked_in_the_obsidian_note(meeting):
    from samarizator.knowledge import task_key

    store, mid, settings = meeting
    store.save_chunk(mid, 0, [dict(start=0, end=2, speaker="Речь", text="Ирина посчитает к пятнице.")])
    sid = store.segments(mid)[0]["id"]
    task = dict(
        kind="action",
        text="Посчитать скидки",
        evidence=[sid],
        owner="Ирина",
        due="к пятнице",
        status="agreed",
    )
    view = dict(overview="Итог", items=[task], topics=[])
    store.update(mid, summary=json.dumps(dict(**view, brief=view, detailed=dict(**view, resolved=[task]))))
    store.save_checkpoint(mid, "tasks-done", 0, {task_key(task): True})
    text = export(store, mid, settings).read_text(encoding="utf-8")
    assert "- [x] Посчитать скидки" in text


def test_chat_engine_answers_through_the_local_server_and_releases_it(meeting, fake_server, qapp):
    from samarizator import qa
    from samarizator.chat import ChatEngine

    store, mid, settings = meeting
    script, model = fake_server
    settings.llm_model = str(model)
    store.save_chunk(mid, 0, [dict(start=5, end=9, speaker="Речь", text="Утвердили бюджет.")])
    sid = store.segments(mid)[0]["id"]
    item = dict(kind="decision", text="Бюджет согласован", evidence=[sid], status="agreed")
    view = dict(overview="Итог", items=[item], topics=[])
    summary = dict(**view, brief=view, detailed=dict(**view, resolved=[item]))
    engine = ChatEngine()
    with engine.lock:
        client = engine.client(settings, lambda *_: None)
        result = qa.ask(client, summary, {sid: 5.0}, "Что решили?")
        client.close()
    assert result["found"] and result["sources"][0]["evidence"] == [sid]
    assert engine.loaded
    seen = requests_seen(script)
    question = [body for body in seen if "messages" in body][-1]
    assert "grammar" in question and "Утвердили бюджет." not in question["messages"][-1]["content"]
    engine.stop()
    assert not engine.loaded
