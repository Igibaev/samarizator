"""HTML rendering of a summary view, used where rich text is needed outside the window."""

from html import escape

from .knowledge import LABELS, STATUS_LABELS


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
