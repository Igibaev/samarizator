"""Local evidence links: rich summary, on-demand transcript lookup, explicit playback."""

import re
from html import escape

from PySide6.QtCore import QEvent, Signal
from PySide6.QtWidgets import QTextBrowser, QToolTip

from .knowledge import LABELS, STATUS_LABELS, stamp


def html_text(value):
    return escape(str(value)).replace("\n", "<br>")


def summary_html(view, refs):
    parts = [
        '<p style="color:#59716a;font-size:small">Наведите на значок воспроизведения, '
        "чтобы увидеть исходный текст; нажмите, чтобы прослушать все связанные фрагменты.</p>",
        f"<p>{html_text(view['overview'])}</p>",
    ]
    for field in ("quality_warning", "generation_warning"):
        if not view.get(field):
            continue
        parts.append(
            '<p style="color:#8a5a00;font-size:small">'
            + html_text(view[field])
            + "</p>"
        )
    for kind, label in LABELS.items():
        items = [(index, item) for index, item in enumerate(view["items"]) if item["kind"] == kind]
        if not items:
            continue
        parts.append(f"<h3>{label}</h3><ul>")
        for index, item in items:
            text = html_text(item["text"])
            state = STATUS_LABELS.get(item.get("status"), "")
            if state:
                text += f" [{state}]"
            if item.get("owner"):
                text += " — " + html_text(item["owner"])
            if item.get("due"):
                text += " · " + html_text(item["due"])
            has_sources = any(sid in refs for sid in item["evidence"])
            play = ""
            if has_sources:
                play = (
                    f'&nbsp; <a href="samarizator-group:{index}" '
                    'style="text-decoration:none;color:#005f50">'
                    '<span style="background-color:#c4e2d6;font-size:small">&nbsp;▶&nbsp;</span></a>'
                )
            parts.append(f"<li><p>{text}{play}</p></li>")
        parts.append("</ul>")
    return "".join(parts)


class SummaryBrowser(QTextBrowser):
    playEvidence = Signal(str, int)
    playGroup = Signal(str, object)

    def __init__(self, lookup, parent=None):
        super().__init__(parent)
        self.lookup = lookup
        self.meeting_id = None
        self.evidence = set()
        self.groups = {}
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.anchorClicked.connect(self.activate_evidence)

    def show_summary(self, mid, view, refs):
        self.meeting_id = mid
        self.evidence = {sid for item in view["items"] for sid in item["evidence"] if sid in refs}
        self.groups = {
            index: list(dict.fromkeys(sid for sid in item["evidence"] if sid in refs))
            for index, item in enumerate(view["items"])
        }
        QToolTip.hideText()
        self.setHtml(summary_html(view, refs))

    def setPlainText(self, text):
        self.meeting_id = None
        self.evidence = set()
        self.groups = {}
        QToolTip.hideText()
        super().setPlainText(text)

    def clear(self):
        self.setPlainText("")

    def evidence_id(self, link):
        match = re.fullmatch(r"samarizator-evidence:([0-9]+)", link)
        sid = int(match[1]) if match else None
        return sid if self.meeting_id and sid in self.evidence else None

    def activate_evidence(self, url):
        group = self.group_ids(url.toString())
        if group:
            self.playGroup.emit(self.meeting_id, group)
            return
        sid = self.evidence_id(url.toString())
        if sid is not None:
            self.playEvidence.emit(self.meeting_id, sid)

    def group_ids(self, link):
        match = re.fullmatch(r"samarizator-group:([0-9]+)", link)
        return self.groups.get(int(match[1]), []) if match and self.meeting_id else []

    def evidence_tooltip(self, link):
        group = self.group_ids(link)
        if group:
            rows = [self.lookup(self.meeting_id, sid) for sid in group]
            rows = [row for row in rows if row]
            if not rows:
                return ""
            excerpts = "".join(
                f"<p><b>{stamp(row['start'])}–{stamp(row['end'])}</b><br>"
                f"{html_text(row['text'])}</p>"
                for row in rows
            )
            return (
                "<p><b>Источники тезиса</b></p>"
                + excerpts
                + "<p>Нажмите ▶, чтобы прослушать их по порядку. "
                "Близкие фрагменты объединяются.</p>"
            )
        sid = self.evidence_id(link)
        row = self.lookup(self.meeting_id, sid) if sid is not None else None
        if not row:
            return ""
        return (
            f"<p><b>{stamp(row['start'])}–{stamp(row['end'])}</b></p>"
            f'<p style="white-space:pre-wrap">{html_text(row["text"])}</p>'
            "<p>Нажмите на метку, чтобы прослушать этот фрагмент.</p>"
        )

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            tooltip = self.evidence_tooltip(self.anchorAt(event.pos()))
            if tooltip:
                QToolTip.showText(event.globalPos(), tooltip, self.viewport())
            else:
                QToolTip.hideText()
            return True
        return super().viewportEvent(event)
