import json
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

from PySide6.QtCore import QLockFile, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .config import Settings, data_dir, set_api_key
from .knowledge import stamp
from .live import (
    BOTH,
    MICROPHONE,
    SOURCE_LABELS,
    SYSTEM,
    LiveCaptureError,
    LiveRecorder,
    audio_devices,
    capture_supported,
    system_audio_devices,
)
from .playback import evidence_intervals
from .process import supervise
from .store import Store
from .summary import summary_views
from .summary_browser import SummaryBrowser

PROVIDERS = [
    ("Указать вручную", ""),
    ("OpenAI", "https://api.openai.com/v1"),
    ("OpenRouter", "https://openrouter.ai/api/v1"),
    ("Groq", "https://api.groq.com/openai/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("Together", "https://api.together.xyz/v1"),
    ("Ollama · на этом Mac", "http://localhost:11434/v1"),
    ("LM Studio · на этом Mac", "http://localhost:1234/v1"),
]

STATUS = {
    "new": "Новая",
    "transcribing": "Распознавание",
    "review": "Готова к проверке",
    "retrying": "Повторный проход по сомнительным репликам",
    "summarizing": "Создание сводки",
    "done": "Готово",
    "error": "Ошибка",
    "interrupted": "Приостановлена",
}


class Job(QThread):
    memory = Signal(float)
    result = Signal(str)

    def __init__(self, phase, mid, budget):
        super().__init__()
        self.phase, self.mid, self.budget = phase, mid, budget
        self.stop = threading.Event()

    def run(self):
        try:
            code = supervise(
                [sys.executable, "-m", "samarizator.worker", self.phase, self.mid],
                self.budget,
                callback=lambda rss: self.memory.emit(rss / 1024**3),
                cancelled=self.stop.is_set,
            )
            self.result.emit("" if code == 0 else "worker-error")
        except Exception as exc:
            self.result.emit(str(exc))


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки Samarizator")
        self.resize(700, 650)
        self.settings = settings
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        self.fields = {}
        local = QWidget()
        form = QFormLayout(local)
        tabs.addTab(local, "Распознавание речи · локально")
        intro = QLabel(
            "Whisper работает на вашем Mac и превращает аудио в текст. Аудио никуда не отправляется.\n"
            "Облачная модель со второй вкладки работает только с готовым текстом — она составляет сводку."
        )
        intro.setWordWrap(True)
        form.addRow(intro)
        for key, label in [
            ("whisper_model", "Модель Whisper (.bin)"),
            ("vault", "Папка Obsidian"),
        ]:
            line = QLineEdit(str(getattr(settings, key)))
            row = QHBoxLayout()
            row.addWidget(line)
            button = QPushButton("Выбрать…")
            button.clicked.connect(lambda checked=False, k=key, w=line: self.pick(k, w))
            row.addWidget(button)
            form.addRow(label, row)
            self.fields[key] = line
        memory = QDoubleSpinBox()
        memory.setRange(2, 64)
        memory.setSuffix(" ГиБ")
        memory.setValue(settings.memory_gb)
        form.addRow("Бюджет памяти", memory)
        self.fields["memory_gb"] = memory
        for key, label, lo, hi in [
            ("threads", "Потоки CPU", 1, 16),
            ("chunk_seconds", "Фрагмент, секунд", 30, 300),
        ]:
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(getattr(settings, key))
            self.fields[key] = spin
            form.addRow(label, spin)
        language = QLineEdit(settings.language)
        language.setPlaceholderText("ru, en, kk или auto")
        self.fields["language"] = language
        form.addRow("Язык Whisper", language)
        gpu = QCheckBox("Считать на GPU (Metal) вместо CPU")
        gpu.setChecked(settings.gpu)
        self.fields["gpu"] = gpu
        form.addRow("Ускорение", gpu)
        hint = QLabel(
            "Контроль RSS останавливает обработку на 90% бюджета. Это не жёсткая квота ОС.\n"
            "GPU может ускорить распознавание, но память Metal "
            "не попадает в этот подсчёт: на длинных записях бюджет перестаёт быть точной оценкой."
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        form.addRow(QLabel("<b>Live-запись</b>"))
        source = QComboBox()
        for value, label in [
            (MICROPHONE, "Только микрофон"),
            (SYSTEM, "Только системный звук"),
            (BOTH, "Микрофон и системный звук — раздельными каналами"),
        ]:
            source.addItem(label, value)
        source.setCurrentIndex(max(0, source.findData(settings.live_source)))
        self.fields["live_source"] = source
        form.addRow("Источник", source)
        self.inputs, self.loopback = self.live_devices()
        for key, label, only_loopback in [
            ("live_microphone_device", "Устройство микрофона", False),
            ("live_system_device", "Устройство системного звука", True),
        ]:
            box = QComboBox()
            box.setEditable(True)
            box.addItem("По умолчанию", "")
            for _, name in (self.loopback if only_loopback else self.inputs):
                box.addItem(name, name)
            saved = getattr(settings, key)
            found = box.findData(saved)
            if saved and found < 0:
                box.addItem(saved, saved)
                found = box.count() - 1
            box.setCurrentIndex(max(0, found))
            self.fields[key] = box
            form.addRow(label, box)
        live_hint = QLabel(
            "Системный звук macOS не отдаёт как вход сама: нужно устройство петли (BlackHole, "
            "Loopback или агрегатное устройство в «Настройке Audio-MIDI»), выбранное выходом звука. "
            "Обход корпоративного запрета приложение не выполняет — согласуйте установку с IT.\n"
            "Оба источника пишутся в один WAV: канал 1 — микрофон, канал 2 — системный звук, "
            "на общей шкале времени. Whisper сводит каналы в моно, оба голоса попадают в текст.\n"
            "При выводе в динамики микрофон повторно захватит удалённую речь — используйте наушники."
        )
        live_hint.setWordWrap(True)
        form.addRow(live_hint)
        if self.inputs and not self.loopback:
            missing = QLabel(
                "Устройства петли сейчас не видно. Установите его и переоткройте настройки, "
                "либо впишите имя устройства вручную."
            )
            missing.setWordWrap(True)
            form.addRow(missing)
        quality = QWidget()
        qform = QFormLayout(quality)
        tabs.addTab(quality, "Качество и термины")
        profile = QPushButton("Применить профиль M4 Pro · 48 ГБ")
        profile.clicked.connect(self.quality_profile)
        qform.addRow(profile)
        for key, label in [
            ("pause_boundaries", "Сдвигать границы фрагментов к паузам"),
            ("vad", "Локальный VAD · выделение речи"),
        ]:
            field = QCheckBox(label)
            field.setChecked(getattr(settings, key))
            self.fields[key] = field
            qform.addRow(field)
        for key, label in [("vad_model", "Модель VAD (.bin)"), ("glossary", "Термины, имена, аббревиатуры")]:
            field = QLineEdit(getattr(settings, key))
            self.fields[key] = field
            qform.addRow(label, field)
        self.fields["glossary"].setMaxLength(800)
        self.fields["glossary"].setPlaceholderText("Samarizator, Иванов, EBITDA, названия ваших проектов")
        beam = QSpinBox()
        beam.setRange(1, 8)
        beam.setValue(settings.beam_size)
        self.fields["beam_size"] = beam
        qform.addRow("Ширина поиска Whisper", beam)
        hint = QLabel(
            "Профиль: 16 ГиБ, 8 потоков CPU, фрагменты около 90 секунд, VAD и границы по паузам. "
            "Выбранная модель и корпоративный API сохраняются. Для загрузки VAD: ./start.sh --quality.\n\n"
            "Словарь — короткий список ожидаемых слов, а не инструкция. "
            "Он помогает с написанием терминов, но может смещать распознавание. "
            "Если VAD пропускает тихую речь, сравните запись с отключённым VAD."
        )
        hint.setWordWrap(True)
        qform.addRow(hint)
        corporate = QWidget()
        api = QFormLayout(corporate)
        tabs.addTab(corporate, "Облачная модель · сводки")
        self.provider = QComboBox()
        for label, url in PROVIDERS:
            self.provider.addItem(label, url)
        self.provider.setCurrentIndex(max(0, self.provider.findData(settings.base_url)))
        self.provider.activated.connect(self.pick_provider)
        api.addRow("Провайдер", self.provider)
        for key, label in [
            ("base_url", "Base URL"),
            ("model", "Название модели"),
        ]:
            line = QLineEdit(getattr(settings, key))
            self.fields[key] = line
            api.addRow(label, line)
        self.fields["base_url"].setPlaceholderText("https://openrouter.ai/api/v1")
        self.fields["model"].setPlaceholderText("openai/gpt-4o-mini")
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("Пусто — оставить сохранённый ключ")
        api.addRow("API-ключ → macOS Keychain", self.key)
        for key, label, lo, hi in [
            ("input_chars", "Символов текста на запрос", 4000, 48000),
            ("max_output_tokens", "Лимит токенов ответа", 512, 64000),
        ]:
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(getattr(settings, key))
            self.fields[key] = spin
            api.addRow(label, spin)
        hint = QLabel(
            "Подходит любой OpenAI-совместимый Chat Completions API: OpenAI, OpenRouter, Groq, DeepSeek, "
            "Together, а также локальные Ollama и LM Studio по адресу localhost. Ключ уходит "
            "заголовком Authorization: Bearer; для localhost его можно оставить любым непустым.\n"
            "Аудио не отправляется. Текст уходит на этот адрес только при нажатии «Создать сводку». "
            "Размер блока и лимит ответа определяют, сколько токенов расходуется на одну запись."
        )
        hint.setWordWrap(True)
        api.addRow(hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

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
        if not capture_supported():
            return [], []
        try:
            devices = audio_devices(shutil.which("ffmpeg") or "ffmpeg")
        except LiveCaptureError:
            return [], []
        return devices, system_audio_devices(devices)

    def pick_provider(self, index):
        if url := self.provider.itemData(index):
            self.fields["base_url"].setText(url)

    def pick(self, key, widget):
        path = (
            QFileDialog.getExistingDirectory(self, "Папка базы знаний")
            if key == "vault"
            else QFileDialog.getOpenFileName(self, "Выберите локальную модель")[0]
        )
        if path:
            widget.setText(path)

    def save(self):
        try:
            values = asdict(self.settings)
            for key, field in self.fields.items():
                values[key] = (
                    field.currentData()
                    if isinstance(field, QComboBox)
                    else field.isChecked()
                    if isinstance(field, QCheckBox)
                    else field.value()
                    if isinstance(field, (QSpinBox, QDoubleSpinBox))
                    else field.text()
                )
            updated = Settings(**values)
            updated.validate(api=bool(updated.base_url))
            if self.key.text():
                set_api_key(self.key.text())
            updated.save()
            self.settings = updated
            self.accept()
        except Exception as exc:
            message = (
                str(exc)
                if isinstance(exc, ValueError)
                else "Не удалось сохранить ключ в Keychain. Проверьте доступ к связке ключей."
            )
            QMessageBox.warning(self, "Настройки", message)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.store = Store()
        self.store.recover()
        from .worker import cleanup

        cleanup()
        self.job = None
        self.active_id = None
        self.mid = None
        self.page = 0
        self.player_proc = None
        self.playback_queue = deque()
        self.playback_total = 0
        self.playback_mid = None
        self.live_recorder = None
        from .build import build_label

        self.build_label = build_label()
        self.setWindowTitle("Samarizator · " + self.build_label)
        self.resize(1200, 800)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        title = QLabel("Samarizator")
        title.setObjectName("brand")
        top.addWidget(title)
        top.addWidget(QLabel("Локальное аудио  ·  Кратко + подробно  ·  " + self.build_label))
        top.addStretch()
        self.settings_button = QPushButton("Настройки")
        self.settings_button.clicked.connect(self.configure)
        top.addWidget(self.settings_button)
        layout.addLayout(top)
        split = QSplitter()
        layout.addWidget(split, 1)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        self.add_button = QPushButton("+ Добавить аудио или видео")
        self.add_button.clicked.connect(self.add_file)
        side.addWidget(self.add_button)
        self.live_button = QPushButton("● Live: начать запись")
        self.live_button.setToolTip(
            "Записывает локально выбранный в настройках источник: микрофон, системный звук или оба "
            "раздельными каналами. После остановки запись добавляется и запускается распознавание. "
            "Текст и сводка по ходу записи пока не создаются."
        )
        self.live_button.clicked.connect(self.toggle_live)
        side.addWidget(self.live_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск в записях и сводках…")
        self.search.textChanged.connect(self.refresh_list)
        side.addWidget(self.search)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self.select)
        side.addWidget(self.list)
        split.addWidget(sidebar)
        detail = QWidget()
        body = QVBoxLayout(detail)
        self.heading = QLabel("Добавьте запись встречи, лекции или интервью")
        self.heading.setObjectName("heading")
        self.heading.setWordWrap(True)
        body.addWidget(self.heading)
        self.info = QLabel("1. Распознайте локально → 2. Проверьте текст → 3. Создайте сводку")
        self.info.setWordWrap(True)
        body.addWidget(self.info)
        self.error_detail = QLabel()
        self.error_detail.setWordWrap(True)
        self.error_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_detail.setStyleSheet("color: #a52d27;")
        body.addWidget(self.error_detail)
        actions = QHBoxLayout()
        self.transcribe = QPushButton("1. Распознать / продолжить")
        self.transcribe.clicked.connect(lambda: self.start("transcribe"))
        self.retry = QPushButton("Повторить сомнительные реплики")
        self.retry.setToolTip(
            "Второй проход Whisper только по репликам, отмеченным для проверки, "
            "с запасом аудио по краям. Ничего не заменяет автоматически — "
            "вариант нужно принять вручную ниже."
        )
        self.retry.clicked.connect(lambda: self.start("retry"))
        self.summarize = QPushButton("2. Создать сводку")
        self.summarize.clicked.connect(lambda: self.start("summary"))
        self.cancel = QPushButton("Остановить")
        self.cancel.clicked.connect(self.cancel_job)
        for button in [self.transcribe, self.retry, self.summarize, self.cancel]:
            actions.addWidget(button)
        body.addLayout(actions)
        maintenance = QHBoxLayout()
        self.rerun_button = QPushButton("Распознать заново")
        self.rerun_button.setToolTip(
            "Новая запись с текущей моделью и настройками. Старый результат сохранится для сравнения."
        )
        self.rerun_button.clicked.connect(self.rerun_transcription)
        self.copy_error_button = QPushButton("Скопировать ошибку")
        self.copy_error_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.error_detail.text())
        )
        for button in [self.rerun_button, self.copy_error_button]:
            maintenance.addWidget(button)
        body.addLayout(maintenance)
        self.tabs = QTabWidget()
        body.addWidget(self.tabs, 1)
        transcript = QWidget()
        tbox = QVBoxLayout(transcript)
        self.uncertain_only = QCheckBox("Только требующие проверки")
        self.uncertain_only.setToolTip(
            "Показывать только реплики, отмеченные для проверки: граница фрагмента, "
            "низкая уверенность Whisper или возможный повтор на стыке."
        )
        self.uncertain_only.stateChanged.connect(self.toggle_uncertain_filter)
        tbox.addWidget(self.uncertain_only)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Время", "Текст", "Проверка"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            1, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self.selected_segment)
        tbox.addWidget(self.table)
        nav = QHBoxLayout()
        prev = QPushButton("← Назад")
        prev.clicked.connect(lambda: self.turn_page(-1))
        nxt = QPushButton("Дальше →")
        nxt.clicked.connect(lambda: self.turn_page(1))
        self.page_label = QLabel()
        for widget in [prev, self.page_label, nxt]:
            nav.addWidget(widget)
        tbox.addLayout(nav)
        playback = QHBoxLayout()
        self.play_button = QPushButton("▶ Прослушать реплику")
        self.play_button.setToolTip(
            "Открывает исходную запись в ffplay на выбранной реплике, с запасом по 2 с с каждой "
            "стороны. Нужен ffplay (обычно ставится вместе с ffmpeg)."
        )
        self.play_button.clicked.connect(self.play_segment)
        self.stop_button = QPushButton("■ Стоп")
        self.stop_button.clicked.connect(self.stop_playback)
        self.playback_label = QLabel()
        playback.addWidget(self.play_button)
        tbox.addLayout(playback)
        edit = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Исправить выбранную реплику")
        self.save_segment = QPushButton("Сохранить реплику")
        self.save_segment.clicked.connect(self.edit_segment)
        edit.addWidget(self.text, 1)
        edit.addWidget(self.save_segment)
        tbox.addLayout(edit)
        retry_row = QHBoxLayout()
        self.retry_label = QLabel()
        self.retry_label.setWordWrap(True)
        self.accept_retry_button = QPushButton("Принять повторный вариант")
        self.accept_retry_button.clicked.connect(self.accept_retry)
        retry_row.addWidget(self.retry_label, 1)
        retry_row.addWidget(self.accept_retry_button)
        self.undo_retry_button = QPushButton("Отменить принятие")
        self.undo_retry_button.clicked.connect(self.undo_retry)
        retry_row.addWidget(self.undo_retry_button)
        tbox.addLayout(retry_row)
        self.tabs.addTab(transcript, "Расшифровка")
        self.summary = SummaryBrowser(self.store.segment)
        self.tabs.addTab(self.summary, "Кратко · тезисы")
        self.detailed_summary = SummaryBrowser(self.store.segment)
        self.tabs.addTab(self.detailed_summary, "Подробная сводка")
        self.resolved_summary = SummaryBrowser(self.store.segment)
        self.resolved_summary.setToolTip(
            "Итоговый статус решений и задач с учётом более поздних правок и отмен, отдельно "
            "от полной истории в «Подробной сводке» — там ничего не удаляется и не заменяется."
        )
        self.tabs.addTab(self.resolved_summary, "Итог по решениям")
        self.audio_report = QTextBrowser()
        self.tabs.addTab(self.audio_report, "Качество записи")
        for browser in [self.summary, self.detailed_summary, self.resolved_summary]:
            browser.playEvidence.connect(self.play_evidence)
            browser.playGroup.connect(self.play_evidence_group)
        shared_playback = QHBoxLayout()
        shared_playback.addWidget(self.stop_button)
        shared_playback.addWidget(self.playback_label, 1)
        body.addLayout(shared_playback)
        bottom = QHBoxLayout()
        source = QPushButton("Открыть исходную запись")
        source.clicked.connect(self.open_source)
        self.obsidian = QPushButton("Открыть в Obsidian")
        self.obsidian.clicked.connect(self.open_obsidian)
        vault = QPushButton("Папка базы знаний")
        vault.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.settings.vault)))
        self.reexport = QPushButton("Экспортировать снова")
        self.reexport.clicked.connect(lambda: self.start("export"))
        for widget in [source, self.obsidian, vault, self.reexport]:
            bottom.addWidget(widget)
        body.addLayout(bottom)
        split.addWidget(detail)
        split.setSizes([290, 910])
        self.progress = QLabel("Готов к работе. Медиа обрабатывается на этом компьютере.")
        self.progress.setWordWrap(True)
        layout.addWidget(self.progress)
        self.ram = QLabel("Бюджет: " + str(self.settings.memory_gb) + " ГиБ · CPU")
        layout.addWidget(self.ram)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(800)
        self.refresh_list()
        self.controls()

    def configure(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.settings
            self.ram.setText(f"Бюджет: {self.settings.memory_gb:g} ГиБ · CPU")

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Добавить запись",
            "",
            "Аудио и видео (*.mp3 *.mp4 *.m4a *.wav *.mov *.mkv *.webm *.ogg *.flac *.aac);;Все файлы (*)",
        )
        if path:
            self.mid = self.store.create(path, self.settings)
            self.refresh_list()

    def toggle_live(self):
        if self.job:
            return
        if self.live_recorder is not None:
            self.stop_live()
            return
        try:
            recorder = LiveRecorder(
                source=self.settings.live_source,
                microphone_device=self.settings.live_microphone_device,
                system_device=self.settings.live_system_device,
            )
            recorder.start()
        except LiveCaptureError as exc:
            QMessageBox.warning(self, "Live-запись", str(exc))
            return
        self.live_recorder = recorder
        self.stop_playback()
        self.progress.setText(
            f"Live-запись началась ({SOURCE_LABELS[recorder.source]}). "
            "Аудио сохраняется только на этом Mac."
        )
        self.controls()

    def stop_live(self, start_transcription=True):
        recorder = self.live_recorder
        if recorder is None:
            return
        self.live_recorder = None
        try:
            path = recorder.stop()
            mid = self.store.create(path, self.settings)
        except (LiveCaptureError, OSError, ValueError) as exc:
            self.progress.setText(str(exc))
            QMessageBox.warning(self, "Live-запись", str(exc))
            self.controls()
            return
        # `note` holds the exported Obsidian path, so the track layout goes into the title.
        moment = path.stem.removeprefix("live-").rsplit("-", 1)[0]
        layout = " · канал 1 микрофон, канал 2 системный" if recorder.tracks == (MICROPHONE, SYSTEM) else ""
        self.store.update(mid, title=f"Live-запись ({SOURCE_LABELS[recorder.source]}){layout} {moment}")
        self.mid, self.page = mid, 0
        self.refresh_list()
        self.progress.setText("Live-запись сохранена локально.")
        self.controls()
        if start_transcription:
            self.start("transcribe")

    def refresh_list(self):
        current = self.mid
        self.list.blockSignals(True)
        self.list.clear()
        found = False
        for meeting in self.store.meetings(self.search.text()):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, meeting["id"])
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 8, 6, 8)
            label = QLabel(f"{meeting['title']}\n{STATUS.get(meeting['status'], meeting['status'])}")
            label.setWordWrap(True)
            row_layout.addWidget(label, 1)
            trash = QPushButton("🗑")
            trash.setObjectName("trashButton")
            trash.setToolTip("Удалить запись")
            trash.setFixedWidth(30)
            trash.clicked.connect(lambda checked=False, mid=meeting["id"]: self.delete_meeting(mid))
            row_layout.addWidget(trash)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            if meeting["id"] == current:
                self.list.setCurrentItem(item)
                found = True
        self.list.blockSignals(False)
        if current and found:
            self.load_detail()
        else:
            self.mid = None
            self.clear_detail()

    def clear_detail(self):
        self.stop_playback()
        self.heading.setText("Добавьте запись встречи, лекции или интервью")
        self.info.setText("1. Распознайте локально → 2. Проверьте текст → 3. Создайте сводку")
        self.error_detail.clear()
        self.table.setRowCount(0)
        self.visible_rows = []
        self.summary.setPlainText("")
        self.detailed_summary.clear()
        self.resolved_summary.clear()
        self.audio_report.clear()
        self.controls()

    def delete_meeting(self, mid=None):
        mid = mid or self.mid
        if self.job or self.live_recorder is not None or not mid:
            return
        meeting = self.store.meeting(mid)
        confirm = QMessageBox.question(
            self,
            "Удалить запись",
            f"Удалить «{meeting['title']}» из приложения? Расшифровка и сводка будут удалены безвозвратно.\n"
            "Исходный аудио/видео файл и уже экспортированные заметки в Obsidian не удаляются.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(mid)
        if mid == self.mid:
            self.mid = None
        self.refresh_list()

    def select(self, item, previous=None):
        if item:
            self.stop_playback()
            self.mid = item.data(Qt.ItemDataRole.UserRole)
            self.page = 0
            self.load_detail()

    def load_detail(self):
        if not self.mid:
            return
        meeting = self.store.meeting(self.mid)
        self.heading.setText(meeting["title"])
        self.info.setText(
            f"{STATUS.get(meeting['status'], meeting['status'])} · {stamp(meeting['duration'])} · "
            "Проверьте отмеченные места перед созданием сводки."
        )
        self.error_detail.setText(
            "Причина: " + meeting["error"]
            if meeting["error"] and meeting["status"] not in {"transcribing", "summarizing", "retrying"}
            else ""
        )
        self.load_rows()
        if meeting["summary"]:
            result = json.loads(meeting["summary"])
            brief, detailed = summary_views(result)
            refs = {r["id"]: stamp(r["start"]) for r in self.store.iter_segments(self.mid)}
            self.summary.show_summary(self.mid, brief, refs)
            self.detailed_summary.show_summary(self.mid, detailed, refs)
            resolved = detailed.get("resolved") or []
            if resolved:
                self.resolved_summary.show_summary(
                    self.mid,
                    dict(
                        overview=detailed.get("resolution_warning")
                        or "Финальный статус с учётом более поздних правок и отмен.",
                        items=resolved,
                    ),
                    refs,
                )
            else:
                self.resolved_summary.setPlainText(
                    detailed.get("resolution_warning")
                    or "В записи нет решений или задач для согласования, либо сводка создана "
                    "до появления этого раздела — пересоздайте сводку, чтобы получить его."
                )
        else:
            self.summary.setPlainText(
                "После распознавания проверьте текст. Затем нажмите «Создать сводку».\n\n"
                "Будет отправлен только текст на выбранный API модели. "
                "Результат автоматически сохранится в базе знаний."
            )
            self.detailed_summary.setPlainText(self.summary.toPlainText())
            self.resolved_summary.setPlainText(self.summary.toPlainText())
        report = []
        plan = self.store.checkpoint(self.mid, "asr-plan", 0) or []
        for i, (start, end) in enumerate(plan):
            for d in self.store.checkpoint(self.mid, "audio-quality", i) or []:
                channel = f" · канал {d['channel'] + 1}" if d["channel"] is not None else ""
                report.append(
                    f"{stamp(start)}–{stamp(end)}{channel}: RMS {d['rms_dbfs']} dBFS; "
                    + (", ".join(d["warnings"]) or "нет предупреждений об уровне сигнала")
                )
        self.audio_report.setPlainText(
            "Диагностика уровня звука. Это не оценка точности текста и не измерение шума.\n\n"
            + ("\n".join(report) or "Диагностика появится для новой обработки записи.")
        )
        self.controls()

    def load_rows(self):
        rows = self.store.segments(
            self.mid, self.page * 200, 200, uncertain_only=self.uncertain_only.isChecked()
        )
        # A new page/meeting invalidates the old selection and displayed proposal.
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.visible_rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, value in enumerate(
                [
                    stamp(row["start"]),
                    row["text"],
                    self.review_label(row),
                ]
            ):
                self.table.setItem(i, col, QTableWidgetItem(value))
        self.table.blockSignals(False)
        self.selected_segment()
        self.table.resizeRowsToContents()
        self.page_label.setText(f"Страница {self.page + 1} · до 200 реплик")

    def review_label(self, row):
        if not row["uncertain"]:
            return ""
        reasons = [r.strip() for r in (row.get("review") or "").split(",") if r.strip() != "говорящий"]
        label = ", ".join(reasons) or "Проверить"
        if row.get("retry_text"):
            label += "; есть повторный вариант"
        return label

    def turn_page(self, delta):
        if not self.mid:
            return
        new = max(0, self.page + delta)
        only = self.uncertain_only.isChecked()
        if new == 0 or self.store.segments(self.mid, new * 200, 1, uncertain_only=only):
            self.page = new
            self.load_rows()

    def toggle_uncertain_filter(self):
        if not self.mid:
            return
        self.page = 0
        self.load_rows()

    def selected_segment(self):
        index = self.table.currentRow()
        if hasattr(self, "visible_rows") and 0 <= index < len(self.visible_rows):
            row = self.visible_rows[index]
            self.text.setText(row["text"])
            self.retry_label.setText(
                f"Повторный вариант: {row['retry_text']}" if row.get("retry_text") else ""
            )
        else:
            self.text.clear()
            self.retry_label.setText("")
        self.controls()

    def edit_segment(self):
        index = self.table.currentRow()
        if self.job or not self.mid or not 0 <= index < len(self.visible_rows):
            return
        row = self.visible_rows[index]
        self.store.edit_segment(self.mid, row["id"], self.text.text())
        self.load_detail()

    def accept_retry(self):
        index = self.table.currentRow()
        if self.job or not self.mid or not 0 <= index < len(self.visible_rows):
            return
        row = self.visible_rows[index]
        if not row.get("retry_text"):
            return
        try:
            self.store.accept_retry(self.mid, row["id"], expected_text=row["retry_text"])
        except ValueError as exc:
            QMessageBox.warning(self, "Повторный вариант", str(exc))
        self.load_detail()

    def undo_retry(self):
        index = self.table.currentRow()
        if self.job or not self.mid or not 0 <= index < len(self.visible_rows):
            return
        try:
            self.store.undo_retry(self.mid, self.visible_rows[index]["id"])
        except ValueError as exc:
            QMessageBox.information(self, "Отмена принятия", str(exc))
        self.load_detail()

    def play_segment(self):
        index = self.table.currentRow()
        if not self.mid or not 0 <= index < len(self.visible_rows):
            return
        self.play_row(self.visible_rows[index], pad=2.0)

    def play_evidence(self, mid, sid):
        if mid != self.mid:
            return
        row = self.store.segment(mid, sid)
        if row is None:
            QMessageBox.information(self, "Реплика недоступна", "Исходная реплика больше не найдена.")
            return
        self.play_row(row, pad=0.0)

    def play_row(self, row, pad=0.0):
        start = max(0, row["start"] - pad)
        self.start_playback([dict(row, start=start, end=row["end"] + pad)])

    def play_evidence_group(self, mid, ids):
        if mid != self.mid:
            return
        rows = [self.store.segment(mid, sid) for sid in dict.fromkeys(ids)]
        if not rows or any(row is None for row in rows):
            QMessageBox.information(self, "Реплика недоступна", "Одна из исходных реплик больше не найдена.")
            return
        intervals = evidence_intervals(rows, duration=self.store.meeting(mid)["duration"])
        self.start_playback(intervals)

    def start_playback(self, intervals):
        if not intervals:
            return
        source = Path(self.store.meeting(self.mid)["source"])
        if not source.is_file():
            QMessageBox.information(
                self, "Запись недоступна", "Исходный аудио- или видеофайл перемещён либо удалён."
            )
            return
        player = shutil.which("ffplay")
        if not player:
            QMessageBox.information(
                self,
                "Прослушивание недоступно",
                "ffplay не найден. Обычно он ставится вместе с ffmpeg (brew install ffmpeg). "
                "Пока можно открыть всю запись кнопкой «Открыть исходную запись» ниже.",
            )
            return
        self.stop_playback()
        self.playback_mid = self.mid
        self.playback_queue = deque(intervals)
        self.playback_total = len(intervals)
        self.playback_source = str(source)
        self.playback_player = player
        self.play_next_excerpt()

    def play_next_excerpt(self):
        if not self.playback_queue or self.playback_mid != self.mid:
            self.stop_playback()
            return
        row = self.playback_queue.popleft()
        start = row["start"]
        duration = max(0.1, row["end"] - start)
        try:
            self.player_proc = subprocess.Popen(
                [
                    self.playback_player,
                    "-nodisp",
                    "-autoexit",
                    "-ss",
                    str(start),
                    "-t",
                    str(duration),
                    self.playback_source,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError:
            self.stop_playback()
            QMessageBox.information(self, "Прослушивание недоступно", "Не удалось запустить ffplay.")
            return
        part = self.playback_total - len(self.playback_queue)
        position = f" · {part}/{self.playback_total}" if self.playback_total > 1 else ""
        self.playback_label.setText(f"Играет {stamp(start)}–{stamp(start + duration)}{position}…")
        self.controls()

    def stop_playback(self):
        self.playback_queue.clear()
        self.playback_mid = None
        if self.player_proc and self.player_proc.poll() is None:
            self.player_proc.terminate()
        self.player_proc = None
        self.playback_label.setText("")
        self.stop_button.setEnabled(False)

    def rerun_transcription(self):
        if self.job or not self.mid:
            return
        old = self.store.meeting(self.mid)
        try:
            new_id = self.store.create(old["source"], self.settings)
        except (OSError, ValueError):
            QMessageBox.warning(self, "Запись недоступна", "Исходный файл перемещён. Добавьте его заново.")
            return
        self.store.update(new_id, title=old["title"] + " · заново")
        self.mid, self.page = new_id, 0
        self.refresh_list()
        self.start("transcribe")

    def start(self, phase):
        if self.job or self.live_recorder is not None or not self.mid:
            return
        try:
            meeting = self.store.meeting(self.mid)
            original = Settings.from_dict(json.loads(meeting["settings"]))
            # Preserve transcription chunk geometry on resume.
            if phase == "transcribe":
                if not self.store.checkpoint(self.mid, "source", 0):
                    original = Settings(**asdict(self.settings))
                else:
                    for key in ["memory_gb", "threads", "gpu"]:
                        setattr(original, key, getattr(self.settings, key))
            elif phase == "retry":
                if not self.store.checkpoint(self.mid, "asr_complete", 0):
                    raise ValueError("Сначала завершите распознавание всей записи.")
                # Same rule as resuming transcription: the ASR model/VAD/beam stay
                # exactly as recorded, only memory/threads/GPU follow current settings.
                for key in ["memory_gb", "threads", "gpu"]:
                    setattr(original, key, getattr(self.settings, key))
            else:
                for key in [
                    "base_url",
                    "model",
                    "input_chars",
                    "max_output_tokens",
                    "vault",
                    "memory_gb",
                ]:
                    setattr(original, key, getattr(self.settings, key))
                if phase == "summary":
                    original.validate(api=True)
                    if not self.store.checkpoint(self.mid, "asr_complete", 0):
                        raise ValueError("Сначала завершите распознавание всей записи.")
            self.store.update(
                self.mid,
                settings=json.dumps(asdict(original)),
                status={"transcribe": "transcribing", "retry": "retrying"}.get(phase, "summarizing"),
                error=None,
            )
            self.active_id = self.mid
            self.job = Job(phase, self.mid, original.memory_gb)
            self.job.memory.connect(
                lambda rss: self.ram.setText(
                    f"RSS приложения и обработчиков: {rss:.2f} ГиБ / {original.memory_gb:g} ГиБ · CPU"
                )
            )
            self.job.result.connect(self.job_result)
            self.job.finished.connect(self.job_finished)
            self.job.start()
            self.controls()
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось запустить", str(exc))

    def job_result(self, error):
        if error and error != "worker-error":
            self.store.update(self.active_id, status="interrupted", error=error)
        elif error == "worker-error":
            meeting = self.store.meeting(self.active_id)
            if meeting["status"] != "error":
                self.store.update(
                    self.active_id,
                    status="error",
                    error="Обработчик аварийно завершился. Можно повторить запуск.",
                )

    def job_finished(self):
        meeting = self.store.meeting(self.active_id)
        if self.job.phase == "summary" and meeting["summary"] and self.mid == self.active_id:
            self.tabs.setCurrentWidget(self.summary)
        self.progress.setText(meeting["error"] or "Готово. Результат сохранён.")
        self.job.deleteLater()
        self.job = None
        self.active_id = None
        self.refresh_list()
        self.controls()

    def cancel_job(self):
        if self.job:
            self.job.stop.set()
            self.progress.setText("Остановка обработчиков…")

    def poll(self):
        if self.live_recorder is not None:
            if self.live_recorder.recording:
                self.progress.setText(
                    f"● Live-запись · {SOURCE_LABELS[self.live_recorder.source]} · "
                    f"{stamp(self.live_recorder.elapsed)} · "
                    "нажмите кнопку ещё раз для остановки"
                )
            else:
                self.stop_live(start_transcription=False)
        if self.active_id:
            meeting = self.store.meeting(self.active_id)
            self.progress.setText(meeting["error"] or "Подготовка…")
        if self.player_proc and self.player_proc.poll() is not None:
            code = self.player_proc.poll()
            self.player_proc = None
            if code != 0:
                self.stop_playback()
                self.playback_label.setText("Ошибка воспроизведения. Проверьте исходный файл и аудиовыход.")
            elif self.playback_queue:
                self.play_next_excerpt()
            else:
                self.stop_playback()
            self.controls()

    def controls(self):
        recording = self.live_recorder is not None
        busy = self.job is not None or recording
        ready = self.mid is not None
        self.settings_button.setEnabled(not busy)
        self.add_button.setEnabled(not busy)
        self.search.setEnabled(not busy)
        self.list.setEnabled(not busy)
        self.live_button.setEnabled(self.job is None)
        self.live_button.setText("■ Live: остановить и распознать" if recording else "● Live: начать запись")
        complete = ready and bool(self.store.checkpoint(self.mid, "asr_complete", 0))
        self.transcribe.setText("Распознавание завершено" if complete else "1. Распознать / продолжить")
        self.transcribe.setEnabled(ready and not busy and not complete)
        self.rerun_button.setEnabled(ready and not busy)
        self.copy_error_button.setEnabled(bool(self.error_detail.text()))
        self.retry.setEnabled(ready and not busy and bool(self.store.checkpoint(self.mid, "asr_complete", 0)))
        self.summarize.setEnabled(bool(complete) and not busy)
        self.cancel.setEnabled(busy)
        self.save_segment.setEnabled(ready and not busy)
        index = self.table.currentRow()
        has_retry = (
            ready
            and hasattr(self, "visible_rows")
            and 0 <= index < len(self.visible_rows)
            and bool(self.visible_rows[index].get("retry_text"))
        )
        self.accept_retry_button.setEnabled(has_retry and not busy)
        has_row = ready and hasattr(self, "visible_rows") and 0 <= index < len(self.visible_rows)
        self.undo_retry_button.setEnabled(bool(has_row) and not busy)
        self.play_button.setEnabled(has_row)
        self.stop_button.setEnabled(bool(self.player_proc) and self.player_proc.poll() is None)
        self.obsidian.setEnabled(
            ready
            and bool(self.store.meeting(self.mid)["note"])
            and bool(self.store.meeting(self.mid)["summary"])
        )
        self.reexport.setEnabled(ready and not busy and bool(self.store.meeting(self.mid)["summary"]))

    def open_source(self):
        if self.mid:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.store.meeting(self.mid)["source"]))

    def open_obsidian(self):
        if not self.mid or not (note := self.store.meeting(self.mid)["note"]):
            return
        # obsidian://open?path=<absolute path> only resolves if that exact path matches a
        # vault Obsidian already knows about. On macOS that absolute path very often does not
        # match: iCloud Drive's "Desktop & Documents Folders" sync turns ~/Documents into a
        # symlink, so Path.resolve() (used both here and when the note was exported) yields
        # .../Library/Mobile Documents/com~apple~CloudDocs/..., not the ~/Documents path the
        # user picked and Obsidian registered the vault under. vault=<name>&file=<relative
        # path> sidesteps this: Obsidian just needs a vault already open under that name, and
        # resolves the file relative to it the same way it resolves a wikilink.
        vault_root = Path(self.settings.vault).expanduser().resolve()
        try:
            relative = Path(note).resolve().relative_to(vault_root)
        except ValueError:
            QMessageBox.warning(
                self,
                "Не удалось открыть в Obsidian",
                "Путь заметки не совпадает с текущим хранилищем в настройках. "
                "Нажмите «Экспортировать снова», затем повторите попытку.",
            )
            return
        file_param = relative.with_suffix("").as_posix()
        url = f"obsidian://open?vault={quote(vault_root.name, safe='')}&file={quote(file_param, safe='')}"
        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.warning(
                self,
                "Не удалось открыть в Obsidian",
                "Проверьте, что Obsidian установлен и хранилище хотя бы раз было открыто "
                f"внутри самого приложения Obsidian (Файл → Открыть хранилище → «{vault_root.name}»). "
                "Без этого шага ссылка obsidian:// не находит хранилище, даже если папка есть на диске.",
            )

    def closeEvent(self, event):
        if self.live_recorder is not None:
            self.stop_live(start_transcription=False)
        if self.job:
            self.job.stop.set()
            if not self.job.wait(5000):
                event.ignore()
                return
        self.stop_playback()
        event.accept()


def main():
    os.umask(0o077)
    app = QApplication(sys.argv)
    app.setApplicationName("Samarizator")
    app.setStyle("Fusion")
    palette = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#f4f5f7"),
        (QPalette.ColorRole.WindowText, "#223e35"),
        (QPalette.ColorRole.Base, "#ffffff"),
        (QPalette.ColorRole.AlternateBase, "#f0f4f1"),
        (QPalette.ColorRole.Text, "#223e35"),
        (QPalette.ColorRole.Button, "#e3ece8"),
        (QPalette.ColorRole.ButtonText, "#173e35"),
        (QPalette.ColorRole.Highlight, "#d9e9e2"),
        (QPalette.ColorRole.HighlightedText, "#163f33"),
    ]:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    lock = QLockFile(str(data_dir() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Samarizator", "Приложение уже запущено.")
        return 0
    app.setStyleSheet("""
        QWidget { font-size: 13px; }
        QMainWindow { background: #f4f5f7; }
        QLabel#brand { font-size: 25px; font-weight: 700; color: #185c50; padding: 14px 10px; }
        QLabel#heading { font-size: 21px; font-weight: 600; padding: 14px 0; }
        QPushButton { padding: 8px 12px; border-radius: 6px; background: #e3ece8; color: #173e35; }
        QPushButton:hover { background: #ccded5; }
        QPushButton:disabled { color: #87968f; background: #edf0ee; }
        QLineEdit { padding: 7px; }
        QListWidget, QTableWidget, QTextBrowser { background: white; border: 1px solid #d9dfdb; }
        QListWidget::item:selected { background: #d9e9e2; color: #163f33; }
        QPushButton#trashButton { padding: 4px; background: transparent; border-radius: 4px; }
        QPushButton#trashButton:hover { background: #f0d3d3; }
    """)
    window = Window()
    window.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
