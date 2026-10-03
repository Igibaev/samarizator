import json

import httpx
import pytest

from samarizator.config import Settings
from samarizator.summary import LocalClient, SummaryTooLong, system_prompt
from samarizator.summary_prompts import SYSTEM


def make(handler, **values):
    settings = Settings(**values)
    return LocalClient(
        settings, "http://127.0.0.1:8123", key="one-time-key", transport=httpx.MockTransport(handler)
    )


def response(content, finish="stop"):
    text = content if isinstance(content, str) else json.dumps(content)
    return httpx.Response(200, json={"choices": [{"finish_reason": finish, "message": {"content": text}}]})


def valid():
    return {
        "overview": "Итог",
        "topics": ["Тема"],
        "items": [dict(kind="action", text="Проверить", evidence=[1], owner=None, due=None)],
    }


def test_requests_stay_on_loopback_with_json_grammar_and_one_time_key():
    requests = []

    def handler(req):
        requests.append(req)
        assert req.url.host == "127.0.0.1"
        assert req.url.path == "/v1/chat/completions"
        assert req.headers["Authorization"] == "Bearer one-time-key"
        body = json.loads(req.content)
        assert body["grammar"].startswith("root   ::= object")
        assert body["messages"][0]["content"] == SYSTEM
        assert "audio" not in body
        return response(valid())

    assert make(handler).complete("текст записи", {1})["items"][0]["evidence"] == [1]
    assert len(requests) == 1


def test_user_instructions_extend_but_do_not_replace_the_rules():
    settings = Settings(summary_instructions="Выделяй цены и сроки.")
    prompt = system_prompt(settings)
    assert prompt.startswith(SYSTEM)
    assert prompt.endswith("Выделяй цены и сроки.")
    seen = []

    def handler(req):
        seen.append(json.loads(req.content)["messages"][0]["content"])
        return response(valid())

    make(handler, summary_instructions="Выделяй цены и сроки.").complete("x", {1})
    assert seen == [prompt]


def test_redirect_never_followed():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(307, headers={"Location": "https://public.example/upload"})

    with pytest.raises(RuntimeError, match="HTTP 307"):
        make(handler).complete("private", {1})
    assert len(calls) == 1


def test_length_limit_triggers_adaptive_split():
    with pytest.raises(SummaryTooLong):
        make(lambda req: response(valid(), "length")).complete("private", {1})


def test_context_overflow_is_reported_as_too_long():
    def handler(req):
        return httpx.Response(
            400, json={"error": {"message": "the request exceeds the available context size"}}
        )

    with pytest.raises(SummaryTooLong):
        make(handler).complete("private", {1})


def test_invalid_json_and_fabricated_evidence():
    with pytest.raises(RuntimeError, match="формате"):
        make(lambda req: httpx.Response(200, text="<html>gateway</html>")).complete("private", {1})
    bad = valid()
    bad["items"][0]["evidence"] = [999]
    with pytest.raises(ValueError, match="несуществующий"):
        make(lambda req: response(bad)).complete("private", {1})


def test_thinking_blocks_are_stripped():
    content = "<think>рассуждения модели</think>" + json.dumps(valid())
    assert make(lambda req: response(content)).complete("x", {1})["overview"] == "Итог"


def test_errors_do_not_expose_response_body():
    with pytest.raises(RuntimeError) as caught:
        make(lambda req: httpx.Response(500, text="private transcript")).complete("private", {1})
    assert "private" not in str(caught.value)


def test_retry_while_model_is_busy():
    count = [0]

    def handler(req):
        count[0] += 1
        return httpx.Response(503) if count[0] == 1 else response(valid())

    assert make(handler).complete("text", {1})
    assert count[0] == 2


def test_free_text_has_no_grammar_and_reports_truncation():
    bodies = []

    def handler(req):
        bodies.append(json.loads(req.content))
        return response("```markdown\n# Протокол\n\nТекст\n```", "length")

    text, truncated = make(handler).complete_text("система", "запрос", 6000)
    assert text == "# Протокол\n\nТекст"
    assert truncated
    assert "grammar" not in bodies[0]
    assert bodies[0]["max_tokens"] == 6000
