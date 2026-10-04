"""«Вопросы по записи»: a side panel that answers from the summary or from the transcript.

By the summary the model sees only the numbered points of the summary; by the transcript it
sees the recognised speech itself (qa.ask_transcript). Either way an answer is shown only
with valid references, and nothing leaves the Mac.

The summary model is loaded on the first question and kept while the conversation goes
on (a reload takes seconds to a minute); it is released after a quiet period, before a
summary job starts and when the window closes, so two copies never share the memory.
A question about a transcript may restart it with a larger window, as memory allows.
"""

import re
import threading
from html import escape

from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from . import qa
from .config import data_dir
from .summary_page import EvidenceButton, sources_tooltip
from .theme import ACCENT, SECONDARY, icon
from .widgets import SegmentedControl, label, primary

IDLE_MINUTES = 10
SUMMARY, TRANSCRIPT = "summary", "transcript"
SUGGESTIONS = {
    SUMMARY: [
        "Какие решения приняли?",
        "Кто что должен сделать и к какому сроку?",
        "Что осталось нерешённым?",
    ],
    TRANSCRIPT: [
        "Какие суммы и числа назывались?",
        "Что говорили о сроках?",
        "Какие вопросы задавали и что на них ответили?",
    ],
}
NOTES = {
    SUMMARY: "Отвечает только по сводке этой записи: без интернета и знаний извне.",
    TRANSCRIPT: (
        "Отвечает по расшифровке — по тому, что прозвучало, со ссылками на реплики. "
        "Говорящие не подписаны. Первый вопрос дольше: модель читает запись целиком."
    ),
}
THINKING = {SUMMARY: "Ищу ответ в сводке…", TRANSCRIPT: "Ищу ответ в расшифровке…"}


class ChatEngine(QObject):
    """Owns the llama-server used for questions; one question at a time."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.server = None
        self.model = None
        self.context = 0
        # The recording whose transcript the server has just read: it is still in its cache.
        self.read = None
        self.lock = threading.Lock()
        self.idle = QTimer(self)
        self.idle.setSingleShot(True)
        self.idle.timeout.connect(self.stop)

    def client(self, settings, progress, context=None):
        """Called from the worker thread under the lock.

        `context` — the window a transcript question needs; the server is restarted with it
        only when the running one is smaller.
        """
        from .local_llm import LlamaServer, check_fits, context_tokens
        from .summary import LocalClient

        context = max(context or 0, context_tokens(settings))
        if self.server is None or self.model != settings.llm_model or self.context < context:
            self.stop_locked()
            settings.validate(llm=True)
            check_fits(settings)
            work = data_dir() / "work" / "chat"
            work.mkdir(parents=True, exist_ok=True)
            # Questions come one at a time: one slot, no memory for parallel requests.
            server = LlamaServer(settings, work, progress, slots=1, ctx_size=context)
            server.__enter__()
            self.server, self.model, self.context = server, settings.llm_model, context
        client = LocalClient(settings, self.server.url, self.server.key)
        client.n_ctx = self.context
        return client

    def touch(self):
        self.idle.start(IDLE_MINUTES * 60 * 1000)

    def stop_locked(self):
        self.read = None
        if self.server is not None:
            self.server.__exit__(None, None, None)
            self.server = None

    def stop(self):
        with self.lock:
            self.stop_locked()

    @property
    def loaded(self):
        return self.server is not None


class ChatWorker(QThread):
    answered = Signal(dict)
    failed = Signal(str)
    status = Signal(str)

    def __init__(self, engine, settings, summary, times, question, history, title, mode=SUMMARY, rows=()):
        super().__init__()
        self.engine, self.settings = engine, settings
        self.summary, self.times = summary, times
        self.question, self.history, self.title = question, history, title
        self.mode, self.rows = mode, list(rows)

    def run(self):
        try:
            with self.engine.lock:
                if self.mode == TRANSCRIPT:
                    from .local_llm import chat_context_tokens

                    lines = qa.transcript_lines(self.rows, self.settings.clean_input)
                    chars = sum(len(line[1]) + 1 for line in lines)
                    wanted = chars / qa.TRANSCRIPT_CHARS_PER_TOKEN + 4000
                    context = chat_context_tokens(self.settings, wanted)
                    client = self.engine.client(self.settings, self.status.emit, context)
                else:
                    client = self.engine.client(self.settings, self.status.emit)
                try:
                    if self.mode == TRANSCRIPT:
                        result = qa.ask_transcript(
                            client,
                            self.rows,
                            self.question,
                            self.history,
                            self.title,
                            tidy=self.settings.clean_input,
                        )
                    else:
                        budget = self.settings.input_chars * 2
                        result = qa.ask(
                            client, self.summary, self.times, self.question, self.history, self.title, budget
                        )
                        result["source"] = SUMMARY
                finally:
                    client.close()
            self.answered.emit(result)
        except (ValueError, RuntimeError, OSError) as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Не удалось получить ответ локальной модели.")


def bubble(text, mine):
    frame = QFrame()
    frame.setObjectName("bubbleMine" if mine else "bubble")
    box = QVBoxLayout(frame)
    box.setContentsMargins(12, 8, 12, 9)
    box.setSpacing(6)
    words = QLabel()
    words.setWordWrap(True)
    words.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    if mine:
        words.setTextFormat(Qt.TextFormat.PlainText)
        words.setText(text)
    else:
        # Model text is escaped first; only the [N] markers become styled spans.
        html = escape(text).replace("\n", "<br>")
        html = re.sub(
            r"\[(\d+)\]",
            rf'<span style="color:{ACCENT}; font-size:11px; font-weight:600;">[\1]</span>',
            html,
        )
        words.setTextFormat(Qt.TextFormat.RichText)
        words.setText(html)
    # Word-wrapped labels report a narrow size hint; let short lines stay on one line.
    plain = re.sub(r"<[^>]+>", "", words.text()) if not mine else text
    widest = max(
        (words.fontMetrics().horizontalAdvance(line) for line in plain.splitlines() or [""]), default=0
    )
    words.setMinimumWidth(min(widest + 4, 260))
    box.addWidget(words)
    return frame, box


class ChatPanel(QFrame):
    """Messages on top, suggestions when empty, the question field at the bottom."""

    ask = Signal(str, str)  # question, SUMMARY or TRANSCRIPT
    cleared = Signal()
    playGroup = Signal(str, object)

    def __init__(self, lookup, parent=None):
        super().__init__(parent)
        self.setObjectName("chat")
        self.setFixedWidth(380)
        self.lookup = lookup
        self.mid = None
        self.times = {}
        self.busy = False
        self.has_summary = True
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        head = QWidget()
        head_box = QVBoxLayout(head)
        head_box.setContentsMargins(18, 14, 12, 10)
        head_box.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(label("Вопросы по записи", "title"))
        top.addStretch(1)
        self.clear_button = QPushButton("Очистить")
        self.clear_button.setFlat(True)
        self.clear_button.clicked.connect(self.cleared)
        top.addWidget(self.clear_button)
        head_box.addLayout(top)
        self.source = SegmentedControl()
        self.source.add(SUMMARY, "По сводке")
        self.source.add(TRANSCRIPT, "По расшифровке")
        self.source.changed.connect(self.source_changed)
        source_row = QHBoxLayout()
        source_row.setContentsMargins(0, 6, 0, 4)
        source_row.addWidget(self.source)
        source_row.addStretch(1)
        head_box.addLayout(source_row)
        self.note = label(NOTES[SUMMARY], "small", wrap=True)
        head_box.addWidget(self.note)
        layout.addWidget(head)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("chatHolder")
        self.messages = QVBoxLayout(holder)
        self.messages.setContentsMargins(14, 8, 14, 12)
        self.messages.setSpacing(10)
        self.messages.addStretch(1)
        self.scroll.setWidget(holder)
        layout.addWidget(self.scroll, 1)
        self.state = label("", "small", wrap=True)
        self.state.setContentsMargins(18, 0, 18, 4)
        layout.addWidget(self.state)
        entry = QHBoxLayout()
        entry.setContentsMargins(14, 8, 14, 14)
        entry.setSpacing(8)
        self.field = QLineEdit()
        self.field.setPlaceholderText("Спросите о встрече…")
        self.field.setMaxLength(qa.QUESTION_LIMIT)
        self.field.returnPressed.connect(self.send)
        entry.addWidget(self.field, 1)
        self.send_button = primary(QPushButton())
        self.send_button.setIcon(icon("send", "#ffffff", 16, 2.2))
        self.send_button.setToolTip("Спросить (Enter)")
        self.send_button.setFixedWidth(40)
        self.send_button.clicked.connect(self.send)
        entry.addWidget(self.send_button)
        layout.addLayout(entry)
        self.thinking = None

    # -- the owner interface EvidenceButton expects ------------------------------------

    def sources_tooltip(self, ids):
        return sources_tooltip(self.lookup, self.mid, ids)

    # -- content ------------------------------------------------------------------------

    def clear_messages(self):
        while self.messages.count() > 1:
            item = self.messages.takeAt(0)
            if widget := item.widget():
                widget.setParent(None)
                widget.deleteLater()
        self.thinking = None

    @property
    def mode(self):
        return self.source.current

    def source_changed(self, key):
        self.note.setText(NOTES[key])
        # Fresh suggestions for the other source, if the conversation has not started.
        for index in range(self.messages.count() - 1):
            widget = self.messages.itemAt(index).widget()
            if widget and widget.findChild(QPushButton, "suggestion"):
                widget.setParent(None)
                widget.deleteLater()
                self.add_suggestions()
                break
        self.field.setFocus()

    def show_meeting(self, mid, times, history, has_summary=True):
        """`has_summary` — without a summary only the transcript can be asked."""
        self.mid, self.times, self.has_summary = mid, times, has_summary
        self.source.buttons[SUMMARY].setEnabled(has_summary)
        self.source.buttons[SUMMARY].setToolTip(
            "По сводке" if has_summary else "Сводки ещё нет — спросите по расшифровке"
        )
        if not has_summary and self.mode != TRANSCRIPT:
            self.source.select(TRANSCRIPT)
        self.note.setText(NOTES[self.mode])
        self.clear_messages()
        if not history:
            self.add_suggestions()
        for turn in history:
            self.add_question(turn["q"])
            self.add_answer(turn)
        self.state.setText("")
        self.set_busy(False)

    def add_suggestions(self):
        box = QWidget()
        column = QVBoxLayout(box)
        column.setContentsMargins(0, 8, 0, 0)
        column.setSpacing(6)
        column.addWidget(label("Например", "caption"))
        for text in SUGGESTIONS[self.mode]:
            button = QPushButton(text)
            button.setObjectName("suggestion")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, t=text: self.ask_text(t))
            column.addWidget(button)
        self.messages.insertWidget(self.messages.count() - 1, box)

    def add_question(self, text):
        frame, _ = bubble(text, mine=True)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(frame)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(40, 0, 0, 0)
        self.messages.insertWidget(self.messages.count() - 1, holder)

    def add_answer(self, turn):
        frame, box = bubble(turn["a"], mine=False)
        if not turn.get("found"):
            frame.setObjectName("bubbleMuted")
        if turn.get("source") == TRANSCRIPT:
            where = "по расшифровке"
            if turn.get("partial"):
                where += " · запись длинная, модель читала найденные по словам фрагменты"
            caption = label(where, "caption", wrap=True)
            box.insertWidget(0, caption)
        sources = [s for s in turn.get("sources", []) if s.get("evidence")]
        if sources:
            row = QHBoxLayout()
            row.setSpacing(6)
            for source in sources[:6]:
                button = EvidenceButton(self, source["evidence"])
                button.setText(f"[{source['n']}] " + button.text())
                button.setToolTip(source["text"])
                row.addWidget(button)
            row.addStretch(1)
            box.addLayout(row)
        holder = QWidget()
        line = QHBoxLayout(holder)
        line.setContentsMargins(0, 0, 24, 0)
        line.addWidget(frame)
        self.messages.insertWidget(self.messages.count() - 1, holder)
        self.scroll_down()

    def add_thinking(self, text=None):
        frame, _ = bubble(text or THINKING[self.mode], mine=False)
        frame.setObjectName("bubbleMuted")
        self.thinking = frame
        self.messages.insertWidget(self.messages.count() - 1, frame)
        self.scroll_down()

    def drop_thinking(self):
        if self.thinking is not None:
            self.thinking.setParent(None)
            self.thinking.deleteLater()
            self.thinking = None

    def scroll_down(self):
        QTimer.singleShot(
            30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum())
        )

    # -- input --------------------------------------------------------------------------

    def ask_text(self, text):
        self.field.setText(text)
        self.send()

    def send(self):
        text = self.field.text().strip()
        if not text or self.busy:
            return
        self.field.clear()
        # Suggestions disappear once the conversation starts.
        for index in range(self.messages.count() - 1):
            widget = self.messages.itemAt(index).widget()
            if widget and widget.findChild(QPushButton, "suggestion"):
                widget.setParent(None)
                widget.deleteLater()
                break
        self.add_question(text)
        self.ask.emit(text, self.mode)

    def set_busy(self, busy, note=""):
        self.busy = busy
        self.field.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.state.setText(note)
        self.state.setStyleSheet(f"color: {SECONDARY};")
