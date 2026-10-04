"""Pseudonymised copies for external AI, and the Markdown transcript."""

import json

from samarizator.privacy import Masker, restore, transcript_markdown

TEXTS = [
    "Добрый день. Ирина Петрова, вы подготовили отчёт?",
    "Да, я отправила его Ирине вчера, телефон +7 (701) 555-12-34, почта irina.p@romashka.kz.",
    "Вера в успех у нас есть. А Вера Смирнова из ООО «Ромашка» подключится из Алматы.",
    "Карта 4276 3801 2345 6786, ИИН 900101300123, Петровой позвонит Аскар.",
    "Роман сказал, что роман читать не будет. Газпром не участвует, API готов.",
    "Сергей Иванович, проект Альфа идёт по плану. Бюджет 1,5 млн, 2026 год, 12 человек.",
]


def masked(**kwargs):
    masker = Masker(**kwargs).scan(TEXTS)
    return masker, [masker.mask(text) for text in TEXTS]


def test_people_in_every_case_get_one_label_and_identifiers_are_hidden():
    masker, out = masked(custom=["Альфа"])
    assert out[0] == "Добрый день. [Человек 1], вы подготовили отчёт?"
    assert out[1] == "Да, я отправила его [Человек 1] вчера, телефон [Телефон 1], почта [Почта 1]."
    assert out[2] == "Вера в успех у нас есть. А [Человек 2] из [Организация 1] подключится из [Место 1]."
    assert out[3] == "Карта [Номер 1], ИИН [Номер 2], [Человек 1] позвонит [Человек 3]."
    # A noun is not a name, an abbreviation is not a person; ordinary numbers stay.
    assert out[4].startswith("Роман сказал, что роман читать") and "API готов" in out[4]
    assert "[Организация 2]" in out[4]
    assert out[5] == "[Человек 4], проект [Скрыто 1] идёт по плану. Бюджет 1,5 млн, 2026 год, 12 человек."
    person = next(e for e in masker.ordered() if e.label == "[Человек 1]")
    assert person.value == "Ирина Петрова" and set(person.forms) == {"Ирина Петрова", "Ирине", "Петровой"}
    assert all(entry.count for entry in masker.ordered())


def test_custom_words_are_hidden_in_every_case_and_kept_items_stay():
    masker = Masker(custom=["Ромашка"]).scan(["Звонил в «Ромашку», «Ромашке» отправили счёт."])
    assert masker.mask("Звонил в «Ромашку», «Ромашке» отправили счёт.") == (
        "Звонил в «[Скрыто 1]», «[Скрыто 1]» отправили счёт."
    )
    kept, out = masked(disabled=["place:алматы"])
    assert "подключится из Алматы." in out[2]
    assert next(e for e in kept.ordered() if e.key == "place:алматы").enabled is False


def test_the_answer_of_an_external_ai_gets_the_real_data_back():
    masker, _ = masked()
    answer = "Итог: [Человек 1] отвечает за отчёт, позвонить по [Телефон 1]. [Человек 99] — нет в таблице."
    assert restore(answer, masker.ordered()) == (
        "Итог: Ирина Петрова отвечает за отчёт, позвонить по +7 (701) 555-12-34. "
        "[Человек 99] — нет в таблице."
    )


def test_transcript_markdown_has_timestamped_paragraphs():
    rows = [
        dict(start=5, end=8, speaker="Речь", text="Начинаем. Ирина, слово вам.", uncertain=0),
        dict(start=8.5, end=12, speaker="Речь", text="Спасибо.", uncertain=1),
        dict(start=75, end=80, speaker="Речь", text="Следующий вопрос.", uncertain=0),
    ]
    text = transcript_markdown(rows, "Планёрка", "03.10.2026")
    assert text.startswith("# Планёрка\n\n03.10.2026\n\n")
    assert "**00:00:05** Начинаем. Ирина, слово вам. Спасибо. *(проверить по записи)*" in text
    assert "**00:01:15** Следующий вопрос." in text and "Речь:" not in text
    two = [dict(rows[0], speaker="Микрофон"), dict(rows[2], speaker="Звонок")]
    assert "**00:00:05** Микрофон: " in transcript_markdown(two)
    masker = Masker().scan(row["text"] for row in rows)
    assert "[Человек 1], слово вам" in transcript_markdown(rows, mask=masker.mask)


def test_privacy_tab_shows_what_was_hidden_and_exports(tmp_path, monkeypatch, qapp):
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from samarizator.app import Window
    from samarizator.privacy_page import RestoreDialog

    w = Window()
    source = tmp_path / "Встреча с Петровой.wav"
    source.write_bytes(b"x")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(
        mid,
        0,
        [
            dict(start=1, end=3, speaker="Речь", text=TEXTS[0], uncertain=0),
            dict(start=4, end=9, speaker="Речь", text=TEXTS[1], uncertain=0),
        ],
    )
    final = dict(title="Протокол", format="protocol", text="# Протокол\n\n- Ирина Петрова готовит отчёт.")
    brief = dict(overview="Отчёт", items=[], topics=[])
    w.store.update(mid, summary=json.dumps(dict(**brief, brief=brief, final=final)), status="done")
    w.mid = mid
    w.refresh_list()
    w.set_view("privacy")
    page = w.privacy_page
    assert w.stack.currentWidget() is page
    labels = [page.table.item(row, 1).text() for row in range(page.table.rowCount())]
    assert labels == ["[Человек 1]", "[Почта 1]", "[Телефон 1]"]
    document = page.document()
    assert "Петров" not in document and "Встреча" not in document  # the title is not exported
    assert "[Человек 1], вы подготовили отчёт?" in document
    # The final text uses the same labels as the transcript.
    page.document_choice.setCurrentIndex(page.document_choice.findData("final"))
    assert page.document() == "# Протокол\n\n- [Человек 1] готовит отчёт.\n"
    # Keeping an item: unchecked, saved with the meeting, back in the text.
    page.table.item(1, 0).setCheckState(page.table.item(1, 0).checkState().__class__.Unchecked)
    assert w.store.checkpoint(mid, "privacy", 0)["disabled"] == ["email:irina.p@romashka.kz"]
    page.document_choice.setCurrentIndex(page.document_choice.findData("transcript"))
    assert "irina.p@romashka.kz" in page.document()
    dialog = RestoreDialog(page.masker.ordered())
    dialog.source.setPlainText("Ответ: [Человек 1] и [Телефон 1].")
    assert dialog.result.toPlainText() == "Ответ: Ирина Петрова и +7 (701) 555-12-34."
    # The plain transcript export from the «⋯» menu.
    target = tmp_path / "out.md"
    monkeypatch.setattr(
        "samarizator.app.QFileDialog.getSaveFileName", lambda *a, **k: (str(target), "Markdown (*.md)")
    )
    w.export_transcript()
    exported = target.read_text(encoding="utf-8")
    assert exported.startswith("# Встреча с Петровой") and "Ирина Петрова, вы подготовили" in exported
    dialog.deleteLater()
    w.timer.stop()
    w.close()
    w.deleteLater()
    qapp.processEvents()


def test_numbers_are_told_apart():
    texts = [
        "Договор 4500012345, мобильный 9161234567, паспорт 45 06 123456, СНИЛС 112-233-445 95.",
        "Сайт https://romashka.kz/price, офис 8 (727) 300-00-00.",
    ]
    masker = Masker().scan(texts)
    kinds = {entry.value: entry.kind for entry in masker.ordered()}
    assert kinds == {
        "4500012345": "number",
        "9161234567": "phone",
        "45 06 123456": "passport",
        "112-233-445 95": "snils",
        "https://romashka.kz/price": "url",
        "8 (727) 300-00-00": "phone",
    }
