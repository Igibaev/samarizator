"""The «Сводка» view: brief, decisions, tasks, risks and the full timeline on one page.

Every model-written string is shown as plain text (never HTML), and every point keeps a
button to its source replies: hover lists them, a click plays them in order.
"""

from html import escape

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QBoxLayout,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from .knowledge import STATUS_LABELS, stamp, task_key
from .theme import ACCENT, GREEN, GREY, ORANGE, SECONDARY, pixmap
from .widgets import Banner, FlowLayout, card, label, separator, tag

STATUS_TAGS = dict(agreed="ok", proposed="proposed", cancelled="cancelled", disputed="disputed")
KIND_DOTS = dict(decision=GREEN, action=ACCENT, risk=ORANGE, question="#7a3fc4", point=GREY)
AVATARS = ["#8e5bd8", "#2f8f62", "#d0661a", "#0a7aff", "#c2185b", "#5f6b7a"]
PART_SIZE = 15  # summaries made before parts existed: group the timeline by this many points


def sources_tooltip(lookup, mid, ids):
    """Current text of the source replies, escaped: the transcript may have been edited."""
    if not mid:
        return ""
    rows = [row for row in (lookup(mid, sid) for sid in ids) if row]
    if not rows:
        return ""
    noun = "реплика" if len(rows) == 1 else "реплики" if len(rows) < 5 else "реплик"
    excerpts = "".join(
        f"<p><b>{stamp(row['start'])}–{stamp(row['end'])}</b><br>{escape(str(row['text']))}</p>"
        for row in rows
    )
    hint = "Нажмите, чтобы прослушать по порядку." if len(rows) > 1 else "Нажмите, чтобы прослушать."
    return f"<p><b>Источники · {len(rows)} {noun}</b></p>{excerpts}<p>{hint}</p>"


class EvidenceButton(QPushButton):
    """`▶ 00:41:22 · 3`: hover shows the replies, a click plays them in order."""

    def __init__(self, page, ids):
        self.page, self.ids = page, list(dict.fromkeys(ids))
        starts = [page.times[i] for i in self.ids if i in page.times]
        text = "▶  " + (stamp(min(starts)) if starts else "источник")
        if len(self.ids) > 1:
            text += f" · {len(self.ids)}"
        super().__init__(text)
        self.setObjectName("evidence")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clicked.connect(lambda: page.playGroup.emit(page.mid, list(self.ids)))

    def event(self, event):
        if event.type() == QEvent.Type.ToolTip:
            tip = self.page.sources_tooltip(self.ids)
            if tip:
                QToolTip.showText(event.globalPos(), tip, self)
            else:
                QToolTip.hideText()
            return True
        return super().event(event)


def plural_blocks(count):
    """Genitive after «для» and «конспект»: 1 блока, 2 блоков, 21 блока."""
    return f"{count} {'блока' if count % 10 == 1 and count % 100 != 11 else 'блоков'}"


class SummaryPage(QScrollArea):
    playGroup = Signal(str, object)
    taskToggled = Signal(str, str, bool)

    def __init__(self, lookup, parent=None):
        super().__init__(parent)
        self.lookup = lookup
        self.mid = None
        self.times = {}
        self.sections = {}
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        holder = QWidget()
        holder.setObjectName("pageGround")
        outer = QHBoxLayout(holder)
        outer.setContentsMargins(32, 32, 32, 56)
        outer.setSpacing(32)
        outer.addStretch(1)
        self.column_widget = QWidget()
        self.column_widget.setMaximumWidth(720)
        self.column_widget.setMinimumWidth(420)
        self.column = QVBoxLayout(self.column_widget)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(36)
        outer.addWidget(self.column_widget, 10)
        self.toc_widget = QWidget()
        self.toc_widget.setFixedWidth(190)
        self.toc = QVBoxLayout(self.toc_widget)
        self.toc.setContentsMargins(0, 4, 0, 0)
        self.toc.setSpacing(2)
        outer.addWidget(self.toc_widget, 0, Qt.AlignmentFlag.AlignTop)
        outer.addStretch(1)
        self.setWidget(holder)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.arrange()

    def arrange(self):
        """Risks and questions side by side when there is room, one under the other when not."""
        pair = getattr(self, "pair", None)
        if pair is not None:
            wide = self.column_widget.width() >= 620
            try:
                pair.setDirection(
                    QBoxLayout.Direction.LeftToRight if wide else QBoxLayout.Direction.TopToBottom
                )
            except RuntimeError:  # the layout was deleted with an older summary
                self.pair = None

    # -- public ---------------------------------------------------------------------

    def clear(self, message=""):
        self.mid = None
        self.pair = None
        self.sections = {}
        for layout in (self.column, self.toc):
            while layout.count():
                item = layout.takeAt(0)
                if widget := item.widget():
                    # Detached now, freed later: a cleared page must not still list old buttons.
                    widget.setParent(None)
                    widget.deleteLater()
        if message:
            self.column.addWidget(label(message, "secondary", wrap=True))
        self.column.addStretch(1)

    def show_summary(self, mid, summary, times, done=None):
        from .summary import summary_views

        self.clear()
        self.column.takeAt(self.column.count() - 1)  # the stretch clear() added
        self.mid, self.times = mid, times
        brief, detailed = summary_views(summary)
        if summary.get("source") == "external":
            note = Banner("info")
            note.set_text(
                "Сводку написала внешняя нейросеть",
                "Её текст — во вкладке «Итоговый текст». Решения и задачи со ссылками на реплики "
                "составляет модель на этом Mac: нажмите «Создать сводку» — ответ внешней нейросети "
                "будет заменён сводкой модели.",
            )
            self.column.addWidget(note)
            self.column.addStretch(1)
            return
        resolved = list(detailed.get("resolved") or [])
        done = done or {}
        warnings = [
            view.get(field)
            for view in (brief, detailed)
            for field in ("quality_warning", "generation_warning")
            if view.get(field)
        ] + ([detailed["resolution_warning"]] if detailed.get("resolution_warning") else [])
        if warnings:
            banner = Banner("warn")
            banner.set_text("Проверьте по записи", " ".join(dict.fromkeys(warnings)))
            self.column.addWidget(banner)
        prepared = summary.get("preparation") or {}
        if prepared.get("pruned"):
            minutes = max(1, round(prepared.get("pruned_seconds", 0) / 60))
            note = Banner("info")
            note.set_text(
                "Пустые фрагменты не вошли в сводку",
                f"{prepared['pruned']} реплик, около {minutes} мин: приветствия, проверка связи, шум. "
                "В расшифровке они остались.",
            )
            self.column.addWidget(note)
        budget = summary.get("budget") or {}
        cuts = []
        if budget.get("checks_skipped"):
            cuts.append(f"проверка по расшифровке пропущена для {plural_blocks(budget['checks_skipped'])}")
        if budget.get("terse_blocks"):
            cuts.append(f"конспект {plural_blocks(budget['terse_blocks'])} написан короче")
        if cuts:
            note = Banner("info")
            limit = max(1, round(budget.get("limit", 0) / 60))
            note.set_text(
                f"Чтобы уложиться в {limit} мин",
                "; ".join(cuts).capitalize()
                + ". Расшифровка полная. Без спешки: Настройки → Сводка → Время на сводку → "
                "«без ограничения», затем «Сделать сводку заново».",
            )
            self.column.addWidget(note)
        self.add_brief(brief)
        points = [item for item in brief["items"] if item["kind"] not in {"decision", "action"}]
        main = [item for item in points if item["kind"] == "point"]
        if main:
            self.add_list("main", "Главное", f"{len(main)}", main)
        source = resolved or brief["items"]
        decisions = [item for item in source if item["kind"] == "decision"]
        tasks = [item for item in source if item["kind"] == "action"]
        if decisions:
            active = sum(item.get("status") != "cancelled" for item in decisions)
            count = f"{active} действуют" + (
                f" · {len(decisions) - active} отменено" if len(decisions) > active else ""
            )
            self.add_list("decisions", "Решения", count, decisions, tags=True)
        if tasks:
            self.add_tasks(tasks, done)
        risks = [item for item in points if item["kind"] == "risk"]
        questions = [item for item in points if item["kind"] == "question"]
        if not risks and not questions:
            ledger = detailed["items"]
            risks = [item for item in ledger if item["kind"] == "risk"][:6]
            questions = [item for item in ledger if item["kind"] == "question"][:6]
        if risks or questions:
            self.add_risks(risks, questions)
        self.add_timeline(detailed)
        self.column.addStretch(1)
        self.toc.addStretch(1)
        self.arrange()

    def plain_text(self, section=None):
        roots = [self.sections[section]] if section else [self.column_widget]
        return "\n".join(
            widget.text()
            for root in roots
            for widget in root.findChildren(QWidget)
            if isinstance(widget, (QLabel, QPushButton, QCheckBox)) and widget.text()
        )

    def evidence_buttons(self, section=None):
        root = self.sections[section] if section else self.column_widget
        return root.findChildren(EvidenceButton)

    def sources_tooltip(self, ids):
        return sources_tooltip(self.lookup, self.mid, ids)

    # -- sections ---------------------------------------------------------------------

    def section(self, key, title, count=""):
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        if title:
            head = QHBoxLayout()
            head.setSpacing(8)
            head.addWidget(label(title, "h2"))
            if count:
                counter = label(count, "secondary")
                counter.setStyleSheet("font-size: 13px; padding-top: 4px;")
                head.addWidget(counter)
            head.addStretch(1)
            layout.addLayout(head)
        self.sections[key] = box
        self.column.addWidget(box)
        if title:
            link = QPushButton(title + (f" · {count.split()[0]}" if count else ""))
            link.setFlat(True)
            link.setStyleSheet(
                "QPushButton { text-align: left; color: #424245; padding: 0 10px; min-height: 28px; "
                "background: transparent; border-radius: 6px; font-weight: 400; }"
                "QPushButton:hover { background: rgba(0,0,0,0.05); }"
            )
            link.clicked.connect(lambda checked=False, w=box: self.ensureWidgetVisible(w, 0, 24))
            self.toc.addWidget(link)
        return layout

    def add_brief(self, brief):
        if not self.toc.count():
            self.toc.addWidget(label("НА ЭТОЙ СТРАНИЦЕ", "caption"))
        layout = self.section("brief", "")
        layout.addWidget(label("КРАТКО", "caption"))
        overview = label(brief["overview"], "lead", wrap=True, selectable=True)
        layout.addWidget(overview)
        if brief.get("topics"):
            pills = QWidget()
            flow = FlowLayout(pills)
            for topic in brief["topics"][:12]:
                flow.addWidget(label(topic, "pill"))
            layout.addWidget(pills)
        link = QPushButton("Кратко")
        link.setFlat(True)
        link.setStyleSheet(
            "QPushButton { text-align: left; color: #1d1d1f; padding: 0 10px; min-height: 28px; "
            "background: rgba(0,0,0,0.05); border-radius: 6px; }"
        )
        link.clicked.connect(lambda: self.verticalScrollBar().setValue(0))
        self.toc.addWidget(link)

    def row(self, item, tags=False, timeline=False):
        widget = QWidget()
        line = QHBoxLayout(widget)
        line.setContentsMargins(18, 13, 14, 13)
        line.setSpacing(14)
        if timeline:
            starts = [self.times[i] for i in item.get("evidence", []) if i in self.times]
            when = label(stamp(min(starts)) if starts else "", "secondary")
            when.setStyleSheet("font-family: 'SF Mono', Menlo, monospace; font-size: 12px;")
            when.setFixedWidth(64)
            line.addWidget(when, 0, Qt.AlignmentFlag.AlignTop)
            dot = QLabel()
            dot.setFixedSize(8, 8)
            dot.setStyleSheet(f"background: {KIND_DOTS.get(item['kind'], GREY)}; border-radius: 4px;")
            line.addWidget(dot, 0, Qt.AlignmentFlag.AlignTop)
            line.itemAt(line.count() - 1).widget().setContentsMargins(0, 6, 0, 0)
        body = QVBoxLayout()
        body.setSpacing(6)
        status = item.get("status")
        if tags and status in STATUS_TAGS:
            row = QHBoxLayout()
            row.addWidget(tag(STATUS_LABELS[status], STATUS_TAGS[status]))
            row.addStretch(1)
            body.addLayout(row)
        text = label(item["text"], "body", wrap=True, selectable=True)
        text.setStyleSheet("font-size: 15px;" if not timeline else "font-size: 14px;")
        if status == "cancelled":
            text.setStyleSheet(text.styleSheet() + f" color: {SECONDARY};")
        body.addWidget(text)
        if timeline and status in STATUS_TAGS and status != "agreed":
            body.addWidget(tag(STATUS_LABELS[status], STATUS_TAGS[status]))
        line.addLayout(body, 1)
        if item.get("evidence"):
            line.addWidget(EvidenceButton(self, item["evidence"]), 0, Qt.AlignmentFlag.AlignTop)
        return widget

    def add_rows(self, frame, rows):
        for index, widget in enumerate(rows):
            if index:
                frame.layout().addWidget(separator())
            frame.layout().addWidget(widget)

    def add_list(self, key, title, count, items, tags=False):
        layout = self.section(key, title, count)
        frame = card()
        self.add_rows(frame, [self.row(item, tags=tags) for item in items])
        layout.addWidget(frame)

    def add_tasks(self, tasks, done):
        layout = self.section("tasks", "Задачи", str(len(tasks)))
        frame = card()
        rows = []
        for item in tasks:
            widget = QWidget()
            line = QHBoxLayout(widget)
            line.setContentsMargins(18, 13, 14, 13)
            line.setSpacing(12)
            key = task_key(item)
            box = QCheckBox()
            box.setObjectName("task")
            box.setChecked(bool(done.get(key)))
            box.setToolTip("Отметить выполненной — отметка попадёт и в заметку Obsidian")
            box.toggled.connect(lambda state, k=key: self.taskToggled.emit(self.mid, k, state))
            line.addWidget(box, 0, Qt.AlignmentFlag.AlignTop)
            body = QVBoxLayout()
            body.setSpacing(6)
            body.addWidget(label(item["text"], "body", wrap=True, selectable=True))
            meta = QHBoxLayout()
            meta.setSpacing(10)
            if item.get("owner"):
                owner = str(item["owner"])
                avatar = QLabel(owner[:1].upper())
                avatar.setFixedSize(22, 22)
                avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
                color = AVATARS[sum(map(ord, owner)) % len(AVATARS)]
                avatar.setStyleSheet(
                    f"background: {color}; color: white; border-radius: 11px; font-size: 11px; font-weight: 600;"
                )
                meta.addWidget(avatar)
                meta.addWidget(label(owner))
            else:
                meta.addWidget(label("Ответственный не назван", "secondary"))
            if item.get("due"):
                icon = QLabel()
                icon.setPixmap(pixmap("calendar", "#c46b00", 13, 2))
                meta.addWidget(icon)
                due = label(str(item["due"]))
                due.setStyleSheet("color: #c46b00;")
                meta.addWidget(due)
            else:
                meta.addWidget(label("· срок не назван", "secondary"))
            if item.get("status") in {"proposed", "disputed", "cancelled"}:
                meta.addWidget(tag(STATUS_LABELS[item["status"]], STATUS_TAGS[item["status"]]))
            meta.addStretch(1)
            body.addLayout(meta)
            line.addLayout(body, 1)
            if item.get("evidence"):
                line.addWidget(EvidenceButton(self, item["evidence"]), 0, Qt.AlignmentFlag.AlignTop)
            rows.append(widget)
        self.add_rows(frame, rows)
        layout.addWidget(frame)

    def add_risks(self, risks, questions):
        count = len(risks) + len(questions)
        layout = self.section("risks", "Риски и вопросы", str(count))
        pair = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        pair.setSpacing(16)
        self.pair = pair
        for title, items, icon, color in [
            ("Риски", risks, "warning", "#c46b00"),
            ("Открытые вопросы", questions, "question", ACCENT),
        ]:
            column = QVBoxLayout()
            column.setSpacing(8)
            column.addWidget(label(title, "caption"))
            frame = card()
            frame.layout().setContentsMargins(16, 14, 16, 14)
            frame.layout().setSpacing(12)
            for item in items or [None]:
                line = QHBoxLayout()
                line.setSpacing(10)
                if item is None:
                    line.addWidget(label("Не прозвучали", "secondary"))
                else:
                    mark = QLabel()
                    mark.setPixmap(pixmap(icon, color, 18, 1.9))
                    line.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
                    line.addWidget(label(item["text"], "body", wrap=True, selectable=True), 1)
                    if item.get("evidence"):
                        line.addWidget(EvidenceButton(self, item["evidence"]), 0, Qt.AlignmentFlag.AlignTop)
                frame.layout().addLayout(line)
            column.addWidget(frame)
            column.addStretch(1)
            pair.addLayout(column, 1)
        layout.addLayout(pair)

    def add_timeline(self, detailed):
        items = detailed["items"]
        if not items:
            return
        parts = [p for p in detailed.get("parts") or [] if p.get("last", 0) > p.get("first", 0)]
        if not parts:
            parts = [
                dict(title="", first=start, last=min(start + PART_SIZE, len(items)))
                for start in range(0, len(items), PART_SIZE)
            ]
        layout = self.section("detailed", "По ходу встречи", f"{len(items)} пунктов · ничего не сокращено")
        frame = card()
        for number, part in enumerate(parts):
            chunk = items[part["first"] : part["last"]]
            starts = [self.times[i] for item in chunk for i in item.get("evidence", []) if i in self.times]
            span = f"{stamp(min(starts))} – {stamp(max(starts))}" if starts else ""
            title = part.get("title") or f"Часть {number + 1}"
            header = QPushButton(f"{title}")
            header.setObjectName("partHeader")
            header.setCheckable(True)
            header.setCursor(Qt.CursorShape.PointingHandCursor)
            info = QHBoxLayout(header)
            info.setContentsMargins(0, 0, 16, 0)
            info.addStretch(1)
            meta = label(
                f"{span}   ·   {len(chunk)} пунктов" if span else f"{len(chunk)} пунктов", "secondary"
            )
            meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            info.addWidget(meta)
            body = QWidget()
            body.setVisible(False)
            body_layout = QVBoxLayout(body)
            body_layout.setContentsMargins(0, 0, 0, 8)
            body_layout.setSpacing(0)
            if number:
                frame.layout().addWidget(separator())
            frame.layout().addWidget(header)
            frame.layout().addWidget(body)

            def toggle(state, body=body, chunk=chunk, header=header):
                # Built on first open: a two-hour meeting has hundreds of points.
                if state and not body.layout().count():
                    for item in chunk:
                        body.layout().addWidget(self.row(item, timeline=True))
                body.setVisible(state)
                header.setIcon(QIconCache.get("chevron-down" if state else "chevron-right"))

            header.toggled.connect(toggle)
            header.setIcon(QIconCache.get("chevron-right"))
            if number == 0:
                header.setChecked(True)
        layout.addWidget(frame)


class QIconCache:
    cache = {}

    @classmethod
    def get(cls, name):
        if name not in cls.cache:
            from .theme import icon

            cls.cache[name] = icon(name, SECONDARY, 12, 2.5)
        return cls.cache[name]
