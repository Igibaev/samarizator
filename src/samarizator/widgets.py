"""Building blocks of the window: segmented control, list rows, progress ring, steps."""

from PySide6.QtCore import QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyledItemDelegate,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, GREEN, GREY, SECONDARY, TEXT, pixmap, set_flag


def label(text="", name=None, wrap=False, selectable=False):
    widget = QLabel(text)
    widget.setTextFormat(Qt.TextFormat.PlainText)
    if name:
        widget.setObjectName(name)
    widget.setWordWrap(wrap)
    if selectable:
        widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def tag(text, kind):
    widget = label(text)
    widget.setProperty("tag", kind)
    widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return widget


def separator():
    line = QFrame()
    line.setObjectName("separator")
    line.setFrameShape(QFrame.Shape.NoFrame)
    return line


def card():
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    return frame


def primary(button, value=True, large=False):
    set_flag(button, "primary", value)
    if large:
        set_flag(button, "large", True)
    return button


class SegmentedControl(QFrame):
    """macOS-style segmented control. Buttons stay ordinary checkable QPushButtons."""

    changed = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("segmented")
        self.layout_ = QHBoxLayout(self)
        self.layout_.setContentsMargins(2, 2, 2, 2)
        self.layout_.setSpacing(2)
        self.buttons = {}
        self.current = None

    def add(self, key, text):
        button = QPushButton(text)
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(lambda checked=False, k=key: self.select(k, emit=True))
        self.layout_.addWidget(button)
        self.buttons[key] = button
        if self.current is None:
            self.select(key)
        return button

    def select(self, key, emit=False):
        self.current = key
        for name, button in self.buttons.items():
            button.setChecked(name == key)
        if emit:
            self.changed.emit(key)


class RecordingDelegate(QStyledItemDelegate):
    """Source-list row: optional day header, title, then duration · status dot · status."""

    HEADER, INFO = Qt.ItemDataRole.UserRole + 1, Qt.ItemDataRole.UserRole + 2
    ROW, HEAD = 48, 28

    def sizeHint(self, option, index):
        extra = self.HEAD if index.data(self.HEADER) else 0
        return QSize(option.rect.width(), self.ROW + extra)

    def paint(self, painter, option, index):
        info = index.data(self.INFO) or {}
        header = index.data(self.HEADER)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRect(option.rect)
        base = QFont(option.font)
        if header:
            font = QFont(base)
            font.setPixelSize(11)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(SECONDARY))
            painter.drawText(rect.adjusted(10, 10, -10, 0), Qt.AlignmentFlag.AlignLeft, header)
            rect.setTop(rect.top() + self.HEAD)
        row = rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(ACCENT))
            painter.drawRoundedRect(row, 7, 7)
        elif option.state & QStyle.StateFlag.State_MouseOver:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(0, 0, 0, 13))
            painter.drawRoundedRect(row, 7, 7)
        title = QFont(base)
        title.setPixelSize(13)
        title.setWeight(QFont.Weight.DemiBold if selected else QFont.Weight.Medium)
        painter.setFont(title)
        painter.setPen(QColor("#ffffff" if selected else TEXT))
        metrics = QFontMetrics(title)
        text = metrics.elidedText(info.get("title", ""), Qt.TextElideMode.ElideRight, row.width() - 20)
        painter.drawText(
            QRect(row.left() + 10, row.top() + 6, row.width() - 20, 18), Qt.AlignmentFlag.AlignLeft, text
        )
        meta = QFont(base)
        meta.setPixelSize(12)
        painter.setFont(meta)
        color = QColor("#e3eefa" if selected else SECONDARY)
        painter.setPen(color)
        x, y = row.left() + 10, row.top() + 26
        duration = info.get("duration", "")
        if duration:
            painter.drawText(QRect(x, y, 200, 16), Qt.AlignmentFlag.AlignLeft, duration + "  ·")
            x += QFontMetrics(meta).horizontalAdvance(duration + "  ·") + 6
        dot = QColor("#ffffff" if selected and info.get("dot") == ACCENT else info.get("dot", GREY))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(dot)
        painter.drawEllipse(QRectF(x, y + 4.5, 7, 7))
        painter.setPen(color)
        status = QFontMetrics(meta).elidedText(
            info.get("status", ""), Qt.TextElideMode.ElideRight, row.right() - x - 20
        )
        painter.drawText(QRect(x + 12, y, row.right() - x - 12, 16), Qt.AlignmentFlag.AlignLeft, status)
        painter.restore()


class ProgressRing(QWidget):
    """Determinate ring with a centred label; None spins as an indeterminate arc."""

    def __init__(self, size=128, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.value = None
        self.text = ""
        self.color = QColor(ACCENT)
        self.angle = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.spin)

    def set(self, value, text="", color=ACCENT):
        self.value, self.text, self.color = value, text, QColor(color)
        if value is None and not self.timer.isActive():
            self.timer.start(40)
        elif value is not None:
            self.timer.stop()
        self.update()

    def spin(self):
        self.angle = (self.angle + 8) % 360
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        width = 9
        box = QRectF(width / 2 + 1, width / 2 + 1, self.width() - width - 2, self.height() - width - 2)
        painter.setPen(QPen(QColor("#ececf0"), width))
        painter.drawEllipse(box)
        pen = QPen(self.color, width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        if self.value is None:
            painter.drawArc(box, int((90 - self.angle) * 16), int(-90 * 16))
        elif self.value > 0:
            painter.drawArc(box, 90 * 16, int(-360 * 16 * min(1.0, self.value)))
        font = QFont(self.font())
        font.setPixelSize(int(self.height() * 0.22))
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(TEXT))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.text)


def step_icon(state):
    size = 20
    image = QPixmap(size * 2, size * 2)
    image.fill(Qt.GlobalColor.transparent)
    image.setDevicePixelRatio(2.0)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    box = QRectF(1.5, 1.5, size - 3, size - 3)
    if state == "done":
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(GREEN))
        painter.drawEllipse(box)
        pen = QPen(QColor("#ffffff"), 2.0)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.drawPolyline([QPoint(6, 10), QPoint(9, 13), QPoint(14, 7)])
    elif state == "current":
        painter.setPen(QPen(QColor(ACCENT), 2))
        painter.drawEllipse(box)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT))
        painter.drawEllipse(QRectF(6, 6, 8, 8))
    else:
        painter.setPen(QPen(QColor("#c7c7cc"), 2))
        painter.drawEllipse(box)
    painter.end()
    return image


class StepList(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)
        self.rows = []

    def set_steps(self, steps):
        signature = list(steps)
        if signature == self.rows:
            return
        self.rows = signature
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if widget := item.widget():
                widget.setParent(None)
                widget.deleteLater()
        for index, (title, detail, state) in enumerate(steps):
            row = QWidget()
            line = QHBoxLayout(row)
            line.setContentsMargins(0, 11, 0, 11)
            line.setSpacing(12)
            icon = QLabel()
            icon.setPixmap(step_icon(state))
            line.addWidget(icon)
            name = label(title)
            if state == "current":
                name.setStyleSheet("font-weight: 600;")
            elif state == "todo":
                name.setStyleSheet(f"color: {SECONDARY};")
            line.addWidget(name, 1)
            extra = label(detail)
            extra.setStyleSheet(f"color: {ACCENT if state == 'current' else SECONDARY}; font-size: 12px;")
            line.addWidget(extra)
            self.layout_.addWidget(row)
            if index < len(steps) - 1:
                self.layout_.addWidget(separator())


class Banner(QFrame):
    """Soft coloured strip: icon, title, explanation and its own buttons."""

    ICONS = dict(warn=("warning", "#c46b00"), error=("error", "#d70015"), info=("check-circle", ACCENT))

    def __init__(self, kind="warn", parent=None):
        super().__init__(parent)
        self.setObjectName("banner")
        self.setProperty("kind", kind)
        line = QHBoxLayout(self)
        line.setContentsMargins(16, 12, 12, 12)
        line.setSpacing(12)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        line.addWidget(self.icon)
        text = QVBoxLayout()
        text.setSpacing(2)
        self.title = label("")
        self.title.setStyleSheet("font-weight: 600; font-size: 14px;")
        self.body = label("", "secondary", wrap=True, selectable=True)
        self.body.setStyleSheet("font-size: 13px;")
        text.addWidget(self.title)
        text.addWidget(self.body)
        line.addLayout(text, 1)
        self.actions = QHBoxLayout()
        self.actions.setSpacing(8)
        line.addLayout(self.actions)
        self.set_kind(kind)

    def set_kind(self, kind):
        name, color = self.ICONS[kind]
        self.icon.setPixmap(pixmap(name, color, 20, 1.9))
        set_flag(self, "kind", kind)

    def set_text(self, title, body=""):
        self.title.setText(title)
        self.title.setVisible(bool(title))
        self.body.setText(body)
        self.body.setVisible(bool(body))

    def add(self, widget):
        self.actions.addWidget(widget)
        return widget


class FlowLayout(QLayout):
    """Wrapping row for topic pills."""

    def __init__(self, parent=None, spacing=6):
        super().__init__(parent)
        self.items = []
        self.gap = spacing
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, apply=True)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            size = size.expandedTo(item.minimumSize())
        return size

    def arrange(self, rect, apply):
        x, y, line = rect.x(), rect.y(), 0
        for item in self.items:
            hint = item.sizeHint()
            if x + hint.width() > rect.right() + 1 and line > 0:
                x, y, line = rect.x(), y + line + self.gap, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self.gap
            line = max(line, hint.height())
        return y + line - rect.y()


class CenteredBrowser(QTextBrowser):
    """Text column of a comfortable reading width, centred in whatever space there is."""

    def __init__(self, max_width=700, parent=None):
        super().__init__(parent)
        self.max_width = max_width
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.document().setDocumentMargin(0)

    def resizeEvent(self, event):
        side = max(28, (self.width() - self.max_width) // 2)
        self.setViewportMargins(side, 36, side, 36)
        super().resizeEvent(event)
