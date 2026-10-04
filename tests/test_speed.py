"""Faster summaries with the same guarantees: diff reviews, cleaner input, parallel blocks."""

import json

import pytest

from samarizator.summary import SummaryFormatError, review_source
from samarizator.summary_prompts import REVIEW_PROMPT


def item(text, ref, **extra):
    return dict(kind="point", text=text, evidence=[ref], owner=None, due=None, status="unspecified", **extra)


SOURCE = dict(
    before=[],
    segments=[dict(id=i, start=float(i), uncertain=False, text=f"Реплика {i}") for i in (1, 2, 3, 4)],
    after=[],
)
DRAFT = dict(
    overview="Обсудили бюджет.",
    topics=["Бюджет"],
    items=[item("Бюджет 17 млн", 1), item("Срок — пятница", 2), item("Срок до пятницы", 3)],
)


class Client:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete(self, prompt, allowed):
        self.prompts.append(prompt)
        return self.answers.pop(0)


def test_review_returns_only_its_changes_and_the_rest_is_copied_from_the_draft():
    diff = dict(
        keep=[0],
        edit=[dict(item("Срок сдачи отчёта — пятница", 2), draft_ids=[1, 2])],
        add=[item("Ответственный не назначен", 4)],
        removed=[],
        overview=None,
        topics=None,
    )
    client = Client(diff)
    result = review_source(client, SOURCE, DRAFT, 100000)
    assert [i["text"] for i in result["items"]] == [
        "Бюджет 17 млн",
        "Срок сдачи отчёта — пятница",
        "Ответственный не назначен",
    ]
    assert result["overview"] == "Обсудили бюджет." and result["topics"] == ["Бюджет"]
    assert client.prompts[0].startswith(REVIEW_PROMPT) and "ТОЛЬКО ИЗМЕНЕНИЯ" in REVIEW_PROMPT
    receipt = result["review_receipts"][0]["result"]
    assert [i["draft_ids"] for i in receipt["items"]] == [[0], [1, 2], []]


def test_a_review_that_loses_a_draft_point_is_rejected():
    lost = dict(keep=[0], edit=[], add=[], removed=[])  # points 1 and 2 are not accounted for
    with pytest.raises(SummaryFormatError, match="потеряла"):
        review_source(Client(lost, lost, lost), SOURCE, DRAFT, 100000)
    no_ids = dict(keep=[0, 1], edit=[item("Без индексов", 3)], add=[], removed=[])
    with pytest.raises(SummaryFormatError, match="draft_ids"):
        review_source(Client(no_ids, no_ids, no_ids), SOURCE, DRAFT, 100000)


def test_the_full_review_format_is_still_accepted():
    full = dict(
        overview="Обзор",
        topics=[],
        items=[dict(i, draft_ids=[n]) for n, i in enumerate(DRAFT["items"])],
        removed=[],
    )
    result = review_source(Client(json.loads(json.dumps(full))), SOURCE, DRAFT, 100000)
    assert len(result["items"]) == 3 and result["overview"] == "Обзор"
