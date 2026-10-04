"""Typed decisions by a small local model: which fragments carry nothing for the summary.

The idea of "System One" models such as Jev — a fixed question with fixed answers, and
a probability for each answer instead of generated text — done locally: a 0.6B model on
the bundled llama-server reads one short fragment and answers yes/no to «is there
anything here that belongs in the minutes?». Only the next-token probabilities are read,
so a decision costs one forward pass. Nothing leaves the Mac, nothing new is installed.

Selection is conservative: only short fragments are asked about, and one is left out
only when the model is at least 90% sure it is empty (greetings, «меня слышно?», noise).
The fragments stay in the transcript; the summary page says how many were left out.
"""

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor

import httpx

from .privacy import paragraphs
from .speech import tidy_rows

# Fragments longer than this are kept without asking: they almost always say something.
SHORT_CHARS = 300
DROP_THRESHOLD = 0.9
SYSTEM = (
    "Ты классификатор фрагментов расшифровки рабочей встречи. Отвечай одним словом: yes или no."
)
QUESTION = """Фрагмент:
<<<
{text}
>>>
Есть ли в этом фрагменте хоть что-то по делу встречи: факт, мнение, вопрос, предложение,
решение, задача, число, имя, срок, договорённость?
Приветствия, прощания, проверка связи и звука («меня слышно?», «вы меня видите?»),
паузы, шум и обрывки без смысла — это no. Если сомневаешься — yes.
Ответ (yes или no):"""
YES = {"yes", "да"}
NO = {"no", "нет"}


class Decider:
    """yes/no probabilities from llama-server's next-token distribution."""

    def __init__(self, url, key="", transport=None, parallel=1):
        self.url = url.rstrip("/") + "/v1/chat/completions"
        self.key = key
        self.parallel = parallel
        self.client = httpx.Client(
            trust_env=False, follow_redirects=False, timeout=httpx.Timeout(120, connect=10), transport=transport
        )

    def close(self):
        self.client.close()

    def keep_probability(self, text):
        payload = dict(
            messages=[dict(role="system", content=SYSTEM), dict(role="user", content=QUESTION.format(text=text))],
            max_tokens=1,
            temperature=0,
            logprobs=True,
            top_logprobs=10,
            chat_template_kwargs=dict(enable_thinking=False),
        )
        headers = {"Authorization": f"Bearer {self.key}"} if self.key else {}
        response = self.client.post(self.url, json=payload, headers=headers)
        if response.status_code != 200:
            raise RuntimeError(f"Модель отбора: HTTP {response.status_code}.")
        try:
            first = response.json()["choices"][0]["logprobs"]["content"][0]
            candidates = first.get("top_logprobs") or [first]
        except (KeyError, IndexError, TypeError, ValueError):
            return 1.0  # no distribution: keep the fragment
        yes = no = 0.0
        for candidate in candidates:
            word = str(candidate.get("token", "")).strip().casefold()
            probability = math.exp(float(candidate.get("logprob", -math.inf)))
            if word in YES:
                yes += probability
            elif word in NO:
                no += probability
        return 1.0 if yes + no == 0 else yes / (yes + no)


class decision_model:
    """Context manager: the small model on its own llama-server, with a Decider bound to it."""

    SLOTS = 4
    CONTEXT = 1024

    def __init__(self, settings, work, progress):
        from dataclasses import replace

        from .local_llm import LlamaServer, decision_model_path

        self.settings = replace(settings, llm_model=str(decision_model_path()), llm_parallel=0)
        self.server = LlamaServer(self.settings, work, progress, slots=self.SLOTS, ctx_size=self.CONTEXT)
        self.decider = None

    def __enter__(self):
        self.server.__enter__()
        self.decider = Decider(self.server.url, self.server.key, parallel=self.server.slots)
        return self.decider

    def __exit__(self, *exc):
        if self.decider:
            self.decider.close()
        self.server.__exit__(*exc)
        return False


def empty_fragments(store, mid, settings, decider, progress=lambda *_: None):
    """(segment ids to leave out, their seconds). Cached per meeting in a summary checkpoint."""
    groups = [
        group
        for group in paragraphs(list(tidy_rows(store.iter_segments(mid), settings.clean_input)))
        if sum(len(row["text"]) for row in group) <= SHORT_CHARS
    ]
    texts = [" ".join(row["text"] for row in group) for group in groups]
    digest = hashlib.sha256(json.dumps([QUESTION, SYSTEM, DROP_THRESHOLD, texts]).encode()).hexdigest()
    cached = store.checkpoint(mid, "summary-prune", 0)
    if cached and cached.get("digest") == digest:
        return set(cached["dropped"]), cached["seconds"]
    if not groups:
        return set(), 0.0
    done = []

    def ask(text):
        probability = decider.keep_probability(text)
        done.append(1)
        if len(done) % 20 == 0 or len(done) == len(texts):
            progress(f"Отбор содержательных фрагментов: {len(done)} из {len(texts)}")
        return probability

    progress(f"Отбор содержательных фрагментов: 0 из {len(texts)}")
    with ThreadPoolExecutor(max_workers=max(1, decider.parallel)) as pool:
        keep = list(pool.map(ask, texts))
    dropped, seconds = set(), 0.0
    for group, probability in zip(groups, keep):
        if 1 - probability >= DROP_THRESHOLD:
            dropped.update(row["id"] for row in group)
            seconds += group[-1]["end"] - group[0]["start"]
    store.save_checkpoint(mid, "summary-prune", 0, dict(digest=digest, dropped=sorted(dropped), seconds=seconds))
    return dropped, seconds


def select_fragments(store, mid, settings, progress, decider=None, work=None, local=True):
    """Fragments to leave out of the summary; never fails the summary itself."""
    from .local_llm import decision_model_path

    if not settings.prune_fragments:
        return set(), 0.0
    try:
        if decider is not None:
            return empty_fragments(store, mid, settings, decider, progress)
        if not local or not decision_model_path().is_file():
            return set(), 0.0
        import tempfile

        with tempfile.TemporaryDirectory(prefix="samarizator-decide-") as temp:
            with decision_model(settings, work or temp, progress) as model:
                return empty_fragments(store, mid, settings, model, progress)
    except (RuntimeError, ValueError, OSError, httpx.HTTPError):
        progress("Отбор фрагментов пропущен: сводка будет по всей расшифровке")
        return set(), 0.0
