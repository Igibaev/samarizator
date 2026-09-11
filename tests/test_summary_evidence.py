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
    w.show()
    app.processEvents()
    yield w, app, mid, source, row
    w.timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()


def badge_position(browser):
    cursor = browser.document().find("▶")
    assert not cursor.isNull()
    cursor.setPosition(cursor.selectionStart() + 1)
    return browser.cursorRect(cursor).center()


def test_evidence_hover_reads_current_source_and_escapes_html(evidence_window, monkeypatch):
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QHelpEvent
    from PySide6.QtWidgets import QApplication, QToolTip

    w, app, mid, _, row = evidence_window
    browser = w.summary
    w.tabs.setCurrentWidget(browser)
    app.processEvents()
    # A source on the second transcript page is available without changing pages.
    assert row["id"] not in {r["id"] for r in w.visible_rows}
    with w.store.connect() as db:
        db.execute("UPDATE segments SET text=? WHERE id=?", ("Оригинал <b>17 млн</b> & срок", row["id"]))
    tips = []
    monkeypatch.setattr(QToolTip, "showText", lambda *args: tips.append(args[1]))
    pos = badge_position(browser)
    QApplication.sendEvent(
        browser.viewport(), QHelpEvent(QEvent.Type.ToolTip, pos, browser.viewport().mapToGlobal(pos))
    )
    assert len(tips) == 1
    assert "Анна" not in tips[0] and "00:10:03–00:10:04" in tips[0]
    assert "Оригинал &lt;b&gt;17 млн&lt;/b&gt; &amp; срок" in tips[0]
    assert "<img " not in browser.toHtml()  # model output is rendered as text, never a remote image
    assert browser.evidence_tooltip("https://example.com") == ""
    assert browser.evidence_tooltip("samarizator-evidence:999999") == ""


def test_click_plays_exact_interval_from_each_summary_and_stops_previous(evidence_window, monkeypatch):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

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
    for browser in [w.summary, w.detailed_summary, w.resolved_summary]:
        w.tabs.setCurrentWidget(browser)
        app.processEvents()
        QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=badge_position(browser))
        assert w.tabs.currentWidget() is browser
        assert w.stop_button.isVisible() and w.stop_button.isEnabled()
    assert len(calls) == 3
    for args in calls:
        assert args[args.index("-ss") + 1] == str(row["start"])
        assert args[args.index("-t") + 1] == "1.5"
        assert args[-1] == str(source) and "-nodisp" in args
    assert processes[0].stopped and processes[1].stopped and not processes[2].stopped
    w.stop_button.click()
    assert processes[2].stopped and not w.stop_button.isEnabled()


def test_stale_or_missing_sources_do_not_launch_playback(evidence_window, monkeypatch):
    from PySide6.QtCore import QUrl
    from PySide6.QtWidgets import QMessageBox

    w, _, mid, source, row = evidence_window
    calls, warnings = [], []
    monkeypatch.setattr("samarizator.app.subprocess.Popen", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(QMessageBox, "information", lambda *args: warnings.append(args))
    other = w.store.create(source, w.settings)
    assert w.store.segment(other, row["id"]) is None
    w.play_evidence(other, row["id"])
    assert not calls and not warnings
    source.unlink()
    w.play_evidence(mid, row["id"])
    assert not calls and len(warnings) == 1
    w.summary.setPlainText("Сводки нет")
    w.summary.activate_evidence(QUrl(f"samarizator-evidence:{row['id']}"))
    assert not calls and len(warnings) == 1
