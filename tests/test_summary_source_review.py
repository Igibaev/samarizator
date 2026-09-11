import json

import httpx
import pytest

from samarizator.knowledge import export
from samarizator.summary import (
    ChatClient,
    SummaryFormatError,
    contextual_blocks,
    review_source,
    summarize,
)
from samarizator.summary_browser import summary_html
from samarizator.summary_prompts import MAP_PROMPT, REVIEW_PROMPT


def point(text, refs, **extra):
    return dict(kind="point", text=text, evidence=refs, owner=None, due=None, **extra)


def summary(items):
    return dict(overview="Обсуждение отчёта", items=items, topics=["Отчёт"])


def reviewed(draft):
    return dict(draft, items=[dict(item, draft_ids=[i]) for i, item in enumerate(draft["items"])])


def test_raw_neighbours_are_bounded_and_primary_text_is_never_dropped():
    rows = [dict(id=i, start=i, uncertain=False, text=('Тема "TOS" \\ ' * 100)) for i in range(1, 7)]
    windows = list(contextual_blocks(iter(rows), 4000))
    reconstructed = {row["id"]: "" for row in rows}
    original = {row["id"]: row["text"] for row in rows}
    for block, before, after in windows:
        for sid, line in block:
            reconstructed[sid] += json.loads(line)["text"]
        for context in (before, after):
            assert len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= 400
            assert all(row["text"] in original[row["id"]] for row in context)
        payload = dict(
            source=dict(before=before, segments=[json.loads(line) for _, line in block], after=after)
        )
        assert len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"))) <= 4000
    assert reconstructed == original
    assert windows[0][2] and windows[-1][1]
    assert windows[0][1] == [] and windows[-1][2] == []


def test_http_review_corrects_status_and_adds_omitted_condition_before_synthesis(meeting):
    store, mid, settings = meeting
    store.save_chunk(
        mid,
        0,
        [
            dict(start=0, end=3, speaker="Речь", text="Предлагаю бюджет 17 млн, пока не согласовано."),
            dict(
                start=3, end=6, speaker="Речь", text="Отчёт годится только после проверки пропусков событий."
            ),
        ],
    )
    ids = [r["id"] for r in store.segments(mid)]
    calls = []

    def handler(request):
        assert request.url.host == "company.example"
        prompt = json.loads(request.content)["messages"][-1]["content"]
        if prompt.startswith(MAP_PROMPT):
            calls.append("map")
            result = summary([point("Бюджет 17 млн согласован", [ids[0]], status="agreed")])
        elif prompt.startswith(REVIEW_PROMPT):
            calls.append("review")
            payload = json.loads(prompt[len(REVIEW_PROMPT) :])
            assert "не согласовано" in payload["source"]["segments"][0]["text"]
            result = summary(
                [
                    point(
                        "Предложен бюджет 17 млн; согласования нет",
                        [ids[0]],
                        status="proposed",
                        draft_ids=[0],
                    ),
                    point("Отчёт применим только после проверки пропусков", [ids[1]], draft_ids=[]),
                ]
            )
        else:
            calls.append("brief")
            assert "согласования нет" in prompt and "только после проверки" in prompt
            assert "draft_ids" not in prompt and "review_receipts" not in prompt
            result = summary([point("Бюджет пока предложен; отчёт требует проверки пропусков", ids)])
        return httpx.Response(
            200,
            json={
                "choices": [
                    {"finish_reason": "stop", "message": {"content": json.dumps(result, ensure_ascii=False)}}
                ]
            },
        )

    client = ChatClient(settings, transport=httpx.MockTransport(handler), key="test")
    result = summarize(store, mid, settings, client=client)
    assert len(result["detailed"]["items"]) == 2
    assert result["detailed"]["items"][0]["status"] == "proposed"
    assert not result["detailed"].get("quality_warning")
    checkpoint = store.checkpoint(mid, "summary-map", 0)
    assert checkpoint["draft"]["items"][0]["status"] == "agreed"
    assert checkpoint["review_receipts"][0]["result"]["items"][0]["draft_ids"] == [0]
    summarize(store, mid, settings, client=client)
    assert calls == ["map", "review", "brief", "brief"]


@pytest.mark.parametrize("failure", ["lost_item", "unknown_source", "duplicate", "empty", "network"])
def test_review_failure_preserves_draft_warns_and_retries_without_reextracting(meeting, failure):
    store, mid, settings = meeting
    store.save_chunk(
        mid, 0, [dict(start=0, end=3, speaker="Речь", text="Бюджет 17 млн. Срок ещё не согласован.")]
    )
    sid = store.segments(mid)[0]["id"]
    draft = summary([point("Бюджет 17 млн", [sid]), point("Срок не согласован", [sid])])

    class Client:
        repaired = False
        maps = 0
        audits = 0

        def complete(self, prompt, allowed):
            if prompt.startswith(MAP_PROMPT):
                self.maps += 1
                return draft
            if prompt.startswith(REVIEW_PROMPT):
                self.audits += 1
                result = reviewed(draft)
                if not self.repaired:
                    if failure == "network":
                        raise ValueError("API unavailable")
                    if failure == "lost_item":
                        result["items"] = result["items"][:1]  # same evidence ID, but a lost condition
                    if failure == "unknown_source":
                        result["items"][0]["evidence"] = [99999]
                    if failure == "duplicate":
                        result["items"][1]["draft_ids"] = [0]
                    if failure == "empty":
                        result["items"] = []
                return result
            return draft

    client = Client()
    result = summarize(store, mid, settings, client=client)
    assert result["detailed"]["items"] == draft["items"]
    warning = result["detailed"]["quality_warning"]
    assert warning and warning in summary_html(result["brief"], {sid: "00:00:00"})
    store.update(mid, summary=json.dumps(result))
    assert warning in export(store, mid, settings).read_text()
    assert client.audits == (1 if failure == "network" else 3)
    client.repaired = True
    result = summarize(store, mid, settings, client=client)
    assert client.maps == 1 and not result["detailed"].get("quality_warning")
    assert result["detailed"]["items"] == draft["items"]


def test_large_review_is_batched_without_discarding_draft_or_source():
    source = dict(before=[], segments=[dict(id=1, text="Источники " * 100)], after=[])
    draft = summary([point(f"Тема {i}: " + "условие " * 40, [1]) for i in range(12)])
    requests = []

    class Client:
        def complete(self, prompt, allowed):
            data = prompt[len(REVIEW_PROMPT) :]
            assert len(data) <= 4000
            payload = json.loads(data)
            assert payload["source"] == source and payload["scope"] == "items"
            requests.append(payload)
            return reviewed(payload["draft"])

    result = review_source(Client(), source, draft, 4000)
    assert len(requests) > 1
    assert result["items"] == draft["items"]


def test_review_cannot_replace_primary_topic_with_context_only():
    source = dict(before=[dict(id=1, text="Старая тема")], segments=[dict(id=2, text="Новая тема")], after=[])
    draft = summary([point("Новая тема", [2])])

    class Client:
        def complete(self, prompt, allowed):
            return summary([point("Старая тема", [1], draft_ids=[0])])

    with pytest.raises(SummaryFormatError, match="соседние"):
        review_source(Client(), source, draft, 4000)


def test_explicit_removal_of_unsupported_claim_is_preserved_in_receipt():
    source = dict(before=[], segments=[dict(id=1, text="Бюджет неизвестен")], after=[])
    draft = summary([point("Бюджет неизвестен", [1]), point("Бюджет 100 млн", [1])])

    class Client:
        def complete(self, prompt, allowed):
            return dict(
                summary([point("Бюджет неизвестен", [1], draft_ids=[0])]),
                removed=[dict(draft_id=1, reason="Число 100 млн отсутствует в источнике", evidence=[1])],
            )

    result = review_source(Client(), source, draft, 4000)
    assert len(result["items"]) == 1
    assert result["review_receipts"][0]["result"]["removed"][0]["draft_id"] == 1
