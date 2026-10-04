"""Turn the worker's status line into what the progress screen shows.

The worker reports one human-readable line at a time (`store.update(error=…)`). This
module reads the numbers out of it, so the window can show a percentage, the current
step and a time estimate without a second reporting channel.
"""

import re

FRAGMENT = re.compile(r"фрагмент (\d+)\s*/\s*(\d+)")
BLOCK = re.compile(r"блок\D{0,3}(\d+) из (\d+)")
REPLICA = re.compile(r"реплика (\d+)\s*/\s*(\d+)")

DONE, CURRENT, TODO = "done", "current", "todo"


def _ratio(match):
    done, total = int(match[1]), int(match[2])
    return (max(0, done - 1) / total) if total else 0.0, f"{done} из {total}"


def transcription(message):
    match = FRAGMENT.search(message or "")
    if not match:
        return dict(fraction=0.0, detail="подготовка")
    fraction, detail = _ratio(match)
    return dict(fraction=fraction, detail=f"фрагмент {detail}")


def summary_stage(message):
    """loading → blocks → brief → final, from the wording summary.py uses."""
    text = (message or "").lower()
    if "итоговый текст" in text or "сводка готова" in text:
        return "final"
    if "объединение" in text or "согласование" in text or "сборка" in text:
        return "brief"
    if BLOCK.search(text) or "делю блок" in text or "сводка: блок" in text:
        return "blocks"
    return "loading"


def summary(message, final_only=False):
    stage = summary_stage(message)
    if final_only:
        return dict(stage=stage, fraction=0.6 if stage == "final" else 0.1, detail="")
    match = BLOCK.search((message or "").lower())
    if stage == "blocks" and match:
        fraction, detail = _ratio(match)
        if "провер" in message.lower():
            fraction += 0.5 / int(match[2])
        return dict(stage=stage, fraction=0.05 + 0.75 * fraction, detail=detail)
    base = dict(loading=0.02, blocks=0.05, brief=0.82, final=0.93)[stage]
    return dict(stage=stage, fraction=base, detail="")


def summary_steps(message, segments=0, format_title="", final_only=False):
    """Rows of the step list: (title, detail, state)."""
    stage = summary_stage(message)
    order = ["loading", "blocks", "brief", "final"]
    if final_only:
        order = ["loading", "final"]
    titles = dict(
        loading=("Загрузка модели сводок", ""),
        blocks=("Разбор записи по блокам", ""),
        brief=("Краткая сводка и решения", ""),
        final=("Итоговый текст" + (f" · {format_title}" if format_title else ""), ""),
    )
    rows = [("Распознавание речи", f"{segments} реплик" if segments else "", DONE)]
    current = order.index(stage) if stage in order else 0
    for index, key in enumerate(order):
        title, detail = titles[key]
        if key == "blocks" and stage == "blocks":
            detail = summary(message)["detail"]
        state = DONE if index < current else CURRENT if index == current else TODO
        rows.append((title, detail, state))
    return rows


def retry(message):
    match = REPLICA.search(message or "")
    if not match:
        return dict(fraction=0.0, detail="")
    fraction, detail = _ratio(match)
    return dict(fraction=fraction, detail=detail)


ETA = re.compile(r"осталось ≈ (меньше минуты|\d+ мин)")


def remaining(fraction, elapsed, message=""):
    """'осталось около 12 мин' once there is enough history to say anything honest.

    A line that carries the summary's own estimate (from the measured speed of the model)
    wins over the extrapolation of the progress fraction.
    """
    match = ETA.search(message or "")
    if match:
        return "осталось " + ("меньше минуты" if match[1].startswith("меньше") else "около " + match[1])
    if fraction < 0.08 or elapsed < 20:
        return ""
    left = elapsed * (1 - fraction) / fraction
    if left < 60:
        return "осталось меньше минуты"
    minutes = round(left / 60)
    if minutes >= 90:
        hours, minutes = divmod(minutes, 60)
        return f"осталось около {hours} ч {minutes} мин"
    return f"осталось около {minutes} мин"


def percent(status, message):
    """Short label for the recordings list: 'Сводка 34%'."""
    if status == "transcribing":
        value = transcription(message)["fraction"]
        return f"Распознавание {round(value * 100)}%" if value else "Распознавание"
    if status == "summarizing":
        value = summary(message)["fraction"]
        return f"Сводка {round(value * 100)}%"
    if status == "retrying":
        return "Повторный проход"
    return ""
