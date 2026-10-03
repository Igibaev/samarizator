"""«Обезличивание»: what was hidden, the pseudonymised text, and the way back."""

import json
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .privacy import KIND_TITLES, LABEL, Masker, clock, restore, transcript_markdown
from .summary import summary_views
from .theme import ACCENT
from .widgets import Banner, label, primary

DOCUMENTS = [("transcript", "Расшифровка"), ("final", "Итоговый текст"), ("summary", "Сводка")]


def highlighted(text):
    """The Markdown rendered as in the other tabs, with every label marked."""
    from PySide6.QtGui import QTextDocument

    from .pages import polish

    document = QTextDocument()
    font = document.defaultFont()
    font.setPixelSize(14)
    document.setDefaultFont(font)
    document.setMarkdown(text)
    polish(document)
    html = document.toHtml()
    return LABEL.sub(
        lambda m: f'<span style="background-color:#e8f0fb; color:{ACCENT}; font-weight:600;">{m.group(0)}</span>',
        html,
    )


def summary_markdown(summary, mask):
    brief, detailed = summary_views(summary)
    lines = ["# Сводка встречи", "", mask(" ".join(str(brief.get("overview", "")).split())), ""]
    if brief.get("items"):
        lines += ["## Главное", ""] + [f"- {mask(str(item['text']))}" for item in brief["items"]] + [""]
    if detailed.get("items"):
        lines += ["## Подробно", ""] + [f"- {mask(str(item['text']))}" for item in detailed["items"]]
    return "\n".join(lines).rstrip() + "\n"


class RestoreDialog(QDialog):
    """Paste the answer of an external AI; the labels become the real names again."""

    def __init__(self, entries, parent=None):
        super().__init__(parent)
        self.entries = entries
        self.setWindowTitle("Вернуть данные в ответ ИИ")
        self.resize(760, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 18)
        layout.setSpacing(10)
        layout.addWidget(label("Вернуть данные в ответ ИИ", "h2"))
        layout.addWidget(
            label(
                "Вставьте ответ, который вернула внешняя нейросеть: метки вида [Человек 1] будут заменены "
                "на исходные имена и номера из таблицы этой записи. Замена идёт только на этом Mac.",
                "hint",
                wrap=True,
            )
        )
        panes = QHBoxLayout()
        self.source = QPlainTextEdit()
        self.source.setPlaceholderText("Ответ внешней нейросети с метками…")
        self.result = QPlainTextEdit()
        self.result.setReadOnly(True)
        self.result.setPlaceholderText("Здесь появится текст с настоящими данными")
        panes.addWidget(self.source)
        panes.addWidget(self.result)
        layout.addLayout(panes, 1)
        row = QHBoxLayout()
        self.state = label("", "secondary")
        row.addWidget(self.state, 1)
        close = QPushButton("Закрыть")
        close.clicked.connect(self.reject)
        copy = primary(QPushButton("Скопировать результат"))
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.result.toPlainText()))
        row.addWidget(close)
        row.addWidget(copy)
        layout.addLayout(row)
        self.source.textChanged.connect(self.update_result)

    def update_result(self):
        text = self.source.toPlainText()
        self.result.setPlainText(restore(text, self.entries))
        found = len(LABEL.findall(text))
        self.state.setText(f"Заменено меток: {found}" if found else "")


class PrivacyPage(QWidget):
    """Table of hidden data on the left, the pseudonymised document on the right."""

    COLUMNS = ["", "Метка", "Что скрыто", "Тип", "Раз"]

    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.mid = None
        self.masker = None
        self.decisions = dict(disabled=[], custom=[])
        self.filling = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(12)
        self.banner = Banner("info")
        self.banner.set_text(
            "Обезличенная копия для внешних нейросетей",
            "Имена, места, организации, телефоны, почта и номера документов заменены метками. "
            "Таблица соответствий хранится только на этом Mac и в файл не попадает. Автоматический "
            "поиск находит не всё — просмотрите текст перед отправкой и добавьте свои слова.",
        )
        outer.addWidget(self.banner)
        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        left = QWidget()
        left_box = QVBoxLayout(left)
        left_box.setContentsMargins(0, 0, 8, 0)
        left_box.setSpacing(8)
        self.summary_line = label("", "secondary", wrap=True)
        left_box.addWidget(self.summary_line)
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self.toggled)
        left_box.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        add = QPushButton("Скрыть своё слово…")
        add.setToolTip("Имя клиента, название проекта — будет скрыто во всех падежах")
        add.clicked.connect(self.add_word)
        back = QPushButton("Вернуть данные в ответ ИИ…")
        back.clicked.connect(self.restore_answer)
        buttons.addWidget(add)
        buttons.addWidget(back)
        buttons.addStretch(1)
        left_box.addLayout(buttons)
        split.addWidget(left)
        right = QWidget()
        right_box = QVBoxLayout(right)
        right_box.setContentsMargins(8, 0, 0, 0)
        right_box.setSpacing(8)
        bar = QHBoxLayout()
        self.document_choice = QComboBox()
        self.document_choice.currentIndexChanged.connect(self.render)
        bar.addWidget(self.document_choice)
        bar.addStretch(1)
        copy = QPushButton("Скопировать")
        copy.clicked.connect(lambda: QApplication.clipboard().setText(self.document()))
        save = primary(QPushButton("Сохранить .md…"))
        save.clicked.connect(self.save)
        bar.addWidget(copy)
        bar.addWidget(save)
        right_box.addLayout(bar)
        self.preview = QTextBrowser()
        self.preview.setOpenLinks(False)
        right_box.addWidget(self.preview, 1)
        split.addWidget(right)
        split.setSizes([430, 620])
        outer.addWidget(split, 1)

    # -- data -------------------------------------------------------------------------

    def show_meeting(self, mid):
        self.mid = mid
        stored = self.store.checkpoint(mid, "privacy", 0) or {}
        self.decisions = dict(
            disabled=list(stored.get("disabled", [])), custom=list(stored.get("custom", []))
        )
        self.rebuild()

    def texts(self):
        texts = [row["text"] for row in self.store.iter_segments(self.mid)]
        summary = self.summary()
        if summary:
            brief, detailed = summary_views(summary)
            texts.append(str(brief.get("overview", "")))
            texts += [
                str(item.get("text", "")) for item in brief.get("items", []) + detailed.get("items", [])
            ]
            texts.append(str((summary.get("final") or {}).get("text", "")))
        return texts

    def summary(self):
        raw = self.store.meeting(self.mid)["summary"]
        return json.loads(raw) if raw else None

    def rebuild(self):
        self.masker = Masker(self.decisions["disabled"], self.decisions["custom"]).scan(self.texts())
        self.fill_table()
        current = self.document_choice.currentData()
        self.document_choice.blockSignals(True)
        self.document_choice.clear()
        summary = self.summary()
        for key, title in DOCUMENTS:
            if (
                key == "transcript"
                or summary
                and (key == "summary" or (summary.get("final") or {}).get("text"))
            ):
                self.document_choice.addItem(title, key)
        self.document_choice.setCurrentIndex(max(0, self.document_choice.findData(current)))
        self.document_choice.blockSignals(False)
        self.render()

    def save_decisions(self):
        self.store.save_checkpoint(self.mid, "privacy", 0, self.decisions)

    def fill_table(self):
        self.filling = True
        entries = self.masker.ordered()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked if entry.enabled else Qt.CheckState.Unchecked)
            check.setData(Qt.ItemDataRole.UserRole, entry.key)
            check.setToolTip("Скрывать" if entry.enabled else "Оставить как есть")
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(entry.label))
            forms = [form for form in entry.forms if form != entry.value]
            shown = entry.value + (f"  ({', '.join(forms[:4])})" if forms else "")
            what = QTableWidgetItem(shown)
            what.setToolTip("\n".join(entry.forms) or entry.value)
            self.table.setItem(row, 2, what)
            self.table.setItem(row, 3, QTableWidgetItem(KIND_TITLES[entry.kind]))
            count = QTableWidgetItem(str(entry.count))
            count.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, 4, count)
        hidden = sum(1 for e in entries if e.enabled)
        occurrences = sum(e.count for e in entries if e.enabled)
        self.summary_line.setText(
            f"Скрыто: {hidden} из {len(entries)} — {occurrences} мест в тексте. "
            "Снимите галочку, чтобы оставить данные как есть."
            if entries
            else "Персональных данных не найдено. Добавьте свои слова, если нужно что-то скрыть."
        )
        self.filling = False

    def toggled(self, item):
        if self.filling or item.column() != 0:
            return
        key = item.data(Qt.ItemDataRole.UserRole)
        enabled = item.checkState() == Qt.CheckState.Checked
        if key.startswith("custom:") and not enabled:
            # A word the user added is removed rather than kept unchecked.
            self.decisions["custom"] = [
                w for w in self.decisions["custom"] if "custom:" + w.casefold() != key
            ]
        elif enabled:
            self.decisions["disabled"] = [k for k in self.decisions["disabled"] if k != key]
        elif key not in self.decisions["disabled"]:
            self.decisions["disabled"].append(key)
        self.save_decisions()
        self.rebuild()

    def add_word(self):
        word, ok = QInputDialog.getText(
            self,
            "Скрыть своё слово",
            "Слово или фраза (имя клиента, название проекта). Одно слово скрывается во всех падежах:",
        )
        word = " ".join(word.split())
        if ok and word and word not in self.decisions["custom"]:
            self.decisions["custom"].append(word)
            self.save_decisions()
            self.rebuild()

    # -- documents --------------------------------------------------------------------

    def document(self):
        key = self.document_choice.currentData() or "transcript"
        mask = self.masker.mask
        if key == "transcript":
            meeting = self.store.meeting(self.mid)
            meta = []
            try:
                meta.append(datetime.fromisoformat(meeting["created"]).astimezone().strftime("%d.%m.%Y"))
            except (TypeError, ValueError):
                pass
            if meeting["duration"]:
                meta.append("длительность " + clock(meeting["duration"]))
            # The title comes from the file name and may itself contain a name: not exported.
            return transcript_markdown(
                self.store.iter_segments(self.mid), "Расшифровка встречи", " · ".join(meta), mask
            )
        summary = self.summary() or {}
        if key == "final":
            return mask(str((summary.get("final") or {}).get("text", ""))).rstrip() + "\n"
        return summary_markdown(summary, mask)

    def render(self, *_):
        if self.masker is None:
            return
        self.preview.setHtml(highlighted(self.document()))

    def save(self):
        title = self.document_choice.currentText() or "Расшифровка"
        default = str(Path.home() / "Documents" / f"{title} (обезличено).md")
        path, _ = QFileDialog.getSaveFileName(
            self, "Сохранить обезличенный текст", default, "Markdown (*.md)"
        )
        if path:
            Path(path).write_text(self.document(), encoding="utf-8")

    def restore_answer(self):
        RestoreDialog(self.masker.ordered() if self.masker else [], self).exec()
