"""Render the app icon into an .iconset folder; build.sh turns it into .icns with iconutil."""

import math
import sys
from pathlib import Path

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen


def render(size):
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    margin = size * 0.09
    rect = QRectF(margin, margin, size - 2 * margin, size - 2 * margin)
    gradient = QLinearGradient(rect.topLeft(), rect.bottomRight())
    gradient.setColorAt(0, QColor("#0f7a63"))
    gradient.setColorAt(1, QColor("#0b4f5c"))
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.2, size * 0.2)
    p.fillPath(path, gradient)
    # A waveform that settles into lines of text: audio in, notes out.
    pen = QPen(QColor("#e9fbf4"), max(1.0, size * 0.045), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    p.setPen(pen)
    bars = 7
    left, width = rect.left() + rect.width() * 0.2, rect.width() * 0.6
    for i in range(bars):
        x = left + width * i / (bars - 1)
        height = rect.height() * (0.08 + 0.22 * abs(math.sin(i * 1.3 + 0.4)))
        y = rect.top() + rect.height() * 0.36
        p.drawLine(int(x), int(y - height / 2), int(x), int(y + height / 2))
    for i, share in enumerate([0.6, 0.45]):
        y = rect.top() + rect.height() * (0.68 + i * 0.13)
        p.drawLine(int(left), int(y), int(left + width * share / 0.6), int(y))
    p.end()
    return image


def main():
    target = Path(sys.argv[1])
    target.mkdir(parents=True, exist_ok=True)
    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - QImage text/fonts need it
    for base in [16, 32, 128, 256, 512]:
        render(base).save(str(target / f"icon_{base}x{base}.png"))
        render(base * 2).save(str(target / f"icon_{base}x{base}@2x.png"))


if __name__ == "__main__":
    main()
