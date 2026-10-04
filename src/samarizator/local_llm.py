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
from .summary_prompts import (
    ASSEMBLE_PROMPT,
    CHECK_PROMPT,
    FINAL_FORMATS,
    FINAL_SYSTEM,
    MAP_PROMPT,
    NOTES_SYSTEM,
    REVIEW_PROMPT,
    SYSTEM,
)


@dataclass(frozen=True)
class Preset:
    key: str
    label: str
    file: str
    url: str
    size_gb: float
    min_ram_gb: int
    note: str
    # Memory the model occupies while it works; 0 means it is computed (summary models).
    ram_gb: float = 0.0


HF = "https://huggingface.co/"
# Ordered by preference: the first preset that suits a Mac is recommended for it.
# Repositories listed after "hf-repo:" are searched in turn for the Q4_K_M file.
PRESETS = {
    preset.key: preset
    for preset in [
        Preset(
            "gemma4-26b-a4b",
            "Максимальное качество · Gemma 4 26B-A4B",
            "gemma-4-26B-A4B-it-Q4_K_M.gguf",
            "hf-repo:unsloth/gemma-4-26B-A4B-it-GGUF|ggml-org/gemma-4-26B-A4B-it-GGUF",
            16.9,
            32,
            "Новое поколение Gemma, смесь экспертов: качество крупной модели при скорости небольшой.",
        ),
        Preset(
            "qwen3-30b-a3b",
            "Точная · Qwen3 30B-A3B Instruct",
            "Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
            HF
            + "unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF/resolve/main/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf",
            18.6,
            32,
            "Точные формулировки, уверенный русский, длинные встречи.",
        ),
        Preset(
            "gigachat3.1-lightning",
            "Быстрая для русского · GigaChat 3.1 Lightning",
            "GigaChat3.1-10B-A1.8B-Q4_K_M.gguf",
            "hf-repo:ai-sage/GigaChat3.1-10B-A1.8B-GGUF|bartowski/ai-sage_GigaChat3.1-10B-A1.8B-GGUF"
            "|bartowski/ai-sage_GigaChat3-10B-A1.8B-GGUF",
            6.4,
            16,
            "Сбер, обучена на русском. Смесь экспертов с 1,8B активных параметров — самая быстрая.",
        ),
        Preset(
            "gemma4-12b",
            "Сбалансированная · Gemma 4 12B",
            "gemma-4-12b-it-Q4_K_M.gguf",
            "hf-repo:unsloth/gemma-4-12b-it-GGUF|ggml-org/gemma-4-12b-it-GGUF",
            7.1,
            16,
            "Аккуратные сводки и ответы по-русски, медленнее GigaChat Lightning.",
        ),
        Preset(
            "gemma3-12b",
            "Предыдущее поколение · Gemma 3 12B",
            "gemma-3-12b-it-Q4_K_M.gguf",
            HF + "unsloth/gemma-3-12b-it-GGUF/resolve/main/gemma-3-12b-it-Q4_K_M.gguf",
            7.3,
            16,
            "Хорошие сводки по-русски; Gemma 4 12B новее при том же размере.",
        ),
        Preset(
            "qwen3-4b",
            "Лёгкая · Qwen3 4B Instruct",
            "Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            HF + "unsloth/Qwen3-4B-Instruct-2507-GGUF/resolve/main/Qwen3-4B-Instruct-2507-Q4_K_M.gguf",
            2.5,
            8,
            "Для Mac с 8 ГБ: быстрая и лёгкая, но формулирует проще.",
        ),
    ]
}

# A small model asked typed questions («есть ли в фрагменте что-то по делу: yes/no») —
# decisions with probabilities instead of text, in the spirit of System One models.
DECISION_PRESET = Preset(
    "qwen3-0.6b",
    "Отбор фрагментов · Qwen3 0.6B",
    "Qwen3-0.6B-Q8_0.gguf",
    "hf-repo:unsloth/Qwen3-0.6B-GGUF|ggml-org/Qwen3-0.6B-GGUF#q8_0",
    0.64,
    0,
    "Отсеивает пустые фрагменты перед сводкой: приветствия, «меня слышно?», шум.",
    ram_gb=1.2,
)


def decision_model_path():
    return models_dir() / DECISION_PRESET.file


# The same models converted for Apple MLX (4-bit). A model is a folder of files, taken
# from the first repository listed that has it; sizes are approximate.
MLX_PRESETS = {
    preset.key: preset
    for preset in [
        Preset(
            "gemma4-26b-a4b",
            "Максимальное качество · Gemma 4 26B-A4B · MLX",
            "mlx/gemma-4-26b-a4b-it-4bit",
            "hf-snapshot:mlx-community/gemma-4-26b-a4b-it-4bit|mlx-community/gemma-4-26B-A4B-it-4bit",
            15.6,
            32,
            "Смесь экспертов: быстрая для своего качества. Для Mac от 32 ГБ.",
        ),
        Preset(
            "qwen3-30b-a3b",
            "Точная · Qwen3 30B-A3B Instruct · MLX",
            "mlx/Qwen3-30B-A3B-Instruct-2507-4bit",
            "hf-snapshot:mlx-community/Qwen3-30B-A3B-Instruct-2507-4bit",
            17.2,
            32,
            "Точные формулировки, уверенный русский. Для Mac от 32 ГБ.",
        ),
        Preset(
            "gemma4-12b",
            "Сбалансированная · Gemma 4 12B · MLX",
            "mlx/gemma-4-12b-it-4bit",
            "hf-snapshot:mlx-community/gemma-4-12b-it-4bit|mlx-community/gemma-4-12B-it-4bit",
            6.9,
            16,
            "Аккуратные сводки по-русски. Для Mac от 16 ГБ.",
        ),
        Preset(
            "qwen3-4b",
            "Лёгкая · Qwen3 4B Instruct · MLX",
            "mlx/Qwen3-4B-Instruct-2507-4bit",
            "hf-snapshot:mlx-community/Qwen3-4B-Instruct-2507-4bit",
            2.3,
            8,
            "Для Mac с 8 ГБ: быстрая, но формулирует проще.",
        ),
    ]
}
MLX_MARK = ".complete"  # written when every file of an MLX model has arrived


def model_present(path):
    """A downloaded model: a GGUF/whisper file, or an MLX folder whose download finished."""
    path = Path(path).expanduser()
    return path.is_file() or (path / MLX_MARK).is_file()


def path_size(path):
    path = Path(path).expanduser()
    if path.is_dir():
        return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())
    return path.stat().st_size if path.exists() else 0


def mlx_supported():
    """Apple Silicon with the MLX packages in the app (tests may allow it elsewhere)."""
    import importlib.util
    import platform
    import sys

    machine_ok = sys.platform == "darwin" and platform.machine() == "arm64"
    if not machine_ok and os.environ.get("SAMARIZATOR_MLX_ANYWHERE") != "1":
        return False
    return all(importlib.util.find_spec(name) for name in ("mlx_lm", "llguidance"))


def mlx_usable(settings):
    return settings.llm_engine == "mlx" and bool(settings.mlx_model) and model_present(
        settings.mlx_model
    ) and mlx_supported()


# Russian text in JSON costs roughly 2.5–3 characters per token for these tokenizers;
# 2.0 leaves room for escaping and denser text.
CHARS_PER_TOKEN = 2.0
# Final text sees a larger slice of the ledger than one extraction request.
FINAL_INPUT_FACTOR = 2
FINAL_OUTPUT_TOKENS = 6144
# CPU threads of a fully offloaded model (they only sample).
GPU_THREADS = 4
# Parallel summary requests at most; more rarely helps on a laptop GPU.
MAX_SLOTS = 4
# q8_0 K and V caches against f16: 8.5 bits per value instead of 16.
COMPACT_KV_FACTOR = 0.55
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


# Answer room of the final text by built-in format: a one-page executive summary never
# needs the room of a full protocol, and a model that cannot end its turn is cut off sooner.
FINAL_FORMAT_TOKENS = dict(executive=2048, interview=3072, protocol=4096, lecture=4096)


def final_output_tokens(settings):
    wanted = max(settings.max_output_tokens, FINAL_OUTPUT_TOKENS)
    template = settings.final_prompt.strip()
    standard = not template or template == FINAL_FORMATS.get(settings.final_format, ("", ""))[1].strip()
    if standard:
        wanted = min(wanted, FINAL_FORMAT_TOKENS.get(settings.final_format, wanted))
    return wanted


def context_tokens(settings):
    """Context window that fits the largest request the pipeline can send."""
    user = len(settings.summary_instructions)
    extraction = len(SYSTEM) + user + max(len(MAP_PROMPT), len(REVIEW_PROMPT)) + settings.input_chars
    extraction = extraction / CHARS_PER_TOKEN + settings.max_output_tokens
    final = len(FINAL_SYSTEM) + user + len(settings.final_prompt) + 2000 + final_input_chars(settings)
    final = final / CHARS_PER_TOKEN + final_output_tokens(settings)
    # Notes algorithm: the check reads the block, its context and the notes (at most one answer
    # long); the assembly reads the register of the whole meeting, as long as the final material.
    check = len(NOTES_SYSTEM) + user + len(CHECK_PROMPT) + settings.input_chars
    check = check / CHARS_PER_TOKEN + 2 * settings.max_output_tokens
    assembly = len(NOTES_SYSTEM) + user + len(ASSEMBLE_PROMPT) + final_input_chars(settings)
    assembly = assembly / CHARS_PER_TOKEN + settings.max_output_tokens
    needed = max(extraction, final, check, assembly)
    return min(131072, int(math.ceil(needed / 1024) * 1024))


def kv_gb(settings, slots=1, compact_kv=False):
    """KV cache of `slots` parallel requests; an 8-bit cache takes about half of f16."""
    per_token = KV_BYTES_PER_TOKEN * (COMPACT_KV_FACTOR if compact_kv else 1)
    return slots * context_tokens(settings) * per_token / 1024**3


def memory_estimate_gb(settings, slots=1, compact_kv=False, mlx=False):
    """Model weights + KV cache + server overhead. A planning number, not a measurement."""
    weights = path_size(settings.mlx_model if mlx else settings.llm_model) / 1024**3
    return weights + kv_gb(settings, slots, compact_kv) + SERVER_OVERHEAD_GB


def parallel_slots(settings, total_gb=None):
    """How many blocks the summary model writes at once.

    Generation on Apple Silicon is limited by memory bandwidth, not by arithmetic, so
    two to four requests decoded together cost little more than one: the job finishes
    sooner and spends less energy per word. Each request needs its own KV cache, so the
    number is what this Mac's memory allows (or the user's choice, if it fits).
    """
    if not settings.llm_gpu:
        return 1
    total_gb = ram_gb() if total_gb is None else total_gb
    wanted = settings.llm_parallel or MAX_SLOTS
    fitting = [
        n
        for n in range(1, min(wanted, MAX_SLOTS) + 1)
        if memory_estimate_gb(settings, n, compact_kv=True) <= total_gb * 0.85
    ]
    return max(fitting, default=1)


def job_budget_gb(settings):
    """Watchdog budget of a summary job: the model must fit, whatever the ASR budget is."""
    need = max(
        memory_estimate_gb(settings, parallel_slots(settings), compact_kv=True),
        memory_estimate_gb(settings),  # the fallback start: one slot, full-precision cache
        memory_estimate_gb(settings, mlx=True) if mlx_usable(settings) else 0,
    )
    return max(settings.memory_gb, math.ceil(need * 1.25 + 1))


def preset_ram_gb(preset, settings=None):
    """Memory a model takes while it works, by the same estimate `check_fits` applies."""
    if preset.ram_gb:
        return preset.ram_gb
    from .config import Settings

    tokens = context_tokens(settings or Settings())
    return preset.size_gb + tokens * KV_BYTES_PER_TOKEN / 1024**3 + SERVER_OVERHEAD_GB


def fit(preset, settings=None, total_gb=None):
    """How a model suits this Mac: "ok", "slow" (below the recommended memory) or "no"."""
    total_gb = ram_gb() if total_gb is None else total_gb
    if preset_ram_gb(preset, settings) > total_gb * 0.85:
        return "no"
    return "slow" if round(total_gb) < preset.min_ram_gb else "ok"


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


def download(
    url, target, progress=lambda done, total: None, cancelled=lambda: False, opener=None, min_size=1024
):
    """Resumable download into `target`; a `.part` file survives interruptions.

    Large weights (up to ~19 GB) must not restart from zero after a network hiccup, so
    an interrupted download continues with an HTTP Range request.
    """
    if url.startswith(HF_SNAPSHOT):
        return download_snapshot(url, target, progress, cancelled, opener)
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    import truststore

    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    listed = url.startswith(HF_REPO)
    whisper_weights = listed and target.suffix != ".gguf"
    if listed:
        # The file is chosen from the repository listing once; a resumed download keeps it.
        chosen = part.with_suffix(part.suffix + ".url")
        if part.exists() and chosen.is_file():
            url = chosen.read_text().strip()
        else:
            url = resolve_hf_repo(url, opener, pick_gguf if target.suffix == ".gguf" else pick_ggml)
            chosen.write_text(url)
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
    # A model file is never tiny; the small configs of a folder model pass min_size=1.
    if not part.exists() or part.stat().st_size < min_size:
        raise ValueError("Вместо модели получен слишком маленький файл.")
    if target.suffix == ".gguf":
        with part.open("rb") as f:
            if f.read(4) != b"GGUF":
                part.unlink()
                raise ValueError("Скачанный файл не похож на модель GGUF. Повторите загрузку.")
    if whisper_weights:
        with part.open("rb") as f:
            if f.read(4) != GGML_MAGIC:
                part.unlink()
                raise ValueError("Скачанный файл не похож на модель whisper.cpp. Повторите загрузку.")
    if listed:
        part.with_suffix(part.suffix + ".url").unlink(missing_ok=True)
    part.replace(target)
    return target


# "hf-repo:owner/name|owner/fallback": the weights are picked from the repository listing
# at download time (repositories name their files differently and rename them over time):
# whisper.cpp ggml files for recognition, the preferred GGUF quantisation for summaries.
HF_REPO = "hf-repo:"
GGML_MAGIC = b"lmgg"  # whisper.cpp ggml files start with 0x67676d6c, little-endian


def ggml_rank(path):
    """Order of preference among whisper.cpp files of one repository: full precision first."""
    name = Path(path).name.lower()
    for rank, mark in enumerate(("q8", "q6", "q5", "q4")):
        if mark in name:
            return rank + 1
    return 0


def pick_ggml(entries):
    """The whisper.cpp weights in a Hugging Face tree listing, or None."""
    files = []
    for entry in entries:
        path = str(entry.get("path", ""))
        name = Path(path).name.lower()
        size = (entry.get("lfs") or {}).get("size") or entry.get("size") or 0
        if (
            entry.get("type", "file") == "file"
            and name.endswith(".bin")
            and "ggml" in name
            and "encoder" not in name
            and ".mlmodelc" not in path.lower()
            and size > 100 * 1024**2
        ):
            files.append((ggml_rank(path), path))
    return min(files)[1] if files else None


# Q4_K_M first: the quality/size point all presets are sized for; then close alternatives.
GGUF_QUANTS = ("q4_k_m", "ud-q4_k_xl", "q4_k_xl", "q4_k_s", "q4_0", "q5_k_m", "q6_k", "q8_0")


def pick_gguf(entries, prefer=None):
    """The single-file GGUF of the preferred quantisation in a tree listing, or None."""
    quants = ((prefer,) if prefer else ()) + tuple(q for q in GGUF_QUANTS if q != prefer)
    files = []
    for entry in entries:
        path = str(entry.get("path", ""))
        name = Path(path).name.lower()
        size = (entry.get("lfs") or {}).get("size") or entry.get("size") or 0
        if (
            entry.get("type", "file") != "file"
            or not name.endswith(".gguf")
            or "mmproj" in name  # vision projector, not the language model
            or "-of-0" in name  # split files: llama-server is given one file
            or size < 200 * 1024**2
        ):
            continue
        rank = next((i for i, quant in enumerate(quants) if quant in name), None)
        if rank is not None:
            files.append((rank, path.count("/"), path))
    return min(files)[2] if files else None


def resolve_hf_repo(spec, opener, pick=pick_ggml):
    """Download address of the weights in the first repository that has them."""
    import json

    # "…#q8_0" asks for another quantisation than the default Q4_K_M.
    spec, _, prefer = spec.removeprefix(HF_REPO).partition("#")
    if prefer and pick is pick_gguf:
        pick = lambda entries, choose=pick_gguf: choose(entries, prefer)  # noqa: E731
    for repo in spec.split("|"):
        request = urllib.request.Request(
            f"{HF}api/models/{repo}/tree/main?recursive=true", headers={"User-Agent": "Samarizator"}
        )
        try:
            with opener.open(request, timeout=60) as response:
                entries = json.loads(response.read(8 * 1024**2))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        if isinstance(entries, list) and (path := pick([e for e in entries if isinstance(e, dict)])):
            return f"{HF}{repo}/resolve/main/{path}"
    raise ValueError(
        "Не удалось найти файл модели в репозитории Hugging Face. Проверьте интернет и повторите "
        "загрузку."
    )


# "hf-snapshot:owner/name|owner/fallback": a model that is a folder of files (MLX).
HF_SNAPSHOT = "hf-snapshot:"
SNAPSHOT_SUFFIXES = {".json", ".safetensors", ".model", ".txt", ".jinja", ".tiktoken"}


def snapshot_files(entries):
    """Files of an MLX model in a tree listing: weights, configs, tokenizer; no code."""
    files = [
        entry
        for entry in entries
        if entry.get("type", "file") == "file"
        and Path(str(entry.get("path", ""))).suffix in SNAPSHOT_SUFFIXES
        and not str(entry.get("path", "")).startswith(".")
    ]
    names = {Path(str(entry["path"])).name for entry in files}
    has_weights = any(name.endswith(".safetensors") for name in names)
    return files if "config.json" in names and has_weights else []


def download_snapshot(spec, target, progress=lambda done, total: None, cancelled=lambda: False, opener=None):
    """Every file of a folder model into `target`, resumable file by file."""
    import json

    import truststore

    target = Path(target)
    context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
    chosen = None
    for repo in spec.removeprefix(HF_SNAPSHOT).split("|"):
        request = urllib.request.Request(
            f"{HF}api/models/{repo}/tree/main?recursive=true", headers={"User-Agent": "Samarizator"}
        )
        try:
            with opener.open(request, timeout=60) as response:
                entries = json.loads(response.read(8 * 1024**2))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            continue
        files = snapshot_files([e for e in entries if isinstance(e, dict)]) if isinstance(entries, list) else []
        if files:
            chosen = repo, files
            break
    if not chosen:
        raise ValueError("Не удалось найти модель MLX на Hugging Face. Проверьте интернет и повторите.")
    repo, files = chosen
    sizes = [(entry.get("lfs") or {}).get("size") or entry.get("size") or 0 for entry in files]
    total, finished = sum(sizes), 0
    target.mkdir(parents=True, exist_ok=True)
    for entry, size in zip(files, sizes):
        path = str(entry["path"])
        destination = target / path
        if not destination.is_file():
            download(
                f"{HF}{repo}/resolve/main/{path}",
                destination,
                progress=lambda done, _total, base=finished: progress(base + done, total),
                cancelled=cancelled,
                opener=opener,
                min_size=1,
            )
        finished += size
        progress(finished, total)
    (target / MLX_MARK).write_text(repo)
    return target


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def server_args(
    settings, port, key, binary=None, speculative=True, slots=1, compact_kv=False, ctx_size=None
):
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
        # Every slot gets the context of the largest request; the system prompt stays cached.
        "--ctx-size",
        str((ctx_size or context_tokens(settings)) * slots),
        "--parallel",
        str(slots),
        # An 8-bit KV cache: half the memory and bandwidth, quality practically unchanged.
        # It needs flash attention, which Metal supports for these models.
        *(["--flash-attn", "on", "--cache-type-k", "q8_0", "--cache-type-v", "q8_0"] if compact_kv else []),
        # With every layer on the GPU the CPU threads only sample tokens: a few suffice,
        # and without polling they sleep instead of spinning (the default busy-waits).
        "--threads",
        str(min(settings.threads, GPU_THREADS) if settings.llm_gpu else settings.threads),
        "--poll",
        "0" if settings.llm_gpu else "50",
        "--n-gpu-layers",
        "999" if settings.llm_gpu else "0",
        # Lossless speedup: when the answer repeats text already in the context (quotes from
        # the transcript, the draft under review, the register in the final text), whole runs
        # of tokens are proposed and verified in one pass instead of one by one. Verification
        # keeps the model's own distribution, so the summary is the same, only sooner.
        *(["--spec-type", "ngram-mod"] if speculative else []),
        # Instruct models answer directly; hybrid "thinking" models skip the hidden essay.
        "--reasoning-budget",
        "0",
        "--jinja",
        # The answer is taken as plain content (JSON is enforced by the request grammar):
        # the chat-format parser fails a cut-off JSON with HTTP 500 instead of "length".
        "--skip-chat-parsing",
        # Special tokens appear in the answer as text. Some models (GigaChat 3) end a turn
        # with a token llama.cpp does not count as end of generation; seen as text, it is
        # caught by the request's stop list instead of writing on to the answer limit.
        "--special",
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


def explain_mlx_failure(log_text):
    lowered = log_text.lower()
    if "memory" in lowered:
        return "Модели MLX не хватило памяти."
    if "unsupported" in lowered or "model type" in lowered:
        return "Эта версия MLX не знает архитектуру выбранной модели."
    return "Модель MLX не запустилась."


def explain_failure(log_text):
    lowered = log_text.lower()
    for marker, message in LOAD_ERRORS:
        if marker in lowered:
            return message
    return "Локальная модель сводок не запустилась. Проверьте файл модели и свободную память."


class ServerExited(RuntimeError):
    """llama-server quit while loading; the message says why."""


class LlamaServer:
    """`with LlamaServer(settings, work) as server:` — server.url and server.key are ready."""

    def __init__(
        self,
        settings,
        work,
        progress=lambda *_: None,
        binary=None,
        load_timeout=900,
        slots=None,
        ctx_size=None,
    ):
        self.settings = settings
        self.ctx_size = ctx_size
        # Fast start first; if llama-server refuses it, the plain one-slot start.
        fast = slots if slots is not None else parallel_slots(settings)
        self.plans = [(fast, settings.llm_gpu, True), (1, False, False)]
        self.slots = 1
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
        self.work.mkdir(parents=True, exist_ok=True)
        for attempt, (slots, compact_kv, speculative) in enumerate(self.plans):
            try:
                self.start(binary, speculative, slots, compact_kv)
                self.slots = slots
                return self
            except ServerExited:
                # Parallel slots, the 8-bit cache and speculative decoding only speed things
                # up: a server that cannot start with them is started plain before giving up.
                self.stop()
                if attempt == len(self.plans) - 1:
                    raise
        return self

    def start(self, binary, speculative, slots=1, compact_kv=False):
        env = os.environ.copy()
        env.pop("SAMARIZATOR_API_KEY", None)
        with self.log.open("wb") as out:
            self.proc = subprocess.Popen(
                server_args(
                    self.settings, self.port, self.key, binary, speculative, slots, compact_kv, self.ctx_size
                ),
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

    def wait_ready(self):
        self.progress("Загрузка локальной модели сводок в память…")
        deadline = time.monotonic() + self.load_timeout
        # Loopback only: never route the health check through a system or corporate proxy.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise ServerExited(explain_failure(self.log.read_text(errors="replace")))
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


class MlxServer(LlamaServer):
    """The MLX engine as a child process with the same API, URL and key as llama-server."""

    def __init__(self, settings, work, progress=lambda *_: None, load_timeout=900):
        super().__init__(settings, work, progress, load_timeout=load_timeout, slots=1)
        self.log = self.work / "mlx-server.log"

    def __enter__(self):
        from .bundle import python_command

        self.work.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.pop("SAMARIZATOR_API_KEY", None)
        command = python_command(
            "samarizator.mlx_server",
            "--model",
            str(Path(self.settings.mlx_model).expanduser()),
            "--port",
            self.port,
            "--api-key",
            self.key,
            "--ctx",
            context_tokens(self.settings),
        )
        with self.log.open("wb") as out:
            self.proc = subprocess.Popen(
                command, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env
            )
        try:
            self.wait_ready()
        except ServerExited:
            self.stop()
            raise ServerExited(explain_mlx_failure(self.log.read_text(errors="replace"))) from None
        except BaseException:
            self.stop()
            raise
        self.slots = 1
        return self


def start_summary_server(settings, work, progress=lambda *_: None):
    """The summary engine for a job: MLX when chosen and available, llama.cpp otherwise.

    MLX is experimental; whatever goes wrong with it, the job continues on llama.cpp.
    """
    if mlx_usable(settings):
        server = MlxServer(settings, work, progress)
        try:
            return server.__enter__()
        except (RuntimeError, OSError) as exc:
            progress(f"MLX не запустился ({str(exc)[:80]}) — сводка на llama.cpp")
    return LlamaServer(settings, work, progress).__enter__()
