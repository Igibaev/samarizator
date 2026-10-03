import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import asdict
from datetime import datetime, timedelta
from pathlib import Path

from PySide6.QtCore import QLockFile, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import local_llm, obsidian
from . import progress as work
from .bundle import python_command, tool
from .chat import ChatEngine, ChatPanel, ChatWorker
from .config import Settings, data_dir
from .knowledge import stamp
from .live import (
    SOURCE_LABELS,
    LiveCaptureError,
    LiveRecorder,
    describe_tracks,
    recording_title,
)
from .pages import FinalPage, ProcessingPage, StatePage, WelcomePage, app_icon
from .playback import evidence_intervals
from .process import supervise
from .settings_dialog import (
    DownloadJob,
    ModelDownloadDialog,
    SettingsDialog,
    download_text,
    gb,
    preset_facts,
    preset_name,
    recommended,
)
from .store import Store
from .summary_page import SummaryPage
from .summary_prompts import DEFAULT_FORMAT, FINAL_FORMATS
from .theme import ACCENT, GREEN, GREY, ORANGE, RED, SECONDARY, apply_theme, icon
from .widgets import Banner, RecordingDelegate, SegmentedControl, label, primary

__all__ = ["ModelDownloadDialog", "SettingsDialog", "Window", "apply_theme", "main"]

STATUS = {
    "new": "Новая",
    "recording": "Идёт запись",
    "transcribing": "Распознавание",
    "review": "Готова к проверке",
    "retrying": "Повторный проход по сомнительным репликам",
    "summarizing": "Создание сводки",
    "done": "Готово",
    "error": "Ошибка",
    "interrupted": "Приостановлена",
}


NEXT_STEP = {
    "new": "Нажмите «Распознать» — текст создаётся на этом Mac, аудио никуда не уходит.",
    "recording": "Идёт запись, готовые фрагменты распознаются по ходу. "
    "Нажмите «Остановить запись», когда встреча закончится.",
    "transcribing": "Идёт распознавание. Кнопки шагов включатся, когда оно закончится.",
    "retrying": "Идёт повторный проход по отмеченным репликам.",
    "review": "Проверьте отмеченные реплики, при необходимости исправьте текст — затем «Создать сводку».",
    "summarizing": "Локальная модель составляет сводку на этом Mac. Длинная запись — это десятки минут.",
    "done": "Готово. Итоговый текст и сводка — в переключателе сверху.",
    "error": "Шаг не выполнен. Причина ниже; после исправления запустите его заново.",
    "interrupted": "Обработка остановлена. Тот же шаг продолжит с места остановки, "
    "уже готовые фрагменты сохранены.",
}

RUNNING = {"recording", "transcribing", "summarizing", "retrying"}
PAGE_SIZE = 200


def explain(button, enabled, reason=""):
    """Enable a control and say why when it stays off: a dead control must not be a riddle."""
    button.setEnabled(bool(enabled))
    if not enabled and reason:
        button.setToolTip(reason)
    elif not enabled:
        button.setToolTip("")
    return enabled


def day_title(created):
    try:
        day = datetime.fromisoformat(created).astimezone().date()
    except (TypeError, ValueError):
        return ""
    today = datetime.now().astimezone().date()
    if day == today:
        return "Сегодня"
    if day == today - timedelta(days=1):
        return "Вчера"
    months = "января февраля марта апреля мая июня июля августа сентября октября ноября декабря".split()
    return f"{day.day} {months[day.month - 1]}" + ("" if day.year == today.year else f" {day.year}")


def list_status(meeting, complete):
    status = meeting["status"]
    if status == "done":
        return "Готово", GREEN
    if status == "recording":
        return "Идёт запись", RED
    if status in {"transcribing", "summarizing", "retrying"}:
        return work.percent(status, meeting["error"]) or STATUS[status], ACCENT
    if status == "error":
        return "Ошибка", RED
    if status == "review" or complete:
        return "Проверьте текст", ORANGE
    if status == "interrupted":
        return "Приостановлена", GREY
    return "Не распознана", GREY


def duration_text(seconds):
    seconds = int(seconds or 0)
    if not seconds:
        return ""
    hours, rest = divmod(seconds, 3600)
    return f"{hours}:{rest // 60:02}:{rest % 60:02}" if hours else f"{rest // 60}:{rest % 60:02}"


def human_length(seconds):
    minutes = round((seconds or 0) / 60)
    if not minutes:
        return ""
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин" if hours else f"{minutes} мин"


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
                python_command("samarizator.worker", self.phase, self.mid),
                self.budget,
                callback=lambda rss: self.memory.emit(rss / 1024**3),
                cancelled=self.stop.is_set,
            )
            self.result.emit("" if code == 0 else "worker-error")
        except Exception as exc:
            self.result.emit(str(exc))


def dot_icon(color, size=8):
    from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap

    image = QPixmap(size * 2, size * 2)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    painter.drawEllipse(0, 0, size * 2, size * 2)
    painter.end()
    image.setDevicePixelRatio(2.0)
    return QIcon(image)


def tool_button(name, tip, text=""):
    button = QToolButton()
    button.setIcon(icon(name))
    button.setIconSize(QSize(18, 18))
    button.setText(text or tip)
    button.setToolTip(tip)
    button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    button.setCursor(Qt.CursorShape.PointingHandCursor)
    return button


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.store = Store()
        self.store.recover()
        from .worker import cleanup

        cleanup()
        self.job = None
        self.job_started = 0.0
        self.pending_phase = ""
        self.active_id = None
        self.mid = None
        self.page = 0
        self.view = "final"
        self.player_proc = None
        self.playback_queue = deque()
        self.playback_total = 0
        self.playback_mid = None
        self.live_recorder = None
        self.recording_id = None
        self.download_job = None
        self.download_queue = []
        self.chat_engine = ChatEngine(self)
        self.chat_worker = None
        self.times = {}
        self.visible_rows = []
        self.audio_text = ""
        from .build import build_label

        self.build_label = build_label()
        self.setWindowTitle("Samarizator")
        self.setWindowIcon(app_icon(128))
        self.setAcceptDrops(True)
        self.resize(1280, 820)
        self.setMinimumSize(980, 640)
        root = QWidget()
        self.setCentralWidget(root)
        shell = QHBoxLayout(root)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        shell.addWidget(self.build_sidebar())
        shell.addWidget(self.build_content(), 1)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(800)
        self.refresh_list()
        self.controls()

    # -- layout ---------------------------------------------------------------------

    def build_sidebar(self):
        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(270)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(10, 10, 10, 12)
        side.setSpacing(8)
        top = QHBoxLayout()
        top.setSpacing(2)
        brand = label("Samarizator")
        brand.setStyleSheet("font-weight: 700; font-size: 14px; padding-left: 6px;")
        top.addWidget(brand)
        top.addStretch(1)
        self.live_button = tool_button("mic", "Начать live-запись", "● Live: начать запись")
        self.live_button.clicked.connect(self.toggle_live)
        self.add_button = tool_button("plus", "Добавить аудио или видео", "+ Добавить аудио или видео")
        self.add_button.clicked.connect(self.add_file)
        top.addWidget(self.live_button)
        top.addWidget(self.add_button)
        side.addLayout(top)
        self.search = QLineEdit()
        self.search.setObjectName("search")
        self.search.setPlaceholderText("Поиск")
        self.search.addAction(icon("search", SECONDARY, 14), QLineEdit.ActionPosition.LeadingPosition)
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.refresh_list)
        side.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("recordings")
        self.list.setItemDelegate(RecordingDelegate(self.list))
        self.list.setMouseTracking(True)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.list_menu)
        self.list.currentItemChanged.connect(self.select)
        remove = QShortcut(QKeySequence.StandardKey.Delete, self.list)
        remove.setContext(Qt.ShortcutContext.WidgetShortcut)
        remove.activated.connect(lambda: self.delete_meeting())
        side.addWidget(self.list, 1)
        self.model_button = QPushButton("Скачать модель сводок")
        self.model_button.setIcon(icon("download", "#8a4b00"))
        self.model_button.setObjectName("warnRow")
        self.model_button.setToolTip("Один раз: после загрузки сводки создаются без интернета.")
        self.model_button.clicked.connect(self.download_missing)
        side.addWidget(self.model_button)
        bottom = QHBoxLayout()
        bottom.setSpacing(2)
        self.model_row = QPushButton()
        self.model_row.setObjectName("modelRow")
        self.model_row.setIconSize(QSize(8, 8))
        self.model_row.clicked.connect(self.configure)
        bottom.addWidget(self.model_row, 1)
        self.settings_button = tool_button("gear", "Настройки")
        self.settings_button.clicked.connect(self.configure)
        bottom.addWidget(self.settings_button)
        side.addLayout(bottom)
        return sidebar

    def build_content(self):
        content = QWidget()
        content.setObjectName("content")
        body = QVBoxLayout(content)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.build_toolbar())
        notices = QVBoxLayout()
        notices.setContentsMargins(24, 0, 24, 0)
        notices.setSpacing(8)
        self.info = label("", "hint", wrap=True)
        self.info.setContentsMargins(0, 10, 0, 0)
        notices.addWidget(self.info)
        self.error_box = Banner("error")
        self.error_detail = self.error_box.body
        self.error_detail.setStyleSheet("color: #a1001a; font-size: 13px;")
        self.copy_error_button = QPushButton("Скопировать ошибку")
        self.copy_error_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.error_detail.text())
        )
        self.error_box.add(self.copy_error_button)
        notices.addWidget(self.error_box)
        body.addLayout(notices)
        self.stack = QStackedWidget()
        self.welcome = WelcomePage()
        self.welcome.download.connect(self.welcome_download)
        self.welcome.stop_download.connect(lambda: self.download_job and self.download_job.stop.set())
        self.welcome.other_model.connect(self.choose_model)
        self.welcome.add_file.connect(self.add_file)
        self.welcome.record.connect(self.toggle_live)
        self.empty = StatePage()
        self.processing = ProcessingPage()
        self.final_page = FinalPage()
        self.final_page.formatChosen.connect(self.choose_format)
        self.final_page.redo.connect(self.redo_summary)
        self.final_text = self.final_page.browser
        self.summary_page = SummaryPage(self.store.segment)
        self.summary_page.playGroup.connect(self.play_evidence_group)
        self.summary_page.taskToggled.connect(self.toggle_task)
        for page in [self.welcome, self.empty, self.processing, self.final_page, self.summary_page]:
            self.stack.addWidget(page)
        self.stack.addWidget(self.build_transcript())
        middle = QHBoxLayout()
        middle.setSpacing(0)
        middle.addWidget(self.stack, 1)
        self.chat_panel = ChatPanel(self.store.segment)
        self.chat_panel.ask.connect(self.ask_question)
        self.chat_panel.cleared.connect(self.clear_chat)
        self.chat_panel.playGroup.connect(self.play_evidence_group)
        self.chat_panel.setVisible(False)
        middle.addWidget(self.chat_panel)
        body.addLayout(middle, 1)
        self.playbar = QFrame()
        self.playbar.setObjectName("playbar")
        play = QHBoxLayout(self.playbar)
        play.setContentsMargins(14, 8, 8, 8)
        self.playback_label = label("")
        play.addWidget(self.playback_label, 1)
        self.stop_button = QPushButton("■  Стоп")
        self.stop_button.clicked.connect(self.stop_playback)
        play.addWidget(self.stop_button)
        holder = QHBoxLayout()
        holder.setContentsMargins(24, 0, 24, 10)
        holder.addStretch(1)
        holder.addWidget(self.playbar)
        holder.addStretch(1)
        body.addLayout(holder)
        footer = QFrame()
        footer.setObjectName("footer")
        foot = QHBoxLayout(footer)
        foot.setContentsMargins(20, 5, 20, 6)
        self.progress = label("Готов к работе. Медиа обрабатывается на этом компьютере.", "small")
        foot.addWidget(self.progress, 1)
        self.ram = label(f"Бюджет памяти: {self.settings.memory_gb:g} ГиБ", "small")
        foot.addWidget(self.ram)
        body.addWidget(footer)
        return content

    def build_toolbar(self):
        bar = QFrame()
        bar.setObjectName("toolbar")
        bar.setFixedHeight(54)
        line = QHBoxLayout(bar)
        line.setContentsMargins(20, 0, 14, 0)
        line.setSpacing(6)
        titles = QVBoxLayout()
        titles.setSpacing(0)
        self.heading = label("Samarizator", "title")
        self.meta = label("", "small")
        titles.addWidget(self.heading)
        titles.addWidget(self.meta)
        left = QWidget()
        left.setLayout(titles)
        line.addWidget(left, 1)
        self.views = SegmentedControl()
        for key, title in [("final", "Итоговый текст"), ("summary", "Сводка"), ("transcript", "Расшифровка")]:
            self.views.add(key, title)
        self.views.changed.connect(self.set_view)
        self.views.setFixedHeight(30)
        line.addWidget(self.views, 0, Qt.AlignmentFlag.AlignVCenter)
        right = QHBoxLayout()
        right.setSpacing(6)
        right.addStretch(1)
        self.ask_button = QPushButton("Спросить")
        self.ask_button.setObjectName("askButton")
        self.ask_button.setIcon(icon("chat"))
        self.ask_button.setCheckable(True)
        self.ask_button.setToolTip("Вопросы по сводке этой записи — модель отвечает только по ней")
        self.ask_button.toggled.connect(self.toggle_chat)
        right.addWidget(self.ask_button)
        self.copy_final_button = tool_button("copy", "Копировать итоговый текст в Markdown")
        self.copy_final_button.clicked.connect(self.copy_final)
        right.addWidget(self.copy_final_button)
        self.more_button = tool_button("more", "Ещё")
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_button.setMenu(self.build_menu())
        right.addWidget(self.more_button)
        self.cancel = QPushButton("Остановить")
        self.cancel.clicked.connect(self.cancel_job)
        self.stop_live_button = QPushButton("■  Остановить запись")
        self.stop_live_button.clicked.connect(self.toggle_live)
        self.transcribe = QPushButton("Распознать")
        self.transcribe.clicked.connect(lambda: self.start("transcribe"))
        self.summarize = QPushButton("Создать сводку")
        self.summarize.clicked.connect(lambda: self.start("summary"))
        self.obsidian = primary(QPushButton("Открыть в Obsidian"))
        self.obsidian.clicked.connect(self.open_obsidian)
        for button in [self.cancel, self.stop_live_button, self.transcribe, self.summarize, self.obsidian]:
            right.addWidget(button)
        holder = QWidget()
        holder.setLayout(right)
        line.addWidget(holder, 1)
        return bar

    def build_menu(self):
        menu = QMenu(self)
        self.retry_action = menu.addAction("Повторить сомнительные реплики", lambda: self.start("retry"))
        self.regenerate_button = menu.addAction("Пересоздать итоговый текст", lambda: self.start("final"))
        self.redo_action = menu.addAction("Сделать сводку заново…", self.redo_summary)
        self.rerun_button = menu.addAction("Распознать заново…", self.rerun_transcription)
        menu.addSeparator()
        self.source_action = menu.addAction("Открыть исходную запись", self.open_source)
        self.reveal_action = menu.addAction("Показать заметку в Finder", self.reveal_note)
        self.reexport = menu.addAction("Экспортировать в Obsidian заново", lambda: self.start("export"))
        self.vault_action = menu.addAction(
            "Открыть папку заметок",
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.settings.vault)),
        )
        self.audio_action = menu.addAction("Качество звука…", self.show_audio_report)
        menu.addSeparator()
        self.delete_action = menu.addAction("Удалить запись…", lambda: self.delete_meeting())
        return menu

    def build_transcript(self):
        page = QWidget()
        page.setObjectName("pageWhite")
        box = QVBoxLayout(page)
        box.setContentsMargins(24, 16, 24, 12)
        box.setSpacing(12)
        self.review_banner = Banner("warn")
        self.retry = QPushButton("Перепроверить автоматически")
        self.retry.setToolTip(
            "Второй проход Whisper только по отмеченным репликам, с запасом аудио по краям. "
            "Ничего не заменяет само — вариант нужно принять."
        )
        self.retry.clicked.connect(lambda: self.start("retry"))
        self.review_banner.add(self.retry)
        box.addWidget(self.review_banner)
        filters = QHBoxLayout()
        self.filter = SegmentedControl()
        self.all_rows = self.filter.add("all", "Все")
        self.uncertain_only = self.filter.add("flagged", "Проверить")
        self.uncertain_only.toggled.connect(self.filter_toggled)
        filters.addWidget(self.filter)
        filters.addStretch(1)
        self.prev_page = QPushButton("←")
        self.prev_page.clicked.connect(lambda: self.turn_page(-1))
        self.page_label = label("", "secondary")
        self.next_page = QPushButton("→")
        self.next_page.clicked.connect(lambda: self.turn_page(1))
        for widget in [self.prev_page, self.page_label, self.next_page]:
            filters.addWidget(widget)
        box.addLayout(filters)
        self.table = QTableWidget(0, 3)
        self.table.setObjectName("transcript")
        self.table.setHorizontalHeaderLabels(["Время", "Текст", "Проверка"])
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setColumnWidth(0, 80)
        self.table.itemSelectionChanged.connect(self.selected_segment)
        self.table.doubleClicked.connect(lambda: self.play_segment())
        box.addWidget(self.table, 1)
        self.editor = QFrame()
        self.editor.setObjectName("soft")
        edit = QVBoxLayout(self.editor)
        edit.setContentsMargins(14, 12, 14, 12)
        edit.setSpacing(8)
        row = QHBoxLayout()
        self.play_button = QPushButton("▶  Прослушать")
        self.play_button.setToolTip(
            "Исходная запись на выбранной реплике, с запасом по 2 с с каждой стороны."
        )
        self.play_button.clicked.connect(self.play_segment)
        row.addWidget(self.play_button)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Текст выбранной реплики")
        self.text.returnPressed.connect(self.edit_segment)
        row.addWidget(self.text, 1)
        self.save_segment = primary(QPushButton("Сохранить"))
        self.save_segment.clicked.connect(self.edit_segment)
        row.addWidget(self.save_segment)
        edit.addLayout(row)
        retry_row = QHBoxLayout()
        self.retry_label = label("", "secondary", wrap=True)
        retry_row.addWidget(self.retry_label, 1)
        self.accept_retry_button = QPushButton("Принять повторный вариант")
        self.accept_retry_button.clicked.connect(self.accept_retry)
        retry_row.addWidget(self.accept_retry_button)
        self.undo_retry_button = QPushButton("Отменить принятие")
        self.undo_retry_button.clicked.connect(self.undo_retry)
        retry_row.addWidget(self.undo_retry_button)
        edit.addLayout(retry_row)
        box.addWidget(self.editor)
        self.transcript_page = page
        return page

    # -- model and settings -----------------------------------------------------------

    def configure(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.settings
            self.ram.setText(f"Бюджет памяти: {self.settings.memory_gb:g} ГиБ")
            if self.mid:
                self.load_detail()
        self.controls()

    def has_llm(self):
        return bool(self.settings.llm_model.strip()) and Path(self.settings.llm_model).expanduser().is_file()

    def adopt_model(self, path, preset):
        self.settings.llm_model, self.settings.llm_preset = str(path), preset
        try:
            self.settings.save()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Настройки", str(exc))
        self.show_page()
        self.controls()

    def download_model(self):
        """Pick and fetch the summary model; returns True when one is ready."""
        preferred = next(
            (k for k, p in local_llm.PRESETS.items() if p.file == Path(self.settings.llm_model).name), None
        )
        dialog = ModelDownloadDialog(self, preferred=preferred)
        if not (dialog.exec() and dialog.path):
            return self.has_llm()
        self.adopt_model(dialog.path, dialog.preset)
        return self.has_llm()

    def whisper_preset(self):
        """The recognition model the settings name, else the one that suits this Mac."""
        from .setup_models import WHISPER_PRESETS

        name = Path(self.settings.whisper_model).name if self.settings.whisper_model.strip() else ""
        named = next((p for p in WHISPER_PRESETS.values() if p.file == name), None)
        return named or WHISPER_PRESETS[recommended(WHISPER_PRESETS, self.settings)]

    def has_whisper(self):
        return (
            bool(self.settings.whisper_model.strip())
            and Path(self.settings.whisper_model).expanduser().is_file()
        )

    def missing_models(self):
        """[(role, preset)] the app still needs; the bundled ones are never in this list."""
        missing = []
        if not self.has_whisper():
            missing.append(("whisper", self.whisper_preset()))
        if not self.has_llm():
            missing.append(("llm", local_llm.PRESETS[recommended(local_llm.PRESETS, self.settings)]))
        return missing

    def welcome_download(self):
        """The welcome screen's big button: everything missing, one after another, progress right there."""
        queue = self.missing_models()
        if not queue:
            self.show_page()
            return
        need = sum(preset.size_gb for _, preset in queue) * 1024**3 * 1.05
        if shutil.disk_usage(local_llm.models_dir()).free < need:
            QMessageBox.warning(self, "Мало места", "На диске не хватает места для моделей.")
            return
        self.download_queue = queue
        self.download_next()

    def download_next(self):
        role, preset = self.download_queue[0]
        target = local_llm.models_dir() / preset.file
        title = "Модель распознавания" if role == "whisper" else "Модель сводок"
        step = f" · {title.lower()}" + (
            f" ({len(self.download_queue)} осталось)" if len(self.download_queue) > 1 else ""
        )
        if target.is_file():
            self.welcome_downloaded("", role, preset, target)
            return
        self.download_job = DownloadJob(preset.url, target)
        self.download_job.progress.connect(
            lambda done, total: self.welcome.set_download(
                True, "Скачано " + download_text(done, total) + step, done / total if total else None
            )
        )
        self.download_job.done.connect(lambda error: self.welcome_downloaded(error, role, preset, target))
        self.welcome.set_download(True, "Подключение…" + step, 0)
        self.download_job.start()

    def welcome_downloaded(self, error, role, preset, target):
        if self.download_job:
            self.download_job.wait()
            self.download_job.deleteLater()
            self.download_job = None
        if error:
            self.welcome.set_download(False, error)
            return
        if role == "whisper":
            self.adopt_whisper(target)
        else:
            self.adopt_model(target, preset.key)
        self.download_queue = self.download_queue[1:]
        if self.download_queue:
            self.download_next()
        else:
            self.welcome.set_download(False, "")
            self.show_page()

    def adopt_whisper(self, path):
        from .setup_models import whisper_memory_gb

        self.settings.whisper_model = str(path)
        self.settings.memory_gb = min(64, max(self.settings.memory_gb, whisper_memory_gb(path)))
        try:
            self.settings.save()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Настройки", str(exc))

    def download_missing(self):
        """The sidebar's warning row: fetch whatever the app still lacks, one dialog each."""
        roles = [role for role, _ in self.missing_models()]
        if "whisper" in roles:
            self.ensure_whisper()
        if "llm" in roles:
            self.download_model()
        self.show_page()
        self.controls()

    def choose_model(self, role):
        """«Другая» / «Сменить» on the welcome screen."""
        if role == "whisper":
            self.download_whisper()
        else:
            self.download_model()
        self.show_page()
        self.controls()

    def download_whisper(self, intro=None):
        from .setup_models import WHISPER_PRESETS

        dialog = ModelDownloadDialog(
            self,
            WHISPER_PRESETS,
            "Модель распознавания",
            intro
            or "Превращает речь из записи в текст. Крупные модели точнее, но медленнее и занимают больше "
            "памяти. Скачивается один раз — дальше распознавание работает без интернета.",
            preferred=self.whisper_preset().key,
        )
        if dialog.exec() and dialog.path:
            self.adopt_whisper(dialog.path)
            return True
        return False

    def ensure_whisper(self):
        """Before recognition: the model is there, or the user downloads one now."""
        if self.has_whisper():
            return True
        return self.download_whisper(
            "Модели распознавания речи ещё нет на этом Mac. Выберите подходящую и скачайте её один раз — "
            "дальше распознавание работает без интернета."
        )

    def model_rows(self):
        """What the welcome screen lists: each model, its size and memory, ready or not."""
        from .setup_models import WHISPER_PRESETS

        rows = []
        for role, caption, presets, path, wanted in (
            (
                "whisper",
                "Распознавание речи",
                WHISPER_PRESETS,
                self.settings.whisper_model,
                self.whisper_preset,
            ),
            (
                "llm",
                "Сводки и вопросы по ним",
                local_llm.PRESETS,
                self.settings.llm_model,
                lambda: local_llm.PRESETS[recommended(local_llm.PRESETS, self.settings)],
            ),
        ):
            file = Path(path).expanduser() if path.strip() else None
            if file and file.is_file():
                preset = next((p for p in presets.values() if p.file == file.name), None)
                if preset:
                    rows.append(
                        (role, caption, preset_name(preset), preset_facts(preset, self.settings), True)
                    )
                else:
                    size = file.stat().st_size / 1024**3
                    rows.append((role, caption, file.stem, f"{gb(size)} ГБ на диске · свой файл", True))
            else:
                preset = wanted()
                rows.append((role, caption, preset_name(preset), preset_facts(preset, self.settings), False))
        missing = self.missing_models()
        if missing:
            total = sum(preset.size_gb for _, preset in missing)
            note = (
                f"Подобраны под этот Mac ({local_llm.ram_gb():.0f} ГБ памяти), скачать один раз {gb(total)} ГБ. "
                "Модели работают по очереди: памяти нужно под одну из них."
            )
        else:
            note = "Модели работают на этом Mac без интернета. Сменить их можно в настройках."
        return rows, note

    def copy_final(self):
        if not self.mid or not (summary := self.store.meeting(self.mid)["summary"]):
            return
        QApplication.clipboard().setText((json.loads(summary).get("final") or {}).get("text", ""))
        self.progress.setText("Итоговый текст скопирован в буфер обмена.")

    def choose_format(self, key):
        if not self.mid:
            return
        current = self.settings.final_format if not self.settings.final_prompt else "custom"
        if key == "custom":
            self.final_page.set(self.current_final(), current)
            dialog = SettingsDialog(self.settings, self)
            dialog.nav.setCurrentRow(1)
            if dialog.exec():
                self.settings = dialog.settings
            return
        if key == current:
            return
        title = FINAL_FORMATS[key][0]
        answer = QMessageBox.question(
            self,
            "Другой формат",
            f"Пересоздать итоговый текст в формате «{title}»?\n\n"
            "Запись заново не разбирается: используется уже проверенный реестр пунктов.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            self.final_page.set(self.current_final(), current)
            return
        self.settings.final_format, self.settings.final_prompt = key, ""
        try:
            self.settings.save()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Настройки", str(exc))
            return
        self.start("final")

    def current_final(self):
        meeting = self.store.meeting(self.mid) if self.mid else None
        if not meeting or not meeting["summary"]:
            return {}
        return json.loads(meeting["summary"]).get("final") or {}

    def toggle_task(self, mid, key, done):
        state = dict(self.store.checkpoint(mid, "tasks-done", 0) or {})
        if done:
            state[key] = True
        else:
            state.pop(key, None)
        self.store.save_checkpoint(mid, "tasks-done", 0, state)
        meeting = self.store.meeting(mid)
        if meeting["note"] and meeting["summary"] and not self.job:
            from .knowledge import export

            try:
                export(self.store, mid, Settings.from_dict(json.loads(meeting["settings"])).resolved())
                self.progress.setText("Отметка задачи сохранена и в заметке Obsidian.")
            except (OSError, ValueError) as exc:
                self.progress.setText(f"Отметка сохранена, заметку обновить не удалось: {exc}")

    # -- questions about the summary ---------------------------------------------------

    def toggle_chat(self, shown):
        self.chat_panel.setVisible(shown)
        # The page's own table of contents yields its width to the conversation.
        self.summary_page.toc_widget.setVisible(not shown)
        if shown:
            self.chat_panel.field.setFocus()

    def ask_question(self, question):
        if not self.mid:
            return
        meeting = self.store.meeting(self.mid)
        if not meeting["summary"]:
            return
        if self.job is not None and self.job.phase in {"summary", "final"}:
            self.chat_panel.drop_thinking()
            self.chat_panel.add_answer(
                dict(a="Модель сейчас составляет сводку. Спросите, когда она закончит.", found=False)
            )
            return
        if not self.has_llm() and not self.download_model():
            return
        history = self.store.checkpoint(self.mid, "chat", 0) or []
        loading = "Загружаю модель — первый вопрос дольше…" if not self.chat_engine.loaded else ""
        self.chat_panel.add_thinking()
        self.chat_panel.set_busy(True, loading)
        worker = ChatWorker(
            self.chat_engine,
            Settings(**asdict(self.settings)),
            json.loads(meeting["summary"]),
            dict(self.times),
            question,
            history,
            meeting["title"],
        )
        worker.answered.connect(lambda result, mid=self.mid: self.chat_answered(mid, result))
        worker.failed.connect(self.chat_failed)
        worker.finished.connect(worker.deleteLater)
        self.chat_worker = worker
        worker.start()

    def chat_answered(self, mid, result):
        self.chat_worker = None
        self.chat_engine.touch()
        history = list(self.store.checkpoint(mid, "chat", 0) or [])
        history.append(result)
        self.store.save_checkpoint(mid, "chat", 0, history[-50:])
        if mid == self.mid:
            self.chat_panel.drop_thinking()
            self.chat_panel.add_answer(result)
            self.chat_panel.set_busy(False)

    def chat_failed(self, error):
        self.chat_worker = None
        self.chat_panel.drop_thinking()
        self.chat_panel.add_answer(dict(a=error, found=False))
        self.chat_panel.set_busy(False)

    def clear_chat(self):
        if not self.mid or self.chat_worker is not None:
            return
        self.store.save_checkpoint(self.mid, "chat", 0, [])
        self.chat_panel.show_meeting(self.mid, self.times, [])

    def release_chat_model(self):
        """Free the model's memory before a summary job loads its own copy."""
        if self.chat_worker is not None:
            self.chat_worker.wait()
        self.chat_engine.stop()

    # -- adding recordings ------------------------------------------------------------

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Добавить запись",
            "",
            "Аудио и видео (*.mp3 *.mp4 *.m4a *.wav *.mov *.mkv *.webm *.ogg *.flac *.aac);;Все файлы (*)",
        )
        if path:
            self.add_path(path)

    def add_path(self, path):
        if self.live_recorder is not None:
            return
        self.mid = self.store.create(path, self.settings)
        self.page = 0
        self.view = "final"
        self.refresh_list()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            if url.isLocalFile() and Path(url.toLocalFile()).is_file():
                self.add_path(url.toLocalFile())
        event.acceptProposedAction()

    def toggle_live(self):
        # Stopping comes first: catch-up recognition runs as a job during the whole
        # recording, and it must never block the button that ends the meeting.
        if self.live_recorder is not None:
            self.stop_live()
            return
        if self.job:
            return
        try:
            recorder = LiveRecorder(
                source=self.settings.live_source,
                microphone_device=self.settings.live_microphone_device,
                system_device=self.settings.live_system_device,
                system_backend=self.settings.live_system_backend,
                mix=self.settings.live_mix,
            )
            recorder.start()
        except LiveCaptureError as exc:
            QMessageBox.warning(self, "Live-запись", str(exc))
            return
        self.live_recorder = recorder
        self.stop_playback()
        # The meeting exists from the first second so recognition can run alongside
        # the recording; its source moves to the finished file when capture stops.
        self.mid = self.store.create(recorder.partial, self.settings)
        # The list stays browsable during a meeting, so the recording keeps its own id.
        self.recording_id = self.mid
        self.store.update(
            self.mid,
            title=recording_title(recorder.source, recorder.partial.stem),
            status="recording",
        )
        self.page = 0
        self.view = "final"
        self.refresh_list()
        self.start_catchup()
        self.progress.setText(
            f"Live-запись началась ({SOURCE_LABELS[recorder.source]}). "
            "Аудио сохраняется только на этом Mac, распознавание идёт по ходу записи."
        )
        self.controls()

    def start_catchup(self):
        """Recognise finished fragments while the meeting is still being recorded."""
        if self.job or not self.mid:
            return
        self.active_id = self.mid
        self.job = Job("catchup", self.mid, self.settings.memory_gb)
        self.job_started = time.monotonic()
        self.job.memory.connect(
            lambda rss: self.ram.setText(f"Память: {rss:.2f} из {self.settings.memory_gb:g} ГиБ")
        )
        self.job.result.connect(self.job_result)
        self.job.finished.connect(self.job_finished)
        self.job.start()

    def stop_live(self, start_transcription=True):
        recorder = self.live_recorder
        mid = self.recording_id or self.mid
        if recorder is None:
            return
        self.live_recorder = None
        self.recording_id = None
        try:
            path = recorder.stop()
        except (LiveCaptureError, OSError, ValueError) as exc:
            self.end_catchup(mid)
            # An empty recording leaves nothing to keep unless catch-up already saved text.
            if mid and not self.store.segments(mid, limit=1):
                self.store.delete(mid)
                self.mid = None
            self.refresh_list()
            self.progress.setText(str(exc))
            QMessageBox.warning(self, "Live-запись", str(exc))
            self.controls()
            return
        self.store.update(mid, source=str(path), status="transcribing")
        self.end_catchup(mid)
        self.mid, self.page = mid, 0
        self.refresh_list()
        self.progress.setText("Live-запись сохранена локально.")
        self.controls()
        self.report_live_tracks(path, recorder.tracks, [e.name for e in recorder.inputs])
        if start_transcription:
            # Catch-up is still finishing its last fragment; chain the closing pass to it.
            if self.job is not None and self.job.phase == "catchup":
                self.pending_phase = "transcribe"
            else:
                self.start("transcribe")

    def end_catchup(self, mid):
        """Tell the catch-up worker the recording is over, so it stops after the tail."""
        if mid:
            self.store.save_checkpoint(mid, "live-stopped", 0, True)

    def report_live_tracks(self, path, tracks, names=()):
        """Say which source made it into the file, instead of leaving it to the ear."""
        if len(tracks) < 2:
            return
        try:
            report, silent = describe_tracks(path, tracks, names)
        except (OSError, ValueError, RuntimeError) as exc:
            self.progress.setText(f"Запись сохранена, проверить дорожки не удалось: {exc}")
            return
        self.progress.setText("Live-запись сохранена локально. " + report.replace("\n", " · "))
        if silent:
            QMessageBox.warning(
                self,
                "Live-запись",
                "В записи нет звука на дорожке: "
                + ", ".join(silent)
                + ".\n\n"
                + report
                + "\n\nЗапись сохранена, распознавание продолжится. Измерены первые "
                "30 секунд: если источник молчал в начале, предупреждение ложное.",
            )

    # -- the list ---------------------------------------------------------------------

    def refresh_list(self):
        current = self.mid
        self.list.blockSignals(True)
        self.list.clear()
        found = False
        previous_day = None
        for meeting in self.store.meetings(self.search.text()):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, meeting["id"])
            day = day_title(meeting["created"])
            item.setData(RecordingDelegate.HEADER, day if day != previous_day else "")
            previous_day = day
            self.describe_item(item, meeting)
            item.setToolTip(meeting["title"])
            self.list.addItem(item)
            if meeting["id"] == current:
                self.list.setCurrentItem(item)
                found = True
        self.list.blockSignals(False)
        if current and found:
            self.load_detail()
        else:
            self.mid = None
            self.clear_detail()

    def describe_item(self, item, meeting):
        complete = meeting["status"] == "review" or (
            meeting["status"] in {"error", "interrupted"}
            and self.store.checkpoint(meeting["id"], "asr_complete", 0)
        )
        status, dot = list_status(meeting, bool(complete) and not meeting["summary"])
        item.setData(
            RecordingDelegate.INFO,
            dict(title=meeting["title"], duration=duration_text(meeting["duration"]), status=status, dot=dot),
        )

    def list_menu(self, point):
        item = self.list.itemAt(point)
        if not item:
            return
        menu = QMenu(self)
        remove = menu.addAction("Удалить запись…")
        remove.setEnabled(self.job is None and self.live_recorder is None)
        if menu.exec(self.list.viewport().mapToGlobal(point)) is remove:
            self.delete_meeting(item.data(Qt.ItemDataRole.UserRole))

    def clear_detail(self):
        self.stop_playback()
        self.heading.setText("Samarizator")
        self.meta.setText("")
        self.info.setText("1. Распознайте локально → 2. Проверьте текст → 3. Создайте сводку")
        self.error_detail.clear()
        self.table.setRowCount(0)
        self.visible_rows = []
        self.summary_page.clear()
        self.final_text.clear()
        self.audio_text = ""
        self.show_page()
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
            meeting = self.store.meeting(self.mid)
            complete = self.store.checkpoint(self.mid, "asr_complete", 0)
            # Open where the next step is: the result if there is one, else the text to check.
            self.view = "final" if meeting["summary"] or not complete else "transcript"
            self.load_detail()

    # -- detail -----------------------------------------------------------------------

    def load_detail(self):
        if not self.mid:
            return
        meeting = self.store.meeting(self.mid)
        self.heading.setText(meeting["title"])
        try:
            when = datetime.fromisoformat(meeting["created"]).astimezone()
            created = day_title(meeting["created"]) + when.strftime(" · %H:%M")
        except (TypeError, ValueError):
            created = ""
        self.meta.setText(" · ".join(part for part in [created, human_length(meeting["duration"])] if part))
        state = STATUS.get(meeting["status"], meeting["status"])
        length = f" · {stamp(meeting['duration'])}" if meeting["duration"] else ""
        self.info.setText(f"{state}{length} · {NEXT_STEP.get(meeting['status'], '')}")
        self.error_detail.setText(
            meeting["error"]
            if meeting["error"] and meeting["status"] not in RUNNING and meeting["status"] != "done"
            else ""
        )
        self.error_box.set_text("Шаг не выполнен", self.error_detail.text())
        self.load_rows()
        total, flagged = self.store.segment_counts(self.mid)
        self.all_rows.setText(f"Все · {total}")
        self.uncertain_only.setText(f"Проверить · {flagged}")
        self.review_banner.set_text(
            f"{flagged} {self.plural(flagged, 'реплику', 'реплики', 'реплик')} стоит прослушать",
            "Поправьте имена и термины — сводка будет точнее. Проверка необязательна.",
        )
        self.review_banner.setVisible(bool(flagged) and not meeting["summary"])
        times = {}
        if meeting["summary"]:
            try:
                result = json.loads(meeting["summary"])
                times = {r["id"]: r["start"] for r in self.store.iter_segments(self.mid)}
                self.times = times
                self.chat_panel.show_meeting(
                    self.mid, times, self.store.checkpoint(self.mid, "chat", 0) or []
                )
                self.summary_page.show_summary(
                    self.mid, result, times, self.store.checkpoint(self.mid, "tasks-done", 0) or {}
                )
                fmt = self.settings.final_format if not self.settings.final_prompt else "custom"
                final = result.get("final") or {}
                if final.get("format") and final.get("title") != "Свой формат":
                    fmt = final["format"]
                self.final_page.set(final, fmt)
            except (ValueError, KeyError, TypeError, AttributeError):
                self.summary_page.clear("Сводку не удалось прочитать. Создайте её заново.")
                self.final_text.setPlainText("Сводку не удалось прочитать. Создайте её заново.")
        else:
            self.summary_page.clear()
            self.final_text.clear()
        report = []
        plan = self.store.checkpoint(self.mid, "asr-plan", 0) or []
        for i, (start, end) in enumerate(plan):
            for d in self.store.checkpoint(self.mid, "audio-quality", i) or []:
                channel = f" · канал {d['channel'] + 1}" if d["channel"] is not None else ""
                report.append(
                    f"{stamp(start)}–{stamp(end)}{channel}: RMS {d['rms_dbfs']} dBFS; "
                    + (", ".join(d["warnings"]) or "нет предупреждений об уровне сигнала")
                )
        self.audio_text = (
            "Диагностика уровня звука. Это не оценка точности текста и не измерение шума.\n\n"
            + ("\n".join(report) or "Диагностика появится для новой обработки записи.")
        )
        self.show_page()
        self.controls()

    @staticmethod
    def plural(count, one, few, many):
        if count % 10 == 1 and count % 100 != 11:
            return one
        if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14:
            return few
        return many

    def set_view(self, view):
        self.view = view
        self.show_page()
        self.controls()

    def show_page(self):
        if not self.mid:
            if self.list.count() == 0 and not self.search.text():
                self.welcome.set_models(*self.model_rows())
                self.stack.setCurrentWidget(self.welcome)
            else:
                self.empty.set(
                    "wave",
                    SECONDARY,
                    "Выберите запись слева",
                    "Или добавьте новую: перетащите аудио или видео в окно.",
                    ("Добавить файл", self.add_file),
                    ("Начать запись", self.toggle_live),
                )
                self.stack.setCurrentWidget(self.empty)
            return
        self.views.select(self.view)
        meeting = self.store.meeting(self.mid)
        if self.view == "transcript":
            self.stack.setCurrentWidget(self.transcript_page)
            return
        # A finished summary stays readable while the transcript is re-checked; only a
        # running summary job replaces it with progress.
        if meeting["summary"] and meeting["status"] != "summarizing":
            self.stack.setCurrentWidget(self.final_page if self.view == "final" else self.summary_page)
            return
        if meeting["status"] in RUNNING:
            self.update_processing(meeting)
            self.stack.setCurrentWidget(self.processing)
            return
        complete = self.store.checkpoint(self.mid, "asr_complete", 0)
        if (
            complete
            and meeting["status"] in {"interrupted", "error"}
            and self.store.summary_progress(self.mid)
        ):
            self.empty.set(
                "lines",
                ORANGE,
                "Сводка не завершена",
                "Готовые шаги сохранены: сводку можно продолжить с места остановки или сделать заново "
                "с самого начала.",
                ("Продолжить сводку", lambda: self.start("summary")),
                ("Сделать заново", self.redo_summary),
            )
            self.stack.setCurrentWidget(self.empty)
        elif complete:
            total, flagged = self.store.segment_counts(self.mid)
            self.empty.set(
                "lines",
                ACCENT,
                "Текст готов — можно делать сводку",
                (
                    f"Распознано {total} {self.plural(total, 'реплика', 'реплики', 'реплик')}. "
                    + (
                        f"{flagged} стоит прослушать перед сводкой."
                        if flagged
                        else "Отмеченных для проверки реплик нет."
                    )
                ),
                ("Создать сводку", lambda: self.start("summary")),
                ("Открыть расшифровку", lambda: self.set_view("transcript")),
            )
            self.stack.setCurrentWidget(self.empty)
        else:
            self.empty.set(
                "wave",
                SECONDARY,
                "Запись ещё не распознана",
                "Распознавание идёт на этом Mac: аудио никуда не отправляется. "
                "Если его остановить, оно продолжится с того же места.",
                ("Распознать", lambda: self.start("transcribe")),
            )
            self.stack.setCurrentWidget(self.empty)

    def update_processing(self, meeting):
        status, message = meeting["status"], meeting["error"] or ""
        running_here = self.job is not None and self.active_id == meeting["id"]
        elapsed = time.monotonic() - self.job_started if running_here else 0
        if status == "recording":
            seconds = self.live_recorder.elapsed if self.live_recorder else 0
            source = SOURCE_LABELS.get(self.live_recorder.source, "") if self.live_recorder else ""
            self.processing.set(
                "Идёт запись",
                f"{source} · аудио сохраняется только на этом Mac".strip(" ·"),
                None,
                stamp(seconds),
                [
                    ("Запись звука", source, "current"),
                    (
                        "Распознавание по ходу записи",
                        message.split(":")[-1].strip() if message else "",
                        "current",
                    ),
                    ("Сводка", "после остановки", "todo"),
                ],
                "Готовые фрагменты распознаются сразу, поэтому после остановки остаётся только хвост.",
                RED,
            )
            return
        if status == "transcribing":
            info = work.transcription(message)
            steps = [
                ("Распознавание речи", info["detail"], "current"),
                ("Проверка текста", "", "todo"),
                ("Сводка", "", "todo"),
            ]
            title, fraction = "Идёт распознавание", info["fraction"]
        elif status == "retrying":
            info = work.retry(message)
            steps = [("Повторный проход по отмеченным репликам", info["detail"], "current")]
            title, fraction = "Перепроверка реплик", info["fraction"]
        else:
            # Only the final-text job keeps the summary; a summary redone from scratch shows all steps.
            final_only = bool(meeting["summary"]) and (not running_here or self.job.phase == "final")
            info = work.summary(message, final_only=final_only)
            fmt = FINAL_FORMATS.get(self.settings.final_format, FINAL_FORMATS[DEFAULT_FORMAT])[0]
            if self.settings.final_prompt:
                fmt = "свой шаблон"
            total, _ = self.store.segment_counts(meeting["id"])
            steps = work.summary_steps(message, total, fmt, final_only=final_only)
            title = "Пересоздаётся итоговый текст" if final_only else "Создаётся сводка"
            fraction = info["fraction"]
        left = work.remaining(fraction, elapsed)
        where = "всё считается на этом Mac"
        self.processing.set(
            title,
            " · ".join(part for part in [left, where] if part),
            fraction if running_here or fraction else None,
            f"{round(fraction * 100)}%" if fraction else "",
            steps,
            "Можно открывать другие записи. Если остановить или закрыть приложение, работа продолжится "
            "с того же места.",
        )

    def load_rows(self):
        rows = self.store.segments(
            self.mid, self.page * PAGE_SIZE, PAGE_SIZE, uncertain_only=self.uncertain_only.isChecked()
        )
        # A new page/meeting invalidates the old selection and displayed proposal.
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.visible_rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, value in enumerate([stamp(row["start"]), row["text"], self.review_label(row)]):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
                if col == 0:
                    item.setForeground(QColor(SECONDARY))
                elif col == 2:
                    item.setForeground(QColor("#b26100"))
                self.table.setItem(i, col, item)
        self.table.blockSignals(False)
        self.selected_segment()
        self.table.resizeRowsToContents()
        first = self.page * PAGE_SIZE
        self.page_label.setText(f"{first + 1}–{first + len(rows)}" if rows else "")

    def review_label(self, row):
        if not row["uncertain"]:
            return ""
        reasons = [r.strip() for r in (row.get("review") or "").split(",") if r.strip() != "говорящий"]
        label_text = ", ".join(reasons) or "Проверить"
        if row.get("retry_text"):
            label_text += "; есть повторный вариант"
        return "● " + label_text

    def turn_page(self, delta):
        if not self.mid:
            return
        new = max(0, self.page + delta)
        only = self.uncertain_only.isChecked()
        if new == 0 or self.store.segments(self.mid, new * PAGE_SIZE, 1, uncertain_only=only):
            self.page = new
            self.load_rows()

    def filter_toggled(self, flagged):
        self.all_rows.blockSignals(True)
        self.all_rows.setChecked(not flagged)
        self.all_rows.blockSignals(False)
        self.filter.current = "flagged" if flagged else "all"
        self.toggle_uncertain_filter()

    def toggle_uncertain_filter(self):
        if not self.mid:
            return
        self.page = 0
        self.load_rows()

    def selected_segment(self):
        index = self.table.currentRow()
        if 0 <= index < len(self.visible_rows):
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

    def show_audio_report(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Качество звука")
        dialog.resize(640, 420)
        box = QVBoxLayout(dialog)
        view = QTextBrowser()
        view.setPlainText(self.audio_text)
        box.addWidget(view)
        dialog.exec()

    def reveal_note(self):
        if self.mid and (note := self.store.meeting(self.mid)["note"]):
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(note).parent)))

    # -- playback ---------------------------------------------------------------------

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
        # ffplay comes with Homebrew FFmpeg; the portable app plays a cut excerpt with the
        # system afplay instead.
        player = shutil.which("ffplay") or shutil.which("afplay")
        if not player:
            QMessageBox.information(
                self,
                "Прослушивание недоступно",
                "Не найден проигрыватель (ffplay или afplay). "
                "Пока можно открыть всю запись: меню «⋯» → «Открыть исходную запись».",
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
            if Path(self.playback_player).name == "afplay":
                excerpt = data_dir() / "work" / "excerpt.wav"
                excerpt.parent.mkdir(exist_ok=True)
                subprocess.run(
                    [
                        tool("ffmpeg"),
                        "-nostdin",
                        "-v",
                        "error",
                        "-y",
                        "-ss",
                        str(start),
                        "-t",
                        str(duration),
                        "-i",
                        self.playback_source,
                        "-vn",
                        str(excerpt),
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                    check=True,
                )
                args = [self.playback_player, str(excerpt)]
            else:
                args = [
                    self.playback_player,
                    "-nodisp",
                    "-autoexit",
                    "-ss",
                    str(start),
                    "-t",
                    str(duration),
                    self.playback_source,
                ]
            self.player_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            self.stop_playback()
            QMessageBox.information(self, "Прослушивание недоступно", "Не удалось запустить проигрыватель.")
            return
        part = self.playback_total - len(self.playback_queue)
        position = f" · {part} из {self.playback_total}" if self.playback_total > 1 else ""
        self.playback_label.setText(f"▶  Играет {stamp(start)}–{stamp(start + duration)}{position}")
        self.controls()

    def stop_playback(self):
        self.playback_queue.clear()
        self.playback_mid = None
        if self.player_proc and self.player_proc.poll() is None:
            self.player_proc.terminate()
        self.player_proc = None
        self.playback_label.setText("")
        self.stop_button.setEnabled(False)
        self.playbar.setVisible(False)

    # -- jobs -------------------------------------------------------------------------

    def redo_summary(self):
        """Rebuild the whole summary from the transcript, ignoring every saved step."""
        if self.job or self.live_recorder is not None or not self.mid:
            return
        if not self.store.checkpoint(self.mid, "asr_complete", 0):
            QMessageBox.information(self, "Сводка", "Сначала распознайте запись целиком.")
            return
        if not self.confirm_redo(self.store.meeting(self.mid)):
            return
        self.store.reset_summary(self.mid)
        self.start("summary")

    def confirm_redo(self, meeting):
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Сделать сводку заново")
        box.setText("Сделать сводку заново?")
        box.setInformativeText(
            "Сводка будет построена с нуля по текущей расшифровке и настройкам — это займёт столько "
            "же времени, сколько первая. "
            + (
                "Текущая сводка останется, пока новая не будет готова."
                if meeting["summary"]
                else "Сохранённые промежуточные шаги будут отброшены."
            )
        )
        redo = box.addButton("Сделать заново", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Отмена", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is redo

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
            if phase in {"transcribe", "retry"} and not self.ensure_whisper():
                return
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
                    "llm_model",
                    "llm_preset",
                    "llm_gpu",
                    "summary_instructions",
                    "final_format",
                    "final_prompt",
                    "input_chars",
                    "max_output_tokens",
                    "vault",
                    "memory_gb",
                    "threads",
                ]:
                    setattr(original, key, getattr(self.settings, key))
                if phase in {"summary", "final"}:
                    if not self.has_llm() and not self.download_model():
                        return
                    self.release_chat_model()
                    original.llm_model, original.llm_preset = (
                        self.settings.llm_model,
                        self.settings.llm_preset,
                    )
                    original.validate(llm=True)
                    local_llm.check_fits(original)
                    if not self.store.checkpoint(self.mid, "asr_complete", 0):
                        raise ValueError("Сначала завершите распознавание всей записи.")
                    if phase == "final" and not meeting["summary"]:
                        raise ValueError("Сначала создайте сводку.")
            budget = (
                local_llm.job_budget_gb(original) if phase in {"summary", "final"} else original.memory_gb
            )
            self.store.update(
                self.mid,
                settings=json.dumps(asdict(original)),
                status={"transcribe": "transcribing", "retry": "retrying"}.get(phase, "summarizing"),
                error=None,
            )
            self.active_id = self.mid
            self.job = Job(phase, self.mid, budget)
            self.job_started = time.monotonic()
            self.job.memory.connect(lambda rss: self.ram.setText(f"Память: {rss:.2f} из {budget:g} ГиБ"))
            self.job.result.connect(self.job_result)
            self.job.finished.connect(self.job_finished)
            self.job.start()
            if phase in {"summary", "final", "transcribe"}:
                self.view = "final"
            self.refresh_list()
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
        pending, self.pending_phase = self.pending_phase, ""
        if self.mid == self.active_id:
            if self.job.phase in {"summary", "final"} and meeting["summary"]:
                self.view = "final"
            elif self.job.phase in {"transcribe", "retry"} and meeting["status"] == "review":
                self.view = "transcript"
        self.progress.setText(meeting["error"] or "Готово. Результат сохранён.")
        self.job.deleteLater()
        self.job = None
        self.active_id = None
        self.refresh_list()
        self.controls()
        if pending:
            self.start(pending)

    def cancel_job(self):
        if self.job:
            self.job.stop.set()
            self.progress.setText("Остановка обработчиков…")

    def poll(self):
        if self.live_recorder is not None:
            if self.live_recorder.recording:
                # One line during a meeting: the timer plus whatever catch-up is doing.
                note = (self.store.meeting(self.active_id)["error"] or "") if self.active_id else ""
                self.progress.setText(
                    f"● Live-запись · {SOURCE_LABELS[self.live_recorder.source]} · "
                    f"{stamp(self.live_recorder.elapsed)} · "
                    + (note.strip() or "нажмите кнопку ещё раз для остановки")
                )
            else:
                self.stop_live(start_transcription=False)
        elif self.active_id:
            meeting = self.store.meeting(self.active_id)
            self.progress.setText(meeting["error"] or "Подготовка…")
        busy_id = self.active_id or self.recording_id
        if busy_id:
            for index in range(self.list.count()):
                item = self.list.item(index)
                if item.data(Qt.ItemDataRole.UserRole) == busy_id:
                    self.describe_item(item, self.store.meeting(busy_id))
            if busy_id == self.mid and self.stack.currentWidget() is self.processing:
                self.update_processing(self.store.meeting(busy_id))
        if self.player_proc and self.player_proc.poll() is not None:
            code = self.player_proc.poll()
            self.player_proc = None
            if code != 0:
                self.stop_playback()
                self.playbar.setVisible(True)
                self.playback_label.setText("Ошибка воспроизведения. Проверьте исходный файл и аудиовыход.")
                QTimer.singleShot(4000, lambda: self.player_proc is None and self.playbar.setVisible(False))
            elif self.playback_queue:
                self.play_next_excerpt()
            else:
                self.stop_playback()
            self.controls()

    def controls(self):
        recording = self.live_recorder is not None
        busy = self.job is not None or recording
        ready = self.mid is not None
        meeting = self.store.meeting(self.mid) if ready else None
        working = "Дождитесь конца текущей обработки." if self.job else "Идёт запись."
        pick = "Выберите запись в списке слева."
        explain(self.settings_button, not busy, working)
        explain(self.add_button, not recording, "Идёт запись.")
        explain(self.model_button, not busy, working)
        # Stopping must stay possible while catch-up recognition is running.
        explain(self.live_button, self.job is None or recording, working)
        self.live_button.setText("■ Live: остановить и распознать" if recording else "● Live: начать запись")
        self.live_button.setToolTip("Остановить запись и распознать" if recording else "Начать live-запись")
        self.live_button.setIcon(icon("stop", RED) if recording else icon("mic"))
        complete = ready and bool(self.store.checkpoint(self.mid, "asr_complete", 0))
        summarized = ready and bool(meeting["summary"])
        here = ready and (
            (self.job is not None and self.active_id == self.mid)
            or (recording and self.recording_id == self.mid)
        )
        explain(
            self.transcribe,
            ready and not busy and not complete,
            "Распознавание уже завершено. Для нового прохода — «Распознать заново» в меню «⋯»."
            if complete
            else (working if busy else pick),
        )
        explain(
            self.summarize,
            bool(complete) and not busy,
            working if busy else ("Сначала распознайте запись целиком." if ready else pick),
        )
        explain(self.cancel, self.job is not None and not recording, "Сейчас нечего останавливать.")
        explain(self.save_segment, ready and not busy, working if busy else pick)
        explain(
            self.retry,
            ready and not busy and complete,
            working if busy else "Сначала распознайте запись целиком.",
        )
        explain(self.retry_action, ready and not busy and complete, "")
        explain(self.rerun_button, ready and not busy, working if busy else pick)
        explain(
            self.regenerate_button, summarized and not busy, working if busy else "Сначала создайте сводку."
        )
        explain(
            self.redo_action,
            bool(complete) and not busy,
            working if busy else "Сначала распознайте запись целиком.",
        )
        explain(self.reexport, ready and not busy and summarized, "")
        explain(self.delete_action, ready and not busy, "")
        explain(self.source_action, ready, "")
        explain(self.reveal_action, ready and bool(meeting["note"]), "")
        explain(self.audio_action, ready, "")
        self.copy_error_button.setVisible(bool(self.error_detail.text()))
        explain(self.copy_error_button, bool(self.error_detail.text()))
        self.error_box.setVisible(bool(self.error_detail.text()))
        # The hint line is for states without their own explanation on the page.
        self.info.setVisible(ready and meeting["status"] in {"new", "interrupted"})
        # Exactly one step is highlighted, so the next action is never a guess.
        step = self.summarize if complete else self.transcribe
        for button in (self.transcribe, self.summarize):
            primary(button, button is step and button.isEnabled())
        self.transcribe.setVisible(ready and not complete and not here)
        self.summarize.setVisible(ready and complete and not summarized and not here)
        resumable = bool(complete) and not summarized and self.store.summary_progress(self.mid)
        self.summarize.setText("Продолжить сводку" if resumable else "Создать сводку")
        self.obsidian.setVisible(ready and summarized and not here)
        self.cancel.setVisible(here and not recording)
        self.stop_live_button.setVisible(recording and self.recording_id == self.mid)
        self.views.setVisible(ready)
        self.more_button.setVisible(ready)
        self.copy_final_button.setVisible(ready and summarized and self.view == "final")
        self.ask_button.setVisible(ready and summarized and not here)
        self.chat_panel.setVisible(ready and summarized and not here and self.ask_button.isChecked())
        self.summary_page.toc_widget.setVisible(not self.chat_panel.isVisibleTo(self))
        index = self.table.currentRow()
        has_row = ready and 0 <= index < len(self.visible_rows)
        has_retry = has_row and bool(self.visible_rows[index].get("retry_text"))
        self.accept_retry_button.setEnabled(has_retry and not busy)
        self.accept_retry_button.setVisible(has_retry)
        self.undo_retry_button.setEnabled(bool(has_row) and not busy)
        self.play_button.setEnabled(has_row)
        self.editor.setVisible(has_row)
        playing = bool(self.player_proc) and self.player_proc.poll() is None
        self.stop_button.setEnabled(playing)
        self.stop_button.setVisible(playing)
        if playing:
            self.playbar.setVisible(True)
        self.obsidian.setEnabled(ready and bool(meeting["note"]) and summarized)
        roles = [role for role, _ in self.missing_models()]
        self.model_button.setText(
            "Скачать модели"
            if len(roles) == 2
            else "Скачать модель распознавания"
            if roles == ["whisper"]
            else "Скачать модель сводок"
        )
        self.model_button.setVisible(bool(roles))
        self.model_row.setVisible(self.has_llm())
        if self.has_llm():
            path = Path(self.settings.llm_model).expanduser()
            preset = next((p for p in local_llm.PRESETS.values() if p.file == path.name), None)
            name = preset.label.split("·")[-1].strip() if preset else path.stem
            self.model_row.setText(name.removesuffix(" Instruct"))
            self.model_row.setIcon(dot_icon(GREEN))
            self.model_row.setToolTip("Модель сводок работает на этом Mac. Сменить — в настройках.")

    def open_source(self):
        if self.mid:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.store.meeting(self.mid)["source"]))

    def open_obsidian(self):
        if not self.mid or not (note := self.store.meeting(self.mid)["note"]):
            return
        # Ask Obsidian which vaults it knows rather than guessing a name from the
        # settings folder: the name only matches if the user opened that exact folder
        # as a vault, and a note can also live inside a vault registered higher up.
        url = obsidian.note_url(note)
        if url and QDesktopServices.openUrl(QUrl(url)):
            return
        folder = Path(self.settings.vault).expanduser()
        registered = "\n".join(f"• {location}" for _, location in obsidian.vaults())
        message = (
            "Obsidian не знает хранилища с этой заметкой.\n\n"
            f"Заметка лежит в {folder}. Откройте Obsidian → «Открыть папку как хранилище» "
            "и выберите эту папку — одного наличия папки на диске недостаточно, "
            "ссылка obsidian:// работает только с зарегистрированным хранилищем."
        )
        if registered:
            message += "\n\nСейчас Obsidian знает такие хранилища:\n" + registered
        elif not obsidian.config_path().is_file():
            message = (
                "Не найден конфиг Obsidian — похоже, приложение не установлено или ни разу "
                f"не запускалось. Заметки лежат в {folder} и открываются любым "
                "Markdown-редактором."
            )
        box = QMessageBox(self)
        box.setWindowTitle("Не удалось открыть в Obsidian")
        box.setText(message)
        box.addButton("Показать папку", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        if box.exec() == 0:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(note).parent)))

    def closeEvent(self, event):
        if self.live_recorder is not None:
            self.stop_live(start_transcription=False)
        if self.download_job:
            self.download_job.stop.set()
            self.download_job.wait(5000)
        self.release_chat_model()
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
    lock = QLockFile(str(data_dir() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Samarizator", "Приложение уже запущено.")
        return 0
    apply_theme(app)
    window = Window()
    window.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
