import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlparse


def data_dir() -> Path:
    path = Path(os.environ.get("SAMARIZATOR_HOME", Path.home() / "Library/Application Support/Samarizator"))
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


@dataclass
class Settings:
    base_url: str = ""
    model: str = ""
    memory_gb: float = 4.0
    threads: int = 4
    chunk_seconds: int = 120
    language: str = "ru"
    gpu: bool = False
    whisper_model: str = ""
    diarization: str = "local"
    speakers: int = -1
    segmentation_model: str = ""
    embedding_model: str = ""
    vault: str = ""
    input_chars: int = 12000
    max_output_tokens: int = 3000

    def chat_url(self):
        base = self.base_url.strip().rstrip("/")
        return base if base.endswith("/chat/completions") else base + "/chat/completions"

    def validate(self, api=False):
        if not 2 <= self.memory_gb <= 64:
            raise ValueError("Бюджет памяти должен быть от 2 до 64 ГиБ.")
        if not 1 <= self.threads <= 16 or not 30 <= self.chunk_seconds <= 300:
            raise ValueError("Некорректные параметры CPU или длины фрагмента.")
        if self.diarization not in {"local", "channels", "manual"}:
            raise ValueError("Неизвестный режим собеседников.")
        if not 4000 <= self.input_chars <= 48000 or not 512 <= self.max_output_tokens <= 8000:
            raise ValueError("Некорректный размер контекста.")
        if self.speakers != -1 and not 1 <= self.speakers <= 20:
            raise ValueError("Число собеседников: -1 (авто) или 1–20.")
        if api:
            parsed = urlparse(self.base_url)
            if (
                parsed.scheme not in {"https", "http"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.fragment
            ):
                raise ValueError("Укажите base URL совместимого API, например https://openrouter.ai/api/v1.")
            if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("HTTP допустим только для локальной модели на localhost. Иначе HTTPS.")
            if not self.model.strip():
                raise ValueError("Укажите название модели.")

    def save(self):
        self.validate()
        path = data_dir() / "settings.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2))
        tmp.chmod(0o600)
        tmp.replace(path)

    @classmethod
    def from_dict(cls, values):
        values = dict(values)
        # Settings saved before the universal API tab held a full chat/completions endpoint.
        if not values.get("base_url") and values.get("endpoint"):
            values["base_url"] = values["endpoint"].strip().rstrip("/").removesuffix("/chat/completions")
        return cls(**{k: v for k, v in values.items() if k in cls.__dataclass_fields__})

    @classmethod
    def load(cls):
        path = data_dir() / "settings.json"
        if path.exists():
            return cls.from_dict(json.loads(path.read_text()))
        models = data_dir() / "models"
        return cls(
            whisper_model=str(models / "ggml-small-q5_1.bin"),
            segmentation_model=str(models / "segmentation.onnx"),
            embedding_model=str(models / "embedding.onnx"),
            vault=str(Path.home() / "Documents/Samarizator"),
        )


def set_api_key(key: str):
    import keyring

    keyring.set_password("samarizator", "corporate-api", key)


def get_api_key() -> str:
    key = os.environ.get("SAMARIZATOR_API_KEY")
    if key:
        return key
    import keyring

    return keyring.get_password("samarizator", "corporate-api") or ""
