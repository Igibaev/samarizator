import pytest
from test_summary_evidence import evidence_window as evidence_window

from samarizator.playback import evidence_intervals


def test_intervals_merge_nearby_and_overlapping_sources_and_skip_long_gaps():
    rows = [
        dict(start=1207, end=1210),
        dict(start=1198, end=1201),
        dict(start=1205, end=1208),
        dict(start=1300, end=1304),
        dict(start=1198, end=1200),
    ]
    ranges = evidence_intervals(rows, duration=1304)
    assert [(r["start"], r["end"]) for r in ranges] == [(1197, 1211), (1299, 1304)]
    assert evidence_intervals([dict(start=0, end=2)])[0]["start"] == 0
    assert evidence_intervals([dict(start=3, end=2)]) == []


@pytest.mark.parametrize("tab", ["summary", "detailed_summary", "resolved_summary"])
def test_group_click_advances_queue_and_stop_cancels_all(evidence_window, monkeypatch, tab):
    from PySide6.QtCore import Qt, QUrl
    from PySide6.QtTest import QTest

    w, app, mid, source, _ = evidence_window
    browser = getattr(w, tab)
    rows = w.store.segments(mid)
    ids = [rows[i]["id"] for i in [201, 2, 0, 2]]  # unsorted evidence with a duplicate
    view = dict(overview="Тема", items=[dict(kind="point", text="Тезис", evidence=ids)])
    browser.show_summary(mid, view, {r["id"]: str(r["start"]) for r in rows})
    w.tabs.setCurrentWidget(browser)
    app.processEvents()
    calls, processes = [], []

    class Process:
        exit_code = None

        def poll(self):
            return self.exit_code

        def terminate(self):
            self.exit_code = 0

    def launch(args, **kwargs):
        calls.append(args)
        process = Process()
        processes.append(process)
        return process

    monkeypatch.setattr("samarizator.app.shutil.which", lambda _: "/usr/bin/ffplay")
    monkeypatch.setattr("samarizator.app.subprocess.Popen", launch)
    cursor = browser.document().find("Прослушать всё")
    cursor.setPosition(cursor.selectionStart() + 1)
    QTest.mouseClick(browser.viewport(), Qt.MouseButton.LeftButton, pos=browser.cursorRect(cursor).center())
    assert len(calls) == 1 and len(w.playback_queue) == 1
    assert calls[0][calls[0].index("-ss") + 1] == "0.0"
    assert calls[0][calls[0].index("-t") + 1] == "8.5"
    assert "1/2" in w.playback_label.text()
    processes[0].exit_code = 0
    w.poll()
    assert len(calls) == 2 and not w.playback_queue
    assert calls[1][calls[1].index("-ss") + 1] == "602.0"
    assert calls[1][-1] == str(source)
    assert "2/2" in w.playback_label.text()
    processes[1].exit_code = 0
    w.poll()
    assert not w.stop_button.isEnabled() and not w.player_proc

    browser.activate_evidence(QUrl("samarizator-group:0"))
    assert len(w.playback_queue) == 1
    w.stop_button.click()
    w.poll()
    assert len(calls) == 3 and not w.playback_queue

    browser.activate_evidence(QUrl("samarizator-group:0"))
    browser.activate_evidence(QUrl(f"samarizator-evidence:{ids[0]}"))
    assert not w.playback_queue and processes[-2].exit_code == 0
    assert calls[-1][calls[-1].index("-t") + 1] == "1.5"  # single badge still exact
    w.stop_playback()

    browser.activate_evidence(QUrl("samarizator-group:0"))
    processes[-1].exit_code = 1
    count = len(calls)
    w.poll()
    assert len(calls) == count and not w.playback_queue
    assert "Ошибка воспроизведения" in w.playback_label.text()

    browser.setPlainText("Очищено")
    browser.activate_evidence(QUrl("samarizator-group:0"))
    assert len(calls) == count
