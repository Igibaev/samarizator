"""The notes algorithm: read a lot, write once.

Writing is what costs time on a laptop: every generated token reads the active weights of
the model from memory, while reading a prompt is many times faster. The classic algorithm
writes the same content three or four times (JSON registers, their review, level-by-level
reduction, reconciliation). Here the model writes it once:

1. Notes per block, in parallel. The model reads numbered lines «[12] текст» instead of
   JSON rows and writes one short line per point: «Решение [12, 14] текст | статус: …».
   A GBNF grammar keeps the answer in this shape, so the parser never guesses.
2. Check per block. The transcript is the beginning of both requests and the task comes
   after it, so the server reuses what it has already read; the check returns only its
   corrections, usually a single «ВСЁ ВЕРНО».
3. Assembly in one request. The notes of a two-hour meeting fit into the model at once:
   the brief and the revisions of decisions come from one answer. The detailed summary is
   the notes themselves, collected by the program. When the register does not fit, the
   classic multi-level reduction takes over.
4. The final text, as before, from the most detailed register that fits.

Evidence rules stay the same: every point refers to real transcript lines of its block.
"""

import hashlib
import math
import re
import threading
import time

import httpx

from .summary import (
    KIND_ALIASES,
    STATUS_ALIASES,
    ReduceFallback,
    SummaryFormatError,
    SummaryTooLong,
    _source_result,
    fallback_brief,
    finish_summary,
    ledger_and_parts,
    model_identity,
    prepared_rows,
    reduce_classic,
    resolve_classic,
    run_blocks,
    validate_summary,
)
from .summary_prompts import ASSEMBLE_PROMPT, CHECK_PROMPT, NOTES_PROMPT, NOTES_SYSTEM, USER_INSTRUCTIONS

# Part of every checkpoint digest: a change of format or prompts means new answers.
NOTES_VERSION = "notes-2"
KIND_WORDS = dict(point="Тезис", decision="Решение", action="Задача", risk="Риск", question="Вопрос")
STATUS_WORDS = dict(
    proposed="предложено", agreed="согласовано", cancelled="отменено", disputed="оспаривается"
)
WORD_KINDS = {word.casefold(): kind for kind, word in KIND_WORDS.items()}
WORD_STATUSES = {word: status for status, word in STATUS_WORDS.items()}
# Context lines around a block, in characters; the same bound as the classic algorithm.
CONTEXT_CHARS = 1200
# A check may not delete more than this share of a block's points (and at least two).
MAX_REMOVED_SHARE = 0.5
BRIEF_LIMIT = 9

# Every answer ends with this line (or with «ПУНКТОВ НЕТ», «ВСЁ ВЕРНО», «НЕТ»): after it the
# grammar is complete and allows only the end of generation. Without an explicit end a model
# whose end-of-turn token llama.cpp does not recognise (GigaChat 3) keeps adding points.
END = "КОНЕЦ"
# Answer limits in tokens. Notes are shorter than the text they cover, corrections shorter than
# the notes: a model that loops is cut off early, and the block is split and asked again.
ASSEMBLE_TOKENS = 3072
# A primary block is never cut finer than this, however many slots wait for work.
MIN_BLOCK_CHARS = 2500
# The final text always gets at least this much room, deadline or not.
FINAL_FLOOR_TOKENS = 512
# Asked of the model when the time left does not cover detailed notes of the remaining blocks.
TERSE_NOTE = """

Времени мало: не более 10 пунктов для этого фрагмента — решения, задачи, числа, сроки, имена
и главные тезисы; второстепенное опусти."""
clock = time.monotonic  # replaced in tests


def notes_tokens(settings, block, terse=False):
    tokens = 512 + sum(len(row["text"]) for row in block) // 3
    return min(settings.max_output_tokens, tokens // 2 if terse else tokens)


def check_tokens(settings, notes):
    return min(settings.max_output_tokens, 384 + len(notes_text(notes)) // 2)


# One point; shared by all three grammars. Text cannot contain «|» or a line break, so the
# optional fields are found without guessing. Numbers are digits only: no «12?» or «12–14».
_ITEM_RULES = r"""
item   ::= kind " [" ids "] " text fields "\n"
kind   ::= "Тезис" | "Решение" | "Задача" | "Риск" | "Вопрос"
ids    ::= num (", " num)*
num    ::= [0-9]+
nums   ::= num (", " num)*
text   ::= [^\n|<]+
fields ::= (" | отв: " value)? (" | срок: " value)? (" | статус: " status)?
value  ::= [^\n|<]+
status ::= "предложено" | "согласовано" | "отменено" | "оспаривается"
line   ::= [^\n|<]+
"""
NOTES_GRAMMAR = (
    r"""root ::= "ТЕМА: " line "\n" "ТЕМЫ: " line "\n" "ОБЗОР: " line "\n" body
body ::= ("- " item)+ "КОНЕЦ\n" | "ПУНКТОВ НЕТ\n"
"""
    + _ITEM_RULES
)
CHECK_GRAMMAR = (
    r"""root ::= "ВСЁ ВЕРНО\n" | fix+ "КОНЕЦ\n"
fix  ::= "Исправить " nums ": " item | "Удалить " nums ": " line "\n" | "Добавить: " item
"""
    + _ITEM_RULES
)


def _optional_chain(rule, count):
    """`rule (rule (rule)?)?` — one to `count` repetitions without {m,n} syntax."""
    chain = rule
    for _ in range(count - 1):
        chain = f"{rule} ({chain})?"
    return chain


ASSEMBLE_GRAMMAR = (
    r"""root ::= "ОБЗОР: " line "\n" "ТЕМЫ: " line "\n" "ТЕЗИСЫ\n" theses "ПЕРЕСМОТРЫ\n" revisions
thesis ::= "- " item
theses ::= """
    + _optional_chain("thesis", BRIEF_LIMIT)
    + r"""
revisions ::= "НЕТ\n" | revision+ "КОНЕЦ\n"
revision  ::= "- " nums " → " item
"""
    + _ITEM_RULES
)


def notes_system(settings):
    extra = settings.summary_instructions.strip()
    return NOTES_SYSTEM + (USER_INSTRUCTIONS + extra if extra else "")


# -- format ------------------------------------------------------------------------------------


def item_line(item):
    """A point as the model writes it: «Решение [12, 14] текст | отв: … | статус: …»."""
    line = f"{KIND_WORDS.get(item['kind'], 'Тезис')} [{', '.join(map(str, item['evidence']))}] "
    line += " ".join(str(item["text"]).replace("|", "/").split())
    if item.get("owner"):
        line += " | отв: " + " ".join(str(item["owner"]).replace("|", "/").split())
    if item.get("due"):
        line += " | срок: " + " ".join(str(item["due"]).replace("|", "/").split())
    if item.get("status") in STATUS_WORDS:
        line += " | статус: " + STATUS_WORDS[item["status"]]
    return line


ITEM = re.compile(r"(?P<kind>[^\[\]]+?)\s*\[(?P<ids>[^\]]*)\]\s*(?:[—–:-]\s+)?(?P<rest>.*)")
BULLET = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")


def parse_item(text):
    """One point line → the standard item; SummaryFormatError when it is not a point."""
    match = ITEM.fullmatch(BULLET.sub("", text).strip())
    if not match:
        raise SummaryFormatError(f"Строка не в формате пункта: «{text.strip()[:120]}».")
    word = " ".join(match["kind"].split()).casefold()
    kind = WORD_KINDS.get(word) or KIND_ALIASES.get(word)
    if kind is None:
        raise SummaryFormatError(f"Неизвестный вид пункта «{match['kind'].strip()[:40]}».")
    if not re.fullmatch(r"\s*\d+(?:\s*[,;]\s*\d+)*\s*", match["ids"]):
        raise SummaryFormatError("В квадратных скобках пункта должны быть только номера реплик.")
    fields = match["rest"].split("|")
    item = dict(
        kind=kind,
        text=fields[0].strip(),
        evidence=[int(n) for n in re.findall(r"\d+", match["ids"])],
        owner=None,
        due=None,
        status="unspecified",
    )
    for field in fields[1:]:
        key, colon, value = field.partition(":")
        key, value = key.strip().casefold(), value.strip()
        if colon and key in {"отв", "ответственный", "исполнитель"}:
            item["owner"] = value or None
        elif colon and key == "срок":
            item["due"] = value or None
        elif colon and key == "статус":
            word = value.casefold()
            item["status"] = WORD_STATUSES.get(word) or STATUS_ALIASES.get(word, "unspecified")
        elif field.strip():
            item["text"] += " / " + field.strip()  # an unknown field stays with the text
    if not item["text"]:
        raise SummaryFormatError("У пункта нет текста.")
    return item


def clean_topics(value):
    topics = []
    for topic in re.split(r"[;,]", value):
        topic = " ".join(topic.split()).strip(" .")
        if topic and len(topic) <= 100 and topic.casefold() not in {t.casefold() for t in topics}:
            topics.append(topic)
    return topics[:30]


def parse_notes(content):
    """The notes of one block → dict(title, topics, overview, items)."""
    result = dict(title="", topics=[], overview="", items=[])
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.upper() == END or line.upper().startswith("ПУНКТОВ НЕТ"):
            continue
        head, colon, value = line.partition(":")
        head = head.strip().upper()
        if colon and head == "ТЕМА":
            result["title"] = " ".join(value.split())[:120]
        elif colon and head == "ТЕМЫ":
            result["topics"] = clean_topics(value)
        elif colon and head == "ОБЗОР":
            result["overview"] = " ".join(value.split())[:12000]
        else:
            result["items"].append(parse_item(line))
    return result


# -- transcript --------------------------------------------------------------------------------


def row_line(row):
    mark = "?" if row.get("uncertain") else ""
    return f"[{row['id']}{mark}] " + " ".join(str(row["text"]).split())


def note_blocks(rows, max_chars):
    """Primary blocks of compact lines within `max_chars`; a long row is cut into pieces."""
    block, size = [], 0
    width = max(200, max_chars // 2)
    for row in rows:
        text = " ".join(str(row["text"]).split())
        pieces = [text[i : i + width] for i in range(0, len(text), width)] or [""]
        for piece in pieces:
            part = dict(id=row["id"], start=row["start"], uncertain=bool(row.get("uncertain")), text=piece)
            length = len(row_line(part)) + 1
            if block and size + length > max_chars:
                yield block
                block, size = [], 0
            block.append(part)
            size += length
    if block:
        yield block


def context_rows(rows, limit, tail=False):
    """At most `limit` characters of neighbouring rows; a long row is cut with «…»."""
    picked, size = [], 0
    for row in reversed(rows) if tail else rows:
        length = len(row_line(row)) + 1
        if size + length > limit:
            if not picked:
                room = max(0, limit - len(row_line(dict(row, text=""))) - 2)
                text = row["text"]
                if room:
                    picked.append(dict(row, text="…" + text[-room:] if tail else text[:room] + "…"))
            break
        picked.append(row)
        size += length
    return list(reversed(picked)) if tail else picked


def block_chars(rows, max_chars, slots=1):
    """Characters of a primary block: half the request, or finer so that every slot has work.

    A short recording in one or two blocks would leave the other slots idle and write every
    note in one long answer; cut into at least two blocks per slot, it is written at once.
    """
    size = max_chars // 2
    total = sum(len(row_line(row)) + 1 for row in rows)
    wanted = 2 * max(1, slots)
    if total and total < size * wanted:
        # A tenth of slack: rows do not divide evenly, and one row over must not add a block.
        size = max(MIN_BLOCK_CHARS, min(size, math.ceil(total / wanted * 1.1)))
    return size


def planned_blocks(rows, max_chars, slots=1):
    """[(block, before, after)] — primary rows with bounded raw context on both sides."""
    rows = list(rows)
    primary = list(note_blocks(rows, block_chars(rows, max_chars, slots)))
    limit = min(CONTEXT_CHARS, max_chars // 10)
    return [
        (
            block,
            context_rows(primary[i - 1], limit, tail=True) if i else [],
            context_rows(primary[i + 1], limit) if i + 1 < len(primary) else [],
        )
        for i, block in enumerate(primary)
    ]


def transcript_text(block, before, after):
    """The shared beginning of the notes and the check requests."""
    lines = ["РАСШИФРОВКА"]
    if before:
        lines += ["До основного фрагмента (только для понимания связей):", *map(row_line, before)]
    lines += ["Основной фрагмент:", *map(row_line, block)]
    if after:
        lines += ["После основного фрагмента (только для понимания связей):", *map(row_line, after)]
    return "\n".join(lines) + "\n\n"


def notes_text(notes):
    lines = [f"ТЕМА: {notes.get('title', '')}", f"ОБЗОР: {notes['overview']}"]
    lines += [f"{n}. {item_line(item)}" for n, item in enumerate(notes["items"], 1)]
    return "\n".join(lines)


# -- requests ----------------------------------------------------------------------------------


def looped(text, least=3):
    """A cut-off answer that went round in circles: its complete lines, each once; else None.

    A model that never reaches the end line repeats points until the answer limit. Points it
    wrote before looping are fine; splitting the block would only repeat the loop twice.
    """
    lines = [line.strip() for line in text.splitlines()[:-1] if line.strip()]  # last may be cut
    points = [line for line in lines if line.startswith(("- ", "Исправить", "Удалить", "Добавить"))]
    if len(points) - len(set(points)) < least:
        return None
    return "\n".join(dict.fromkeys(lines)) + "\n"


def ask(client, system, prompt, grammar, parse, max_tokens, minimum=1024, lenient=False, on_cut=None):
    """A grammar-shaped answer, parsed; a rejected answer is asked again twice with the reason.

    The reason is appended at the end, so the transcript at the beginning stays cached.
    `lenient`: an answer cut off by the limit keeps its complete lines instead of splitting
    the block — when there is no time for a second attempt; `on_cut` is told about it.
    """
    for attempt in range(3):
        text, truncated = client.complete_text(system, prompt, max_tokens, minimum, grammar=grammar)
        if truncated:
            whole = looped(text)
            if whole is None and lenient:
                whole = "\n".join(text.splitlines()[:-1]) + "\n"
                if on_cut:
                    on_cut()
            if whole is None:
                raise SummaryTooLong("Модель не уложилась в лимит ответа; блок будет разделён.")
            text = whole
        try:
            return parse(text)
        except SummaryFormatError as exc:
            if attempt == 2:
                raise
            prompt += (
                f"\n\nПредыдущий ответ не прошёл проверку: {str(exc)[:300]} Ответь строго в заданном "
                "формате; в квадратных скобках — только номера реплик из расшифровки."
            )
    raise AssertionError("unreachable")


def apply_check(content, draft, allowed, primary):
    """The draft with the check's corrections; (result, receipt).

    Points the check does not mention stay as they are, so a check cannot lose a point by
    omission. Each point may be corrected or deleted once; deletions need a reason.
    """
    count = len(draft["items"])
    edits, touched, removed, added = {}, set(), [], []
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.upper() == END or line.upper().startswith(("ВСЁ ВЕРНО", "ВСЕ ВЕРНО")):
            continue
        match = re.match(r"(?i)(исправить|удалить)\s+([\d,\s]+?)\s*:\s*(.*)", line)
        if match:
            numbers = [int(n) for n in re.findall(r"\d+", match[2])]
            indices = [n - 1 for n in numbers]
            if not indices or any(not 0 <= i < count for i in indices):
                raise SummaryFormatError(f"Номера пунктов конспекта — от 1 до {count}.")
            if touched.intersection(indices) or len(set(indices)) != len(indices):
                raise SummaryFormatError("Каждый пункт конспекта исправляют или удаляют один раз.")
            touched.update(indices)
            if match[1].casefold() == "исправить":
                edits[min(indices)] = parse_item(match[3])
            else:
                if not match[3].strip():
                    raise SummaryFormatError("Удаление пункта требует причины.")
                removed += [dict(draft_id=i, reason=match[3].strip()[:2000]) for i in indices]
            continue
        match = re.match(r"(?i)добавить\s*:\s*(.*)", line)
        if not match:
            raise SummaryFormatError(f"Непонятная строка проверки: «{line[:120]}».")
        added.append(parse_item(match[1]))
    if len(removed) > max(2, int(count * MAX_REMOVED_SHARE)):
        raise SummaryFormatError("Проверка удалила слишком много пунктов; перепроверь конспект.")
    items = []
    for index, item in enumerate(draft["items"]):
        if index in edits:
            items.append(edits[index])
        elif index not in touched:
            items.append(item)
    result = dict(draft, items=items + added)
    _source_result(result, allowed, primary)
    receipt = dict(text=content.strip()[:20000], edited=len(edits), removed=removed, added=len(added))
    return result, receipt


def extractive_fallback(block, reason):
    """Source lines kept as points when the model never answers in shape even for a tiny block."""
    items = []
    for row in block:
        text = row["text"].strip()
        for start in range(0, len(text), 2000):
            if piece := text[start : start + 2000].strip():
                items.append(
                    dict(
                        kind="point",
                        text=piece,
                        evidence=[row["id"]],
                        owner=None,
                        due=None,
                        status="unspecified",
                    )
                )
    if not items:
        raise ValueError("В блоке нет текста, который можно сохранить в резервную сводку.")
    result = dict(
        title="",
        overview="Часть разговора сохранена из исходной расшифровки без модельного обобщения.",
        items=items,
        topics=[],
        quality_warning=(
            "Модель сводок не вернула конспект в нужном формате даже после уменьшения блока. "
            "Исходные реплики сохранены без перефразирования; проверьте этот фрагмент. " + str(reason)[:300]
        ),
    )
    validate_summary(result, {row["id"] for row in block})
    return result


# -- assembly ----------------------------------------------------------------------------------


def register_text(ledger, parts):
    lines = ["РЕЕСТР ВСТРЕЧИ", "Части записи по порядку:"]
    for n, part in enumerate(parts, 1):
        title = part["title"].rstrip(".")
        lines.append(f"Часть {n}. " + (f"{title}. " if title else "") + " ".join(part["overview"].split()))
    lines.append("Пункты по порядку записи:")
    lines += [f"{n}. {item_line(item)}" for n, item in enumerate(ledger, 1)]
    return "\n".join(lines) + "\n\n"


def parse_assembly(content, ledger):
    """(brief, revisions) from the assembly answer; revisions are [(ledger indices, item)]."""
    allowed = {ref for item in ledger for ref in item["evidence"]}
    brief = dict(overview="", items=[], topics=[])
    revisions, section = [], None
    for raw in content.splitlines():
        line = raw.strip()
        head, colon, value = line.partition(":")
        if not line:
            continue
        if colon and head.strip().upper() == "ОБЗОР":
            brief["overview"] = " ".join(value.split())[:12000]
        elif colon and head.strip().upper() == "ТЕМЫ":
            brief["topics"] = clean_topics(value)
        elif line.upper().rstrip(":") in {"ТЕЗИСЫ", "ПЕРЕСМОТРЫ"}:
            section = line.upper().rstrip(":")
        elif line.upper() == END or (section == "ПЕРЕСМОТРЫ" and line.upper() == "НЕТ"):
            continue
        elif section == "ТЕЗИСЫ":
            brief["items"].append(parse_item(line))
        elif section == "ПЕРЕСМОТРЫ":
            numbers, arrow, rest = BULLET.sub("", line).partition("→")
            if not arrow:
                numbers, arrow, rest = BULLET.sub("", line).partition("->")
            indices = [int(n) - 1 for n in re.findall(r"\d+", numbers)]
            if not arrow or not indices:
                raise SummaryFormatError(f"Строка пересмотра не в формате «3, 17 → пункт»: «{line[:120]}».")
            if any(
                not 0 <= i < len(ledger) or ledger[i]["kind"] not in {"decision", "action"} for i in indices
            ):
                raise SummaryFormatError("Пересмотр ссылается не на решение или задачу реестра.")
            revisions.append((sorted(set(indices)), parse_item(rest)))
        else:
            raise SummaryFormatError(f"Непонятная строка сводки: «{line[:120]}».")
    if not brief["overview"]:
        raise SummaryFormatError("Нет обзора встречи.")
    if not brief["items"] or len(brief["items"]) > BRIEF_LIMIT:
        raise SummaryFormatError(f"Нужно от 1 до {BRIEF_LIMIT} тезисов.")
    try:
        validate_summary(brief, allowed)
        validate_summary(dict(overview="", items=[item for _, item in revisions], topics=[]), allowed)
    except (ValueError, TypeError) as exc:
        raise SummaryFormatError(str(exc)) from None
    seen = [i for indices, _ in revisions for i in indices]
    if len(seen) != len(set(seen)):
        raise SummaryFormatError("Один пункт реестра попал в два пересмотра.")
    return brief, revisions


def resolved_from(ledger, revisions):
    """Decisions and tasks with their final status: a revised history becomes its last state."""
    replacing = {indices[0]: item for indices, item in revisions}
    folded = {i for indices, _ in revisions for i in indices}
    resolved = []
    for index, item in enumerate(ledger):
        if index in replacing:
            resolved.append(replacing[index])
        elif index not in folded and item["kind"] in {"decision", "action"}:
            resolved.append(item)
    return resolved


# -- time budget -------------------------------------------------------------------------------


class Budget:
    """How long the summary may take, and how fast this Mac and model turn out to be.

    Speeds come from the server's own timings in every answer (tokens read and written and
    the milliseconds each took), so after the first block the estimates are measured, not
    assumed. `pressure` says what the remaining steps must give up to finish in time:
    nothing, the check of the blocks, or part of the detail. Without a limit it only measures.
    """

    # Until measured: a base Apple Silicon Mac with a small MoE model, on the slow side.
    DEFAULT_PROMPT_SPEED = 300.0  # tokens read per second
    DEFAULT_PREDICT_SPEED = 15.0  # tokens written per second
    # Register of notes: characters per token (Russian, numbers), kept low to be safe.
    REGISTER_CHARS_PER_TOKEN = 2.0
    # Model switches, checkpoints, the pause between stages.
    OVERHEAD = 10.0

    def __init__(self, seconds=0, parallel=1):
        self.limit = seconds or None
        self.parallel = max(1, parallel)
        self.started = clock()
        self.lock = threading.Lock()
        self.prompt_tokens = self.prompt_seconds = 0.0
        self.predicted_tokens = self.predicted_seconds = 0.0
        self.notes_time = self.check_time = 0.0
        self.notes_chars = 0
        self.finished = 0  # blocks whose notes (and check, if any) are done
        self.checked = 0
        self.checks_skipped = self.terse_blocks = self.cut_blocks = 0
        self.final_tokens = None

    # -- measurements --------------------------------------------------------------------------

    def observe(self, timings):
        """llama-server's `timings` of one answer (the MLX engine reports the same fields)."""
        if not isinstance(timings, dict):
            return
        with self.lock:
            self.prompt_tokens += float(timings.get("prompt_n") or 0)
            self.prompt_seconds += float(timings.get("prompt_ms") or 0) / 1000
            self.predicted_tokens += float(timings.get("predicted_n") or 0)
            self.predicted_seconds += float(timings.get("predicted_ms") or 0) / 1000

    def measured(self):
        return self.predicted_tokens >= 32 and self.predicted_seconds > 0

    def prompt_speed(self):
        if self.prompt_tokens >= 256 and self.prompt_seconds > 0:
            return self.prompt_tokens / self.prompt_seconds
        return self.DEFAULT_PROMPT_SPEED

    def predict_speed(self):
        return (
            self.predicted_tokens / self.predicted_seconds if self.measured() else self.DEFAULT_PREDICT_SPEED
        )

    def cost(self, prompt_tokens, output_tokens):
        """Expected seconds of one request."""
        return prompt_tokens / self.prompt_speed() + output_tokens / self.predict_speed()

    def notes_done(self, seconds, chars):
        with self.lock:
            self.notes_time += seconds or 0.0
            self.notes_chars += chars

    def check_done(self, seconds):
        with self.lock:
            self.check_time += seconds or 0.0
            self.checked += 1

    def block_finished(self):
        with self.lock:
            self.finished += 1

    # -- the clock -----------------------------------------------------------------------------

    def elapsed(self):
        return clock() - self.started

    def left(self):
        return None if self.limit is None else self.limit - self.elapsed()

    def register_tokens(self, total_blocks):
        """Tokens of the register the assembly and the final text will read."""
        done = max(1, self.finished)
        chars = self.notes_chars * max(total_blocks, done) / done
        return chars / self.REGISTER_CHARS_PER_TOKEN + 400

    def tail(self, total_blocks, final_tokens):
        """Seconds the assembly and the final text are expected to take after the blocks.

        Both read the register; the assembly writes nine theses and the revisions, the final
        text usually well under its room — the estimates are typical answers, not the caps.
        """
        register = self.register_tokens(total_blocks)
        assembly = min(ASSEMBLE_TOKENS, 500 + register / 10)
        return self.cost(register, assembly) + self.cost(register, final_tokens * 0.6) + self.OVERHEAD

    def remaining(self, total_blocks, final_tokens, checks=True):
        """Expected seconds to the end from now, with or without checks of the remaining blocks."""
        left_blocks = max(0, total_blocks - self.finished)
        if self.finished:
            per_block = self.notes_time / self.finished
            if checks:
                per_block += self.check_time / self.checked if self.checked else per_block * 0.4
            blocks = math.ceil(left_blocks / self.parallel) * per_block
        else:
            blocks = 0.0  # nothing to extrapolate from yet
        return blocks + self.tail(total_blocks, final_tokens)

    def pressure(self, total_blocks, final_tokens):
        """0 — as planned; 1 — no checks for the remaining blocks; 2 — terse notes as well."""
        if self.limit is None or not self.finished or self.finished >= total_blocks:
            return 0
        if self.elapsed() + self.remaining(total_blocks, final_tokens) <= self.limit:
            return 0
        if self.elapsed() + self.remaining(total_blocks, final_tokens, checks=False) <= self.limit:
            return 1
        return 2

    def eta(self, total_blocks, final_tokens):
        """'осталось ≈ 4 мин' once a block has been measured, else ''."""
        if not self.finished or self.finished >= total_blocks:
            return ""
        checks = self.pressure(total_blocks, final_tokens) < 1
        seconds = self.remaining(total_blocks, final_tokens, checks)
        return " · осталось ≈ " + ("меньше минуты" if seconds < 60 else f"{math.ceil(seconds / 60)} мин")

    def final_plan(self, wanted, total_blocks):
        """(answer tokens, continuations) for the final text in the time that is left."""
        if self.limit is None:
            return wanted, None
        register = self.register_tokens(total_blocks)
        seconds = (self.left() or 0) - self.cost(register, 0) - self.OVERHEAD
        fit = int(seconds * self.predict_speed())
        self.final_tokens = max(FINAL_FLOOR_TOKENS, min(wanted, fit))
        return self.final_tokens, 1

    def report(self):
        measured = self.measured()
        return dict(
            limit=int(self.limit or 0),
            elapsed=round(self.elapsed(), 1),
            checks_skipped=self.checks_skipped,
            terse_blocks=self.terse_blocks,
            cut_blocks=self.cut_blocks,
            final_tokens=self.final_tokens,
            prompt_speed=round(self.prompt_speed()) if measured else None,
            predict_speed=round(self.predict_speed(), 1) if measured else None,
        )


# -- the pipeline ------------------------------------------------------------------------------


def summarize_notes(store, mid, settings, progress, client, dropped=(set(), 0.0)):
    from .local_llm import final_input_chars, final_output_tokens

    system = notes_system(settings)
    preparation = dict(pruned=0, pruned_seconds=0.0, cleaned_percent=0)
    rows = prepared_rows(store, mid, settings, dropped, progress, preparation)
    budget = Budget(settings.summary_minutes * 60, getattr(client, "parallel", 1))
    client.observe = budget.observe
    client.expected = budget.cost
    planned = planned_blocks(rows, settings.input_chars, budget.parallel)
    total = len(planned)
    wanted_final = final_output_tokens(settings)
    identity = NOTES_VERSION + model_identity(settings) + str(settings.max_output_tokens) + system

    def block_notes(index, block, before, after, node=1, depth=0):
        transcript = transcript_text(block, before, after)
        digest = hashlib.sha256((identity + NOTES_PROMPT + CHECK_PROMPT + transcript).encode()).hexdigest()
        phase, part = (
            ("summary-notes-map", index) if node == 1 else ("summary-notes-map-split", index * 100 + node)
        )
        cached = store.checkpoint(mid, phase, part)
        cached = cached if cached and cached.get("digest") == digest else {}
        allowed = {row["id"] for row in [*before, *block, *after]}
        primary = {row["id"] for row in block}

        def validated(notes):
            _source_result(notes, allowed, primary)
            return notes

        if cached.get("parts") is not None:
            return [validated(r) for r in cached["parts"]]

        def split(reason):
            fallback = None
            if depth >= 3 or (len(block) == 1 and len(block[0]["text"]) < 400):
                fallback = extractive_fallback(block, reason)
            if fallback:
                store.save_checkpoint(mid, phase, part, dict(digest=digest, parts=[fallback]))
                progress(f"Блок {index + 1}: сохранён исходный текст вместо повреждённого ответа модели")
                return [fallback]
            if len(block) > 1:
                halves = [block[: len(block) // 2], block[len(block) // 2 :]]
            else:
                text = block[0]["text"]
                halves = [[dict(block[0], text=t)] for t in (text[: len(text) // 2], text[len(text) // 2 :])]
            store.save_checkpoint(mid, phase, part, dict(digest=digest, split=True))
            progress(f"Делю блок {index + 1} на меньшие части для полного ответа…")
            limit = min(CONTEXT_CHARS, settings.input_chars // 10)
            first = block_notes(index, halves[0], before, context_rows(halves[1], limit), node * 2, depth + 1)
            second = block_notes(
                index, halves[1], context_rows(halves[0], limit, tail=True), after, node * 2 + 1, depth + 1
            )
            return first + second

        if cached.get("split"):
            return split("Продолжаю обработку частей блока.")
        # Short on time: terse notes, and an answer cut off by its limit is kept as it is.
        terse = budget.pressure(total, wanted_final) >= 2
        started = clock()
        try:
            if "draft" in cached:
                draft = validated(cached["draft"])
            else:
                if terse:
                    budget.terse_blocks += 1
                draft = ask(
                    client,
                    system,
                    transcript + NOTES_PROMPT + (TERSE_NOTE if terse else ""),
                    NOTES_GRAMMAR,
                    lambda text: validated(parse_notes(text)),
                    notes_tokens(settings, block, terse),
                    lenient=terse,
                    on_cut=lambda: setattr(budget, "cut_blocks", budget.cut_blocks + 1),
                )
                budget.notes_done(clock() - started, len(notes_text(draft)))
        except (SummaryTooLong, SummaryFormatError) as exc:
            return split(exc)
        if "draft" in cached:
            budget.notes_done(None, len(notes_text(draft)))
        store.save_checkpoint(mid, phase, part, dict(digest=digest, draft=draft))
        if not draft["items"]:
            store.save_checkpoint(mid, phase, part, dict(digest=digest, draft=draft, parts=[draft]))
            return [draft]
        if budget.pressure(total, wanted_final) >= 1:
            budget.checks_skipped += 1
            store.save_checkpoint(
                mid, phase, part, dict(digest=digest, draft=draft, parts=[draft], check=dict(skipped=True))
            )
            return [draft]
        progress(f"Проверка блока {index + 1} из {total} по расшифровке…")
        started = clock()
        try:
            checked, receipt = ask(
                client,
                system,
                transcript + CHECK_PROMPT + notes_text(draft),
                CHECK_GRAMMAR,
                lambda text: apply_check(text, draft, allowed, primary),
                check_tokens(settings, draft),
                minimum=512,
            )
            budget.check_done(clock() - started)
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
        store.save_checkpoint(
            mid, phase, part, dict(digest=digest, draft=draft, parts=[checked], check=receipt)
        )
        return [checked]

    def block(index, *rest):
        results = block_notes(index, *rest)
        budget.block_finished()
        return results

    started = time.monotonic()
    produced = run_blocks(planned, block, client, progress, lambda: budget.eta(total, wanted_final))
    timings = dict(blocks=round(time.monotonic() - started, 1))
    if budget.measured():
        progress(
            f"Скорость модели: читает ≈ {round(budget.prompt_speed())} ток/с, "
            f"пишет ≈ {budget.predict_speed():.0f} ток/с"
        )
    maps, ledger, parts = ledger_and_parts(produced)
    started = time.monotonic()
    built = dict(
        ledger=ledger,
        maps=maps,
        resolved=[],
        parts=parts,
        preparation=preparation,
        resolve_warnings=[],
        levels=[],
    )
    if not ledger:
        brief = dict(
            overview=maps[0]["overview"], items=[], topics=sorted({t for m in maps for t in m["topics"]})
        )
    else:
        brief = assemble(store, mid, settings, client, progress, system, built, final_input_chars(settings))
    timings["assembly"] = round(time.monotonic() - started, 1)
    final = budget.final_plan(wanted_final, total)
    result = finish_summary(store, mid, settings, client, progress, brief, built, timings, final)
    result["budget"] = budget.report()
    return result


def assemble(store, mid, settings, client, progress, system, built, budget):
    """The brief and the final status of decisions from one request; classic reduction otherwise."""
    ledger, maps = built["ledger"], built["maps"]
    register = register_text(ledger, built["parts"])
    digest = hashlib.sha256(
        (NOTES_VERSION + model_identity(settings) + system + ASSEMBLE_PROMPT + register).encode()
    ).hexdigest()
    cached = store.checkpoint(mid, "summary-notes-assemble", 0)
    if cached and cached.get("digest") == digest:
        built["resolved"] = cached["resolved"]
        return cached["brief"]
    reason = "Реестр встречи не поместился в один запрос."
    if len(register) <= budget:
        progress(f"Сборка сводки: {len(ledger)} пунктов за один заход…")
        try:
            brief, revisions = ask(
                client,
                system,
                register + ASSEMBLE_PROMPT,
                ASSEMBLE_GRAMMAR,
                lambda text: parse_assembly(text, ledger),
                min(settings.max_output_tokens, ASSEMBLE_TOKENS),
            )
        except (SummaryTooLong, SummaryFormatError) as exc:
            reason = str(exc)
        else:
            if not brief["topics"]:
                brief["topics"] = sorted({t for m in maps for t in m["topics"]})[:30]
            built["resolved"] = resolved_from(ledger, revisions)
            store.save_checkpoint(
                mid,
                "summary-notes-assemble",
                0,
                dict(digest=digest, brief=brief, resolved=built["resolved"], revisions=len(revisions)),
            )
            return brief
    progress("Сборка за один заход не удалась — объединяю по уровням. " + reason[:200])
    resolved, warnings = resolve_classic(store, mid, settings, client, ledger, progress)
    built["resolved"] = resolved
    built["resolve_warnings"] += warnings
    try:
        brief = reduce_classic(client, ledger, settings, progress, built["levels"])
    except ReduceFallback as exc:
        brief = fallback_brief(ledger, maps, str(exc))
    return brief or dict(overview=maps[0]["overview"], items=[], topics=[])
