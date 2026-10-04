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


def test_the_model_reads_a_tidied_transcript_but_the_transcript_stays(meeting):
    from samarizator.summary import summarize
    from samarizator.summary_prompts import MAP_PROMPT

    store, mid, settings = meeting
    store.save_chunk(
        mid,
        0,
        [
            dict(
                start=0,
                end=2,
                speaker="Речь",
                text="Эээ, ну, я я я думаю, бюджет как бы 17 миллионов.",
                uncertain=0,
            ),
            dict(start=2, end=3, speaker="Речь", text="Ммм… эм.", uncertain=0),
            dict(start=3, end=5, speaker="Речь", text="Как бы не опоздать с отчётом.", uncertain=0),
        ],
    )
    ids = [row["id"] for row in store.segments(mid)]

    class Fake:
        def __init__(self):
            self.maps = []

        def complete(self, prompt, allowed):
            if prompt.startswith(REVIEW_PROMPT):
                draft = json.loads(prompt[len(REVIEW_PROMPT) :])["draft"]
                return dict(keep=list(range(len(draft["items"]))), edit=[], add=[], removed=[])
            if prompt.startswith(MAP_PROMPT):
                self.maps.append(json.loads(prompt[len(MAP_PROMPT) :])["source"]["segments"])
            return dict(overview="Бюджет.", topics=[], items=[item("Бюджет 17 млн", min(allowed))])

    fake = Fake()
    summarize(store, mid, settings, client=fake)
    texts = {row["id"]: row["text"] for row in fake.maps[0]}
    assert texts == {ids[0]: "Я думаю, бюджет 17 миллионов.", ids[2]: "Как бы не опоздать с отчётом."}
    assert store.segments(mid)[0]["text"].startswith("Эээ")  # the transcript itself is untouched
    settings.clean_input = False
    fake = Fake()
    store.reset_summary(mid)
    summarize(store, mid, settings, client=fake)
    assert fake.maps[0][0]["text"].startswith("Эээ")


def test_parallel_slots_follow_the_memory_of_the_mac(tmp_path):
    from samarizator import local_llm
    from samarizator.config import Settings

    model = tmp_path / "m.gguf"
    with model.open("wb") as f:
        f.truncate(int(6.4 * 1024**3))  # GigaChat Lightning
    settings = Settings(llm_model=str(model))
    # 16 GB: the 8-bit cache leaves room for a few blocks at once; 8 GB: one.
    assert local_llm.parallel_slots(settings, total_gb=16) >= 2
    assert local_llm.parallel_slots(settings, total_gb=10) == 1
    assert local_llm.parallel_slots(settings, total_gb=128) == local_llm.MAX_SLOTS
    settings.llm_parallel = 2
    assert local_llm.parallel_slots(settings, total_gb=128) == 2
    settings.llm_gpu = False
    assert local_llm.parallel_slots(settings, total_gb=128) == 1
    # The budget covers both the fast start and the plain fallback.
    settings.llm_gpu, settings.llm_parallel = True, 0
    fast = local_llm.memory_estimate_gb(settings, local_llm.parallel_slots(settings), compact_kv=True)
    assert local_llm.job_budget_gb(settings) >= max(fast, local_llm.memory_estimate_gb(settings))


def test_blocks_are_written_in_parallel_and_kept_in_order(meeting):
    import threading
    import time

    from samarizator.summary import summarize
    from samarizator.summary_prompts import MAP_PROMPT

    store, mid, settings = meeting
    settings.input_chars = 4000
    store.save_chunk(
        mid,
        0,
        [
            dict(start=i * 10, end=i * 10 + 3, speaker="Речь", text=f"Тема {i}. " * 150, uncertain=0)
            for i in range(6)
        ],
    )

    class Fake:
        parallel = 3

        def __init__(self):
            self.active = self.peak = 0
            self.lock = threading.Lock()

        def complete(self, prompt, allowed):
            if prompt.startswith(REVIEW_PROMPT):
                draft = json.loads(prompt[len(REVIEW_PROMPT) :])["draft"]
                return dict(keep=list(range(len(draft["items"]))), edit=[], add=[], removed=[])
            with self.lock:
                self.active += 1
                self.peak = max(self.peak, self.active)
            time.sleep(0.05)
            with self.lock:
                self.active -= 1
            ids = allowed
            if prompt.startswith(MAP_PROMPT):
                ids = {r["id"] for r in json.loads(prompt[len(MAP_PROMPT) :])["source"]["segments"]}
            first = min(ids)
            return dict(overview=f"Блок {first}.", topics=[], items=[item(f"Пункт {first}", first)])

    fake = Fake()
    progress = []
    result = summarize(store, mid, settings, client=fake, progress=progress.append)
    assert fake.peak >= 2  # requests really overlapped
    firsts = [it["evidence"][0] for it in result["detailed"]["items"]]
    assert firsts == sorted(firsts)  # the register keeps the order of the recording
    assert any("одновременно" in line for line in progress)
