"""Bounded map/reduce with evidence validation and an unabridged topic ledger."""

import hashlib
import json
import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from .speech import tidy_rows
from .summary_prompts import (
    BRIEF_PROMPT,
    DEFAULT_FORMAT,
    FINAL_FORMATS,
    FINAL_PROMPT,
    FINAL_SYSTEM,
    MAP_PROMPT,
    RECONCILE_PROMPT,
    REDUCE_PROMPT,
    REVIEW_PROMPT,
    SYSTEM,
    USER_INSTRUCTIONS,
)

KINDS = {"point", "decision", "action", "risk", "question"}
STATUSES = {"proposed", "agreed", "cancelled", "disputed", "unspecified"}
KIND_ALIASES = {
    "point": "point",
    "key point": "point",
    "fact": "point",
    "тезис": "point",
    "пункт": "point",
    "факт": "point",
    "основная мысль": "point",
    "decision": "decision",
    "решение": "decision",
    "action": "action",
    "action item": "action",
    "task": "action",
    "задача": "action",
    "действие": "action",
    "поручение": "action",
    "risk": "risk",
    "issue": "risk",
    "риск": "risk",
    "проблема": "risk",
    "question": "question",
    "open question": "question",
    "вопрос": "question",
    "открытый вопрос": "question",
}
STATUS_ALIASES = {
    "proposed": "proposed",
    "proposal": "proposed",
    "предложено": "proposed",
    "предложение": "proposed",
    "agreed": "agreed",
    "accepted": "agreed",
    "approved": "agreed",
    "согласовано": "agreed",
    "принято": "agreed",
    "утверждено": "agreed",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "отменено": "cancelled",
    "отменён": "cancelled",
    "disputed": "disputed",
    "оспаривается": "disputed",
    "спорно": "disputed",
    "разногласие": "disputed",
    "unspecified": "unspecified",
    "unknown": "unspecified",
    "не указано": "unspecified",
    "неизвестно": "unspecified",
}


def summary_views(summary):
    """Read both current results and notes created before dual summaries existed."""
    brief = summary.get("brief", summary)
    if "detailed" in summary:
        return brief, summary["detailed"]
    detailed = dict(
        overview="Существенные пункты исходных блоков. Возможны повторы между блоками.",
        items=summary.get("ledger", summary["items"]),
        topics=summary["topics"],
    )
    return brief, detailed


def package_summary(brief, ledger, maps, resolved, parts=None):
    # Detailed facts bypass lossy reduction entirely, including topics mentioned only once.
    detailed = dict(
        overview="\n\n".join(f"Часть {i + 1}. {m['overview']}" for i, m in enumerate(maps)),
        items=ledger,
        topics=sorted({topic for m in maps for topic in m["topics"]}),
        # Final status after reconciling later revisions/cancellations; the ledger above
        # is never edited or shortened because of this -- it stays the full history.
        resolved=resolved,
        # Ledger index ranges per source block, for the timeline view: [first, last).
        parts=parts or [],
    )
    return dict(**brief, brief=brief, detailed=detailed, ledger=ledger, blocks=len(maps), version=2)


class SummaryTooLong(ValueError):
    """The provider explicitly reports a truncated completion."""


class SummaryFormatError(ValueError):
    """A completion arrived, but cannot be used as evidence-backed JSON."""


def checked_complete(client, prompt, allowed, validator=None):
    for attempt in range(3):
        try:
            result = client.complete(prompt, allowed)
            return validator(result) if validator else result
        except SummaryFormatError as exc:
            if attempt == 2:
                raise
            prompt = (
                prompt
                + "\nОтвет не прошёл проверку: "
                + str(exc)[:400]
                + " Верни только JSON указанной структуры. Каждый items[] обязан содержать "
                "kind, text и непустой evidence; evidence — только целые ID из входных данных. "
                "Не добавляй пояснений вне JSON."
            )
    raise AssertionError("unreachable")


def _normalized_enum(value, aliases, default):
    if value is None:
        return default
    if not isinstance(value, str):
        return value
    key = re.sub(r"[\s_-]+", " ", value.strip().casefold())
    return aliases.get(key, value)


def _normalized_refs(value):
    if type(value) is int:
        return [value]
    if isinstance(value, str) and re.fullmatch(r"\s*\d+(?:\s*[,;]\s*\d+)*\s*", value):
        return [int(part) for part in re.split(r"\s*[,;]\s*", value.strip())]
    if not isinstance(value, list):
        return value
    refs = []
    for ref in value:
        if isinstance(ref, str) and ref.strip().isdigit():
            refs.append(int(ref.strip()))
        elif isinstance(ref, dict) and type(ref.get("id")) is int:
            refs.append(ref["id"])
        else:
            refs.append(ref)
    return refs


def normalize_model_summary(obj):
    """Repair harmless schema variations without inventing facts or evidence."""
    if isinstance(obj, dict) and isinstance(obj.get("summary"), dict):
        obj = obj["summary"]
    if isinstance(obj, list):
        obj = {"overview": "", "items": obj, "topics": []}
    if not isinstance(obj, dict):
        return obj

    result = dict(obj)
    if "overview" not in result:
        result["overview"] = next(
            (
                result[key]
                for key in ("abstract", "introduction", "summary")
                if isinstance(result.get(key), str)
            ),
            "",
        )
    if "items" not in result:
        result["items"] = next(
            (
                result[key]
                for key in ("points", "key_points", "keyPoints")
                if isinstance(result.get(key), list)
            ),
            result.get("items"),
        )
    if "topics" not in result:
        result["topics"] = next(
            (result[key] for key in ("themes", "темы") if isinstance(result.get(key), list)), []
        )

    if isinstance(result.get("items"), list):
        normalized_items = []
        for raw in result["items"]:
            if not isinstance(raw, dict):
                normalized_items.append(raw)
                continue
            item = dict(raw)
            item["kind"] = _normalized_enum(item.get("kind", item.get("type")), KIND_ALIASES, "point")
            if "text" not in item:
                item["text"] = next(
                    (
                        item[key]
                        for key in ("content", "point", "description", "title")
                        if isinstance(item.get(key), str)
                    ),
                    item.get("text"),
                )
            evidence = next(
                (
                    item[key]
                    for key in ("evidence", "evidence_ids", "segment_ids", "sources", "refs")
                    if key in item
                ),
                None,
            )
            item["evidence"] = _normalized_refs(evidence)
            item["status"] = _normalized_enum(item.get("status"), STATUS_ALIASES, "unspecified")
            item.setdefault("owner", item.get("assignee"))
            item.setdefault("due", item.get("deadline"))
            normalized_items.append(item)
        result["items"] = normalized_items
    return result


def parse_model_summary(content, allowed):
    """Parse JSON even when an otherwise valid response is wrapped in prose/fences."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[-1].strip() == "```":
            content = "\n".join(lines[1:-1]).strip()
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        start = content.find("{")
        if start < 0:
            raise SummaryFormatError("Модель вернула некорректный JSON.") from None
        try:
            obj, _ = json.JSONDecoder().raw_decode(content[start:])
        except json.JSONDecodeError:
            raise SummaryFormatError("Модель вернула некорректный JSON.") from None
    if is_review_diff(obj):
        return obj  # only the changes of a review: expanded and validated by review_source()
    try:
        return validate_summary(normalize_model_summary(obj), allowed)
    except (ValueError, TypeError) as exc:
        raise SummaryFormatError(str(exc)) from None


def is_review_diff(obj):
    return isinstance(obj, dict) and "items" not in obj and any(k in obj for k in ("keep", "edit", "add"))


def expand_review(diff, draft):
    """A review that lists only its changes, as the full reviewed summary.

    Kept points are copied from the draft unchanged, so the model does not spend time
    writing them again; edited points take the place of the first point they replace;
    added points come last. Accounting for every draft point is checked afterwards.
    """

    def ids(value):
        return value if isinstance(value, list) else []

    def int_list(value):
        if not isinstance(value, list) or any(type(i) is not int for i in value):
            raise SummaryFormatError("keep и draft_ids должны быть списками индексов черновика.")
        return value

    draft_items = draft["items"]
    keep = set(int_list(ids(diff.get("keep"))))
    edits = [dict(e) for e in ids(diff.get("edit")) if isinstance(e, dict)]
    by_first = {}
    for edit in edits:
        replaced = int_list(edit.get("draft_ids", []))
        if not replaced:
            raise SummaryFormatError("Исправленный пункт должен указывать draft_ids заменяемых пунктов.")
        by_first.setdefault(min(replaced), []).append(edit)
    items = []
    for index, item in enumerate(draft_items):
        if index in keep:
            items.append(dict(item, draft_ids=[index]))
        items.extend(by_first.get(index, []))
    # Out-of-range indices are kept so that the accounting check reports them.
    for first in sorted(i for i in by_first if not 0 <= i < len(draft_items)):
        items.extend(by_first[first])
    for added in ids(diff.get("add")):
        if isinstance(added, dict):
            items.append(dict(added, draft_ids=[]))
    overview = diff.get("overview")
    topics = diff.get("topics")
    full = dict(
        overview=overview if isinstance(overview, str) and overview.strip() else draft["overview"],
        topics=topics if isinstance(topics, list) else draft["topics"],
        items=items,
        removed=diff.get("removed", []),
    )
    return normalize_model_summary(full)


def validate_summary(obj, allowed):
    if not isinstance(obj, dict) or not isinstance(obj.get("overview"), str):
        raise ValueError("Модель вернула неверную структуру сводки.")
    if len(obj["overview"]) > 12000 or not isinstance(obj.get("items"), list) or len(obj["items"]) > 200:
        raise ValueError("Некорректный размер сводки.")
    for item in obj["items"]:
        if not isinstance(item, dict):
            raise ValueError("Каждый items[] должен быть JSON-объектом.")
        if item.get("kind") not in KINDS:
            raise ValueError("Поле kind должно быть одним из: point, decision, action, risk, question.")
        if not isinstance(item.get("text"), str) or not item["text"].strip() or len(item["text"]) > 6000:
            raise ValueError("Поле text каждого пункта должно быть непустой строкой.")
        refs = item.get("evidence")
        if item.get("status", "unspecified") not in STATUSES:
            raise ValueError("Некорректный статус решения.")
        if (
            not isinstance(refs, list)
            or not refs
            or any(type(r) is not int or r not in allowed for r in refs)
        ):
            raise ValueError("Модель сослалась на несуществующий фрагмент. Повторите сводку.")
        for field in ["owner", "due"]:
            if item.get(field) is not None and (not isinstance(item[field], str) or len(item[field]) > 500):
                raise ValueError("Некорректные ответственный или срок.")
    if not isinstance(obj.get("topics"), list) or len(obj["topics"]) > 30:
        raise ValueError("Некорректный список тем.")
    if any(not isinstance(t, str) or not t.strip() or len(t) > 100 for t in obj["topics"]):
        raise ValueError("Некорректная тема.")
    return obj


def system_prompt(settings):
    """Evidence rules first; the user's own wishes ride along without overriding them."""
    extra = settings.summary_instructions.strip()
    return SYSTEM + (USER_INSTRUCTIONS + extra if extra else "")


def model_identity(settings):
    """Part of every checkpoint digest: another model or quantization means a new answer."""
    path = Path(settings.llm_model).expanduser()
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return f"{path.name}:{size}"


THINKING = re.compile(r"<think>.*?</think>", re.S)
# End-of-turn markers as text (llama-server runs with --special). A free-text answer stops at
# the first of them: GigaChat 3 ends a turn with <|message_sep|>, which llama.cpp does not
# treat as end of generation. Grammar answers end by their grammar instead.
END_MARKERS = [
    "<|message_sep|>",
    "<|im_end|>",
    "<|eot_id|>",
    "<|endoftext|>",
    "<end_of_turn>",
    "<turn|>",
    "<eos>",
    "</s>",
]
SPECIAL_TOKENS = re.compile(r"<\|[A-Za-z0-9_]+\|>|</s>|<end_of_turn>|<start_of_turn>|<turn\|>|<eos>")
# Chat template wrappers (roles, special tokens) around system and user text.
TEMPLATE_TOKENS = 64
# Kept free in every request so a slightly longer template never overflows the window.
CONTEXT_MARGIN = 128
# Fallback when the server cannot tokenize: digits and JSON punctuation make Russian
# meeting rows as dense as ~1.6 characters per token in Qwen and Gemma tokenizers.
ESTIMATE_CHARS_PER_TOKEN = 1.5
# A request is given up as stuck after this many times its expected duration, plus a grace.
STUCK_FACTOR = 3
STUCK_GRACE = 30

# llama.cpp's grammars/json.gbnf: the sampler can only produce a JSON object. Whitespace is
# limited to single spaces: indentation and line breaks cost tokens and say nothing. Passed as a
# raw grammar (not response_format) because the server then skips its own chat-format
# parser, which answers HTTP 500 instead of finish_reason=length on a cut-off JSON.
JSON_GRAMMAR = r"""root   ::= object
value  ::= object | array | string | number | ("true" | "false" | "null") ws
object ::= "{" ws ( string ":" ws value ("," ws string ":" ws value)* )? "}" ws
array  ::= "[" ws ( value ("," ws value)* )? "]" ws
string ::= "\"" ( [^"\\\x7F\x00-\x1F] | "\\" (["\\bfnrt] | "u" [0-9a-fA-F]{4}) )* "\"" ws
number ::= ("-"? ([0-9] | [1-9] [0-9]{0,15})) ("." [0-9]+)? ([eE] [-+]? [0-9] [1-9]{0,15})? ws
ws ::= | " "
"""


class LocalClient:
    """OpenAI-compatible chat requests to the llama-server started for this job.

    Requests never leave 127.0.0.1: no proxy from the environment, no redirects.
    JSON answers are constrained by a llama.cpp grammar, which removes most
    broken-JSON retries that small local models otherwise need.
    """

    def __init__(self, settings, url, key="", transport=None):
        from .local_llm import context_tokens

        self.settings = settings
        self.base = url.rstrip("/")
        self.url = self.base + "/v1/chat/completions"
        self.key = key
        self.transport = transport
        self.system = system_prompt(settings)
        # The server is started with exactly this window (local_llm.server_args).
        self.n_ctx = context_tokens(settings)
        self.tokenizer = None  # unknown until the first /tokenize call
        # Called before each generation; a summary job pauses here while the Mac is hot.
        self.pace = lambda: None
        # Receives the server's timings of every answer (tokens read and written, and how
        # long each took): the summary plans its remaining steps by the measured speed.
        self.observe = None
        # Expected seconds of a request, (prompt tokens, answer tokens) → seconds, once the
        # speed is known; a request that takes three times longer is given up as stuck.
        self.expected = None
        self.counted = {}  # system prompts repeat in every request
        self.counts = {}
        self.client = httpx.Client(
            trust_env=False,
            follow_redirects=False,
            # One long generation on a laptop can take many minutes.
            timeout=httpx.Timeout(3600, connect=10),
            transport=transport,
        )

    def close(self):
        self.client.close()

    def headers(self):
        headers = {"Content-Type": "application/json"}
        if self.key:
            headers["Authorization"] = f"Bearer {self.key}"
        return headers

    def count_tokens(self, text):
        """Tokens by the model's own tokenizer; a deliberately high estimate if it is unavailable.

        The last few texts are remembered: a request is measured for its room and for its
        expected duration, and the system prompt repeats in every request.
        """
        key = hash(text)
        if key in self.counts:
            return self.counts[key]
        count = self._count_tokens(text)
        if len(self.counts) > 64:
            self.counts.clear()
        self.counts[key] = count
        return count

    def _count_tokens(self, text):
        if self.tokenizer is not False:
            try:
                response = self.client.post(
                    self.base + "/tokenize", headers=self.headers(), json=dict(content=text)
                )
                tokens = response.json()["tokens"] if response.status_code == 200 else None
                if isinstance(tokens, list):
                    self.tokenizer = True
                    return len(tokens)
            except (httpx.HTTPError, ValueError, KeyError, TypeError):
                pass
            self.tokenizer = False
        return math.ceil(len(text) / ESTIMATE_CHARS_PER_TOKEN)

    def prompt_tokens(self, system, prompt):
        """Tokens a request takes before the answer."""
        if system not in self.counted:
            self.counted[system] = self.count_tokens(system)
        return self.counted[system] + self.count_tokens(prompt) + TEMPLATE_TOKENS

    def room(self, system, prompt, wanted, minimum):
        """Answer tokens that fit next to this prompt in the model's context window.

        A request that leaves less than `minimum` is refused up front with SummaryTooLong,
        so the caller splits its input instead of getting an answer cut off mid-way.
        """
        free = self.n_ctx - self.prompt_tokens(system, prompt) - CONTEXT_MARGIN
        if free < min(wanted, minimum):
            raise SummaryTooLong("Запрос не поместился в контекст модели.")
        return min(wanted, free)

    def timeout(self, prompt_tokens, max_tokens):
        """Seconds to wait for one answer: generous against a stuck server, never a whole hour."""
        if self.expected is None:
            return httpx.Timeout(3600, connect=10)
        return httpx.Timeout(STUCK_GRACE + STUCK_FACTOR * self.expected(prompt_tokens, max_tokens), connect=10)

    def _choice(self, payload, raw=False, prompt_tokens=0):
        self.pace()
        headers = self.headers()
        timeout = self.timeout(prompt_tokens, payload.get("max_tokens") or 0)
        for attempt in range(3):
            try:
                response = self.client.post(self.url, headers=headers, json=payload, timeout=timeout)
            except httpx.TimeoutException:
                # Closing the connection frees the slot: llama-server cancels an abandoned task.
                raise SummaryTooLong("Модель не ответила за отведённое время; блок будет разделён.") from None
            except httpx.HTTPError:
                if attempt == 2:
                    raise RuntimeError("Локальная модель сводок перестала отвечать.") from None
                continue
            if response.status_code == 503 and attempt < 2:
                continue  # still loading or busy
            if response.status_code == 400 and "context" in response.text.lower():
                raise SummaryTooLong("Запрос не поместился в контекст модели.")
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"Локальная модель сводок: HTTP {response.status_code}.")
            if len(response.content) > 4_000_000:
                raise ValueError("Ответ модели слишком большой.")
            try:
                data = response.json()
                choice = data["choices"][0]
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError):
                raise RuntimeError("Локальная модель вернула ответ в неожиданном формате.") from None
            if not isinstance(content, str):
                raise SummaryFormatError("Модель вернула нетекстовый ответ.")
            if self.observe is not None and isinstance(data.get("timings"), dict):
                self.observe(data["timings"])
            content = SPECIAL_TOKENS.sub("", THINKING.sub("", content))
            return choice.get("finish_reason"), content if raw else content.strip()
        raise RuntimeError("Локальная модель сводок недоступна.")

    def _payload(self, system, prompt, max_tokens, json_mode, minimum=1024, grammar=None):
        """(request body, prompt tokens)."""
        room = self.room(system, prompt, max_tokens, minimum)
        used = self.prompt_tokens(system, prompt)
        payload = dict(
            temperature=0.2,
            top_p=0.9,
            max_tokens=room,
            messages=[dict(role="system", content=system), dict(role="user", content=prompt)],
            chat_template_kwargs=dict(enable_thinking=False),
        )
        if json_mode or grammar:
            payload["grammar"] = grammar or JSON_GRAMMAR
        else:
            payload["stop"] = END_MARKERS
        return payload, used

    def complete(self, prompt, allowed):
        finish, content = self._choice(
            *self._payload(self.system, prompt, self.settings.max_output_tokens, json_mode=True)
        )
        if finish == "length":
            raise SummaryTooLong("Модель не уложилась в лимит ответа; блок будет разделён.")
        return parse_model_summary(content, allowed)

    def complete_json(self, system, prompt, max_tokens):
        """Any JSON object under the grammar, for callers with their own schema (questions)."""
        from .qa import parse_json

        finish, content = self._choice(*self._payload(system, prompt, max_tokens, json_mode=True))
        if finish == "length":
            raise SummaryTooLong("Модель не уложилась в лимит ответа.")
        return parse_json(content)

    def complete_text(self, system, prompt, max_tokens, minimum=1024, continuation=False, grammar=None):
        """Free Markdown text, or lines in the shape of a GBNF `grammar`. Returns (text, truncated).

        A continuation keeps its leading space or line break: it is glued to a cut-off text.
        """
        payload, used = self._payload(system, prompt, max_tokens, False, minimum, grammar)
        finish, content = self._choice(payload, raw=True, prompt_tokens=used)
        content = content.rstrip() if continuation else content.strip()
        if content.startswith("```"):
            lines = content.splitlines()
            if lines[-1].strip() == "```":
                content = "\n".join(lines[1:-1]).strip()
        return content, finish == "length"


def _size_groups(items, max_chars):
    groups, group, size = [], [], 2
    for item in items:
        length = len(json.dumps(item, ensure_ascii=False)) + 2
        if length + 2 > max_chars:
            raise ValueError("Один пункт реестра решений превышает размер блока. Увеличьте входной лимит.")
        if group and size + length > max_chars:
            groups.append(group)
            group, size = [], 2
        group.append(item)
        size += length
    if group:
        groups.append(group)
    return groups


def reconcile_decisions(client, ledger, input_chars, progress=lambda *_: None, on_warning=lambda *_: None):
    """Resolve superseded decisions/tasks in the full ledger into a final-status list.

    Never edits or shortens the ledger itself -- this is an additional, bounded
    pass over just the "decision"/"action" items, same size-bounded reduction
    style as the brief summary, capped so it cannot loop forever on a model
    that won't converge.
    """
    current = [item for item in ledger if item["kind"] in {"decision", "action"}]
    if not current:
        return []
    def reconcile(group):
        """One group; halved while the request does not fit the model's context."""
        allowed = {ref for item in group for ref in item["evidence"]}
        try:
            result = checked_complete(client, RECONCILE_PROMPT + json.dumps(group, ensure_ascii=False), allowed)
        except SummaryTooLong:
            if len(group) < 2:
                raise
            half = len(group) // 2
            return reconcile(group[:half]) + reconcile(group[half:])
        if {ref for item in result["items"] for ref in item["evidence"]} != allowed:
            raise ValueError("Согласование потеряло ссылки на часть исходных решений.")
        return [result["items"]]

    for level in range(4):
        groups = _size_groups(current, input_chars)
        reduced, pieces = [], 0
        for i, group in enumerate(groups):
            for items in reconcile(group):
                reduced.extend(items)
                pieces += 1
            progress(f"Согласование решений: уровень {level + 1}, блок {i + 1}/{len(groups)}")
        if pieces == 1:
            return reduced
        if len(json.dumps(reduced)) >= len(json.dumps(current)):
            on_warning(
                "Решения согласованы внутри блоков. Между блоками возможны повторы и пересмотры; проверьте полный реестр."
            )
            return reduced
        current = reduced
    on_warning("Достигнут предел согласования. Показаны промежуточные результаты; проверьте полный реестр.")
    return current


def blocks(segments, max_chars):
    block, size = [], 0
    for row in segments:
        position = 0
        while position < max(1, len(row["text"])):
            width = min(max_chars // 2, max(1, len(row["text"]) - position))
            while True:
                line = json.dumps(
                    dict(
                        id=row["id"],
                        start=round(row["start"], 2),
                        uncertain=bool(row["uncertain"]),
                        text=row["text"][position : position + width],
                    ),
                    ensure_ascii=False,
                )
                if len(line) + 1 <= max_chars:
                    break
                if width == 1:
                    raise ValueError("Реплика слишком велика для размера блока.")
                width = max(1, width // 2)
            if block and size + len(line) + 1 > max_chars:
                yield block
                block, size = [], 0
            block.append((row["id"], line))
            size += len(line) + 1
            position += width
    if block:
        yield block


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _context(block, max_chars, tail=False):
    """A bounded excerpt of actual neighbouring rows, never generated summary context."""
    rows = []
    for _, line in reversed(block) if tail else block:
        row = json.loads(line)
        candidate = [row, *rows] if tail else [*rows, row]
        if len(_json(candidate)) > max_chars:
            if rows:
                break
            # A long neighbouring segment must not exhaust the request budget.
            row["excerpt"] = True
            width = max_chars - len(_json([dict(row, text="")]))
            if width <= 0:
                break
            row["text"] = row["text"][-width:] if tail else row["text"][:width]
            # JSON escaping can be larger than the number of source characters.
            while row["text"] and len(_json([row])) > max_chars:
                row["text"] = row["text"][1:] if tail else row["text"][:-1]
            rows = [row] if row["text"] else []
            break
        rows = candidate
    return rows


def contextual_blocks(segments, max_chars):
    """Stream primary blocks with bounded raw context on both sides."""
    iterator = iter(blocks(segments, max_chars // 2))
    previous, current = [], next(iterator, None)
    context_limit = min(1200, max_chars // 10)
    while current is not None:
        following = next(iterator, None)
        yield current, _context(previous, context_limit, tail=True), _context(following or [], context_limit)
        previous, current = current, following


def source_fallback(block, reason):
    """Keep source text when a provider never returns usable JSON for a tiny map block.

    This is deliberately extractive: it never invents a summary or evidence. The visible
    warning makes clear that the affected part still needs review.
    """
    items = []
    topics = []
    for sid, line in block:
        text = json.loads(line)["text"].strip()
        for start in range(0, len(text), 2000):
            piece = text[start : start + 2000].strip()
            if piece:
                items.append(
                    dict(
                        kind="point",
                        text=piece,
                        evidence=[sid],
                        owner=None,
                        due=None,
                        status="unspecified",
                    )
                )
    if not items:
        raise ValueError("В блоке нет текста, который можно сохранить в резервную сводку.")
    warning = (
        "Модель сводок не вернула корректный JSON для этой части даже после уменьшения "
        "блока. Исходные реплики сохранены без перефразирования; проверьте этот фрагмент. "
        + str(reason)[:300]
    )
    result = dict(
        overview="Часть разговора сохранена из исходной расшифровки без модельного обобщения.",
        items=items,
        topics=topics,
        quality_warning=warning,
    )
    validate_summary(result, {sid for sid, _ in block})
    return result


def _source_result(result, allowed, primary):
    try:
        validate_summary(result, allowed)
        if any(not primary.intersection(item["evidence"]) for item in result["items"]):
            raise ValueError("Пункт ссылается только на соседние реплики, а не на основной блок.")
    except (ValueError, TypeError) as exc:
        raise SummaryFormatError(str(exc)) from None
    return result


def review_source(client, source, draft, max_chars):
    """Review against raw text; split oversized drafts without enlarging requests."""
    allowed = {row["id"] for rows in source.values() for row in rows}
    primary = {row["id"] for row in source["segments"]}
    payload = dict(source=source, draft=draft, scope="full")
    if len(_json(payload)) <= max_chars:
        batches = [payload]
    else:
        # Do not truncate either the evidence or the draft to make a review fit.
        base = dict(source=source, draft=dict(draft, items=[]), scope="items")
        room = max_chars - len(_json(base))
        groups = _size_groups(draft["items"], room)
        if not groups:
            raise SummaryFormatError("Обзор блока не помещается в запрос проверки.")
        batches = [dict(base, draft=dict(draft, items=group)) for group in groups]
    checked = []
    for payload in batches:
        data = _json(payload)
        if len(data) > max_chars:
            raise SummaryFormatError("Материал проверки превышает размер входного блока.")

        def validate_review(result, draft=payload["draft"]):
            if is_review_diff(result):
                result = expand_review(result, draft)
            _source_result(result, allowed, primary)
            expected = set(range(len(payload["draft"]["items"])))
            accounted = []
            for item in result["items"]:
                ids = item.get("draft_ids")
                if not isinstance(ids, list) or any(type(i) is not int or i not in expected for i in ids):
                    raise SummaryFormatError("Проверка должна вернуть draft_ids для каждого пункта.")
                accounted.extend(ids)
            removed = result.get("removed", [])
            if not isinstance(removed, list) or len(removed) > len(expected):
                raise SummaryFormatError("Некорректный список удалённых пунктов проверки.")
            for entry in removed:
                if (
                    not isinstance(entry, dict)
                    or type(entry.get("draft_id")) is not int
                    or entry["draft_id"] not in expected
                    or not isinstance(entry.get("reason"), str)
                    or not entry["reason"].strip()
                    or len(entry["reason"]) > 2000
                ):
                    raise SummaryFormatError("Удаление пункта требует draft_id и явной причины.")
                refs = entry.get("evidence")
                if (
                    not isinstance(refs, list)
                    or not refs
                    or any(type(ref) is not int or ref not in allowed for ref in refs)
                    or not primary.intersection(refs)
                ):
                    raise SummaryFormatError("Причина удаления требует источников основного блока.")
                accounted.append(entry["draft_id"])
            if set(accounted) != expected or len(accounted) != len(expected):
                raise SummaryFormatError("Проверка потеряла или повторно учла пункт черновика.")
            return result

        response = checked_complete(client, REVIEW_PROMPT + data, allowed, validate_review)
        # Audit receipts stay in the checkpoint; they are not facts for subsequent synthesis.
        checked.append(response)
    receipts = [dict(draft=payload["draft"], result=response) for payload, response in zip(batches, checked)]
    checked = [
        dict(
            overview=r["overview"],
            topics=r["topics"],
            items=[
                {k: v for k, v in item.items() if k in {"kind", "text", "evidence", "owner", "due", "status"}}
                for item in r["items"]
            ],
        )
        for r in checked
    ]
    if len(checked) == 1:
        return dict(checked[0], review_receipts=receipts)
    result = dict(
        overview="\n\n".join(dict.fromkeys(r["overview"] for r in checked)),
        items=[item for r in checked for item in r["items"]],
        topics=sorted({topic for r in checked for topic in r["topics"]})[:30],
    )
    return dict(_source_result(result, allowed, primary), review_receipts=receipts)


def summarize(store, mid, settings, progress=lambda *_: None, client=None, work=None, decider=None):
    """Whole pipeline. Without a client, starts the local model for the duration of the job.

    First a small decision model may set aside empty fragments (decide.py); it runs and
    stops before the summary model loads, so the two never share the memory.
    """
    from .decide import select_fragments

    dropped = select_fragments(store, mid, settings, progress, decider, work, local=client is None)
    if client is not None:
        return _summarize(store, mid, settings, progress, client, dropped)
    with local_model(settings, work, progress) as client:
        return _summarize(store, mid, settings, progress, client, dropped)


class local_model:
    """Context manager: llama-server for this job plus a client bound to it."""

    def __init__(self, settings, work, progress):
        import tempfile

        from .local_llm import check_fits

        settings.validate(llm=True)
        check_fits(settings)
        self.temp = None if work else tempfile.TemporaryDirectory(prefix="samarizator-llm-")
        self.work = work or self.temp.name
        self.server = None
        self.settings = settings
        self.progress = progress
        self.client = None

    def __enter__(self):
        from .local_llm import start_summary_server
        from .thermal import cool_down

        self.server = start_summary_server(self.settings, self.work, self.progress)
        self.client = LocalClient(self.settings, self.server.url, self.server.key)
        self.client.parallel = self.server.slots
        self.client.pace = lambda: cool_down(self.progress, self.settings.cool_down)
        return self.client

    def __exit__(self, *exc):
        if self.client:
            self.client.close()
        if self.server:
            self.server.__exit__(*exc)
        if self.temp:
            self.temp.cleanup()
        return False


def _summarize(store, mid, settings, progress, client, dropped=(set(), 0.0)):
    started = time.monotonic()
    if settings.summary_algorithm == "notes":
        from .summary_notes import summarize_notes

        result = summarize_notes(store, mid, settings, progress, client, dropped)
    else:
        result = _summarize_classic(store, mid, settings, progress, client, dropped)
    result["algorithm"] = settings.summary_algorithm
    timings = result.setdefault("timings", {})
    timings["total"] = round(time.monotonic() - started, 1)
    line = "Сводка готова за " + duration_text(timings["total"]) + timing_details(timings)
    budget = result.get("budget") or {}
    if budget.get("limit") and timings["total"] > budget["limit"] and budget.get("predict_speed"):
        line += f". Дольше лимита в {budget['limit'] // 60} мин: модель пишет {budget['predict_speed']:.0f} ток/с"
    progress(line)
    return result


TIMING_NAMES = dict(blocks="разбор", assembly="сборка", final="итоговый текст")


def duration_text(seconds):
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60} мин {seconds % 60} с" if seconds >= 60 else f"{seconds} с"


def timing_details(timings):
    parts = [f"{name} {duration_text(timings[key])}" for key, name in TIMING_NAMES.items() if key in timings]
    return " (" + ", ".join(parts) + ")" if parts else ""


def prepared_rows(store, mid, settings, dropped, progress, preparation):
    """Rows the model reads: tidied, without the fragments the decision model set aside."""
    left_out, left_seconds = dropped
    stats = {}
    rows = [
        row
        for row in tidy_rows(store.iter_segments(mid), settings.clean_input, stats)
        if row["id"] not in left_out
    ]
    if left_out:
        fragments = len(left_out)
        preparation.update(pruned=fragments, pruned_seconds=round(left_seconds, 1))
        progress(f"Подготовка: {fragments} пустых реплик не пойдут в сводку")
    if stats.get("before") and stats["after"] < stats["before"]:
        saved = round(100 * (1 - stats["after"] / stats["before"]))
        preparation["cleaned_percent"] = saved
        if saved:
            progress(f"Подготовка: убрано {saved}% текста — паузы, повторы и слова-паразиты")
    return rows


def run_blocks(planned, work, client, progress, suffix=lambda: ""):
    """work(index, *block) for every planned block; several at once when the server has slots.

    Results keep the order of the recording whatever order the blocks finish in. `suffix()`
    is added to the progress line after each block — the time still expected, for instance.
    """
    total = len(planned)
    workers = max(1, min(getattr(client, "parallel", 1), total))
    if workers == 1:
        produced = []
        for index, block in enumerate(planned):
            progress(f"Разбор записи: блок {index + 1} из {total}{suffix()}")
            produced.append(work(index, *block))
        return produced
    progress(f"Разбор записи: блок 1 из {total} · {workers} одновременно")
    done = []
    lock = threading.Lock()

    def run(index, *block):
        result = work(index, *block)
        with lock:
            done.append(index)
            progress(f"Разбор записи: блок {len(done)} из {total} · {workers} одновременно{suffix()}")
        return result

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run, i, *block) for i, block in enumerate(planned)]
        return [future.result() for future in futures]


def ledger_and_parts(produced):
    """Block results → (maps, deduplicated ledger in recording order, per-block parts)."""
    maps, ledger, seen, parts = [], [], set(), []
    for results in produced:
        maps.extend(results)
        first = len(ledger)
        for result in results:
            for item in result["items"]:
                signature = (
                    item["kind"],
                    item["text"].casefold(),
                    tuple(sorted(item["evidence"])),
                    item.get("owner"),
                    item.get("due"),
                    item.get("status"),
                )
                if signature not in seen:
                    ledger.append(item)
                    seen.add(signature)
        titles = [r["title"].strip() for r in results if isinstance(r.get("title"), str) and r["title"].strip()]
        parts.append(
            dict(
                title=titles[0][:120] if titles else "",
                overview=" ".join(r["overview"].strip() for r in results if r["overview"].strip()),
                first=first,
                last=len(ledger),
            )
        )
    if not maps:
        raise ValueError("Нет распознанной речи для сводки.")
    return maps, ledger, parts


def resolve_classic(store, mid, settings, client, ledger, progress):
    """Final status of decisions and tasks by the bounded multi-level reconciliation."""
    resolve_digest = hashlib.sha256(
        (
            model_identity(settings)
            + str(settings.max_output_tokens)
            + system_prompt(settings)
            + RECONCILE_PROMPT
            + "safe-resolution-v2"
            + str(settings.input_chars)
            + json.dumps(ledger, ensure_ascii=False)
        ).encode()
    ).hexdigest()
    cached = store.checkpoint(mid, "summary-resolve", 0)
    if cached and cached.get("digest") == resolve_digest:
        return cached["result"], cached.get("warnings", [])
    warnings = []
    try:
        resolved = reconcile_decisions(client, ledger, settings.input_chars, progress, warnings.append)
    except (ValueError, RuntimeError, httpx.HTTPError):
        # Optional enrichment must never discard the two primary summaries.
        warnings.append(
            "Дополнительное согласование не удалось. Краткая сводка и полный реестр сохранены; итоговый статус решений проверьте по записи."
        )
        return [], warnings
    store.save_checkpoint(
        mid, "summary-resolve", 0, dict(digest=resolve_digest, result=resolved, warnings=warnings)
    )
    return resolved, warnings


class ReduceFallback(Exception):
    """The multi-level reduction could not produce a valid brief; the reason is shown."""


def reduce_classic(client, ledger, settings, progress, levels):
    """The brief by multi-level reduction of the ledger; None for an empty ledger.

    Intermediate registers are appended to `levels`: the final text may use them.
    """

    def reduce_group(group, final):
        """Compress one group; halved while the request does not fit the model's context."""
        instruction = BRIEF_PROMPT if final else REDUCE_PROMPT
        prompt = (
            instruction + "Не смешивай противоположные мнения и разных ответственных. "
            "Пункты упорядочены по времени: явно отрази отмену или пересмотр решений.\n"
            + json.dumps(group, ensure_ascii=False)
        )
        allowed = {ref for item in group for ref in item["evidence"]}
        try:
            return [checked_complete(client, prompt, allowed)]
        except SummaryTooLong:
            if len(group) < 2:
                raise
            progress("Делю блок объединения на меньшие части, чтобы он поместился в модель…")
            half = len(group) // 2
            return reduce_group(group[:half], False) + reduce_group(group[half:], False)

    # A reduction unit is an item, not a whole map: every request remains bounded.
    current = ledger
    for level in range(8):
        units, group, size = [], [], 2
        for item in current:
            length = len(json.dumps(item, ensure_ascii=False)) + 2
            if length + 2 > settings.input_chars:
                raise ReduceFallback("Один пункт подробной сводки не поместился в блок финального сжатия.")
            if group and size + length > settings.input_chars:
                units.append(group)
                group, size = [], 2
            group.append(item)
            size += length
        if group:
            units.append(group)
        if not units:
            return None
        reduced, results = [], []
        try:
            for i, group in enumerate(units):
                for result in reduce_group(group, final=len(units) == 1):
                    results.append(result)
                    reduced.extend(result["items"])
                progress(f"Объединение: уровень {level + 1}, блок {i + 1}/{len(units)}")
        except (SummaryFormatError, SummaryTooLong) as exc:
            raise ReduceFallback(str(exc)) from None
        last = results[-1]
        if len(results) == 1:
            if len(last["items"]) > 9:
                raise ReduceFallback("Модель вернула более 9 кратких тезисов.")
            return last
        if len(json.dumps(reduced)) >= len(json.dumps(current)):
            raise ReduceFallback("Модель не сократила промежуточный результат.")
        current = reduced
        levels.append(reduced)
    raise ReduceFallback("Достигнут предел объединения сводки.")


def fallback_brief(ledger, maps, reason):
    """A selection of ledger points shown instead of a brief the model failed to write."""
    priorities = {"decision": 0, "action": 1, "risk": 2, "question": 3, "point": 4}
    selected = sorted(enumerate(ledger), key=lambda pair: (priorities.get(pair[1]["kind"], 9), pair[0]))[:9]
    selected = [item for _, item in sorted(selected)]
    overview = " ".join(m["overview"].strip() for m in maps if m["overview"].strip())[:1000]
    return dict(
        overview=overview or "Существенные пункты встречи сохранены ниже.",
        items=selected,
        topics=sorted({topic for m in maps for topic in m["topics"]})[:30],
        generation_warning=(
            "Финальное сжатие ответа модели не прошло проверку. "
            "Показана выборка пунктов подробной сводки; это запасной вариант, не финальный синтез. "
            + reason
        ),
    )


def finish_summary(store, mid, settings, client, progress, brief, built, timings=None, final=None):
    """The stored result: both summaries, warnings, and the final text written from them.

    `built` holds ledger, maps, resolved, parts, preparation, resolve_warnings and levels;
    `final` — (answer tokens, continuations) when the remaining time bounds the final text.
    """
    result = package_summary(brief, built["ledger"], built["maps"], built["resolved"], built["parts"])
    result["preparation"] = built["preparation"]
    result["detailed"]["resolution_warning"] = " ".join(built["resolve_warnings"])
    maps = built["maps"]
    warning = " ".join(dict.fromkeys(m["quality_warning"] for m in maps if m.get("quality_warning")))
    if warning:
        result["brief"]["quality_warning"] = warning
        result["detailed"]["quality_warning"] = warning
    # Intermediate registers: the final text takes the most detailed one that fits.
    result["reduce_levels"] = built["levels"]
    times = {row["id"]: row["start"] for row in store.iter_segments(mid)}
    started = time.monotonic()
    limit, continuations = final or (None, None)
    result["final"] = final_document(client, settings, result, times, progress, limit, continuations)
    result["timings"] = dict(timings or {}, final=round(time.monotonic() - started, 1))
    return result


def _summarize_classic(store, mid, settings, progress, client, dropped=(set(), 0.0)):
    preparation = dict(pruned=0, pruned_seconds=0.0, cleaned_percent=0)

    def map_block(index, block, before, after, node=1, depth=0):
        source = dict(before=before, segments=[json.loads(line) for _, line in block], after=after)
        prompt = MAP_PROMPT + _json(dict(source=source))
        digest = hashlib.sha256(
            (
                model_identity(settings)
                + str(settings.max_output_tokens)
                + str(settings.input_chars)
                + system_prompt(settings)
                + REVIEW_PROMPT
                + prompt
            ).encode()
        ).hexdigest()
        phase, part = ("summary-map", index) if node == 1 else ("summary-map-split", index * 100 + node)
        cached = store.checkpoint(mid, phase, part)
        cached = cached if cached and cached.get("digest") == digest else {}
        allowed = {row["id"] for rows in source.values() for row in rows}
        primary = {sid for sid, _ in block}

        def validate(result):
            return _source_result(result, allowed, primary)

        if cached.get("parts") is not None:
            return [validate(r) for r in cached["parts"]]

        def split(reason):
            if depth >= 3:
                fallback = source_fallback(block, reason)
                store.save_checkpoint(mid, phase, part, dict(digest=digest, parts=[fallback]))
                progress(f"Блок {index + 1}: сохранён исходный текст вместо повреждённого ответа модели")
                return [fallback]
            if len(block) > 1:
                halves = [block[: len(block) // 2], block[len(block) // 2 :]]
            else:
                row = json.loads(block[0][1])
                text = row["text"]
                if len(text) < 400:
                    fallback = source_fallback(block, reason)
                    store.save_checkpoint(mid, phase, part, dict(digest=digest, parts=[fallback]))
                    progress(
                        f"Блок {index + 1}: сохранён исходный текст вместо повреждённого ответа модели"
                    )
                    return [fallback]
                halves = [
                    [(row["id"], _json(dict(row, text=piece)))]
                    for piece in [text[: len(text) // 2], text[len(text) // 2 :]]
                ]
            store.save_checkpoint(mid, phase, part, dict(digest=digest, split=True))
            progress(f"Делю блок {index + 1} на меньшие части для полного ответа…")
            limit = min(1200, settings.input_chars // 10)
            return map_block(
                index, halves[0], before, _context(halves[1], limit), node * 2, depth + 1
            ) + map_block(
                index, halves[1], _context(halves[0], limit, tail=True), after, node * 2 + 1, depth + 1
            )

        if cached.get("split"):
            return split("Продолжаю обработку частей блока.")
        try:
            draft = (
                validate(cached["draft"])
                if "draft" in cached
                else checked_complete(client, prompt, allowed, validate)
            )
        except (SummaryTooLong, SummaryFormatError) as exc:
            return split(exc)
        # Keep extraction even if the more expensive review is interrupted or fails.
        store.save_checkpoint(mid, phase, part, dict(digest=digest, draft=draft))
        progress(f"Проверка блока {index + 1} из {total} по расшифровке…")
        try:
            reviewed = review_source(client, source, draft, settings.input_chars)
        except (ValueError, RuntimeError, httpx.HTTPError):
            return [
                dict(
                    draft,
                    quality_warning=(
                        "Повторная проверка части сводки не завершена. Сохранён первоначальный вариант; "
                        "проверьте его по записи или повторите создание сводки."
                    ),
                )
            ]
        receipts = reviewed.pop("review_receipts", [])
        # The review returns the standard schema; the block's short title comes from the draft.
        if not reviewed.get("title") and isinstance(draft.get("title"), str):
            reviewed["title"] = draft["title"]
        store.save_checkpoint(
            mid, phase, part, dict(digest=digest, parts=[reviewed], draft=draft, review_receipts=receipts)
        )
        return [reviewed]

    # Planned up front so progress can say "block 8 of 22" instead of a bare counter.
    # The model reads a tidied copy: hesitations and stutters cost tokens and mean nothing.
    rows = prepared_rows(store, mid, settings, dropped, progress, preparation)
    planned = list(contextual_blocks(rows, settings.input_chars))
    total = len(planned)
    started = time.monotonic()
    produced = run_blocks(planned, map_block, client, progress)
    timings = dict(blocks=round(time.monotonic() - started, 1))
    maps, ledger, parts = ledger_and_parts(produced)
    started = time.monotonic()
    # Keep source-grounded map items separately so reduction cannot erase a topic.
    resolved, resolve_warnings = resolve_classic(store, mid, settings, client, ledger, progress)
    built = dict(
        ledger=ledger,
        maps=maps,
        resolved=resolved,
        parts=parts,
        preparation=preparation,
        resolve_warnings=resolve_warnings,
        levels=[],
    )
    try:
        brief = reduce_classic(client, ledger, settings, progress, built["levels"])
    except ReduceFallback as exc:
        brief = fallback_brief(ledger, maps, str(exc))
    if brief is None:
        brief = dict(overview=maps[0]["overview"], items=[], topics=[])
    timings["assembly"] = round(time.monotonic() - started, 1)
    return finish_summary(store, mid, settings, client, progress, brief, built, timings)


KIND_NAMES = dict(point="Тезис", decision="Решение", action="Задача", risk="Риск", question="Вопрос")
STATUS_NAMES = dict(
    proposed="предложено", agreed="согласовано", cancelled="отменено", disputed="оспаривается"
)


def _clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def material_line(item, times):
    starts = [times[ref] for ref in item.get("evidence", []) if ref in times]
    parts = [KIND_NAMES.get(item.get("kind"), "Тезис")]
    if status := STATUS_NAMES.get(item.get("status")):
        parts.append(status)
    if item.get("owner"):
        parts.append("ответственный: " + str(item["owner"]))
    if item.get("due"):
        parts.append("срок: " + str(item["due"]))
    stamp = f"[{_clock(min(starts))}] " if starts else ""
    return f"- {stamp}{' · '.join(parts)} — {' '.join(str(item['text']).split())}"


def render_material(overview, items, resolved, times, parts=None):
    lines = ["Обзор записи: " + " ".join(str(overview).split())]
    if parts:
        lines += ["", "Обзоры частей записи по порядку:", *(" ".join(p.split()) for p in parts if p.strip())]
    lines += ["", "Пункты в порядке записи:", *(material_line(item, times) for item in items)]
    if resolved:
        lines += [
            "",
            "Итоговый статус решений и задач с учётом пересмотров (приоритетнее ранних пунктов):",
            *(material_line(item, times) for item in resolved),
        ]
    return "\n".join(lines)


def final_candidates(summary, times, budget):
    """Material for the final text, most detailed first: full ledger → reduced registers → brief.

    Only options within `budget` characters are offered; the brief always comes last, so
    there is something to write from even when every register is too long.
    """
    brief, detailed = summary_views(summary)
    resolved = detailed.get("resolved") or []
    overview = brief.get("overview", "")
    parts = [p for p in str(detailed.get("overview", "")).split("\n\n") if p.strip()]
    candidates = [
        ("full", dict(items=detailed["items"], parts=parts)),
        ("full", dict(items=detailed["items"])),
        *((f"level-{n + 1}", dict(items=level)) for n, level in enumerate(summary.get("reduce_levels") or [])),
    ]
    for name, option in candidates:
        material = render_material(overview, option["items"], resolved, times, option.get("parts"))
        if len(material) <= budget:
            yield name, material
    yield "brief", render_material(overview, brief["items"], resolved, times)


def final_material(summary, times, budget):
    """The most detailed material that fits `budget` characters."""
    return next(final_candidates(summary, times, budget))


FINAL_SOURCES = {
    "full": "",
    "brief": "Запись слишком длинная для одного запроса: итоговый текст составлен по краткой сводке. "
    "Подробности — во вкладке «Подробная сводка».",
}


# Answer room the final text needs at least; a smaller register is used otherwise.
FINAL_MIN_TOKENS = 2048
# How many times a final text cut off by the answer limit is continued.
FINAL_CONTINUATIONS = 4
# The end of the written text the model sees when it continues.
CONTINUE_TAIL_CHARS = 1500
CONTINUE_PROMPT = """

Ты уже начал писать этот документ, и ответ оборвался на лимите длины. Последние строки
написанного (между <<< и >>>):
<<<
{tail}
>>>
Продолжи документ ровно с места обрыва: если оборвалось слово или строка таблицы, допиши
их. Не повторяй написанное, не начинай документ заново, сохрани формат и разметку. Верни
только продолжение."""


def join_continuation(text, more):
    """Glue a continuation to the cut-off text, dropping a repeated last line."""
    more = more.lstrip("\n") if text.endswith("\n") else more
    last = text.rstrip().rsplit("\n", 1)[-1].strip()
    if last and more.lstrip().startswith(last) and len(last) > 20:
        more = more.lstrip()[len(last) :]
    return text + more


def final_document(client, settings, summary, times, progress=lambda *_: None, limit=None, continuations=None):
    """Markdown in the user's format. Optional: a failure here never discards the summaries.

    Every request is measured against the model's context window: the material shrinks
    until the answer has room, and a text cut off by the answer limit is continued from
    where it stopped, so a long protocol is not silently truncated. `limit` is a smaller
    answer room when the summary has little time left; `continuations` likewise.
    """
    from .local_llm import final_input_chars, final_output_tokens

    key = settings.final_format if settings.final_format in FINAL_FORMATS else DEFAULT_FORMAT
    custom = settings.final_prompt.strip()
    template = custom or FINAL_FORMATS[key][1]
    title = FINAL_FORMATS[key][0] if template == FINAL_FORMATS[key][1] else "Свой формат"
    system = FINAL_SYSTEM
    if extra := settings.summary_instructions.strip():
        system += USER_INSTRUCTIONS + extra
    progress("Итоговый текст по выбранному формату…")
    wanted = min(final_output_tokens(settings), limit) if limit else final_output_tokens(settings)
    continuations = FINAL_CONTINUATIONS if continuations is None else continuations
    # Clients that cannot measure (test doubles) accept the most detailed material.
    room = getattr(client, "room", lambda *_: wanted)
    source = prompt = None
    rounds = 0
    try:
        for source, material in final_candidates(summary, times, final_input_chars(settings)):
            candidate = FINAL_PROMPT.format(template=template, material=material)
            try:
                room(system, candidate, wanted, FINAL_MIN_TOKENS)
            except SummaryTooLong:
                continue
            prompt = candidate
            break
        if prompt is None:
            raise SummaryTooLong(
                "Материал не поместился в контекст модели даже в кратком виде. Уменьшите размер блока "
                "в настройках или выберите модель с большей памятью."
            )
        text, truncated = client.complete_text(system, prompt, wanted, FINAL_MIN_TOKENS)
        while truncated and rounds < continuations:
            rounds += 1
            progress(f"Итоговый текст длинный — дописываю продолжение ({rounds})…")
            follow = prompt + CONTINUE_PROMPT.format(tail=text[-CONTINUE_TAIL_CHARS:])
            try:
                more, truncated = client.complete_text(system, follow, wanted, 512, continuation=True)
            except SummaryTooLong:
                break
            if not more.strip():
                break
            text = join_continuation(text, more)
    except (ValueError, RuntimeError, httpx.HTTPError) as exc:
        return dict(
            title=title,
            format=key,
            text="",
            warning="Итоговый текст не создан: " + str(exc)[:300] + " Краткая и подробная сводки сохранены.",
        )
    warnings = []
    if source.startswith("level-"):
        warnings.append(
            "Запись длинная: итоговый текст составлен по сжатому реестру пунктов. "
            "Полный список — во вкладке «Подробная сводка»."
        )
    elif FINAL_SOURCES.get(source):
        warnings.append(FINAL_SOURCES[source])
    if truncated:
        warnings.append(
            "Текст получился очень длинным и после нескольких продолжений всё ещё обрывается: конец может "
            "отсутствовать. Выберите более краткий формат или сделайте сводку заново."
        )
    if not text.strip():
        warnings.append("Модель вернула пустой текст. Попробуйте другой формат или модель.")
    return dict(
        title=title, format=key, text=text, source=source, continued=rounds, warning=" ".join(warnings)
    )


def regenerate_final(store, mid, settings, progress=lambda *_: None, client=None, work=None):
    """Only the final text, from a stored summary: fast way to try another format."""
    meeting = store.meeting(mid)
    if not meeting["summary"]:
        raise ValueError("Сначала создайте сводку.")
    summary = json.loads(meeting["summary"])
    times = {row["id"]: row["start"] for row in store.iter_segments(mid)}
    if client is not None:
        summary["final"] = final_document(client, settings, summary, times, progress)
        return summary
    with local_model(settings, work, progress) as client:
        summary["final"] = final_document(client, settings, summary, times, progress)
    return summary
