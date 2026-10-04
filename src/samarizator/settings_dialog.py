"""Settings in the style of macOS System Settings, and the one-time model download."""

import shutil
import threading
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QRectF, QSize, Qt, QThread, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
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
    QRadioButton,
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
from .theme import ACCENT, pixmap, set_flag
from .widgets import label, primary, separator, tag

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


def model_line(path, presets=None, settings=None):
    """«Gemma 3 12B · 7,3 ГБ на диске · ≈ 12 ГБ памяти при работе», or ("", False)."""
    file = Path(path).expanduser() if path else None
    if not file or not file.is_file():
        return "", False
    preset = next((p for p in (presets or local_llm.PRESETS).values() if p.file == file.name), None)
    if preset:
        return f"{preset_name(preset)} · {preset_facts(preset, settings)}", True
    return f"{file.stem} · {gb(file.stat().st_size / 1024**3)} ГБ на диске", True


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
        whisper_menu.addAction("Скачать модель…", self.download_whisper)
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
        manage = QPushButton("Управлять…")
        manage.clicked.connect(self.manage_models)
        self.models_detail = models.row("Скачанные модели", manage, subtitle="")
        self.describe_storage()
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
            subtitle="Metal: в разы быстрее и холоднее, чем на процессоре. Выключайте только при сбоях.",
        )
        self.fields["clean_input"] = Switch(s.clean_input)
        resources.row(
            "Чистить расшифровку для сводки",
            self.fields["clean_input"],
            subtitle="Модель читает текст без «эээ», повторов и слов-паразитов — быстрее. Расшифровка не меняется.",
        )
        self.fields["cool_down"] = Switch(s.cool_down)
        resources.row(
            "Беречь Mac от перегрева",
            self.fields["cool_down"],
            subtitle="Если macOS сообщает о сильном нагреве, обработка делает паузы, пока Mac не остынет.",
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
        name = Path(self.fields["llm_model"].text()).name
        preferred = next((k for k, p in local_llm.PRESETS.items() if p.file == name), None)
        dialog = ModelDownloadDialog(self, preferred=preferred)
        if dialog.exec() and dialog.path:
            self.fields["llm_model"].setText(str(dialog.path))
            self.fields["llm_preset"].setText(dialog.preset)

    def download_whisper(self):
        from .setup_models import WHISPER_PRESETS, whisper_memory_gb

        name = Path(self.fields["whisper_model"].text()).name
        dialog = ModelDownloadDialog(
            self,
            WHISPER_PRESETS,
            "Модель распознавания",
            "Крупные модели точнее на живой речи, но медленнее и занимают больше памяти. Новые записи "
            "распознаются выбранной моделью, старые — через «Распознать заново».",
            preferred=next((k for k, p in WHISPER_PRESETS.items() if p.file == name), None),
        )
        if dialog.exec() and dialog.path:
            self.fields["whisper_model"].setText(str(dialog.path))
            # The transcription refuses a model that does not fit the memory budget.
            need = whisper_memory_gb(dialog.path)
            if self.fields["memory_gb"].value() < need:
                self.fields["memory_gb"].setValue(need)

    def describe_llm(self):
        line, ready = model_line(self.fields["llm_model"].text().strip(), settings=self.settings)
        self.llm_state.setText("● Готова" if ready else "● Не скачана")
        self.llm_state.setStyleSheet(f"color: {'#1b6b33' if ready else '#c46b00'}; font-size: 12px;")
        if hasattr(self, "llm_detail") and self.llm_detail:
            self.llm_detail.setText(line or "Скачайте модель — это нужно один раз.")
            self.llm_detail.setVisible(True)

    def describe_whisper(self):
        from .setup_models import WHISPER_PRESETS

        line, ready = model_line(self.fields["whisper_model"].text().strip(), WHISPER_PRESETS)
        self.whisper_state.setText("● Готова" if ready else "● Не скачана")
        self.whisper_state.setStyleSheet(f"color: {'#1b6b33' if ready else '#c46b00'}; font-size: 12px;")
        if hasattr(self, "whisper_detail") and self.whisper_detail:
            self.whisper_detail.setText(line or "Скачайте модель — это нужно один раз.")
            self.whisper_detail.setVisible(True)

    def describe_storage(self):
        files = downloaded_models()
        total = sum(size for _, size in files)
        self.models_detail.setText(
            f"{len(files)} {plural(len(files), 'файл', 'файла', 'файлов')} · {gb(total / 1024**3)} ГБ на диске"
            if files
            else "Пока ничего не скачано."
        )
        self.models_detail.setVisible(True)

    def manage_models(self):
        in_use = {key: self.fields[key].text().strip() for key in ("llm_model", "whisper_model", "vad_model")}
        dialog = ModelsDialog(in_use, self)
        dialog.exec()
        # A deleted model stops being the selected one; the app offers a download instead.
        for key in dialog.removed_in_use:
            self.fields[key].setText("")
            if key == "llm_model":
                self.fields["llm_preset"].setText("")
            if key == "vad_model":
                self.fields["vad"].setChecked(False)
        self.describe_storage()

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


def plural(count, one, few, many):
    if count % 10 == 1 and count % 100 != 11:
        return one
    if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
        return few
    return many


def downloaded_models():
    """[(path, bytes)] of model files and unfinished downloads in the app's models folder."""
    folder = local_llm.models_dir()
    files = [
        path for path in folder.iterdir() if path.is_file() and path.suffix in {".gguf", ".bin", ".part"}
    ]
    return sorted(((path, path.stat().st_size) for path in files), key=lambda pair: -pair[1])


def describe_model_file(path):
    """(name, role) a person recognises: the preset name when the file is one of ours."""
    from .setup_models import WHISPER_PRESETS

    if path.suffix == ".part":
        name = path.name.removesuffix(".part")
        return f"Недокачанная загрузка · {name}", "Можно удалить: загрузка начнётся заново"
    for presets in (local_llm.PRESETS, WHISPER_PRESETS):
        preset = next((p for p in presets.values() if p.file == path.name), None)
        if preset:
            return preset_name(
                preset
            ), "Сводки и вопросы" if presets is local_llm.PRESETS else "Распознавание речи"
    if path.suffix == ".gguf":
        return path.stem, "Сводки и вопросы"
    if "silero" in path.name or "vad" in path.name.lower():
        return path.stem, "Выделение речи (VAD)"
    return path.stem, "Распознавание речи"


class ModelsDialog(QDialog):
    """Downloaded models with their size; any of them can be deleted to free the disk."""

    ROLES = {"llm_model": "модель сводок", "whisper_model": "модель распознавания", "vad_model": "модель VAD"}

    def __init__(self, in_use, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Скачанные модели")
        self.setMinimumWidth(560)
        self.in_use = {key: value for key, value in in_use.items() if value}
        self.removed_in_use = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(12)
        layout.addWidget(label("Скачанные модели", "h2"))
        layout.addWidget(
            label(
                "Модели занимают место на диске. Удалённую модель можно скачать снова в любой момент; "
                "записи и сводки при этом не пропадают.",
                "hint",
                wrap=True,
            )
        )
        self.list = QVBoxLayout()
        self.list.setSpacing(0)
        holder = QWidget()
        holder.setObjectName("optionList")
        holder.setLayout(self.list)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        scroll.setMinimumHeight(240)
        layout.addWidget(scroll, 1)
        self.total = label("", "secondary", wrap=True)
        layout.addWidget(self.total)
        row = QHBoxLayout()
        folder = QPushButton("Показать в Finder")
        folder.clicked.connect(self.reveal)
        row.addWidget(folder)
        row.addStretch(1)
        done = primary(QPushButton("Готово"))
        done.clicked.connect(self.accept)
        row.addWidget(done)
        layout.addLayout(row)
        self.fill()

    def using(self, path):
        return [key for key, value in self.in_use.items() if Path(value).expanduser() == path]

    def fill(self):
        while self.list.count():
            item = self.list.takeAt(0)
            if widget := item.widget():
                widget.setParent(None)
                widget.deleteLater()
        files = downloaded_models()
        group = QFrame()
        group.setObjectName("card")
        group.setStyleSheet("QFrame#card { border-radius: 10px; }")
        rows = QVBoxLayout(group)
        rows.setContentsMargins(0, 0, 0, 0)
        rows.setSpacing(0)
        for index, (path, size) in enumerate(files):
            if index:
                rows.addWidget(separator())
            name, role = describe_model_file(path)
            line = QWidget()
            box = QHBoxLayout(line)
            box.setContentsMargins(14, 10, 14, 10)
            box.setSpacing(12)
            words = QVBoxLayout()
            words.setSpacing(1)
            head = QHBoxLayout()
            head.setSpacing(8)
            title = label(name, wrap=True)
            title.setStyleSheet("font-weight: 600;")
            title.setMinimumWidth(120)
            head.addWidget(title, 1)
            if self.using(path):
                head.addWidget(tag("Используется", "info"))
            words.addLayout(head)
            words.addWidget(label(f"{role} · {gb(size / 1024**3)} ГБ", "secondary"))
            box.addLayout(words, 1)
            remove = QPushButton("Удалить")
            remove.setObjectName("dangerLink")
            remove.setFlat(True)
            remove.clicked.connect(lambda checked=False, p=path: self.remove(p))
            box.addWidget(remove)
            rows.addWidget(line)
        if files:
            self.list.addWidget(group)
        else:
            self.list.addWidget(label("Скачанных моделей нет.", "secondary"))
        self.list.addStretch(1)
        free = shutil.disk_usage(local_llm.models_dir()).free / 1024**3
        used = sum(size for _, size in files) / 1024**3
        self.total.setText(f"Занято моделями: {gb(used)} ГБ · свободно на диске: {free:.0f} ГБ")

    def remove(self, path):
        name, _ = describe_model_file(path)
        roles = self.using(path)
        text = f"Удалить «{name}»? Освободится {gb(path.stat().st_size / 1024**3)} ГБ."
        if roles:
            what = ", ".join(self.ROLES[key] for key in roles)
            text += (
                f"\n\nЭто текущая {what}. Без неё обработка не начнётся, пока вы не скачаете её снова "
                "или не выберете другую."
            )
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning if roles else QMessageBox.Icon.Question)
        box.setWindowTitle("Удалить модель")
        box.setText(text)
        confirm = box.addButton("Удалить", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        if not self.confirm(box, confirm):
            return
        try:
            path.unlink()
            path.with_suffix(path.suffix + ".url").unlink(missing_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, "Удалить модель", f"Не удалось удалить файл: {exc.strerror}")
            return
        for key in roles:
            self.removed_in_use.append(key)
            self.in_use.pop(key, None)
        self.fill()

    @staticmethod
    def confirm(box, button):
        box.exec()
        return box.clickedButton() is button

    def reveal(self):
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(str(local_llm.models_dir())))


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


def recommended(presets, settings=None):
    """Largest preset that suits this Mac (presets are ordered largest first)."""
    total = local_llm.ram_gb()
    return next(
        (key for key, preset in presets.items() if local_llm.fit(preset, settings, total) == "ok"),
        list(presets)[-1],
    )


def gb(value):
    """«7,3» / «12» — gigabytes the way a Mac shows them."""
    text = f"{value:.0f}" if value >= 10 else f"{value:.1f}".removesuffix(".0")
    return text.replace(".", ",")


def preset_name(preset):
    name = preset.label.split("·")[-1].strip()
    return f"Whisper {name}" if preset.file.startswith("ggml-") else name


def preset_facts(preset, settings=None):
    """«7,3 ГБ на диске · ≈ 12 ГБ памяти при работе»."""
    ram = local_llm.preset_ram_gb(preset, settings)
    return f"{gb(preset.size_gb)} ГБ на диске · ≈ {gb(ram)} ГБ памяти при работе"


FIT_TEXT = {
    "ok": ("#1b6b33", "✓ Подходит этому Mac"),
    "slow": ("#8a4b00", "Будет работать медленно: лучше от {min} ГБ памяти"),
    "no": ("#a1001a", "Не поместится в память этого Mac ({total} ГБ)"),
}


class ModelOption(QFrame):
    """One model as a selectable card: what it is, its size, its memory, whether it fits."""

    def __init__(self, preset, settings, total, best, downloaded):
        super().__init__()
        self.setObjectName("modelOption")
        self.key = preset.key
        self.fit = local_llm.fit(preset, settings, total)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 12, 16, 12)
        row.setSpacing(12)
        self.radio = QRadioButton()
        row.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignTop)
        column = QVBoxLayout()
        column.setSpacing(3)
        head = QHBoxLayout()
        head.setSpacing(8)
        name = label(preset_name(preset))
        name.setStyleSheet("font-weight: 600; font-size: 14px;")
        head.addWidget(name)
        kind = preset.label.split("·")[0].strip()
        if kind:
            head.addWidget(label(kind, "secondary"))
        head.addStretch(1)
        if preset.key == best:
            head.addWidget(tag("Рекомендуется", "info"))
        if downloaded:
            head.addWidget(tag("Скачана", "ok"))
        column.addLayout(head)
        column.addWidget(label(preset.note, "secondary", wrap=True))
        facts = label(preset_facts(preset, settings))
        facts.setObjectName("facts")
        column.addWidget(facts)
        color, text = FIT_TEXT[self.fit]
        verdict = label(text.format(min=preset.min_ram_gb, total=f"{total:.0f}"))
        verdict.setStyleSheet(f"color: {color}; font-size: 12px; font-weight: 600;")
        column.addWidget(verdict)
        row.addLayout(column, 1)
        self.radio.toggled.connect(lambda on: set_flag(self, "selected", on))
        self.setEnabled(self.fit != "no")

    def mousePressEvent(self, event):
        if self.isEnabled():
            self.radio.setChecked(True)
        super().mousePressEvent(event)


def download_text(done, total):
    if total:
        return f"{done / 1024**3:.2f} из {total / 1024**3:.2f} ГБ"
    return f"{done / 1024**3:.2f} ГБ"


class ModelDownloadDialog(QDialog):
    """One-time resumable download of a model: every option with its size and memory."""

    def __init__(
        self, parent=None, presets=None, title="Модель сводок", intro=None, preferred=None, settings=None
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(600)
        self.presets = presets or local_llm.PRESETS
        self.settings = settings or getattr(parent, "settings", None) or Settings()
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
                or "Сводки и ответы на вопросы готовит локальная модель. Её нужно скачать один раз — "
                "дальше всё работает без интернета.",
                "hint",
                wrap=True,
            )
        )
        total = local_llm.ram_gb()
        free = shutil.disk_usage(local_llm.models_dir()).free / 1024**3
        self.mac = label(
            f"Этот Mac: {total:.0f} ГБ памяти · свободно на диске {free:.0f} ГБ. Модели работают "
            "по очереди, поэтому память нужна под одну из них за раз.",
            "secondary",
            wrap=True,
        )
        layout.addWidget(self.mac)
        best = preferred if preferred in self.presets else recommended(self.presets, self.settings)
        self.group = QButtonGroup(self)
        self.options = {}
        # The list scrolls when there are more models than fit on a laptop screen.
        holder = QWidget()
        holder.setObjectName("optionList")
        cards = QVBoxLayout(holder)
        cards.setContentsMargins(0, 0, 0, 0)
        cards.setSpacing(10)
        for key, preset in self.presets.items():
            option = ModelOption(preset, self.settings, total, best, self.target(key).is_file())
            self.group.addButton(option.radio)
            self.options[key] = option
            cards.addWidget(option)
        cards.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        holder.adjustSize()
        scroll.setMinimumHeight(min(holder.sizeHint().height() + 4, 470))
        layout.addWidget(scroll, 1)
        start = (
            best
            if self.options[best].isEnabled()
            else next((k for k, o in self.options.items() if o.isEnabled()), best)
        )
        self.options[start].radio.setChecked(True)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        self.bar.setTextVisible(False)
        self.bar.setVisible(False)
        layout.addWidget(self.bar)
        self.state = label("", "secondary", wrap=True)
        layout.addWidget(self.state)
        row = QHBoxLayout()
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.close_or_stop)
        self.start_button = primary(QPushButton("Скачать"))
        self.start_button.clicked.connect(self.start)
        row.addStretch(1)
        row.addWidget(self.close_button)
        row.addWidget(self.start_button)
        layout.addSpacing(4)
        layout.addLayout(row)
        self.group.buttonToggled.connect(self.describe)
        self.describe()

    def selected(self):
        return next((key for key, option in self.options.items() if option.radio.isChecked()), None)

    def target(self, key):
        return local_llm.models_dir() / self.presets[key].file

    def describe(self, *_):
        key = self.selected()
        if key is None:
            return
        preset = self.presets[key]
        ready = self.target(key).is_file()
        self.start_button.setText("Выбрать" if ready else f"Скачать {gb(preset.size_gb)} ГБ")
        self.start_button.setEnabled(self.options[key].isEnabled() and self.job is None)

    def lock(self, busy):
        for option in self.options.values():
            option.setEnabled(not busy and option.fit != "no")
        self.start_button.setEnabled(not busy)
        self.close_button.setText("Остановить" if busy else "Закрыть")
        self.bar.setVisible(busy or self.bar.value() > 0)

    def start(self):
        key = self.selected()
        if key is None:
            return
        preset = self.presets[key]
        target = self.target(key)
        if target.is_file():
            self.finish(key, target)
            return
        if shutil.disk_usage(target.parent).free < preset.size_gb * 1024**3 * 1.05:
            QMessageBox.warning(
                self,
                "Мало места",
                f"Для этой модели нужно {gb(preset.size_gb)} ГБ свободного места на диске.",
            )
            return
        self.job = DownloadJob(preset.url, target)
        self.job.progress.connect(self.show_progress)
        self.job.done.connect(lambda error: self.downloaded(error, key, target))
        self.lock(True)
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
        self.lock(False)
        self.describe()
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
