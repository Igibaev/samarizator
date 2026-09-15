"""Floating, low-distraction UI for the local companion."""

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .assistant_store import CompanionStore
from .companion_service import CompanionService
from .obsidian_index import task_from_text
from .ollama import OllamaClient


class AssistantJob(QThread):
    completed = Signal(str, str)

    def __init__(self, service, question):
        super().__init__()
        self.service = service
        self.question = question

    def run(self):
        try:
            self.completed.emit(self.service.answer(self.question), "")
        except Exception as exc:
            self.completed.emit("", str(exc))


class SyncJob(QThread):
    completed = Signal(object, str)

    def __init__(self, service):
        super().__init__()
        self.service = service

    def run(self):
        try:
            self.completed.emit(self.service.sync(), "")
        except Exception as exc:
            self.completed.emit(None, str(exc))


class CompanionWindow(QWidget):
    reminder = Signal(str, str)

    def __init__(self, settings, parent=None, store=None, client=None):
        super().__init__(parent)
        self.settings = settings
        self.store = store or CompanionStore()
        self.client = client or OllamaClient(settings.assistant_url)
        self.service = CompanionService(
            self.store,
            self.client,
            settings.assistant_model,
            settings.vault,
            settings.assistant_keep_alive,
        )
        self.job = None
        self.sync_job = None
        self.collapsed = False
        self.notified = set()
        self.allow_close = False
        self.transcript = []
        self.setObjectName("companionWindow")
        self.setWindowTitle("Samarizator · Помощник")
        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMinimumSize(390, 560)
        self.resize(420, 620)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        self.card = QFrame()
        self.card.setObjectName("companionCard")
        outer.addWidget(self.card)
        layout = QVBoxLayout(self.card)
        layout.setContentsMargins(16, 14, 16, 14)

        header = QHBoxLayout()
        self.mascot = QLabel("(•ᴗ•)")
        self.mascot.setObjectName("mascot")
        header.addWidget(self.mascot)
        names = QVBoxLayout()
        name = QLabel("Локальный помощник")
        name.setObjectName("companionName")
        self.state = QLabel("Отдыхаю · модель не загружена")
        self.state.setObjectName("companionState")
        names.addWidget(name)
        names.addWidget(self.state)
        header.addLayout(names, 1)
        self.collapse_button = QPushButton("—")
        self.collapse_button.setToolTip("Свернуть до персонажа")
        self.collapse_button.clicked.connect(self.toggle_collapsed)
        self.collapse_button.setFixedWidth(34)
        header.addWidget(self.collapse_button)
        layout.addLayout(header)

        self.panel = QWidget()
        panel = QVBoxLayout(self.panel)
        panel.setContentsMargins(0, 4, 0, 0)
        self.tasks_title = QLabel("Сейчас важно")
        self.tasks_title.setObjectName("sectionTitle")
        panel.addWidget(self.tasks_title)
        self.tasks = QListWidget()
        self.tasks.setMaximumHeight(128)
        panel.addWidget(self.tasks)
        task_actions = QHBoxLayout()
        self.done_button = QPushButton("Готово")
        self.done_button.clicked.connect(self.complete_task)
        self.pin_button = QPushButton("Закрепить")
        self.pin_button.clicked.connect(self.pin_task)
        task_actions.addWidget(self.done_button)
        task_actions.addWidget(self.pin_button)
        panel.addLayout(task_actions)
        new_task = QHBoxLayout()
        self.task_input = QLineEdit()
        self.task_input.setPlaceholderText("Добавить локальную задачу…")
        self.task_input.returnPressed.connect(self.add_task)
        add = QPushButton("+")
        add.setToolTip("Добавить задачу")
        add.clicked.connect(self.add_task)
        new_task.addWidget(self.task_input, 1)
        new_task.addWidget(add)
        panel.addLayout(new_task)

        chat_title = QHBoxLayout()
        label = QLabel("Диалог")
        label.setObjectName("sectionTitle")
        chat_title.addWidget(label)
        chat_title.addStretch(1)
        self.sync_button = QPushButton("Обновить Obsidian")
        self.sync_button.setToolTip("Читает только Markdown внутри выбранного хранилища")
        self.sync_button.clicked.connect(self.sync_now)
        chat_title.addWidget(self.sync_button)
        panel.addLayout(chat_title)
        self.chat = QTextBrowser()
        self.chat.setOpenExternalLinks(False)
        self.chat.setPlainText(
            "Я работаю локально. Сначала обновите индекс Obsidian, затем спросите о заметках "
            "или попросите помочь выбрать следующий шаг."
        )
        panel.addWidget(self.chat, 1)
        ask = QHBoxLayout()
        self.question = QLineEdit()
        self.question.setPlaceholderText("Спросить о заметках и задачах…")
        self.question.returnPressed.connect(self.send)
        self.send_button = QPushButton("Отправить")
        self.send_button.setProperty("primary", True)
        self.send_button.clicked.connect(self.send)
        ask.addWidget(self.question, 1)
        ask.addWidget(self.send_button)
        panel.addLayout(ask)
        layout.addWidget(self.panel, 1)

        self.setStyleSheet(
            """
            QFrame#companionCard { background: #fbfcfb; border: 1px solid #cbd9d3;
                border-radius: 18px; }
            QLabel#mascot { font-size: 27px; color: #185c50; padding: 3px 8px; }
            QLabel#companionName { font-size: 15px; font-weight: 700; color: #173e35; }
            QLabel#companionState { font-size: 11px; color: #6b7d76; }
            QLabel#sectionTitle { font-size: 13px; font-weight: 700; color: #294c42;
                padding-top: 7px; }
            QTextBrowser, QListWidget { border: 1px solid #d8e1dd; border-radius: 9px;
                background: white; padding: 5px; }
            QLineEdit { border: 1px solid #ccd9d3; border-radius: 8px; background: white; }
            """
        )
        self.refresh_tasks()
        self.reminder_timer = QTimer(self)
        self.reminder_timer.timeout.connect(self.check_reminders)
        self.reminder_timer.start(60_000)
        self.index_timer = QTimer(self)
        self.index_timer.timeout.connect(self.sync_now)
        self.index_timer.start(5 * 60_000)

    def reconfigure(self, settings):
        self.settings = settings
        self.client = OllamaClient(settings.assistant_url)
        self.service = CompanionService(
            self.store,
            self.client,
            settings.assistant_model,
            settings.vault,
            settings.assistant_keep_alive,
        )

    def show_and_raise(self):
        self.show()
        if not self.property("positioned"):
            screen = QApplication.screenAt(self.frameGeometry().center()) or QApplication.primaryScreen()
            if screen:
                area = screen.availableGeometry()
                self.move(area.right() - self.width() - 20, area.top() + 40)
            self.setProperty("positioned", True)
        self.raise_()
        self.activateWindow()

    def toggle_collapsed(self):
        self.collapsed = not self.collapsed
        self.panel.setVisible(not self.collapsed)
        self.state.setVisible(not self.collapsed)
        self.collapse_button.setText("↗" if self.collapsed else "—")
        if self.collapsed:
            self.setMinimumSize(0, 0)
            self.setFixedSize(94, 76)
        else:
            self.setMinimumSize(390, 560)
            self.setMaximumSize(16_777_215, 16_777_215)
            self.resize(420, 620)

    def _selected_task_id(self):
        item = self.tasks.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh_tasks(self):
        self.tasks.clear()
        for task in self.store.tasks(limit=30):
            prefix = "📌 " if task.pinned else ""
            due = f" · {task.due_at[:10]}" if task.due_at else ""
            item = QListWidgetItem(prefix + task.title + due)
            item.setData(Qt.ItemDataRole.UserRole, task.id)
            if task.source_path:
                item.setToolTip(f"{task.source_path}:{task.source_line}")
            self.tasks.addItem(item)
        enabled = self.tasks.count() > 0
        self.done_button.setEnabled(enabled)
        self.pin_button.setEnabled(enabled)

    def add_task(self):
        title, due_at = task_from_text(self.task_input.text())
        if not title:
            return
        try:
            self.store.add_task(title, due_at=due_at)
        except ValueError as exc:
            self.state.setText(str(exc))
            return
        self.task_input.clear()
        self.refresh_tasks()
        self.state.setText("Задача сохранена локально")

    def complete_task(self):
        if task_id := self._selected_task_id():
            self.store.set_task_status(task_id, "done")
            self.refresh_tasks()
            self.state.setText("Готово ✦")

    def pin_task(self):
        if task_id := self._selected_task_id():
            self.store.toggle_pin(task_id)
            self.refresh_tasks()

    def sync_now(self):
        if self.sync_job and self.sync_job.isRunning():
            return
        self.state.setText("Читаю изменения в Obsidian…")
        self.mascot.setText("(•̀ᴗ•́)")
        self.sync_button.setEnabled(False)
        self.sync_job = SyncJob(self.service)
        self.sync_job.completed.connect(self.sync_finished)
        self.sync_job.start()

    def sync_finished(self, result, error):
        self.sync_button.setEnabled(True)
        self.mascot.setText("(•ᴗ•)")
        if error:
            self.state.setText(error)
        else:
            self.state.setText(
                f"Obsidian: {result.updated} изменено, {result.scanned} проверено"
            )
            self.refresh_tasks()
        self.sync_job.deleteLater()
        self.sync_job = None

    def send(self):
        question = self.question.text().strip()
        if not question or self.job:
            return
        self.question.clear()
        self.transcript.append("Вы: " + question)
        self.chat.setPlainText("\n\n".join(self.transcript))
        self.state.setText("Думаю локально…")
        self.mascot.setText("(•̀⤙•́)")
        self.send_button.setEnabled(False)
        self.job = AssistantJob(self.service, question)
        self.job.completed.connect(self.answer_finished)
        self.job.start()

    def answer_finished(self, answer, error):
        self.send_button.setEnabled(True)
        if error:
            self.transcript.append("Помощник: " + error)
            self.state.setText("Не получилось ответить")
            self.mascot.setText("(•́︿•̀)")
        else:
            self.transcript.append("Помощник: " + answer)
            self.state.setText("Готов помочь · модель выгружается после ответа")
            self.mascot.setText("(•‿•)")
        self.chat.setPlainText("\n\n".join(self.transcript))
        self.chat.verticalScrollBar().setValue(self.chat.verticalScrollBar().maximum())
        self.job.deleteLater()
        self.job = None

    def check_reminders(self):
        for task in self.store.due_tasks():
            if task.id not in self.notified:
                self.notified.add(task.id)
                self.reminder.emit("Напоминание", task.title)
                self.state.setText("Есть задача с наступившим сроком")
                self.mascot.setText("(•̀o•́)")

    def closeEvent(self, event):
        if self.allow_close:
            event.accept()
            return
        self.hide()
        event.ignore()
