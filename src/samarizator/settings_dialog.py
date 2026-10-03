"""Settings in the style of macOS System Settings, and the one-time model download."""

import shutil
import threading
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import live, local_llm, screencapture
from .bundle import tool
from .config import FINAL_PROMPT_LIMIT, INSTRUCTIONS_LIMIT, Settings
from .live import BOTH, DEVICE, MICROPHONE, NATIVE, SYSTEM
from .summary_prompts import DEFAULT_FORMAT, FINAL_FORMATS
from .theme import ACCENT, pixmap
from .widgets import label, primary, separator

LANGUAGES = [
    ("ru", "Русский"),
    ("en", "Английский"),
    ("kk", "Казахский"),
    ("auto", "Определять автоматически"),
]


def badge(name, color, size=22):
    """Coloured rounded square with a white glyph, like System Settings."""
    image = QPixmap(size * 2, size * 2)
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(2.0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(QRectF(0, 0, size, size), 6, 6)
    glyph = pixmap(name, "#ffffff", 14, 2.4)
    painter.drawPixmap(int((size - 14) / 2), int((size - 14) / 2), glyph)
    painter.end()
    return QIcon(image)


class Switch(QCheckBox):
    """macOS switch drawn by hand; still a QCheckBox for isChecked()/setChecked()."""

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(38, 22)

    def sizeHint(self):
        return QSize(38, 22)

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT if self.isChecked() else "#d1d1d6"))
        painter.drawRoundedRect(QRectF(0, 0, 38, 22), 11, 11)
        painter.setBrush(QColor("#ffffff"))
        x = 18 if self.isChecked() else 2
        painter.drawEllipse(QRectF(x, 2, 18, 18))


def model_line(path):
    file = Path(path).expanduser() if path else None
    if not file or not file.is_file():
        return "", False
    preset = next((p for p in local_llm.PRESETS.values() if p.file == file.name), None)
    name = preset.label.split("·")[-1].strip() if preset else file.stem
    return f"{name} · {file.stat().st_size / 1024**3:.1f} ГБ", True


class Group:
    """Caption plus a white rounded block of rows."""

    def __init__(self, parent_layout, title=""):
        self.box = QWidget()
        outer = QVBoxLayout(self.box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(7)
        if title:
            head = label(title)
            head.setStyleSheet("font-weight: 600; padding-left: 2px;")
            outer.addWidget(head)
        self.frame = QFrame()
        self.frame.setObjectName("card")
        self.frame.setStyleSheet("QFrame#card { border-radius: 10px; }")
        self.rows = QVBoxLayout(self.frame)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        outer.addWidget(self.frame)
        self.footer = label("", "hint", wrap=True)
        self.footer.setVisible(False)
        outer.addWidget(self.footer)
        parent_layout.addWidget(self.box)

    def row(self, title, *controls, subtitle="", stacked=False):
        if self.rows.count():
            self.rows.addWidget(separator())
        widget = QWidget()
        line = QVBoxLayout(widget) if stacked else QHBoxLayout(widget)
        line.setContentsMargins(14, 10, 14, 10)
        line.setSpacing(8 if stacked else 14)
        text = QVBoxLayout()
        text.setSpacing(1)
        name = label(title)
        text.addWidget(name)
        detail = None
        if subtitle is not None:
            detail = label(subtitle, "secondary", wrap=True)
            detail.setVisible(bool(subtitle))
            text.addWidget(detail)
        line.addLayout(text, 1)
        for control in controls:
            line.addWidget(control)
        self.rows.addWidget(widget)
        return detail

    def note(self, text):
        self.footer.setText(text)
        self.footer.setVisible(True)


def page(title):
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    body = QWidget()
    body.setObjectName("pageGround")
    layout = QVBoxLayout(body)
    layout.setContentsMargins(28, 18, 28, 28)
    layout.setSpacing(22)
    heading = label(title)
    heading.setStyleSheet("font-size: 15px; font-weight: 700;")
    layout.addWidget(heading)
    scroll.setWidget(body)
    return scroll, layout


class SettingsDialog(QDialog):
    PAGES = [
        ("Основное", "gear", "#8e8e93"),
        ("Шаблон итогового текста", "lines", "#0a7aff"),
        ("Live-запись", "mic", "#ff3b30"),
        ("Для опытных", "sliders", "#636366"),
    ]

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки")
        self.resize(900, 660)
        self.settings = settings
        self.fields = {}
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(230)
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(10, 16, 10, 14)
        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setIconSize(QSize(22, 22))
        for title, glyph, color in self.PAGES:
            QListWidgetItem(badge(glyph, color), "  " + title, self.nav)
        side_layout.addWidget(self.nav)
        outer.addWidget(side)
        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(0)
        self.stack = QStackedWidget()
        right.addWidget(self.stack, 1)
        bar = QFrame()
        bar.setObjectName("footer")
        buttons = QHBoxLayout(bar)
        buttons.setContentsMargins(20, 12, 20, 12)
        buttons.addStretch(1)
        cancel = QPushButton("Отменить")
        cancel.clicked.connect(self.reject)
        save = primary(QPushButton("Сохранить"))
        save.setDefault(True)
        save.clicked.connect(self.save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        right.addWidget(bar)
        outer.addLayout(right, 1)
        self.build_general()
        self.build_template()
        self.build_live()
        self.build_advanced()
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

    # -- pages ------------------------------------------------------------------------

    def hidden(self, key, value):
        field = QLineEdit(str(value))
        field.setVisible(False)
        self.fields[key] = field
        return field

    def build_general(self):
        s = self.settings
        scroll, layout = page("Основное")
        intro = label(
            "Распознавание и сводки работают на этом Mac. Сеть нужна только для загрузки моделей.",
            "hint",
            wrap=True,
        )
        layout.addWidget(intro)
        models = Group(layout, "Модели")
        self.hidden("llm_model", s.llm_model).textChanged.connect(self.describe_llm)
        self.hidden("llm_preset", s.llm_preset)
        self.hidden("whisper_model", s.whisper_model).textChanged.connect(self.describe_whisper)
        self.llm_state = label("")
        llm_button = QPushButton("Сменить…")
        llm_menu = QMenu(llm_button)
        llm_menu.addAction("Скачать модель…", self.download_llm)
        llm_menu.addAction("Выбрать файл .gguf…", self.pick_llm)
        llm_button.setMenu(llm_menu)
        self.llm_detail = models.row("Сводки", self.llm_state, llm_button, subtitle="")
        self.whisper_state = label("")
        whisper_button = QPushButton("Сменить…")
        whisper_menu = QMenu(whisper_button)
        whisper_menu.addAction("Скачать более точную модель…", self.download_whisper)
        whisper_menu.addAction(
            "Выбрать файл .bin…", lambda: self.pick("whisper_model", self.fields["whisper_model"])
        )
        whisper_button.setMenu(whisper_menu)
        self.whisper_detail = models.row(
            "Распознавание речи", self.whisper_state, whisper_button, subtitle=""
        )
        self.fields["llm_gpu"] = Switch(s.llm_gpu)
        models.row(
            "Ускорение сводок на GPU",
            self.fields["llm_gpu"],
            subtitle="Metal на Apple Silicon: сводка в разы быстрее, чем на процессоре.",
        )
        self.describe_llm()
        self.describe_whisper()
        speech = Group(layout, "Распознавание")
        language = QComboBox()
        language.setEditable(True)
        for code, name in LANGUAGES:
            language.addItem(name, code)
        found = language.findData(s.language)
        if found >= 0:
            language.setCurrentIndex(found)
        else:
            language.setEditText(s.language)
        language.setMinimumWidth(220)
        self.fields["language"] = language
        speech.row("Язык записей", language)
        glossary = QLineEdit(s.glossary)
        glossary.setMaxLength(800)
        glossary.setPlaceholderText("Samarizator, Иванов, EBITDA, названия ваших проектов")
        self.fields["glossary"] = glossary
        speech.row(
            "Имена и термины", glossary, subtitle="Подсказка распознаванию, через запятую.", stacked=True
        )
        summary = Group(layout, "Сводка")
        final_format = QComboBox()
        for key, (title, _) in FINAL_FORMATS.items():
            final_format.addItem(title, key)
        final_format.setCurrentIndex(max(0, final_format.findData(s.final_format)))
        final_format.activated.connect(self.pick_format)
        self.fields["final_format"] = final_format
        edit = QPushButton("Изменить шаблон")
        edit.setFlat(True)
        edit.clicked.connect(lambda: self.nav.setCurrentRow(1))
        summary.row("Итоговый текст", edit, final_format)
        instructions = QPlainTextEdit(s.summary_instructions)
        instructions.setPlaceholderText(
            "Например: «Сводка для отдела продаж. Выделяй цены, сроки поставки и возражения клиентов. "
            "QBS — название нашей методики, не расшифровывай»."
        )
        instructions.setFixedHeight(84)
        self.fields["summary_instructions"] = instructions
        summary.row("Что важно в ваших записях", instructions, subtitle=None, stacked=True)
        summary.note(
            "Учитывается на каждом шаге сводки. Правила точности встроены и не отключаются: только "
            "факты из записи, честные статусы решений, ссылки на реплики."
        )
        notes = Group(layout, "Заметки")
        self.hidden("vault", s.vault).textChanged.connect(lambda text: self.vault_label.setText(text))
        pick_vault = QPushButton("Выбрать…")
        pick_vault.clicked.connect(lambda: self.pick("vault", self.fields["vault"]))
        self.vault_label = notes.row("Папка для Obsidian", pick_vault, subtitle=s.vault)
        layout.addStretch(1)
        self.stack.addWidget(scroll)

    def build_template(self):
        s = self.settings
        scroll, layout = page("Шаблон итогового текста")
        layout.addWidget(
            label(
                "Шаблон описывает разделы, порядок и стиль документа обычными словами — модель следует "
                "ему. Сменили шаблон у готовой записи — нажмите «Пересоздать итоговый текст» в меню «⋯»: "
                "запись заново не разбирается.",
                "hint",
                wrap=True,
            )
        )
        template = QPlainTextEdit(
            s.final_prompt or FINAL_FORMATS.get(s.final_format, FINAL_FORMATS[DEFAULT_FORMAT])[1]
        )
        template.setMinimumHeight(360)
        self.fields["final_prompt"] = template
        layout.addWidget(template, 1)
        reset = QPushButton("Вернуть стандартный шаблон")
        reset.clicked.connect(lambda: self.pick_format(self.fields["final_format"].currentIndex()))
        row = QHBoxLayout()
        row.addWidget(reset)
        row.addStretch(1)
        layout.addLayout(row)
        self.stack.addWidget(scroll)

    def build_live(self):
        s = self.settings
        scroll, layout = page("Live-запись")
        sources = Group(layout, "Источник")
        source = QComboBox()
        for value, title in [
            (MICROPHONE, "Только микрофон"),
            (SYSTEM, "Только системный звук"),
            (BOTH, "Микрофон и звук звонка"),
        ]:
            source.addItem(title, value)
        source.setCurrentIndex(max(0, source.findData(s.live_source)))
        self.fields["live_source"] = source
        sources.row("Что записывать", source)
        backend = QComboBox()
        for value, title in [
            (NATIVE, "Штатно (ScreenCaptureKit)"),
            (DEVICE, "Устройство петли"),
        ]:
            backend.addItem(title, value)
        backend.setCurrentIndex(max(0, backend.findData(s.live_system_backend)))
        self.fields["live_system_backend"] = backend
        sources.row(
            "Захват системного звука",
            backend,
            subtitle="Штатный способ не требует драйверов. Петля (BlackHole, Loopback) — если штатный "
            "запрещён политикой компании.",
        )
        self.helper = screencapture.status()
        sources.note(("✓ " if self.helper["available"] else "⚠ ") + self.helper["message"])
        devices = Group(layout, "Устройства")
        self.inputs, self.loopback = self.live_devices()
        for key, title, only_loopback in [
            ("live_microphone_device", "Микрофон", False),
            ("live_system_device", "Системный звук", True),
        ]:
            box = QComboBox()
            box.setEditable(True)
            box.setMinimumWidth(240)
            box.addItem("По умолчанию", "")
            for _, name in self.loopback if only_loopback else self.inputs:
                box.addItem(name, name)
            saved = getattr(s, key)
            found = box.findData(saved)
            if saved and found < 0:
                box.addItem(saved, saved)
                found = box.count() - 1
            box.setCurrentIndex(max(0, found))
            self.fields[key] = box
            devices.row(title, box)
        if self.inputs and not self.loopback:
            devices.note(
                "Устройства петли не видно — оно нужно только для запасного способа. Установите его и "
                "откройте настройки снова либо впишите имя вручную."
            )
        mixing = QComboBox()
        for value, title in [
            ("gentle", "Мягкое выравнивание"),
            ("no-resample", "Без выравнивания"),
            ("stretch", "Выравнивание темпом"),
            ("hard-stuff", "Вставка тишины"),
            ("legacy-pan", "Как до 14.09.2026"),
        ]:
            mixing.addItem(title, value)
        mixing.setCurrentIndex(max(0, mixing.findData(s.live_mix)))
        self.fields["live_mix"] = mixing
        mix = Group(layout, "Сведение двух дорожек")
        mix.row(
            "Профиль",
            mixing,
            subtitle="Микрофон и системный звук идут от разных часов. Сравнить профили: "
            "python -m samarizator.live compare-mix",
        )
        mix.note(
            "Микрофон и системный звук пишутся в один файл раздельными каналами. При выводе в динамики "
            "микрофон повторно захватит собеседника — используйте наушники."
        )
        layout.addStretch(1)
        self.stack.addWidget(scroll)

    def build_advanced(self):
        s = self.settings
        scroll, layout = page("Для опытных")
        layout.addWidget(label("Обычно менять не нужно.", "hint"))
        profile = QPushButton("Применить профиль M4 Pro · 48 ГБ")
        profile.clicked.connect(self.quality_profile)
        row = QHBoxLayout()
        row.addWidget(profile)
        row.addStretch(1)
        layout.addLayout(row)
        resources = Group(layout, "Ресурсы")
        memory = QDoubleSpinBox()
        memory.setRange(2, 64)
        memory.setSuffix(" ГиБ")
        memory.setValue(s.memory_gb)
        self.fields["memory_gb"] = memory
        resources.row(
            "Бюджет памяти распознавания",
            memory,
            subtitle="Обработка останавливается на 90% бюджета. Это не жёсткая квота macOS.",
        )
        for key, title, lo, hi in [
            ("threads", "Потоки CPU", 1, 16),
            ("chunk_seconds", "Фрагмент, секунд", 30, 300),
        ]:
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(getattr(s, key))
            self.fields[key] = spin
            resources.row(title, spin)
        self.fields["gpu"] = Switch(s.gpu)
        resources.row(
            "Распознавание на GPU",
            self.fields["gpu"],
            subtitle="Быстрее и меньше греет, но память Metal не видна контролю бюджета.",
        )
        quality = Group(layout, "Качество распознавания")
        for key, title in [
            ("pause_boundaries", "Сдвигать границы фрагментов к паузам"),
            ("vad", "Выделять речь (VAD)"),
        ]:
            self.fields[key] = Switch(getattr(s, key))
            quality.row(title, self.fields[key])
        vad_model = QLineEdit(s.vad_model)
        self.fields["vad_model"] = vad_model
        quality.row("Модель VAD", vad_model, stacked=True)
        beam = QSpinBox()
        beam.setRange(1, 8)
        beam.setValue(s.beam_size)
        self.fields["beam_size"] = beam
        quality.row("Ширина поиска Whisper", beam)
        cleanup = QComboBox()
        for value, title in [
            ("off", "Без обработки"),
            ("light", "Лёгкая — гул и громкость"),
            ("strong", "Сильная — плюс шипение"),
        ]:
            cleanup.addItem(title, value)
        cleanup.setCurrentIndex(max(0, cleanup.findData(s.audio_cleanup)))
        self.fields["audio_cleanup"] = cleanup
        quality.row(
            "Обработка звука перед распознаванием",
            cleanup,
            subtitle="Меняется только то, что слышит Whisper; запись на диске не трогается.",
        )
        summary = Group(layout, "Сводка")
        for key, title, lo, hi, hint in [
            (
                "input_chars",
                "Символов текста на запрос",
                4000,
                48000,
                "Меньше — точнее у небольших моделей, но дольше.",
            ),
            ("max_output_tokens", "Лимит токенов ответа", 512, 64000, ""),
        ]:
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setSingleStep(1000 if key == "input_chars" else 512)
            spin.setValue(getattr(s, key))
            self.fields[key] = spin
            summary.row(title, spin, subtitle=hint)
        summary.note("Контекст и память для модели сводок рассчитываются автоматически из размера блока.")
        layout.addStretch(1)
        self.stack.addWidget(scroll)

    # -- behaviour -------------------------------------------------------------------

    def quality_profile(self):
        profile = self.settings.quality_profile()
        for key in ["memory_gb", "threads", "chunk_seconds", "beam_size"]:
            self.fields[key].setValue(getattr(profile, key))
        for key in ["gpu", "pause_boundaries", "vad"]:
            self.fields[key].setChecked(getattr(profile, key))
        if not self.fields["vad_model"].text().strip():
            self.fields["vad_model"].setText(profile.vad_model)

    @staticmethod
    def live_devices():
        """Enumerate macOS inputs for the pickers; elsewhere the fields stay free text."""
        if not live.capture_supported():
            return [], []
        try:
            devices = live.audio_devices(tool("ffmpeg"))
        except live.LiveCaptureError:
            return [], []
        return devices, live.system_audio_devices(devices)

    def pick_format(self, index):
        key = self.fields["final_format"].itemData(index)
        self.fields["final_prompt"].setPlainText(FINAL_FORMATS[key][1])

    def pick_llm(self):
        path = QFileDialog.getOpenFileName(self, "Модель сводок", "", "Модель GGUF (*.gguf);;Все файлы (*)")[
            0
        ]
        if path:
            self.fields["llm_model"].setText(path)
            self.fields["llm_preset"].setText("")

    def download_llm(self):
        dialog = ModelDownloadDialog(self)
        if dialog.exec() and dialog.path:
            self.fields["llm_model"].setText(str(dialog.path))
            self.fields["llm_preset"].setText(dialog.preset)

    def download_whisper(self):
        from .setup_models import WHISPER_PRESETS, whisper_memory_gb

        dialog = ModelDownloadDialog(
            self,
            WHISPER_PRESETS,
            "Модель распознавания",
            "Встроенная модель small быстрая, но крупные модели Whisper заметно точнее на живой речи. "
            "Новые записи распознаются выбранной моделью, старые — через «Распознать заново».",
        )
        if dialog.exec() and dialog.path:
            self.fields["whisper_model"].setText(str(dialog.path))
            # The transcription refuses a model that does not fit the memory budget.
            need = whisper_memory_gb(dialog.path)
            if self.fields["memory_gb"].value() < need:
                self.fields["memory_gb"].setValue(need)

    def describe_llm(self):
        line, ready = model_line(self.fields["llm_model"].text().strip())
        self.llm_state.setText("● Готова" if ready else "● Не скачана")
        self.llm_state.setStyleSheet(f"color: {'#1b6b33' if ready else '#c46b00'}; font-size: 12px;")
        if hasattr(self, "llm_detail") and self.llm_detail:
            self.llm_detail.setText(line or "Скачайте модель — это нужно один раз.")
            self.llm_detail.setVisible(True)

    def describe_whisper(self):
        path = Path(self.fields["whisper_model"].text().strip()).expanduser()
        ready = path.is_file()
        self.whisper_state.setText("● Готова" if ready else "● Не найдена")
        self.whisper_state.setStyleSheet(f"color: {'#1b6b33' if ready else '#c46b00'}; font-size: 12px;")
        if hasattr(self, "whisper_detail") and self.whisper_detail:
            name = path.name.removeprefix("ggml-").removesuffix(".bin") if path.name else "не выбрана"
            size = f" · {path.stat().st_size / 1024**3:.1f} ГБ" if ready else ""
            self.whisper_detail.setText(f"Whisper {name}{size}")
            self.whisper_detail.setVisible(True)

    def pick(self, key, widget):
        path = (
            QFileDialog.getExistingDirectory(self, "Папка базы знаний")
            if key == "vault"
            else QFileDialog.getOpenFileName(self, "Выберите локальную модель")[0]
        )
        if path:
            widget.setText(path)

    def value(self, field):
        if isinstance(field, QComboBox):
            data = field.currentData()
            if field.isEditable() and (
                data is None or field.currentText() != field.itemText(field.currentIndex())
            ):
                return field.currentText().strip()
            return data
        if isinstance(field, QCheckBox):
            return field.isChecked()
        if isinstance(field, (QSpinBox, QDoubleSpinBox)):
            return field.value()
        if isinstance(field, QPlainTextEdit):
            return field.toPlainText().strip()
        return field.text()

    def save(self):
        try:
            values = asdict(self.settings)
            for key, field in self.fields.items():
                values[key] = self.value(field)
            # An untouched standard template is stored empty, so improved templates in
            # future versions reach the user automatically.
            preset = FINAL_FORMATS.get(values["final_format"], FINAL_FORMATS[DEFAULT_FORMAT])[1]
            if values["final_prompt"] == preset.strip():
                values["final_prompt"] = ""
            updated = Settings(**values)
            if len(updated.summary_instructions) > INSTRUCTIONS_LIMIT:
                raise ValueError(f"Указания для сводки: не более {INSTRUCTIONS_LIMIT} символов.")
            if len(updated.final_prompt) > FINAL_PROMPT_LIMIT:
                raise ValueError(f"Шаблон итогового текста: не более {FINAL_PROMPT_LIMIT} символов.")
            updated.validate(llm=bool(updated.llm_model.strip()))
            updated.save()
            self.settings = updated
            self.accept()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Настройки", str(exc))


class DownloadJob(QThread):
    progress = Signal(float, float)
    done = Signal(str)

    def __init__(self, url, target):
        super().__init__()
        self.url, self.target = url, target
        self.stop = threading.Event()

    def run(self):
        try:
            local_llm.download(
                self.url,
                self.target,
                progress=lambda done, total: self.progress.emit(float(done), float(total)),
                cancelled=self.stop.is_set,
            )
            self.done.emit("")
        except local_llm.DownloadCancelled:
            self.done.emit("Загрузка остановлена. Следующая продолжит с того же места.")
        except Exception as exc:
            self.done.emit(str(exc) if isinstance(exc, (ValueError, OSError)) else "Загрузка не удалась.")


def recommended(presets):
    """Largest preset this Mac's memory allows (presets are ordered largest first)."""
    total = round(local_llm.ram_gb())
    return next((key for key, preset in presets.items() if total >= preset.min_ram_gb), list(presets)[-1])


def download_text(done, total):
    if total:
        return f"{done / 1024**3:.2f} из {total / 1024**3:.2f} ГБ"
    return f"{done / 1024**3:.2f} ГБ"


class ModelDownloadDialog(QDialog):
    """One-time resumable download of a model, with a recommendation by RAM."""

    def __init__(self, parent=None, presets=None, title="Модель сводок", intro=None, preferred=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(560, 360)
        self.presets = presets or local_llm.PRESETS
        self.path = None
        self.preset = ""
        self.job = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(12)
        layout.addWidget(label(title, "h2"))
        layout.addWidget(
            label(
                intro
                or "Сводки составляет локальная модель. Её нужно скачать один раз — дальше всё работает "
                "без интернета.",
                "hint",
                wrap=True,
            )
        )
        self.choice = QComboBox()
        best = preferred if preferred in self.presets else recommended(self.presets)
        for key, preset in self.presets.items():
            mark = " · рекомендуется" if key == best else ""
            have = " · скачана" if self.target(key).is_file() else ""
            self.choice.addItem(f"{preset.label} — {preset.size_gb:g} ГБ{mark}{have}", key)
        self.choice.setCurrentIndex(max(0, self.choice.findData(best)))
        self.choice.currentIndexChanged.connect(self.describe)
        layout.addWidget(self.choice)
        self.note = label("", "secondary", wrap=True)
        layout.addWidget(self.note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        layout.addWidget(self.bar)
        self.state = label("", "secondary", wrap=True)
        layout.addWidget(self.state)
        layout.addStretch(1)
        row = QHBoxLayout()
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.close_or_stop)
        self.start_button = primary(QPushButton("Скачать"))
        self.start_button.clicked.connect(self.start)
        row.addStretch(1)
        row.addWidget(self.close_button)
        row.addWidget(self.start_button)
        layout.addLayout(row)
        self.describe()

    def target(self, key):
        return local_llm.models_dir() / self.presets[key].file

    def describe(self, *_):
        preset = self.presets[self.choice.currentData()]
        free = shutil.disk_usage(local_llm.models_dir()).free / 1024**3
        target = self.target(preset.key)
        self.note.setText(
            f"{preset.note}\nНужно {preset.size_gb:g} ГБ на диске, свободно {free:.0f} ГБ. "
            f"Памяти в этом Mac: {local_llm.ram_gb():.0f} ГБ."
        )
        self.start_button.setText("Выбрать" if target.is_file() else "Скачать")

    def start(self):
        preset = self.presets[self.choice.currentData()]
        target = self.target(preset.key)
        if target.is_file():
            self.finish(preset.key, target)
            return
        if shutil.disk_usage(target.parent).free < preset.size_gb * 1024**3 * 1.05:
            QMessageBox.warning(self, "Мало места", "На диске не хватает места для этой модели.")
            return
        self.job = DownloadJob(preset.url, target)
        self.job.progress.connect(self.show_progress)
        self.job.done.connect(lambda error: self.downloaded(error, preset.key, target))
        self.start_button.setEnabled(False)
        self.choice.setEnabled(False)
        self.close_button.setText("Остановить")
        self.state.setText("Подключение…")
        self.job.start()

    def show_progress(self, done, total):
        if total:
            self.bar.setValue(int(done / total * 1000))
        self.state.setText("Скачано " + download_text(done, total))

    def downloaded(self, error, key, target):
        self.job.wait()
        self.job.deleteLater()
        self.job = None
        self.start_button.setEnabled(True)
        self.choice.setEnabled(True)
        self.close_button.setText("Закрыть")
        if error:
            self.state.setText(error)
            return
        self.finish(key, target)

    def finish(self, key, target):
        self.path, self.preset = target, key
        self.accept()

    def close_or_stop(self):
        if self.job:
            self.job.stop.set()
            self.state.setText("Останавливаю…")
        else:
            self.reject()

    def reject(self):
        if self.job:
            self.job.stop.set()
            self.job.wait()
        super().reject()
