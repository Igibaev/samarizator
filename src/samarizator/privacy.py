"""Pseudonymised copies of a meeting for external AI, and the Markdown transcript.

Everything here runs on the Mac. Personal data is replaced with stable labels —
[Человек 1], [Телефон 2], [Организация 1] — so a transcript can be summarised by a
cloud model without names, numbers or addresses leaving the computer. The table of
labels stays in the app: it shows exactly what was hidden, lets the user keep or add
items, and puts the real names back into the answer that comes back.

Detection is deliberately conservative and transparent:
- identifiers by pattern: e-mail, links, cards (Luhn), SNILS, passports, phones, any
  long number (INN, IIN, accounts, contracts);
- people, places and organisations by Russian morphology (pymorphy3 dictionary tags
  Name/Surn/Patr, Geox, Orgn) on capitalised words, so every grammatical case of one
  name gets one label: «Ирина», «Ирине», «Ириной» → [Человек 1];
- legal entities by form: ООО «…», АО «…», ИП …;
- the user's own words (client names, project code names), in every case.
Automatic search never finds everything: the page says so and shows the text to check.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache

KINDS = {
    "person": "Человек",
    "place": "Место",
    "org": "Организация",
    "email": "Почта",
    "url": "Ссылка",
    "phone": "Телефон",
    "card": "Карта",
    "snils": "СНИЛС",
    "passport": "Паспорт",
    "number": "Номер",
    "custom": "Скрыто",
}
KIND_TITLES = {
    "person": "Человек",
    "place": "Место",
    "org": "Организация",
    "email": "Почта",
    "url": "Ссылка",
    "phone": "Телефон",
    "card": "Банковская карта",
    "snils": "СНИЛС",
    "passport": "Паспорт",
    "number": "Длинный номер",
    "custom": "Своё слово",
}
LABEL = re.compile(r"\[(" + "|".join(KINDS.values()) + r") (\d+)\]")

PATTERNS = [
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    ("url", re.compile(r"(?:https?://|www\.)[^\s<>()\"«»]+", re.I)),
    ("card", re.compile(r"(?<![\d-])\d{4}(?:[ -]?\d{4}){2}[ -]?\d{1,7}(?![\d-])")),
    ("snils", re.compile(r"(?<!\d)\d{3}-\d{3}-\d{3}[ -]\d{2}(?!\d)")),
    # Series and number as written: «45 06 123456»; ten digits in a row are just a long number.
    ("passport", re.compile(r"(?<!\d)\d{2} \d{2} №? ?\d{6}(?!\d)")),
    (
        "phone",
        re.compile(
            r"(?<![\w+])(?:\+?[78]|\+\d{1,3})?[\s\-–(]{0,2}\d{3}[\s\-–)]{0,2}\d{3}[\s\-–]?\d{2}[\s\-–]?\d{2}(?!\d)"
        ),
    ),
    ("number", re.compile(r"(?<![\d.,])\d{9,}(?!\d|[.,]\d)")),
]
LEGAL = re.compile(
    r"\b(?:ООО|ОАО|ЗАО|ПАО|АО|НАО|ТОО|ИП|ГК|АНО)\s+(?:[«\"„“]([^»\"”\n]{2,60})[»\"”]|"
    r"([А-ЯЁA-Z][\w-]+(?:\s+[А-ЯЁA-Z][\w-]+){0,2}))"
)
WORD = re.compile(r"[А-ЯЁа-яёA-Za-z][А-ЯЁа-яёA-Za-z-]*")
# A capitalised word after these starts a sentence or a list item, not necessarily a name.
SENTENCE_OPENERS = ".!?…:;\n-—–#*|>"
PERSON_TAGS = {"Name": "name", "Surn": "surname", "Patr": "patronymic"}


@lru_cache(maxsize=1)
def analyzer():
    import pymorphy3
    import pymorphy3_dicts_ru

    # An explicit path: the frozen app has no package metadata for entry-point lookup.
    return pymorphy3.MorphAnalyzer(path=pymorphy3_dicts_ru.get_path())


@lru_cache(maxsize=50000)
def classify(word):
    """(kind, role, lemma, nominative) for a word that can be a proper name, else None.

    The first parse carrying a proper-name tag is used, not only the most likely one:
    «Вера» is more often the noun, but capitalised mid-sentence it is a name.
    """
    for parse in analyzer().parse(word):
        tags = parse.tag
        for tag, role in PERSON_TAGS.items():
            if tag in tags:
                return "person", role, parse.normal_form, nominative(parse)
        if "Geox" in tags:
            return "place", "place", parse.normal_form, nominative(parse)
        if "Orgn" in tags:
            return "org", "org", parse.normal_form, nominative(parse)
    return None


def nominative(parse):
    """The word in the nominative, keeping the gender: «Петровой» → «Петрова»."""
    form = parse.inflect({"nomn"})
    word = form.word if form else parse.normal_form
    return "-".join(part[:1].upper() + part[1:] for part in word.split("-"))


@lru_cache(maxsize=50000)
def top_is_proper(word):
    parse = analyzer().parse(word)[0]
    return any(tag in parse.tag for tag in (*PERSON_TAGS, "Geox", "Orgn"))


@lru_cache(maxsize=20000)
def lemma(word):
    return analyzer().parse(word)[0].normal_form


def phone_like(value, digits):
    """Written with separators or a plus — a phone; ten digits in a row only as a mobile number."""
    if not 10 <= len(digits) <= 13:
        return False
    if value != digits:
        return True
    return (len(digits) == 10 and digits[0] in "79") or (len(digits) == 11 and digits[0] in "78")


def luhn(digits):
    total = 0
    for index, digit in enumerate(reversed(digits)):
        value = int(digit) * (2 if index % 2 else 1)
        total += value - 9 if value > 9 else value
    return total % 10 == 0


@dataclass
class Entry:
    key: str
    kind: str
    number: int
    value: str
    forms: dict = field(default_factory=dict)  # surface form → occurrences
    enabled: bool = True

    @property
    def label(self):
        return f"[{KINDS[self.kind]} {self.number}]"

    @property
    def count(self):
        return sum(self.forms.values())


class Masker:
    """Finds personal data in texts and replaces it with stable labels.

    The same Masker labels the transcript, the summary and the final text alike, so
    [Человек 1] is the same person everywhere. `disabled` keys are left in the text;
    `custom` words are always hidden.
    """

    def __init__(self, disabled=(), custom=()):
        self.disabled = set(disabled)
        self.custom = [c.strip() for c in custom if c and c.strip()]
        self.entries = {}
        self.counters = {}
        self.names_of = {}  # first-name lemma → person keys built from full names

    # -- entries ---------------------------------------------------------------------

    def entry(self, key, kind, value):
        if key not in self.entries:
            self.counters[kind] = self.counters.get(kind, 0) + 1
            self.entries[key] = Entry(key, kind, self.counters[kind], value, enabled=key not in self.disabled)
        return self.entries[key]

    def ordered(self):
        order = list(KINDS)
        return sorted(self.entries.values(), key=lambda e: (order.index(e.kind), e.number))

    # -- detection -------------------------------------------------------------------

    def find(self, text, count=False):
        """[(start, end, Entry)] in text order, without overlaps; `count` records the forms."""
        spans = []
        taken = [False] * len(text)

        def claim(start, end, make):
            """Mark text[start:end]; the entry is created only for text nobody claimed yet."""
            if any(taken[start:end]):
                return
            entry = make()
            for i in range(start, end):
                taken[i] = True
            if count:
                surface = text[start:end]
                entry.forms[surface] = entry.forms.get(surface, 0) + 1
            spans.append((start, end, entry))

        for word in self.custom:
            for start, end in self.custom_matches(text, word):
                claim(start, end, lambda w=word: self.entry("custom:" + w.casefold(), "custom", w))
        for kind, pattern in PATTERNS:
            for match in pattern.finditer(text):
                value = match.group(0).strip(" -–()")
                if kind in ("url", "email"):
                    value = value.rstrip(".,;:!?»")  # sentence punctuation is not part of a link
                digits = re.sub(r"\D", "", value)
                if kind == "card" and not luhn(digits):
                    kind_here = "number"  # a long number all the same: contracts, accounts
                else:
                    kind_here = kind
                if kind == "phone" and not phone_like(value, digits):
                    continue
                start = match.start() + match.group(0).index(value)
                key = f"{kind_here}:{digits or value.casefold()}"
                claim(start, start + len(value), lambda k=key, h=kind_here, v=value: self.entry(k, h, v))
        for match in LEGAL.finditer(text):
            name = (match.group(1) or match.group(2) or "").strip()
            if name:
                claim(
                    match.start(),
                    match.end(),
                    lambda n=name, v=match.group(0): self.entry("org:" + n.casefold(), "org", v),
                )
        self.find_names(text, claim)
        return sorted(spans, key=lambda span: span[0])

    def find_names(self, text, claim):
        words = list(WORD.finditer(text))
        index = 0
        while index < len(words):
            match = words[index]
            found = self.proper(text, match)
            if not found:
                index += 1
                continue
            kind, role, word_lemma, nom = found
            if kind != "person":
                claim(
                    match.start(),
                    match.end(),
                    lambda k=kind, w=word_lemma, n=nom: self.entry(f"{k}:{w}", k, n),
                )
                index += 1
                continue
            # Consecutive name words are one mention: «Ирина Сергеевна Петрова».
            parts = [(role, word_lemma, nom)]
            end = index + 1
            while end < len(words) and re.fullmatch(r"\s+", text[words[end - 1].end() : words[end].start()]):
                following = self.proper(text, words[end], inside=True)
                if not following or following[0] != "person":
                    break
                parts.append(following[1:])
                end += 1
            surface = text[match.start() : words[end - 1].end()]
            claim(match.start(), words[end - 1].end(), lambda p=parts, t=surface: self.person(p, t))
            index = end

    def proper(self, text, match, inside=False):
        word = match.group(0)
        if not word[:1].isupper() or word.isupper() and len(word) > 1 and not inside:
            return None  # lower case, or an abbreviation such as «ООО», «API»
        found = classify(word)
        if not found:
            return None
        before = text[max(0, match.start() - 12) : match.start()].rstrip()
        opens = (not before and match.start() <= 12) or (before and before[-1] in SENTENCE_OPENERS)
        sentence_start = not inside and bool(opens)
        if sentence_start and not top_is_proper(word):
            # «Вера в успех…» at the start of a sentence is the noun; mid-sentence it is a name.
            return None
        return found

    def person(self, parts, surface):
        surnames = [p for p in parts if p[0] == "surname"]
        names = [p for p in parts if p[0] == "name"]
        # A full name is shown as said («Ирина Петрова»): the nominative of a surname alone is
        # ambiguous between genders. A single word is shown in the nominative («Ирине» → «Ирина»).
        display = " ".join(surface.split()) if len(parts) > 1 else parts[0][2]
        if surnames:
            key = "person:" + surnames[0][1]
            entry = self.entry(key, "person", display)
            if len(display) > len(entry.value):
                entry.value = display
            for name in names:
                self.names_of.setdefault(name[1], set()).add(key)
            return entry
        first = (names or parts)[0][1]
        known = self.names_of.get(first, set())
        if len(known) == 1:
            return self.entries[next(iter(known))]
        return self.entry("person:" + first, "person", display)

    @staticmethod
    def custom_matches(text, word):
        if " " in word.strip():
            pattern = re.compile(r"(?<!\w)" + re.escape(word) + r"(?!\w)", re.I)
            return [(m.start(), m.end()) for m in pattern.finditer(text)]
        wanted = lemma(word.casefold())
        return [
            (m.start(), m.end())
            for m in WORD.finditer(text)
            if m.group(0).casefold() == word.casefold() or lemma(m.group(0).casefold()) == wanted
        ]

    # -- output ----------------------------------------------------------------------

    def mask(self, text):
        out, last = [], 0
        for start, end, entry in self.find(text):
            if not entry.enabled:
                continue
            out.append(text[last:start])
            out.append(entry.label)
            last = end
        out.append(text[last:])
        return "".join(out)

    def scan(self, texts):
        for text in texts:
            self.find(text, count=True)
        return self


def restore(text, entries):
    """Put the originals back into a text that came back from an external AI."""
    by_label = {entry.label: entry.value for entry in entries}
    return LABEL.sub(lambda m: by_label.get(m.group(0), m.group(0)), text)


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 3600:02}:{seconds // 60 % 60:02}:{seconds % 60:02}"


def paragraphs(segments, gap=2.0, span=60.0):
    """Replies merged into readable paragraphs: same speaker, short pauses, about a minute."""
    group = []
    for row in segments:
        if group and (
            row.get("speaker") != group[0].get("speaker")
            or row["start"] - group[-1]["end"] > gap
            or row["start"] - group[0]["start"] > span
        ):
            yield group
            group = []
        group.append(row)
    if group:
        yield group


def transcript_markdown(segments, title="Расшифровка", meta="", mask=None):
    """The transcript as Markdown: timestamped paragraphs, doubtful places marked."""
    mask = mask or (lambda text: text)
    segments = list(segments)
    speakers = {row.get("speaker") for row in segments if row.get("speaker")}
    lines = [f"# {title}", ""]
    if meta:
        lines += [meta, ""]
    for group in paragraphs(segments):
        parts = []
        for row in group:
            text = mask(" ".join(str(row["text"]).split()))
            if row.get("uncertain"):
                text += " *(проверить по записи)*"
            parts.append(text)
        who = f" {group[0]['speaker']}:" if len(speakers) > 1 and group[0].get("speaker") else ""
        lines += [f"**{clock(group[0]['start'])}**{who} " + " ".join(parts), ""]
    return "\n".join(lines).rstrip() + "\n"
