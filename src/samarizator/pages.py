"""Whole-window states: welcome, an empty state with one next step, work in progress, final text."""

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPen,
    QPixmap,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextFrameFormat,
    QTextTable,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .summary_prompts import FINAL_FORMATS
from .theme import ACCENT, GREEN, pixmap
from .widgets import CenteredBrowser, ProgressRing, StepList, label, primary


def app_icon(size=96):
    """Waveform on a dark rounded square — the app's mark until there is a real icon."""
    image = QPixmap(size * 2, size * 2)
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(2.0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1d1d1f"))
    painter.drawRoundedRect(QRectF(0, 0, size, size), size * 0.23, size * 0.23)
    pen = QPen(QColor("#ffffff"), size * 0.062)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    painter.setPen(pen)
    unit = size / 56
    for x, top, bottom in [
        (10, 22, 28),
        (17, 16, 34),
        (24, 12, 38),
        (31, 18, 32),
        (38, 14, 36),
        (45, 21, 29),
    ]:
        painter.drawLine(int(x * unit), int(top * unit), int(x * unit), int(bottom * unit))
    pen.setColor(QColor("#8fb8e8"))
    painter.setPen(pen)
    painter.drawLine(int(12 * unit), int(45 * unit), int(44 * unit), int(45 * unit))
    painter.end()
    return image


def centered(widget, width):
    """Scrollable page with `widget` in a column of at most `width` px."""
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    holder = QWidget()
    holder.setObjectName("pageWhite")
    line = QHBoxLayout(holder)
    line.setContentsMargins(32, 40, 32, 48)
    line.addStretch(1)
    widget.setMaximumWidth(width)
    line.addWidget(widget, 10, Qt.AlignmentFlag.AlignTop)
    line.addStretch(1)
    scroll.setWidget(holder)
    return scroll


class WelcomePage(QWidget):
    """First run: what the app does, and the one button that makes it work."""

    download = Signal()
    other_model = Signal()
    add_file = Signal()
    record = Signal()
    stop_download = Signal()

    FEATURES = [
        (
            "mic",
            "#e8f0fb",
            ACCENT,
            "Запись или файл",
            "Микрофон и звук звонка одновременно или любое аудио и видео — перетащите его в окно.",
        ),
        (
            "lines",
            "#f1e9fb",
            "#7a3fc4",
            "Сводка в вашем формате",
            "Протокол, резюме или конспект — с решениями, задачами и ссылками на нужное место записи.",
        ),
        (
            "lock",
            "#e6f5ea",
            "#1b6b33",
            "Без облака",
            "Аудио и текст не покидают Mac. Интернет нужен один раз — скачать модель.",
        ),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        logo = QLabel()
        logo.setPixmap(app_icon(96))
        layout.addWidget(logo, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(24)
        title = label("Добро пожаловать в Samarizator", "h1", wrap=True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addSpacing(8)
        lead = label(
            "Встречи, лекции и интервью — в понятные заметки. Всё происходит на этом Mac.", "lead", wrap=True
        )
        lead.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lead.setStyleSheet("color: #636366; font-size: 16px;")
        layout.addWidget(lead)
        layout.addSpacing(36)
        for name, background, color, heading, text in self.FEATURES:
            row = QHBoxLayout()
            row.setSpacing(16)
            badge = QLabel()
            badge.setFixedSize(44, 44)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setPixmap(pixmap(name, color, 24))
            badge.setStyleSheet(f"background: {background}; border-radius: 11px;")
            row.addWidget(badge, 0, Qt.AlignmentFlag.AlignTop)
            words = QVBoxLayout()
            words.setSpacing(2)
            head = label(heading)
            head.setStyleSheet("font-weight: 600; font-size: 15px;")
            words.addWidget(head)
            words.addWidget(label(text, "body", wrap=True))
            words.itemAt(1).widget().setStyleSheet("color: #636366; font-size: 14px;")
            row.addLayout(words, 1)
            layout.addLayout(row)
            layout.addSpacing(22)
        layout.addSpacing(14)
        self.model_card = QFrame()
        self.model_card.setObjectName("soft")
        model = QHBoxLayout(self.model_card)
        model.setContentsMargins(18, 14, 18, 14)
        words = QVBoxLayout()
        words.setSpacing(2)
        self.model_name = label("")
        self.model_name.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.model_note = label("", "secondary", wrap=True)
        words.addWidget(self.model_name)
        words.addWidget(self.model_note)
        model.addLayout(words, 1)
        self.other = QPushButton("Другая модель")
        self.other.setFlat(True)
        self.other.clicked.connect(self.other_model)
        model.addWidget(self.other)
        layout.addWidget(self.model_card)
        layout.addSpacing(16)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        self.main = primary(QPushButton("Скачать модель и начать"), large=True)
        self.main.clicked.connect(self.on_main)
        layout.addWidget(self.main)
        layout.addSpacing(8)
        self.second = QPushButton("Начать запись")
        self.second.setProperty("large", True)
        self.second.clicked.connect(self.record)
        layout.addWidget(self.second)
        layout.addSpacing(10)
        self.hint = label("Загрузку можно прервать — она продолжится с того же места.", "hint", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.hint)
        self.ready = False
        self.downloading = False
        outer.addWidget(centered(column, 520))

    def on_main(self):
        if self.downloading:
            self.stop_download.emit()
        elif self.ready:
            self.add_file.emit()
        else:
            self.download.emit()

    def set_model(self, ready, name, note):
        self.ready = ready
        self.model_name.setText(("✓ " if ready else "") + name)
        self.model_note.setText(note)
        self.other.setText("Сменить" if ready else "Другая модель")
        self.main.setText("Выбрать аудио или видео" if ready else "Скачать модель и начать")
        self.second.setVisible(ready)
        self.hint.setText(
            "Можно просто перетащить файл в это окно."
            if ready
            else "Загрузку можно прервать — она продолжится с того же места."
        )

    def set_download(self, active, text="", fraction=None):
        self.downloading = active
        self.bar.setVisible(active)
        if fraction is not None:
            self.bar.setValue(int(fraction * 1000))
        self.main.setText("Остановить загрузку" if active else self.main.text())
        primary(self.main, not active)
        if text:
            self.hint.setText(text)
        if not active:
            self.set_model(self.ready, self.model_name.text().removeprefix("✓ "), self.model_note.text())


class StatePage(QWidget):
    """An empty state that says what is going on and offers exactly one next step."""

    def __init__(self, parent=None):
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 0, 32, 0)
        outer.addStretch(2)
        column = QWidget()
        column.setMaximumWidth(460)
        layout = QVBoxLayout(column)
        layout.setSpacing(10)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.icon)
        self.title = label("", "h1small", wrap=True)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.body = label("", "body", wrap=True)
        self.body.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.body.setStyleSheet("color: #636366; font-size: 14px;")
        layout.addWidget(self.body)
        layout.addSpacing(10)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.second = QPushButton()
        self.main = primary(QPushButton())
        buttons.addWidget(self.second)
        buttons.addWidget(self.main)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(column)
        row.addStretch(1)
        outer.addLayout(row)
        outer.addStretch(3)
        self.main_action = self.second_action = None
        self.main.clicked.connect(lambda: self.main_action and self.main_action())
        self.second.clicked.connect(lambda: self.second_action and self.second_action())

    def set(self, icon, color, title, body, main=None, second=None):
        self.icon.setPixmap(pixmap(icon, color, 44, 1.5))
        self.title.setText(title)
        self.body.setText(body)
        for button, action, slot in [
            (self.main, main, "main_action"),
            (self.second, second, "second_action"),
        ]:
            button.setVisible(bool(action))
            if action:
                button.setText(action[0])
                setattr(self, slot, action[1])


class ProcessingPage(QWidget):
    """Ring, title, steps — what the app is doing and roughly how long it will take."""

    def __init__(self, parent=None):
        super().__init__(parent)
        column = QWidget()
        layout = QVBoxLayout(column)
        layout.setSpacing(0)
        self.ring = ProgressRing(128)
        layout.addWidget(self.ring, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(22)
        self.title = label("", "h1small")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        layout.addSpacing(6)
        self.subtitle = label("", "secondary", wrap=True)
        self.subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle.setStyleSheet("font-size: 14px;")
        layout.addWidget(self.subtitle)
        layout.addSpacing(26)
        self.steps = StepList()
        layout.addWidget(self.steps)
        layout.addSpacing(18)
        self.note = label("", "hint", wrap=True)
        self.note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.note)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(centered(column, 460))

    def set(self, title, subtitle, fraction, ring_text, steps, note="", color=ACCENT):
        self.title.setText(title)
        self.subtitle.setText(subtitle)
        self.ring.set(fraction, ring_text, color)
        self.steps.set_steps(steps)
        self.note.setText(note)


HEADINGS = {1: (28, 0, 14), 2: (19, 26, 8), 3: (16, 18, 6)}


def polish(document):
    """Book-like rhythm for Markdown: heading sizes, paragraph spacing, light tables."""
    block = document.begin()
    while block.isValid():
        cursor = QTextCursor(block)
        fmt = block.blockFormat()
        level = fmt.headingLevel()
        if cursor.currentTable():
            block = block.next()
            continue
        fmt.setLineHeight(145, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        if level:
            size, top, bottom = HEADINGS.get(level, HEADINGS[3])
            fmt.setTopMargin(top if block != document.begin() else 0)
            fmt.setBottomMargin(bottom)
            fmt.setLineHeight(115, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            cursor.setBlockFormat(fmt)
            cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
            chars = QTextCharFormat()
            chars.setFontPointSize(size * 0.75)
            chars.setFontWeight(QFont.Weight.Bold)
            cursor.mergeCharFormat(chars)
        else:
            fmt.setBottomMargin(max(fmt.bottomMargin(), 8))
            cursor.setBlockFormat(fmt)
        block = block.next()
    for frame in document.rootFrame().childFrames():
        if isinstance(frame, QTextTable):
            table = frame.format()
            table.setBorder(1)
            table.setBorderBrush(QColor("#e5e5ea"))
            table.setBorderStyle(QTextFrameFormat.BorderStyle.BorderStyle_Solid)
            table.setBorderCollapse(True)
            table.setCellPadding(8)
            table.setCellSpacing(0)
            table.setTopMargin(6)
            table.setBottomMargin(12)
            frame.setFormat(table)


class FinalPage(QWidget):
    """The document in the user's format, as a calm reading column."""

    formatChosen = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QWidget()
        bar.setObjectName("pageWhite")
        head = QHBoxLayout(bar)
        head.setContentsMargins(0, 22, 0, 0)
        head.addStretch(1)
        self.state = QLabel()
        self.state.setStyleSheet(f"color: {GREEN}; font-size: 12px; font-weight: 500;")
        head.addWidget(self.state)
        head.addWidget(label("·", "secondary"))
        head.addWidget(label("Формат", "secondary"))
        # A pop-up button, as in macOS toolbars: the current format, a menu of the others.
        self.format = QToolButton()
        self.format.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.format.setStyleSheet(f"QToolButton {{ color: {ACCENT}; font-weight: 500; padding: 2px 4px; }}")
        menu = QMenu(self.format)
        self.titles = {key: title for key, (title, _) in FINAL_FORMATS.items()}
        self.titles["custom"] = "Свой шаблон"
        for key, title in self.titles.items():
            action = menu.addAction(title + ("…" if key == "custom" else ""))
            action.triggered.connect(lambda checked=False, k=key: self.formatChosen.emit(k))
        self.format.setMenu(menu)
        head.addWidget(self.format)
        head.addStretch(1)
        layout.addWidget(bar)
        self.warning = label("", "hint", wrap=True)
        self.warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.warning.setStyleSheet("color: #8a4b00; padding: 8px 40px 0 40px;")
        layout.addWidget(self.warning)
        self.browser = CenteredBrowser(700)
        layout.addWidget(self.browser, 1)

    def set(self, final, format_key):
        final = final or {}
        self.state.setText("✓ Сводка готова" if final.get("text") else "Итогового текста нет")
        self.format.setText(self.titles.get(format_key, self.titles["custom"]) + "  ▾")
        self.warning.setText(" ".join((final.get("warning") or "").split()))
        self.warning.setVisible(bool(final.get("warning")))
        text = final.get("text") or (
            "Итогового текста у этой сводки нет — она создана до его появления. "
            "Выберите формат выше или «Пересоздать итоговый текст» в меню «⋯»."
        )
        document = self.browser.document()
        font = document.defaultFont()
        font.setPixelSize(15)
        document.setDefaultFont(font)
        self.browser.setMarkdown(text)
        polish(document)
