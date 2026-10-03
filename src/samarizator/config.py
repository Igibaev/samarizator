import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .bundle import bundled_model


def data_dir() -> Path:
    path = Path(os.environ.get("SAMARIZATOR_HOME", Path.home() / "Library/Application Support/Samarizator"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


# Free-text limits: the user's instructions travel inside every summary request.
INSTRUCTIONS_LIMIT = 4000
FINAL_PROMPT_LIMIT = 8000


@dataclass
class Settings:
    memory_gb: float = 4.0
    threads: int = 4
    chunk_seconds: int = 120
    language: str = "ru"
    gpu: bool = False
    whisper_model: str = ""
    vault: str = ""
    input_chars: int = 12000
    max_output_tokens: int = 4096
    pause_boundaries: bool = False
    vad: bool = False
    vad_model: str = ""
    glossary: str = ""
    beam_size: int = 5
    live_source: str = "microphone"
    live_microphone_device: str = ""
    live_system_device: str = ""
    live_system_backend: str = "screencapturekit"
    audio_cleanup: str = "off"
    live_mix: str = "gentle"
    # Local summary model (GGUF for llama.cpp). Nothing leaves the Mac.
    llm_model: str = ""
    llm_preset: str = ""
    llm_gpu: bool = True
    # What the user wants from the summary (audience, focus, terminology) and the
    # shape of the final text. Both are prompts; the evidence rules stay in code.
    summary_instructions: str = ""
    final_format: str = "protocol"
    final_prompt: str = ""

    def quality_profile(self):
        """Opt-in profile. Preserve the user's ASR model and summary model configuration."""
        from dataclasses import replace

        return replace(
            self,
            memory_gb=16,
            threads=8,
            chunk_seconds=90,
            gpu=False,
            pause_boundaries=True,
            vad=True,
            beam_size=5,
            vad_model=self.vad_model or str(data_dir() / "models/ggml-silero-v6.2.0.bin"),
        )

    def validate(self, llm=False):
        if not 1 <= self.beam_size <= 8 or len(self.glossary) > 800:
            raise ValueError("Beam size: 1–8; словарь терминов: не более 800 символов.")
        if self.live_source not in {"microphone", "system", "both"}:
            raise ValueError("Источник live-записи: микрофон, системный звук или оба.")
        if self.live_system_backend not in {"screencapturekit", "device"}:
            raise ValueError("Захват системного звука: ScreenCaptureKit или устройство петли.")
        if self.audio_cleanup not in {"off", "light", "strong"}:
            raise ValueError("Обработка звука перед распознаванием: off, light или strong.")
        # Names mirror live.MIX_PROFILES; kept literal so config does not import capture code.
        if self.live_mix not in {"gentle", "no-resample", "hard-stuff", "stretch", "legacy-pan"}:
            raise ValueError("Неизвестный профиль сведения дорожек live-записи.")
        if not 2 <= self.memory_gb <= 64:
            raise ValueError("Бюджет памяти должен быть от 2 до 64 ГиБ.")
        if not 1 <= self.threads <= 16 or not 30 <= self.chunk_seconds <= 300:
            raise ValueError("Некорректные параметры CPU или длины фрагмента.")
        if not 4000 <= self.input_chars <= 48000 or not 512 <= self.max_output_tokens <= 64000:
            raise ValueError("Некорректный размер контекста.")
        if len(self.summary_instructions) > INSTRUCTIONS_LIMIT:
            raise ValueError(f"Указания для сводки: не более {INSTRUCTIONS_LIMIT} символов.")
        if len(self.final_prompt) > FINAL_PROMPT_LIMIT:
            raise ValueError(f"Формат итогового текста: не более {FINAL_PROMPT_LIMIT} символов.")
        if llm:
            path = Path(self.llm_model).expanduser() if self.llm_model.strip() else None
            if not path or not path.is_file():
                raise ValueError(
                    "Локальная модель сводок не найдена. Откройте Настройки → Основное → Сводки "
                    "и скачайте модель или выберите файл .gguf."
                )
            with path.open("rb") as f:
                if f.read(4) != b"GGUF":
                    raise ValueError("Файл модели сводок не в формате GGUF. Выберите файл .gguf для llama.cpp.")

    def save(self):
        self.validate()
        path = data_dir() / "settings.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))
        tmp.chmod(0o600)
        tmp.replace(path)

    @classmethod
    def from_dict(cls, values):
        # Keys of older versions (cloud API base_url/model/endpoint) are simply dropped.
        return cls(**{k: v for k, v in dict(values).items() if k in cls.__dataclass_fields__})

    def resolved(self):
        """Re-point model paths that moved together with the app.

        Settings keep absolute paths; dragging Samarizator.app from Downloads to
        Applications moves the bundled models, so a missing file is looked up by name
        in the bundle and in Application Support before it is reported as missing.
        """
        from dataclasses import replace

        changes = {}
        for key in ("whisper_model", "vad_model", "llm_model"):
            value = getattr(self, key)
            if not value or Path(value).expanduser().is_file():
                continue
            name = Path(value).name
            for candidate in (bundled_model(name), data_dir() / "models" / name):
                if candidate and Path(candidate).is_file():
                    changes[key] = str(candidate)
                    break
        return replace(self, **changes) if changes else self

    @classmethod
    def load(cls):
        path = data_dir() / "settings.json"
        if path.exists():
            return cls.from_dict(json.loads(path.read_text())).resolved()
        return cls.first_run()

    @classmethod
    def first_run(cls):
        """Defaults that work out of the box: the bundled models when the app carries them."""
        models = data_dir() / "models"
        whisper = bundled_model("ggml-small-q5_1.bin") or models / "ggml-small-q5_1.bin"
        vad = bundled_model("ggml-silero-v6.2.0.bin")
        settings = cls(whisper_model=str(whisper), vault=str(Path.home() / "Documents/Samarizator"))
        if vad:
            # The portable app ships the VAD model: use speech detection and pause-aligned
            # chunks from the start, as the quality profile does.
            settings.vad, settings.vad_model, settings.pause_boundaries = True, str(vad), True
        return settings
