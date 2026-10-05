"""Phrases Whisper writes where nobody spoke.

Whisper learned from internet videos with their subtitles, and the end of a Russian
subtitle file carries credits: «Субтитры создавал DimaTorzok», «Редактор субтитров
А.Семкин Корректор А.Егорова», «Продолжение следует…». On silence, noise or music — often at
the seam of two fragments — the model writes these lines as if they were said.

Only a whole reply made of such a phrase counts: a real sentence that merely contains
«спасибо за просмотр» stays. Nothing is removed here; the reply is flagged for review, and
the transcript view offers to delete all of them at once after the user confirms.
"""

import re

REASON = "вероятно, выдумка Whisper"
# Longer replies are real speech even if they start like a credit line.
MAX_CHARS = 160

# A name in a credit line: one to a few words, no comma (a comma starts a real sentence).
NAME = r"\s*:?\s+[^\s,]+(?:\s+[^\s,]+){0,4}"
_WHOLE = [
    # «Субтитры создавал DimaTorzok», «Субтитры сделала Анна»
    r"субтитры\s+(?:создавал|создала?|делала?|сделала?|подготовила?|перев[её]л|перевела|выполнила?)" + NAME,
    r"субтитры\s+(?:подготовлены|сделаны|созданы|предоставлены)\s+(?:сообществом|командой|при\s+поддержке)\b.{0,60}",
    r"субтитры:\s+[^\s,]+(?:\s+[^\s,]+){0,2}",
    # «Редактор субтитров А.Семкин Корректор А.Егорова»
    r"(?:редактор|корректор|переводчик|автор)\s+субтитров" + NAME,
    r"корректор:?\s+[а-яё]\.\s?[а-яё-]+",
    r"продолжение\s+следует",
    r"(?:всем\s+)?спасибо\s+за\s+просмотр(?:\s+и\s+до\s+(?:встречи|свидания|новых\s+встреч))?",
    r"(?:не\s+забудьте\s+)?подпи(?:сывайтесь|шитесь|саться)\s+на\s+(?:наш\s+|мой\s+|этот\s+)?канал"
    r"(?:[\s,]+(?:и\s+)?ставьте\s+лайки?)?",
    r"ставьте\s+лайки?(?:[\s,]+(?:и\s+)?подписывайтесь(?:\s+на\s+(?:наш\s+)?канал)?)?",
    r"thank(?:s|\s+you)\s+for\s+watching",
    # «[музыка]», «(аплодисменты)», «♪»
    r"[\[(]\s*(?:музыка|музыкальная\s+заставка|аплодисменты|смех|шум|тишина|music|applause|laughter)\s*[\])]",
    r"[♪♫♬\s]+",
]
WHOLE = re.compile("|".join(f"(?:{pattern})" for pattern in _WHOLE), re.IGNORECASE)
# Names that only ever come from subtitle credits: anywhere in a reply.
ANYWHERE = re.compile(r"dimatorzok|amara\.org", re.IGNORECASE)


def normalized(text):
    return " ".join(text.split()).strip(" .,!?…-—–«»\"'")


def is_hallucination(text):
    """True when the whole reply is a phrase Whisper is known to invent on silence."""
    if ANYWHERE.search(text or ""):
        return True
    text = normalized(text or "")
    return bool(text) and len(text) <= MAX_CHARS and bool(WHOLE.fullmatch(text))


def hallucinated(rows):
    """The rows whose text is such a phrase, in transcript order."""
    return [row for row in rows if is_hallucination(row["text"])]
