"""What the window must make obvious: the next step, and why a button is off."""

import pytest

from samarizator.app import NEXT_STEP, explain


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    window = Window()
    yield window, app
    window.timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


def recording(window, tmp_path, status="new", complete=False):
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio")
    mid = window.store.create(source, window.settings)
    window.store.update(mid, title="Планёрка", status=status, duration=600)
    if complete:
        window.store.save_checkpoint(mid, "asr_complete", 0, True)
    window.mid = mid
    window.refresh_list()
    return mid


def test_disabled_buttons_say_why(window, tmp_path):
    win, _ = window
    win.controls()
    # Nothing selected: every step explains that a recording comes first.
    assert "Выберите запись" in win.transcribe.toolTip()
    assert "Выберите запись" in win.summarize.toolTip()

    recording(win, tmp_path)
    assert win.transcribe.isEnabled()
    assert "распознайте запись целиком" in win.summarize.toolTip().lower()


def test_exactly_one_step_is_highlighted(window, tmp_path):
    win, _ = window
    recording(win, tmp_path)
    assert win.transcribe.property("primary") is True
    assert win.summarize.property("primary") is False

    recording(win, tmp_path, status="review", complete=True)
    # Recognition is done, so the highlight moves on instead of the button turning into a label.
    assert win.transcribe.property("primary") is False
    assert win.summarize.property("primary") is True
    assert win.transcribe.text() == "1. Распознать / продолжить"
    assert "уже завершено" in win.transcribe.toolTip()


def test_status_line_names_the_next_action(window, tmp_path):
    win, _ = window
    recording(win, tmp_path, status="review", complete=True)
    assert NEXT_STEP["review"] in win.info.text()
    assert "Готова к проверке" in win.info.text()

    recording(win, tmp_path, status="recording")
    assert NEXT_STEP["recording"] in win.info.text()


def test_error_button_appears_only_with_an_error(window, tmp_path):
    win, _ = window
    recording(win, tmp_path, status="review", complete=True)
    # isHidden, not isVisible: an unshown window hides everything, which proves nothing.
    assert win.copy_error_button.isHidden()

    mid = recording(win, tmp_path, status="error")
    win.store.update(mid, error="Модель Whisper не найдена.")
    win.load_detail()
    assert not win.copy_error_button.isHidden() and win.copy_error_button.isEnabled()
    assert "Модель Whisper не найдена." in win.error_detail.text()


def test_explain_clears_a_stale_reason_when_the_button_becomes_usable():
    from PySide6.QtWidgets import QApplication, QPushButton

    QApplication.instance() or QApplication([])
    button = QPushButton("Шаг")
    explain(button, False, "Сначала выберите запись.")
    assert not button.isEnabled() and button.toolTip() == "Сначала выберите запись."
    explain(button, True)
    assert button.isEnabled()
