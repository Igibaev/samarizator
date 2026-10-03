"""Look of the app: macOS-style neutral greys, one blue accent, system font, line icons."""

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

ACCENT = "#0066cc"
TEXT = "#1d1d1f"
SECONDARY = "#636366"
SIDEBAR = "#ececf0"
GROUND = "#f5f5f7"
GREEN = "#28a745"
ORANGE = "#e08a00"
RED = "#d70015"
GREY = "#aeaeb2"

# 24×24 stroke paths, drawn in the requested colour.
ICONS = {
    "mic": '<rect x="9" y="3" width="6" height="11" rx="3"/><path d="M5 11a7 7 0 0 0 14 0M12 18v3"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>',
    "gear": '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1'
    'M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1"/>',
    "copy": '<rect x="9" y="9" width="12" height="12" rx="2.5"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>',
    "more": '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    "play": '<path d="M8 5v14l11-7z" fill="currentColor"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="2" fill="currentColor"/>',
    "pencil": '<path d="M4 20h4L19 9l-4-4L4 16z"/>',
    "warning": '<path d="M12 3 2 20h20z"/><path d="M12 10v4M12 17v.5"/>',
    "error": '<circle cx="12" cy="12" r="10"/><path d="M12 7v6M12 16.5v.5"/>',
    "question": '<circle cx="12" cy="12" r="10"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17v.5"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m8 12 3 3 5-6"/>',
    "chevron-right": '<path d="m9 6 6 6-6 6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "calendar": '<rect x="3" y="5" width="18" height="16" rx="3"/><path d="M3 10h18M8 3v4M16 3v4"/>',
    "lock": '<rect x="5" y="10" width="14" height="10" rx="2.5"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/>',
    "lines": '<path d="M4 6h16M4 10h16M4 14h10M4 18h7"/>',
    "folder": '<path d="M4 6h6l2 2h8v10H4z"/>',
    "sliders": '<path d="M4 7h10M18 7h2M4 17h4M12 17h8"/><circle cx="16" cy="7" r="2"/><circle cx="10" cy="17" r="2"/>',
    "wave": '<path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 11v2"/>',
    "download": '<path d="M12 4v12M6 11l6 6 6-6M5 20h14"/>',
    "send": '<path d="M12 19V5M6 11l6-6 6 6"/>',
    "chat": '<path d="M20 12a8 8 0 0 1-11.6 7.1L4 20l1-4.2A8 8 0 1 1 20 12z"/>',
    "obsidian": '<path d="M14 3h7v7M10 14 21 3M21 14v5a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5"/>',
}


def svg(name, color=TEXT, stroke=1.8):
    body = ICONS[name].replace("currentColor", color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke}" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def pixmap(name, color=TEXT, size=18, stroke=1.8, ratio=2.0):
    renderer = QSvgRenderer(QByteArray(svg(name, color, stroke).encode()))
    image = QPixmap(int(size * ratio), int(size * ratio))
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, size * ratio, size * ratio))
    painter.end()
    image.setDevicePixelRatio(ratio)
    return image


def icon(name, color="#424245", size=18, stroke=1.8):
    result = QIcon(pixmap(name, color, size, stroke))
    result.addPixmap(pixmap(name, "#c7c7cc", size, stroke), QIcon.Mode.Disabled)
    return result


def set_flag(widget, name, value):
    """Change a styling property and make Qt re-read the stylesheet for it."""
    if widget.property(name) != value:
        widget.setProperty(name, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


STYLESHEET = f"""
QWidget {{ font-size: 13px; color: {TEXT}; }}
QMainWindow, QWidget#content, QDialog {{ background: #ffffff; }}
QWidget#pageWhite {{ background: #ffffff; }}
QWidget#pageGround {{ background: {GROUND}; }}
QFrame#sidebar {{ background: {SIDEBAR}; border: none; border-right: 1px solid #d9d9de; }}
QFrame#toolbar {{ background: #ffffff; border: none; border-bottom: 1px solid #e5e5ea; }}
QFrame#footer {{ background: #ffffff; border: none; border-top: 1px solid #f0f0f3; }}
QLabel#title {{ font-size: 13px; font-weight: 600; }}
QLabel#subtitle, QLabel#secondary {{ color: {SECONDARY}; font-size: 12px; }}
QLabel#small {{ color: {SECONDARY}; font-size: 11px; }}
QLabel#h1 {{ font-size: 30px; font-weight: 700; }}
QLabel#h1small {{ font-size: 24px; font-weight: 700; }}
QLabel#h2 {{ font-size: 19px; font-weight: 700; }}
QLabel#caption {{ color: {SECONDARY}; font-size: 11px; font-weight: 600; }}
QLabel#lead {{ font-size: 18px; }}
QLabel#body {{ font-size: 14px; }}
QLabel#pill {{ background: #f0f0f3; border-radius: 12px; padding: 4px 11px; }}
QLabel#hint {{ color: {SECONDARY}; font-size: 12px; }}
QLabel[tag="ok"] {{ background: #e3f4e8; color: #1b6b33; border-radius: 5px; padding: 1px 7px; font-size: 11px; font-weight: 600; }}
QLabel[tag="proposed"] {{ background: #fdf0dc; color: #8a4b00; border-radius: 5px; padding: 1px 7px; font-size: 11px; font-weight: 600; }}
QLabel[tag="cancelled"] {{ background: #f0f0f3; color: {SECONDARY}; border-radius: 5px; padding: 1px 7px; font-size: 11px; font-weight: 600; }}
QLabel[tag="disputed"] {{ background: #fde8ea; color: #a1001a; border-radius: 5px; padding: 1px 7px; font-size: 11px; font-weight: 600; }}
QLabel[tag="info"] {{ background: #e8f0fb; color: {ACCENT}; border-radius: 5px; padding: 1px 7px; font-size: 11px; font-weight: 600; }}
QPushButton {{ background: #e8e8ed; border: none; border-radius: 8px; padding: 0 14px; min-height: 32px; font-weight: 500; }}
QPushButton:hover {{ background: #dedee3; }}
QPushButton:pressed {{ background: #d4d4da; }}
QPushButton:disabled {{ color: #a1a1a6; background: #f2f2f5; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: #ffffff; }}
QPushButton[primary="true"]:hover {{ background: #0058b0; }}
QPushButton[primary="true"]:disabled {{ background: #a9c7e8; color: #f4f8fc; }}
QPushButton[large="true"] {{ min-height: 44px; border-radius: 12px; font-size: 15px; padding: 0 24px; }}
QPushButton[flat="true"] {{ background: transparent; color: {ACCENT}; padding: 0 4px; }}
QPushButton[flat="true"]:hover {{ text-decoration: underline; }}
QPushButton#evidence {{ background: #e8f0fb; color: {ACCENT}; border-radius: 12px; min-height: 24px; padding: 0 10px; font-size: 12px; }}
QPushButton#evidence:hover {{ background: #d8e6f8; }}
QPushButton#partHeader {{ background: transparent; border-radius: 0; text-align: left; padding: 0 16px; min-height: 46px; font-weight: 600; }}
QPushButton#partHeader:hover {{ background: #fafafc; }}
QPushButton#modelRow {{ background: transparent; text-align: left; padding: 0 8px; color: {SECONDARY}; font-weight: 400; font-size: 12px; }}
QPushButton#modelRow:hover {{ background: rgba(0,0,0,0.05); }}
QPushButton#sideRow {{ background: transparent; text-align: left; padding: 6px 10px; color: {TEXT}; font-weight: 400; font-size: 13px; border-radius: 7px; }}
QPushButton#sideRow:hover {{ background: rgba(0,0,0,0.06); }}
QPushButton#sideRow:disabled {{ color: {GREY}; }}
QPushButton#warnRow {{ background: #fdf0dc; color: #8a4b00; text-align: left; padding: 0 10px; font-size: 12px; }}
QPushButton#warnRow:hover {{ background: #f9e5c4; }}
QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 6px; }}
QToolButton:hover {{ background: rgba(0,0,0,0.06); }}
QToolButton:pressed {{ background: rgba(0,0,0,0.1); }}
QToolButton::menu-indicator {{ image: none; width: 0; }}
QFrame#segmented {{ background: #e8e8ed; border-radius: 8px; }}
QFrame#segmented QPushButton {{ background: transparent; min-height: 26px; border-radius: 6px; padding: 0 14px; }}
QFrame#segmented QPushButton:checked {{ background: #ffffff; border: 1px solid #dcdce1; }}
QFrame#segmented QPushButton:hover:!checked {{ background: rgba(0,0,0,0.04); }}
QFrame#card {{ background: #ffffff; border: 1px solid #e5e5ea; border-radius: 14px; }}
QFrame#soft {{ background: {GROUND}; border-radius: 14px; }}
QFrame#modelOption {{ background: #ffffff; border: 1px solid #e5e5ea; border-radius: 12px; }}
QFrame#modelOption[selected="true"] {{ border: 2px solid {ACCENT}; background: #f5f9ff; }}
QFrame#modelOption:disabled {{ background: {GROUND}; }}
QFrame#modelOption QLabel#facts {{ color: {TEXT}; font-size: 12px; }}
QFrame#modelOption QLabel:disabled {{ color: {GREY}; }}
QFrame#separator {{ background: #ececf0; border: none; max-height: 1px; min-height: 1px; }}
QFrame#banner[kind="warn"] {{ background: #fff8ec; border-radius: 12px; }}
QFrame#banner[kind="error"] {{ background: #fff0f1; border-radius: 12px; }}
QFrame#banner[kind="info"] {{ background: #eef4fc; border-radius: 12px; }}
QFrame#chat {{ background: #ffffff; border: none; border-left: 1px solid #e5e5ea; }}
QWidget#chatHolder {{ background: #ffffff; }}
QFrame#bubble {{ background: #f0f0f3; border-radius: 14px; }}
QFrame#bubbleMine {{ background: {ACCENT}; border-radius: 14px; }}
QFrame#bubbleMine QLabel {{ color: #ffffff; }}
QFrame#bubbleMuted {{ background: {GROUND}; border-radius: 14px; }}
QFrame#bubbleMuted QLabel {{ color: {SECONDARY}; }}
QPushButton#suggestion {{ background: #f0f0f3; text-align: left; padding: 0 12px; min-height: 34px; border-radius: 10px; font-weight: 400; }}
QPushButton#suggestion:hover {{ background: #e5e5ea; }}
QPushButton#askButton:checked {{ background: #e8f0fb; color: {ACCENT}; }}
QFrame#playbar {{ background: #1d1d1f; border-radius: 12px; }}
QFrame#playbar QLabel {{ color: #ffffff; }}
QFrame#playbar QPushButton {{ background: #3a3a3c; color: #ffffff; min-height: 28px; }}
QLineEdit#search {{ background: rgba(0,0,0,0.06); border: none; border-radius: 8px; padding: 5px 8px; }}
QLineEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox {{ background: #ffffff; border: 1px solid #d1d1d6; border-radius: 7px; padding: 5px 8px; selection-background-color: #b3d4fb; }}
QLineEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {ACCENT}; }}
QComboBox {{ background: #ffffff; border: 1px solid #d1d1d6; border-radius: 6px; padding: 3px 10px; min-height: 22px; }}
QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ width: 0; border: none; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url("{{chevron}}"); width: 10px; height: 10px; }}
QComboBox[flat="true"] {{ border: none; background: transparent; color: {ACCENT}; font-weight: 500; padding: 0 2px; }}
QComboBox QAbstractItemView {{ background: #ffffff; border: 1px solid #d1d1d6; selection-background-color: {ACCENT}; selection-color: #ffffff; }}
QListWidget#recordings {{ background: transparent; border: none; outline: none; }}
QListWidget#nav {{ background: transparent; border: none; outline: none; }}
QListWidget#nav::item {{ height: 32px; border-radius: 7px; padding-left: 4px; }}
QListWidget#nav::item:selected {{ background: {ACCENT}; color: #ffffff; }}
QListWidget#nav::item:hover:!selected {{ background: rgba(0,0,0,0.05); }}
QTableWidget#transcript {{ background: #ffffff; border: none; gridline-color: transparent; selection-background-color: #e8f0fb; selection-color: {TEXT}; outline: none; }}
QTableWidget#transcript::item {{ padding: 8px 6px; border-bottom: 1px solid #f0f0f3; }}
QTextBrowser {{ background: #ffffff; border: none; }}
QScrollArea {{ border: none; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: rgba(0,0,0,0.22); border-radius: 3px; min-height: 30px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QMenu {{ background: #fafafc; border: 1px solid #d1d1d6; border-radius: 10px; padding: 5px; }}
QMenu::item {{ padding: 6px 22px 6px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {ACCENT}; color: #ffffff; }}
QMenu::item:disabled {{ color: #a1a1a6; }}
QMenu::separator {{ height: 1px; background: #e5e5ea; margin: 4px 8px; }}
QProgressBar {{ background: #ececf0; border: none; border-radius: 4px; max-height: 8px; min-height: 8px; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
QToolTip {{ background: #ffffff; color: {TEXT}; border: 1px solid #d1d1d6; border-radius: 8px; padding: 6px; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox#task::indicator {{ width: 18px; height: 18px; border-radius: 10px; border: 1.5px solid #aeaeb2; background: #ffffff; }}
QCheckBox#task::indicator:checked {{ background: {ACCENT}; border: 1.5px solid {ACCENT}; }}
"""


def apply_theme(app):
    """Palette, font and stylesheet. Separate from main() so a rendered window can be checked."""
    palette = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#ffffff"),
        (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.Base, "#ffffff"),
        (QPalette.ColorRole.AlternateBase, GROUND),
        (QPalette.ColorRole.Text, TEXT),
        (QPalette.ColorRole.Button, "#e8e8ed"),
        (QPalette.ColorRole.ButtonText, TEXT),
        (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"),
        (QPalette.ColorRole.PlaceholderText, "#8e8e93"),
        (QPalette.ColorRole.ToolTipBase, "#ffffff"),
        (QPalette.ColorRole.ToolTipText, TEXT),
    ]:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    font = QFont(app.font())
    font.setPixelSize(13)
    app.setFont(font)
    # Qt style sheets take images by path only: render the combo-box chevron once.
    from .config import data_dir

    chevron = data_dir() / "ui-chevron.png"
    if not chevron.exists():
        pixmap("chevron-down", SECONDARY, 10, 2.4).save(str(chevron))
    app.setStyleSheet(STYLESHEET.replace("{chevron}", chevron.as_posix()))
