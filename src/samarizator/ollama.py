"""Small native Ollama client for the always-on companion.

Summary generation keeps its existing OpenAI-compatible client.  The companion
uses Ollama's native endpoint because it exposes ``think`` and ``keep_alive`` —
both are important for a cool, mostly-idle laptop.
"""

import json
from urllib.parse import urlparse

import httpx


def local_ollama_url(value):
    base = str(value).strip().rstrip("/")
    parsed = urlparse(base)
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
    }:
        raise ValueError("ИИ-помощник подключается только к локальному Ollama на этом Mac.")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Некорректный адрес локального Ollama.")
    return base


class OllamaClient:
    def __init__(self, base_url="http://localhost:11434", transport=None, timeout=120):
        self.base_url = local_ollama_url(base_url)
        self.transport = transport
        self.timeout = timeout

    def _request(self, method, path, payload=None):
        with httpx.Client(
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(self.timeout, connect=3),
            transport=self.transport,
        ) as client:
            try:
                with client.stream(method, self.base_url + path, json=payload) as response:
                    if not 200 <= response.status_code < 300:
                        raise ValueError(
                            f"Ollama: HTTP {response.status_code}. Проверьте, что сервис запущен."
                        )
                    raw = bytearray()
                    for block in response.iter_bytes():
                        raw.extend(block)
                        if len(raw) > 2_000_000:
                            raise ValueError("Ответ локальной модели слишком большой.")
            except httpx.HTTPError:
                raise ValueError(
                    "Локальный Ollama недоступен. Запустите Ollama и проверьте адрес в настройках."
                ) from None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise ValueError("Ollama вернул повреждённый JSON-ответ.") from None

    def models(self):
        body = self._request("GET", "/api/tags")
        return [
            item.get("name")
            for item in body.get("models", [])
            if isinstance(item, dict) and isinstance(item.get("name"), str)
        ]

    def chat(self, model, messages, keep_alive="0"):
        if not str(model).strip():
            raise ValueError("Укажите локальную модель помощника в настройках.")
        body = self._request(
            "POST",
            "/api/chat",
            dict(
                model=str(model).strip(),
                messages=messages,
                stream=False,
                think=False,
                keep_alive=keep_alive or "0",
                options=dict(temperature=0.25, num_ctx=16_384),
            ),
        )
        message = body.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Локальная модель не вернула текстовый ответ.")
        return content.strip()
