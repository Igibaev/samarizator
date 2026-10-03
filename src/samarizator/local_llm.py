"""Local summary model: GGUF weights served by llama.cpp's llama-server on 127.0.0.1.

The server lives only for one summary job. It is a child of the worker, so the RSS
watchdog sees it and cancelling the job (killpg) stops it too. It listens on loopback
with a one-time API key, so other local programs cannot use it while it runs.
Nothing is sent outside the Mac; the network is used only to download weights.
"""

import math
import os
import secrets
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import psutil

from .bundle import tool
from .config import data_dir
from .summary_prompts import FINAL_SYSTEM, MAP_PROMPT, REVIEW_PROMPT, SYSTEM


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    file: str
    url: str
    size_gb: float
    min_ram_gb: int
    note: str


HF = "https://huggingface.co/"
PRESETS = {
    preset.key: preset
    for preset in [
        Preset(
            "qwen3-30b-a3b",
            "Максимальное качество · Qwen3 30B-A3B Instruct",
            "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
            HF
            + "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF/resolve/main/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
            18.6,
            32,
            "Mac с 32 ГБ памяти и больше. Смесь экспертов: качество крупной модели при скорости "
            "небольшой, длинный контекст, уверенный русский и JSON.",
        ),
        Preset(
            "gemma3-12b",
            "Сбалансированная · Gemma 3 12B",
            "gemma-3-12b-it-Q4_K_M.gguf",
            HF + "unsloth/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf",
            7.3,
            16,
            "Mac с 16–24 ГБ памяти. Хорошо пишет по-русски, медленнее Qwen3 30B-A3B на мощном Mac.",
        ),
        Preset(
            "qwen3-4b",
            "Быстрая · Qwen3 4B Instruct",
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            HF + "unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            2.5,
            8,
            "Mac с 8–16 ГБ памяти. Заметно проще в формулировках; для длинных встреч лучше "
            "уменьшить размер блока до 8000 символов.",
        ),
    ]
}

# Russian text in JSON costs roughly 2.5–3 characters per token for these tokenizers;
# 2.0 leaves room for escaping and denser text.
CHARS_PER_TOKEN = 2.0
# Final text sees a larger slice of the ledger than one extraction request.
FINAL_INPUT_FACTOR = 2
FINAL_OUTPUT_TOKENS = 6144
# Rough KV-cache cost per context token across the presets (bytes).
KV_BYTES_PER_TOKEN = 160 * 1024
SERVER_OVERHEAD_GB = 1.5


def models_dir():
    folder = data_dir() / "models"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def preset_path(key):
    return models_dir() / PRESETS[key].file


def ram_gb():
    return psutil.virtual_memory().total / 1024**3


def recommended_preset(total_gb=None):
    # psutil reports usable memory: a "16 GB" Mac shows ~15.6.
    total_gb = round(ram_gb() if total_gb is None else total_gb)
    for preset in PRESETS.values():
        if total_gb >= preset.min_ram_gb:
            return preset.key
    return "qwen3-4b"


def final_input_chars(settings):
    # A Mac with 32 GB+ affords the KV cache for the whole register of a two-hour meeting
    # (~50k characters) in one request, so the final text is written from every item.
    factor = FINAL_INPUT_FACTOR * 2 if round(ram_gb()) >= 32 else FINAL_INPUT_FACTOR
    return settings.input_chars * factor


def final_output_tokens(settings):
    return max(settings.max_output_tokens, FINAL_OUTPUT_TOKENS)


def context_tokens(settings):
    """Context window that fits the largest request the pipeline can send."""
    user = len(settings.summary_instructions)
    extraction = len(SYSTEM) + user + max(len(MAP_PROMPT), len(REVIEW_PROMPT)) + settings.input_chars
    extraction = extraction / CHARS_PER_TOKEN + settings.max_output_tokens
    final = len(FINAL_SYSTEM) + user + len(settings.final_prompt) + 2000 + final_input_chars(settings)
    final = final / CHARS_PER_TOKEN + final_output_tokens(settings)
    return min(131072, int(math.ceil(max(extraction, final) / 1024) * 1024))


def memory_estimate_gb(settings):
    """Model weights + KV cache + server overhead. A planning number, not a measurement."""
    try:
        weights = Path(settings.llm_model).expanduser().stat().st_size / 1024**3
    except OSError:
        weights = 0.0
    return weights + context_tokens(settings) * KV_BYTES_PER_TOKEN / 1024**3 + SERVER_OVERHEAD_GB


def job_budget_gb(settings):
    """Watchdog budget of a summary job: the model must fit, whatever the ASR budget is."""
    return max(settings.memory_gb, math.ceil(memory_estimate_gb(settings) * 1.25 + 1))


def check_fits(settings, total_gb=None):
    total_gb = ram_gb() if total_gb is None else total_gb
    need = memory_estimate_gb(settings)
    if need > total_gb * 0.85:
        raise ValueError(
            f"Модели сводок нужно около {need:.0f} ГБ памяти, а в этом Mac {total_gb:.0f} ГБ. "
            "Выберите модель поменьше в настройках или уменьшите размер блока."
        )


class DownloadCancelled(Exception):
    pass


def download(url, target, progress=lambda done, total: None, cancelled=lambda: False, opener=None):
    """Resumable download into `target`; a `.part` file survives interruptions.

    Large weights (up to ~19 GB) must not restart from zero after a network hiccup, so
    an interrupted download continues with an HTTP Range request.
    """
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    import truststore

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    for attempt in range(6):
        done = part.stat().st_size if part.exists() else 0
        request = urllib.request.Request(url, headers={"User-Agent": "Samarizator"})
        if done:
            request.add_header("Range", f"bytes={done}-")
        try:
            with opener.open(request, timeout=60) as response:
                status = getattr(response, "status", 200)
                if done and status != 206:
                    done = 0  # the server ignored Range: start over
                length = int(response.headers.get("Content-Length") or 0)
                total = done + length if length else 0
                with part.open("ab" if done else "wb") as f:
                    while chunk := response.read(1024 * 1024):
                        if cancelled():
                            raise DownloadCancelled()
                        f.write(chunk)
                        done += len(chunk)
                        progress(done, total)
            if total and done < total:
                # A dropped connection ends the stream early without an exception.
                raise ConnectionError("download interrupted")
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and part.exists():
                break  # already complete
            if exc.code in {401, 403, 404}:
                raise ValueError(
                    f"Сервер моделей ответил HTTP {exc.code}. Скачайте файл .gguf вручную и выберите его "
                    "в настройках."
                ) from None
            if attempt == 5:
                raise ValueError(f"Загрузка модели не удалась: HTTP {exc.code}.") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError):
            if attempt == 5:
                raise ValueError(
                    "Нет соединения с сервером моделей. Проверьте интернет и повторите — загрузка "
                    "продолжится с места остановки."
                ) from None
        time.sleep(min(30, 2**attempt))
    if not part.exists() or part.stat().st_size < 1024:
        raise ValueError("Вместо модели получен слишком маленький файл.")
    if target.suffix == ".gguf":
        with part.open("rb") as f:
            if f.read(4) != b"GGUF":
                part.unlink()
                raise ValueError("Скачанный файл не похож на модель GGUF. Повторите загрузку.")
    part.replace(target)
    return target


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_args(settings, port, key, binary=None):
    args = [
        binary or tool("llama-server"),
        "--model",
        str(Path(settings.llm_model).expanduser()),
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--api-key",
        key,
        "--ctx-size",
        str(context_tokens(settings)),
        # One slot owns the whole context; the system prompt stays cached between requests.
        "--parallel",
        "1",
        "--threads",
        str(settings.threads),
        "--n-gpu-layers",
        "999" if settings.llm_gpu else "0",
        # Instruct models answer directly; hybrid "thinking" models skip the hidden essay.
        "--reasoning-budget",
        "0",
        "--jinja",
        # The answer is taken as plain content (JSON is enforced by the request grammar):
        # the chat-format parser fails a cut-off JSON with HTTP 500 instead of "length".
        "--skip-chat-parsing",
    ]
    return args


LOAD_ERRORS = [
    ("out of memory", "Модели не хватило памяти. Закройте тяжёлые приложения или выберите модель поменьше."),
    (
        "failed to allocate",
        "Модели не хватило памяти. Закройте тяжёлые приложения или выберите модель поменьше.",
    ),
    ("unknown model architecture", "Эта версия llama.cpp не знает архитектуру выбранной модели."),
    (
        "failed to load model",
        "llama.cpp не смог загрузить модель. Проверьте файл .gguf или скачайте его заново.",
    ),
]


def explain_failure(log_text):
    lowered = log_text.lower()
    for marker, message in LOAD_ERRORS:
        if marker in lowered:
            return message
    return "Локальная модель сводок не запустилась. Проверьте файл модели и свободную память."


class LlamaServer:
    """`with LlamaServer(settings, work) as server:` — server.url and server.key are ready."""

    def __init__(self, settings, work, progress=lambda *_: None, binary=None, load_timeout=900):
        self.settings = settings
        self.work = Path(work)
        self.progress = progress
        self.binary = binary
        self.load_timeout = load_timeout
        self.proc = None
        self.port = free_port()
        self.key = secrets.token_urlsafe(24)
        self.url = f"http://127.0.0.1:{self.port}"
        self.log = self.work / "llama-server.log"

    def __enter__(self):
        binary = self.binary or tool("llama-server")
        if not Path(binary).is_absolute():
            raise ValueError("llama-server не найден. Переустановите Samarizator или запустите ./start.sh.")
        env = os.environ.copy()
        env.pop("SAMARIZATOR_API_KEY", None)
        self.work.mkdir(parents=True, exist_ok=True)
        with self.log.open("wb") as out:
            self.proc = subprocess.Popen(
                server_args(self.settings, self.port, self.key, binary),
                stdout=out,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
            )
        try:
            self.wait_ready()
        except BaseException:
            self.stop()
            raise
        return self

    def wait_ready(self):
        self.progress("Загрузка локальной модели сводок в память…")
        deadline = time.monotonic() + self.load_timeout
        # Loopback only: never route the health check through a system or corporate proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(explain_failure(self.log.read_text(errors="replace")))
            try:
                with opener.open(self.url + "/health", timeout=2) as response:
                    if response.status == 200:
                        return
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError):
                pass
            time.sleep(0.5)
        raise RuntimeError("Локальная модель загружалась слишком долго. Попробуйте модель поменьше.")

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc = None

    def __exit__(self, *exc):
        self.stop()
        return False
