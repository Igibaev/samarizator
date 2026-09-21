"""Bounded map/reduce with evidence validation and an unabridged topic ledger."""

import hashlib
import json
import re
import ssl
import time

import httpx
import truststore

from .config import get_api_key
from .summary_prompts import (
    BRIEF_PROMPT,
    MAP_PROMPT,
    RECONCILE_PROMPT,
    REDUCE_PROMPT,
    REVIEW_PROMPT,
    SYSTEM,
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
    detailed = summary.get(
        "detailed",
        dict(
            overview="Существенные пункты исходных блоков. Возможны повторы между блоками.",
            items=summary.get("ledger", summary["items"]),
            topics=summary["topics"],
        ),
    )
    return brief, detailed


def package_summary(brief, ledger, maps, resolved):
    # Detailed facts bypass lossy reduction entirely, including topics mentioned only once.
    detailed = dict(
        overview="\n\n".join(f"Часть {i + 1}. {m['overview']}" for i, m in enumerate(maps)),
        items=ledger,
        topics=sorted({topic for m in maps for topic in m["topics"]}),
        # Final status after reconciling later revisions/cancellations; the ledger above
        # is never edited or shortened because of this -- it stays the full history.
        resolved=resolved,
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
    try:
        return validate_summary(normalize_model_summary(obj), allowed)
    except (ValueError, TypeError) as exc:
        raise SummaryFormatError(str(exc)) from None


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


class ChatClient:
    def __init__(self, settings, transport=None, key=None):
        settings.validate(api=True)
        self.settings = settings
        self.key = get_api_key() if key is None else key
        if not self.key:
            raise ValueError("Сохраните API-ключ модели в настройках.")
        self.transport = transport

    def complete(self, prompt, allowed):
        """Summary call: system prompt, low temperature, evidence-checked JSON result."""
        content = self.raw_complete(
            [dict(role="system", content=SYSTEM), dict(role="user", content=prompt)]
        )
        return parse_model_summary(content, allowed)

    def raw_complete(self, messages, temperature=0.1, max_tokens=None):
        """One Chat Completions call; returns the assistant text without interpreting it.

        Shared by the summary pipeline and the Focus Companion handoff (companion.py):
        one place owns TLS trust, the no-redirect/no-proxy policy, retries and the
        rule that error messages never quote the provider's response body.
        """
        s = self.settings
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        payload = dict(
            model=s.model,
            temperature=temperature,
            max_tokens=max_tokens or s.max_output_tokens,
            messages=messages,
        )
        # Verify against the OS trust store, so a corporate root installed in the system
        # keychain works the way curl does. certifi alone would reject an intercepted TLS chain.
        context = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        # No redirect, telemetry, public fallback, or implicit environment proxy.
        with httpx.Client(
            verify=context,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(180, connect=20),
            transport=self.transport,
        ) as client:
            for attempt in range(3):
                try:
                    with client.stream("POST", s.chat_url(), headers=headers, json=payload) as response:
                        if response.status_code in {429, 502, 503, 504} and attempt < 2:
                            time.sleep(2**attempt)
                            continue
                        if not 200 <= response.status_code < 300:
                            raise ValueError(
                                f"API модели: HTTP {response.status_code}. "
                                "Проверьте base URL, название модели и ключ."
                            )
                        raw = bytearray()
                        for block in response.iter_bytes():
                            raw.extend(block)
                            if len(raw) > 2_000_000:
                                raise ValueError("Ответ модели слишком большой.")
                    body = json.loads(raw)
                    choice = body["choices"][0]
                    if choice.get("finish_reason") == "length":
                        raise SummaryTooLong(
                            "Модель обрезала ответ. Уменьшите входной блок или увеличьте лимит ответа."
                        )
                    if choice.get("finish_reason") not in {"stop", None}:
                        raise ValueError(
                            "API не завершил сводку: проверьте ограничения корпоративной модели."
                        )
                    content = choice["message"]["content"]
                    if not isinstance(content, str):
                        raise SummaryFormatError("Модель вернула нетекстовый ответ.")
                    return content
                except httpx.HTTPError:
                    if attempt == 2:
                        raise ValueError(
                            "Нет соединения с API модели. Проверьте base URL, VPN и доверие корпоративному сертификату."
                        ) from None
                    time.sleep(2**attempt)
                except (KeyError, IndexError, TypeError, json.JSONDecodeError):
                    raise ValueError(
                        "API вернул ответ в неподдерживаемом формате. Нужен Chat Completions JSON."
                    ) from None
        raise RuntimeError("API модели недоступен.")


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
    for level in range(4):
        groups = _size_groups(current, input_chars)
        reduced = []
        for i, group in enumerate(groups):
            allowed = {ref for item in group for ref in item["evidence"]}
            result = checked_complete(
                client, RECONCILE_PROMPT + json.dumps(group, ensure_ascii=False), allowed
            )
            if {ref for item in result["items"] for ref in item["evidence"]} != allowed:
                raise ValueError("Согласование потеряло ссылки на часть исходных решений.")
            reduced.extend(result["items"])
            progress(f"Согласование решений: уровень {level + 1}, блок {i + 1}/{len(groups)}")
        if len(groups) == 1:
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
        "Корпоративная модель не вернула корректный JSON для этой части даже после уменьшения "
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

        def validate_review(result):
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


def summarize(store, mid, settings, progress=lambda *_: None, client=None):
    client = client or ChatClient(settings)
    maps = []

    def map_block(block, index, before, after, node=1, depth=0):
        source = dict(before=before, segments=[json.loads(line) for _, line in block], after=after)
        prompt = MAP_PROMPT + _json(dict(source=source))
        digest = hashlib.sha256(
            (
                settings.chat_url()
                + settings.model
                + str(settings.max_output_tokens)
                + str(settings.input_chars)
                + SYSTEM
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
                halves[0], index, before, _context(halves[1], limit), node * 2, depth + 1
            ) + map_block(
                halves[1], index, _context(halves[0], limit, tail=True), after, node * 2 + 1, depth + 1
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
        progress(f"Проверка полноты и точности блока {index + 1} по расшифровке…")
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
        store.save_checkpoint(
            mid, phase, part, dict(digest=digest, parts=[reviewed], draft=draft, review_receipts=receipts)
        )
        return [reviewed]

    for index, (block, before, after) in enumerate(
        contextual_blocks(store.iter_segments(mid), settings.input_chars)
    ):
        maps.extend(map_block(block, index, before, after))
        progress(f"Сводка: обработан блок {index + 1}")
    if not maps:
        raise ValueError("Нет распознанной речи для сводки.")
    # Keep source-grounded map items separately so reduction cannot erase a topic.
    ledger, seen = [], set()
    for result in maps:
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
    resolve_digest = hashlib.sha256(
        (
            settings.chat_url()
            + settings.model
            + str(settings.max_output_tokens)
            + SYSTEM
            + RECONCILE_PROMPT
            + "safe-resolution-v2"
            + str(settings.input_chars)
            + json.dumps(ledger, ensure_ascii=False)
        ).encode()
    ).hexdigest()
    cached_resolve = store.checkpoint(mid, "summary-resolve", 0)
    resolve_warnings = []
    if cached_resolve and cached_resolve.get("digest") == resolve_digest:
        resolved = cached_resolve["result"]
        resolve_warnings = cached_resolve.get("warnings", [])
    else:
        try:
            resolved = reconcile_decisions(
                client, ledger, settings.input_chars, progress, resolve_warnings.append
            )
        except (ValueError, RuntimeError, httpx.HTTPError):
            # Optional enrichment must never discard the two primary summaries.
            resolved = []
            resolve_warnings.append(
                "Дополнительное согласование не удалось. Краткая сводка и полный реестр сохранены; итоговый статус решений проверьте по записи."
            )
        else:
            store.save_checkpoint(
                mid,
                "summary-resolve",
                0,
                dict(digest=resolve_digest, result=resolved, warnings=resolve_warnings),
            )

    def package(brief):
        result = package_summary(brief, ledger, maps, resolved)
        result["detailed"]["resolution_warning"] = " ".join(resolve_warnings)
        warning = " ".join(dict.fromkeys(m["quality_warning"] for m in maps if m.get("quality_warning")))
        if warning:
            result["brief"]["quality_warning"] = warning
            result["detailed"]["quality_warning"] = warning
        return result

    def fallback_brief(reason):
        priorities = {"decision": 0, "action": 1, "risk": 2, "question": 3, "point": 4}
        selected = sorted(enumerate(ledger), key=lambda pair: (priorities.get(pair[1]["kind"], 9), pair[0]))[
            :9
        ]
        selected = [item for _, item in sorted(selected)]
        overview = " ".join(m["overview"].strip() for m in maps if m["overview"].strip())[:1000]
        brief = dict(
            overview=overview or "Существенные пункты встречи сохранены ниже.",
            items=selected,
            topics=sorted({topic for m in maps for topic in m["topics"]})[:30],
            generation_warning=(
                "Финальное сжатие ответа модели не прошло проверку. "
                "Показана выборка пунктов подробной сводки; это запасной вариант, не финальный синтез. "
                + reason
            ),
        )
        return package(brief)

    # A reduction unit is an item, not a whole map: every request remains bounded.
    current = ledger
    for level in range(8):
        units, group, size = [], [], 2
        for item in current:
            length = len(json.dumps(item, ensure_ascii=False)) + 2
            if length + 2 > settings.input_chars:
                return fallback_brief(
                    "Один пункт подробной сводки не поместился в блок финального сжатия."
                )
            if group and size + length > settings.input_chars:
                units.append(group)
                group, size = [], 2
            group.append(item)
            size += length
        if group:
            units.append(group)
        if not units:
            return package(dict(overview=maps[0]["overview"], items=[], topics=[]))
        reduced = []
        last = None
        for i, group in enumerate(units):
            instruction = BRIEF_PROMPT if len(units) == 1 else REDUCE_PROMPT
            prompt = (
                instruction + "Не смешивай противоположные мнения и разных ответственных. "
                "Пункты упорядочены по времени: явно отрази отмену или пересмотр решений.\n"
                + json.dumps(group, ensure_ascii=False)
            )
            allowed = {ref for item in group for ref in item["evidence"]}
            try:
                last = checked_complete(client, prompt, allowed)
            except (SummaryFormatError, SummaryTooLong) as exc:
                return fallback_brief(str(exc))
            reduced.extend(last["items"])
            progress(f"Объединение: уровень {level + 1}, блок {i + 1}/{len(units)}")
        if len(units) == 1:
            if len(last["items"]) > 9:
                return fallback_brief("Модель вернула более 9 кратких тезисов.")
            return package(last)
        if len(json.dumps(reduced)) >= len(json.dumps(current)):
            return fallback_brief("Модель не сократила промежуточный результат.")
        current = reduced
    return fallback_brief("Достигнут предел объединения сводки.")
