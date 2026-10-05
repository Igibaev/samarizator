"""Tidy spoken Russian before it reaches the summary model.

Only the model's input is tidied: the transcript, its timestamps and the evidence IDs stay
as recognised. Everything removed carries no meaning — hesitation sounds, stutter repeats
and a few set filler phrases — so the model reads (and later quotes) less text for the
same content. Words that are fillers only sometimes («вот», «типа», «значит») are kept.
Replies Whisper invented on silence («Субтитры создавал …») do not reach the model either.
"""

import re

from .hallucinations import is_hallucination

WORD = r"[А-ЯЁа-яёA-Za-z]+"
# «э», «ээ», «э-э», «эм», «мм», «хм», «аа», «ну-у» as a sound, with the comma that follows.
HESITATION = re.compile(
    r"(?<![\w-])(?:э+(?:-э+)*|эм+|м{2,}|хм+|а{2,}|ну-у+)(?![\w-])[,.…]*\s*",
    re.IGNORECASE,
)
# Set phrases that add nothing; «как бы» only when it is not «как бы не …» (= «lest»).
FILLERS = re.compile(
    r",?\s*(?<![\w-])(?:так сказать|скажем так|в общем-то|как бы(?!\s+не\b))(?![\w-]),?",
    re.IGNORECASE,
)
# The same word or two-word phrase said again right away: «я я я думаю», «мы будем мы будем».
REPEAT = re.compile(rf"(?<![\w-])((?:{WORD})(?:\s+{WORD})?)(?:[\s,]+\1)+(?![\w-])", re.IGNORECASE)
LEADING_NU = re.compile(r"^\s*ну[,.]\s*", re.IGNORECASE)


def tidy(text):
    """The utterance without hesitations, stutters and set fillers; '' if nothing is left."""
    cleaned = HESITATION.sub("", text)
    cleaned = FILLERS.sub("", cleaned)
    cleaned = LEADING_NU.sub("", cleaned)
    cleaned = REPEAT.sub(r"\1", cleaned)
    cleaned = re.sub(r"\s+([,.!?…])", r"\1", cleaned)
    cleaned = re.sub(r"([,])\1+", r"\1", cleaned)
    cleaned = " ".join(cleaned.split()).strip(" ,")
    if cleaned and cleaned[0].islower() and text.strip()[:1].isupper():
        cleaned = cleaned[0].upper() + cleaned[1:]
    return cleaned


def tidy_rows(rows, enabled=True, stats=None):
    """Rows with tidied text; rows left empty are skipped. `stats` counts characters."""
    for row in rows:
        text = row["text"]
        if not enabled:
            cleaned = text
        else:
            cleaned = "" if is_hallucination(text) else tidy(text)
        if stats is not None:
            stats["before"] = stats.get("before", 0) + len(text)
            stats["after"] = stats.get("after", 0) + len(cleaned)
        if cleaned.strip():
            yield dict(row, text=cleaned)
