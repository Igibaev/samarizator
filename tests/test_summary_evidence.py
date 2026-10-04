import json

import pytest


@pytest.fixture
def evidence_window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    w = Window()
    source = tmp_path / "call with spaces.wav"
    source.write_bytes(b"fixture")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(
        mid,
        0,
        [dict(start=i * 3.0, end=i * 3.0 + 1.5, speaker="Анна", text=f"Реплика {i}") for i in range(202)],
    )
    row = w.store.segments(mid, offset=201)[0]
    view = dict(
        overview="Обсудили бюджет <img src='https://example.com/tracker'>",
        items=[dict(kind="decision", text="Бюджет согласован", evidence=[row["id"]], status="agreed")],
        topics=[],
    )
    w.store.update(
        mid, summary=json.dumps(dict(**view, brief=view, detailed=dict(**view, resolved=view["items"])))
    )
    w.mid = mid
    w.refresh_list()
    w.set_view("summary")
    w.show()
    app.processEvents()
    yield w, app, mid, source, row
    w.timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()


def test_evidence_hover_reads_current_source_and_escapes_html(evidence_window, monkeypatch):
    from PySide6.QtCore import QEvent, QPoint, Qt
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication, QLabel, QToolTip

    w, app, mid, _, row = evidence_window
    page = w.summary_page
    assert w.stack.currentWidget() is page
    # A source on the second transcript page is available without changing pages.
    assert row["id"] not in {r["id"] for r in w.visible_rows}
    with w.store.connect() as db:
        db.execute("UPDATE segments SET text=? WHERE id=?", ("Оригинал <b>17 млн</b> & срок", row["id"]))
    tips = []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: tips.append(args[1]))
    button = page.evidence_buttons("decisions")[0]
    assert button.text().startswith("▶") and "00:10:03" in button.text()
    QApplication.sendEvent(button, QHelpEvent(QEvent.Type.ToolTip, QPoint(2, 2), button.mapToGlobal(QPoint(2, 2))))
    assert len(tips) == 1
    assert "Анна" not in tips[0] and "00:10:03–00:10:04" in tips[0]
    assert "Оригинал &lt;b&gt;17 млн&lt;/b&gt; &amp; срок" in tips[0]
    # Model output is shown as text, never rendered: no remote image can load.
    overview = [label for label in page.findChildren(QLabel) if "<img" in label.text()]
    assert overview and all(label.textFormat() == Qt.TextFormat.PlainText for label in overview)
    assert page.sources_tooltip([999999]) == ""


def test_each_source_button_plays_its_replies_and_stops_the_previous(evidence_window, monkeypatch):
    w, app, _, source, row = evidence_window
    calls, processes = [], []

    class Process:
        stopped = False

        def poll(self):
            return 0 if self.stopped else None

        def terminate(self):
            self.stopped = True

    def launch(args, **kwargs):
        calls.append(args)
        p = Process()
        processes.append(p)
        return p

    monkeypatch.setattr("samarizator.app.shutil.which", lambda _: "/usr/bin/ffplay")
    monkeypatch.setattr("samarizator.app.subprocess.Popen", launch)
    buttons = w.summary_page.evidence_buttons("decisions") + w.summary_page.evidence_buttons("detailed")
    assert len(buttons) == 2
    for button in buttons:
        button.click()
        app.processEvents()
        assert w.stop_button.isVisible() and w.stop_button.isEnabled()
    assert len(calls) == 2
    for args in calls:
        assert args[args.index("-ss") + 1] == str(row["start"] - 1)
        assert args[args.index("-t") + 1] == "3.5"
        assert args[-1] == str(source) and "-nodisp" in args
    assert processes[0].stopped and not processes[1].stopped
    w.stop_button.click()
    assert processes[1].stopped and not w.stop_button.isEnabled()


def test_stale_or_missing_sources_do_not_launch_playback(evidence_window, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    w, _, mid, source, row = evidence_window
    calls, warnings = [], []
    monkeypatch.setattr("samarizator.app.subprocess.Popen", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: warnings.append(args))
    other = w.store.create(source, w.settings)
    assert w.store.segment(other, row["id"]) is None
    w.play_evidence_group(other, [row["id"]])
    assert not calls and not warnings
    source.unlink()
    w.play_evidence_group(mid, [row["id"]])
    assert not calls and len(warnings) == 1
    w.summary_page.clear("Сводки нет")
    assert not w.summary_page.evidence_buttons()
    assert w.summary_page.sources_tooltip([row["id"]]) == ""
