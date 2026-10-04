"""Less heat, same summaries: GPU recognition, quiet CPU threads, cool-down pauses."""

import ctypes
import struct
import sys

import pytest

from samarizator import local_llm, process, thermal
from samarizator.config import Settings
from samarizator.media import whisper
from samarizator.summary import JSON_GRAMMAR


def test_recognition_moves_to_the_gpu_and_old_settings_follow():
    assert Settings().gpu and Settings().quality_profile().gpu
    # Version 1 kept recognition on the CPU by default; it is migrated once.
    assert Settings.from_dict(dict(gpu=False, threads=8)).gpu
    # A choice made with the current version is respected.
    assert not Settings.from_dict(dict(gpu=False, version=2)).gpu
    assert Settings.from_dict(dict(gpu=False)).version == 2


def test_whisper_runs_on_metal_unless_turned_off(tmp_path, monkeypatch):
    seen = []

    def run(args, log):
        seen.append(args)
        (tmp_path / "whisper-result.json").write_text("{}")

    monkeypatch.setattr("samarizator.media.tool", lambda name: "/bin/whisper-cli")
    monkeypatch.setattr("samarizator.media.run_command", run)
    whisper(tmp_path / "a.wav", "m.bin", "ru", 4, tmp_path, gpu=Settings().gpu)
    assert "-ng" not in seen[0]


def test_summary_server_keeps_cpu_threads_quiet_and_decodes_speculatively():
    settings = Settings(threads=10, llm_model="/m.gguf")
    args = local_llm.server_args(settings, 1234, "key", "/bin/llama-server")
    assert args[args.index("--threads") + 1] == "4"
    assert args[args.index("--poll") + 1] == "0"
    assert args[args.index("--spec-type") + 1] == "ngram-mod"
    plain = local_llm.server_args(settings, 1234, "key", "/bin/llama-server", speculative=False)
    assert "--spec-type" not in plain
    cpu = local_llm.server_args(Settings(threads=10, llm_gpu=False), 1, "k", "/bin/x")
    assert cpu[cpu.index("--threads") + 1] == "10"


def test_a_server_that_rejects_speculation_is_started_without_it(tmp_path):
    script = tmp_path / "llama-server"
    log = tmp_path / "starts"
    script.write_text(
        f"""#!{sys.executable}
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
args = sys.argv[1:]
open({str(log)!r}, "a").write(" ".join(args) + "\\n")
if "--spec-type" in args:
    print("error: invalid argument: --spec-type")
    sys.exit(1)
class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
HTTPServer(("127.0.0.1", int(args[args.index("--port") + 1])), H).serve_forever()
"""
    )
    script.chmod(0o755)
    model = tmp_path / "m.gguf"
    model.write_bytes(b"GGUF")
    with local_llm.LlamaServer(Settings(llm_model=str(model)), tmp_path, binary=str(script)) as server:
        assert server.proc.poll() is None
    starts = log.read_text().splitlines()
    assert len(starts) == 2 and "--spec-type" in starts[0] and "--spec-type" not in starts[1]


def test_json_answers_have_no_indentation_to_generate():
    rule = next(line for line in JSON_GRAMMAR.splitlines() if line.startswith("ws "))
    assert rule == 'ws ::= | " "'


def test_hot_mac_pauses_until_it_cools_down():
    states = iter([thermal.SERIOUS, thermal.CRITICAL, thermal.FAIR, thermal.SERIOUS])
    slept, notes = [], []
    waited = thermal.cool_down(notes.append, state=lambda: next(states), sleep=slept.append)
    assert waited == 2 * thermal.POLL and len(slept) == 2 and len(notes) == 1
    # Nothing to wait for on a cool Mac, when it is switched off, or where it cannot be read.
    assert thermal.cool_down(state=lambda: thermal.NOMINAL, sleep=slept.append) == 0
    assert thermal.cool_down(enabled=False, state=lambda: thermal.CRITICAL) == 0
    assert thermal.cool_down(state=lambda: None) == 0
    # A pause never lasts forever.
    assert thermal.cool_down(state=lambda: thermal.CRITICAL, sleep=lambda _: None, limit=20) == 20
    if sys.platform != "darwin":
        assert thermal.thermal_state() is None


def test_memory_watchdog_counts_gpu_memory_on_macos(monkeypatch):
    footprint = 7 * 1024**3

    class LibProc:
        @staticmethod
        def proc_pid_rusage(pid, flavor, buffer):
            assert flavor == process.RUSAGE_INFO_V2
            raw = bytearray(process.RUSAGE_V2_SIZE)
            struct.pack_into("<Q", raw, process.FOOTPRINT_OFFSET, footprint)
            ctypes.memmove(buffer, bytes(raw), len(raw))
            return 0

    monkeypatch.setattr(process.sys, "platform", "darwin")
    monkeypatch.setattr(process, "_libproc", LibProc())
    assert process.phys_footprint(1) == footprint

    class Proc:
        pid = 1

        def memory_info(self):
            return type("M", (), {"rss": 2 * 1024**3})()

    # Metal buffers are not in RSS; the larger number is what the budget sees.
    assert process.process_memory(Proc()) == footprint
    monkeypatch.setattr(process.sys, "platform", "linux")
    assert process.process_memory(Proc()) == 2 * 1024**3


@pytest.mark.real_final
def test_summary_requests_wait_for_a_cool_mac(meeting, monkeypatch):
    from samarizator.summary import LocalClient

    calls = []
    client = LocalClient(Settings(), "http://127.0.0.1:9")
    client.pace = lambda: calls.append("pace")
    monkeypatch.setattr(client, "room", lambda *a: 100)

    class Response:
        status_code = 200
        content = b"{}"
        text = ""

        @staticmethod
        def json():
            return {"choices": [{"finish_reason": "stop", "message": {"content": "Текст"}}]}

    monkeypatch.setattr(client.client, "post", lambda *a, **k: Response())
    assert client.complete_text("s", "p", 100)[0] == "Текст"
    assert calls == ["pace"]
