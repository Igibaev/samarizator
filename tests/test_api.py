import json

import httpx
import pytest

from samarizator.config import Settings
from samarizator.summary import CorporateClient


def make(handler):
    settings = Settings(endpoint="https://corp.example/v1/chat/completions", model="enterprise")
    return CorporateClient(settings, transport=httpx.MockTransport(handler), key="secret-fixture")


def response(content, finish="stop"):
    return httpx.Response(
        200, json={"choices": [{"finish_reason": finish, "message": {"content": json.dumps(content)}}]}
    )


def valid():
    return {
        "overview": "Итог",
        "topics": ["Тема"],
        "items": [dict(kind="action", text="Проверить", evidence=[1], owner=None, due=None)],
    }


def test_exact_endpoint_text_only_and_header():
    requests = []

    def handler(req):
        requests.append(req)
        assert req.url.host == "corp.example"
        assert req.headers["Authorization"] == "Bearer secret-fixture"
        body = json.loads(req.content)
        assert body["model"] == "enterprise"
        assert "audio" not in body
        assert "instructions" not in body
        return response(valid())

    assert make(handler).complete("текст записи", {1})["items"][0]["evidence"] == [1]
    assert len(requests) == 1


def test_redirect_never_followed():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(307, headers={"Location": "https://public.example/upload"})

    with pytest.raises(ValueError, match="HTTP 307"):
        make(handler).complete("private", {1})
    assert len(calls) == 1


def test_truncated_response_is_not_published():
    with pytest.raises(ValueError, match="обрезала"):
        make(lambda req: response(valid(), "length")).complete("private", {1})


def test_invalid_json_and_fabricated_evidence():
    with pytest.raises(ValueError, match="формате"):
        make(lambda req: httpx.Response(200, text="<html>gateway</html>")).complete("private", {1})
    bad = valid()
    bad["items"][0]["evidence"] = [999]
    with pytest.raises(ValueError, match="несуществующий"):
        make(lambda req: response(bad)).complete("private", {1})


def test_errors_do_not_expose_response_body():
    with pytest.raises(ValueError) as caught:
        make(lambda req: httpx.Response(401, text="secret-fixture private transcript")).complete(
            "private", {1}
        )
    assert "secret-fixture" not in str(caught.value)
    assert "private" not in str(caught.value)


def test_retry_transient(monkeypatch):
    monkeypatch.setattr("samarizator.summary.time.sleep", lambda _: None)
    count = [0]

    def handler(req):
        count[0] += 1
        return httpx.Response(503) if count[0] == 1 else response(valid())

    assert make(handler).complete("text", {1})
    assert count[0] == 2
