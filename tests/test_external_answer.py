"""The answer of an external AI, with the real data put back, saved as the record's summary."""

import json

import pytest

from samarizator.summary import EXTERNAL_TITLE, model_summary, summary_views, with_external_answer

ANSWER = "# Итоги встречи\n\n[Человек 1] пришлёт договор до пятницы, телефон [Телефон 1]."
SPEECH = [
    dict(start=0, end=4, speaker="Речь", text="Ирина Петрова пришлёт договор до пятницы."),
    dict(start=4, end=8, speaker="Речь", text="Её телефон +7 912 345-67-89."),
]


def model_view():
    item = dict(kind="decision", text="Договор до пятницы", evidence=[1], status="agreed")
    view = dict(overview="Обсудили договор.", items=[item], topics=["договор"])
    return dict(
        **view,
        brief=view,
        detailed=dict(view, resolved=[item]),
        final=dict(title="Протокол встречи", format="protocol", text="# Протокол\n\nДоговор до пятницы."),
    )


def test_an_answer_becomes_the_summary_of_a_record_without_one():
    summary = with_external_answer(None, "  Итоги: договор до пятницы.  ", "2026-10-04T18:00")
    assert summary["source"] == "external" and not model_summary(summary)
    assert summary["final"] == dict(
        title=EXTERNAL_TITLE,
        format="external",
        source="external",
        text="Итоги: договор до пятницы.",
        saved="2026-10-04T18:00",
        warning="",
    )
    brief, detailed = summary_views(summary)
    assert brief["items"] == [] and detailed["items"] == [] and detailed["resolved"] == []
    assert not model_summary(None) and not model_summary({})


def test_with_a_model_summary_only_the_final_text_changes():
    summary = with_external_answer(model_view(), "Ответ ИИ", "2026-10-04T18:00")
    assert model_summary(summary) and summary["brief"]["items"][0]["text"] == "Договор до пятницы"
    assert summary["final"]["text"] == "Ответ ИИ" and summary["final"]["source"] == "external"
    # A second answer replaces the first; the model's registers stay.
    again = with_external_answer(summary, "Второй ответ", "2026-10-04T19:00")
    assert again["final"]["text"] == "Второй ответ" and model_summary(again)


@pytest.fixture
def privacy(meeting, qapp):
    from samarizator.privacy_page import PrivacyPage

    store, mid, settings = meeting
    store.save_chunk(mid, 0, SPEECH)
    page = PrivacyPage(store)
    page.show_meeting(mid)
    yield store, mid, page
    page.deleteLater()


def test_the_restore_window_saves_the_answer_with_the_real_data(privacy):
    from samarizator.privacy_page import RestoreDialog

    store, mid, page = privacy
    saved = []
    page.answerSaved.connect(saved.append)
    dialog = RestoreDialog(page.masker.ordered(), page, page.save_answer)
    dialog.save()  # nothing pasted yet
    assert "вставьте ответ" in dialog.state.text().lower() and not store.meeting(mid)["summary"]
    dialog.source.setPlainText(ANSWER)
    assert "Ирина Петрова" in dialog.result.toPlainText()
    dialog.save()
    summary = json.loads(store.meeting(mid)["summary"])
    assert summary["source"] == "external" and saved == [mid]
    assert "Ирина Петрова пришлёт договор" in summary["final"]["text"]
    assert "+7 912 345-67-89" in summary["final"]["text"] and "[Человек 1]" not in summary["final"]["text"]
    # The pseudonymised copy of the final text hides the names again.
    page.document_choice.setCurrentIndex(page.document_choice.findData("final"))
    assert "Ирина" not in page.document() and "[Человек 1]" in page.document()
    dialog.deleteLater()
    # Without a record to save into, the window only copies.
    plain = RestoreDialog([], page)
    assert not hasattr(plain, "save_button")
    plain.deleteLater()


def test_replacing_the_models_final_text_asks_first(privacy, monkeypatch):
    from samarizator.privacy_page import PrivacyPage

    store, mid, page = privacy
    store.update(mid, summary=json.dumps(model_view()))
    monkeypatch.setattr(PrivacyPage, "confirm_replace", lambda self: False)
    assert page.save_answer("Ответ ИИ") is False
    assert json.loads(store.meeting(mid)["summary"])["final"]["format"] == "protocol"
    monkeypatch.setattr(PrivacyPage, "confirm_replace", lambda self: True)
    assert page.save_answer("Ответ ИИ") is True
    summary = json.loads(store.meeting(mid)["summary"])
    assert summary["final"]["text"] == "Ответ ИИ" and summary["brief"]["items"]
    # The next answer replaces the previous external one without asking.
    monkeypatch.setattr(PrivacyPage, "confirm_replace", lambda self: pytest.fail("asked again"))
    assert page.save_answer("Новый ответ ИИ") is True


def test_an_external_summary_is_exported_and_shown_for_what_it_is(meeting, qapp):
    from PySide6.QtWidgets import QLabel

    from samarizator.knowledge import export
    from samarizator.pages import FinalPage
    from samarizator.summary_page import SummaryPage

    store, mid, settings = meeting
    store.save_chunk(mid, 0, SPEECH)
    summary = with_external_answer(None, "# Итоги\n\nДоговор до пятницы.", "2026-10-04T18:00")
    store.update(mid, summary=json.dumps(summary))
    note = export(store, mid, settings).read_text()
    assert EXTERNAL_TITLE in note and "Договор до пятницы." in note
    page = SummaryPage(store.segment)
    page.show_summary(mid, summary, {})
    texts = " ".join(label.text() for label in page.findChildren(QLabel))
    assert "Сводку написала внешняя нейросеть" in texts
    final = FinalPage()
    final.set(summary["final"], "external")
    assert "Ответ внешней нейросети" in final.format.text() and "Сохранён ответ" in final.state.text()
    assert final.origin.isVisibleTo(final) and "по обезличенной копии" in final.origin.text()
    final.set(model_view()["final"], "protocol")
    assert not final.origin.isVisibleTo(final)
    page.deleteLater()
    final.deleteLater()


def test_the_window_shows_the_saved_answer_and_offers_the_models_summary(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication, QMessageBox

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    w = Window()
    source = tmp_path / "meeting.wav"
    source.write_bytes(b"fixture")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(mid, 0, SPEECH)
    w.store.save_checkpoint(mid, "asr_complete", 0, True)
    w.store.update(mid, status="review")
    w.mid = mid
    w.refresh_list()
    w.set_view("privacy")
    w.load_detail()
    w.show()
    app.processEvents()
    assert w.privacy_page.save_answer("# Итоги\n\nДоговор до пятницы.")
    app.processEvents()
    assert w.view == "final" and "Договор до пятницы." in w.final_text.toPlainText()
    assert "сохранён как итоговый текст" in w.progress.text()
    # Only the answer of an external AI: the model's summary is still offered, not a rewrite.
    assert w.summarize.isVisibleTo(w) and not w.regenerate_button.isEnabled()
    assert not w.chat_panel.source.buttons["summary"].isEnabled()
    shown = []
    monkeypatch.setattr(QMessageBox, "information", lambda *args: shown.append(args[1]))
    w.choose_format("executive")
    assert shown == ["Нужна сводка модели"] and w.job is None
    w.close()
