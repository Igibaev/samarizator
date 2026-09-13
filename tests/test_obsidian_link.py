"""Opening a note must address the vault Obsidian really has, not a guessed name."""

import json

import pytest

from samarizator import obsidian


def config(tmp_path, vaults):
    path = tmp_path / "obsidian.json"
    path.write_text(
        json.dumps({"vaults": {vid: {"path": str(p), "ts": 1, "open": True} for vid, p in vaults}}),
        encoding="utf-8",
    )
    return path


def test_note_url_uses_the_registered_vault_id(tmp_path):
    vault = tmp_path / "Documents" / "Samarizator"
    (vault / "Meetings").mkdir(parents=True)
    note = vault / "Meetings" / "583e2492.md"
    note.write_text("# заметка")
    path = config(tmp_path, [("9f1c2d3e4a5b6c7d", vault)])

    url = obsidian.note_url(note, path)
    # The id, not the folder name: two vaults can share a name, an id cannot be wrong.
    assert url == "obsidian://open?vault=9f1c2d3e4a5b6c7d&file=Meetings%2F583e2492"


def test_note_inside_a_vault_registered_higher_up(tmp_path):
    documents = tmp_path / "Documents"
    note = documents / "Samarizator" / "Meetings" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# заметка")
    path = config(tmp_path, [("aaa1", documents)])

    # The vault is ~/Documents, so the file path has to be relative to that, not to the folder
    # chosen in settings — this is exactly the case that produced "Vault not found".
    assert obsidian.note_url(note, path) == (
        "obsidian://open?vault=aaa1&file=Samarizator%2FMeetings%2Fnote"
    )


def test_deepest_vault_wins(tmp_path):
    documents = tmp_path / "Documents"
    inner = documents / "Samarizator"
    note = inner / "Meetings" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# заметка")
    path = config(tmp_path, [("outer", documents), ("inner", inner)])

    assert "vault=inner" in obsidian.note_url(note, path)


def test_symlinked_documents_folder_still_matches(tmp_path):
    real_documents = tmp_path / "CloudDocs" / "Documents"
    (real_documents / "Vault" / "Meetings").mkdir(parents=True)
    note = real_documents / "Vault" / "Meetings" / "note.md"
    note.write_text("# заметка")
    link = tmp_path / "Documents"
    link.symlink_to(real_documents)
    # Obsidian registered the path through the symlink, the note is found through the real one.
    path = config(tmp_path, [("cloud", link / "Vault")])

    assert obsidian.note_url(note, path) == "obsidian://open?vault=cloud&file=Meetings%2Fnote"


def test_no_vault_means_no_url_instead_of_a_broken_one(tmp_path):
    note = tmp_path / "elsewhere" / "note.md"
    note.parent.mkdir()
    note.write_text("# заметка")
    path = config(tmp_path, [("other", tmp_path / "Documents" / "Other")])

    assert obsidian.note_url(note, path) is None
    assert obsidian.vault_for(note, path) is None


@pytest.mark.parametrize("content", ["", "не json", json.dumps({"vaults": {"x": {}}})])
def test_unreadable_config_is_not_an_error(tmp_path, content):
    path = tmp_path / "obsidian.json"
    path.write_text(content, encoding="utf-8")
    assert obsidian.vaults(path) == []
    assert obsidian.note_url(tmp_path / "note.md", path) is None


def test_missing_config_file_is_not_an_error(tmp_path):
    assert obsidian.vaults(tmp_path / "absent.json") == []


def window_with_note(tmp_path, monkeypatch, note):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    window = Window()
    source = tmp_path / "audio.wav"
    source.write_bytes(b"audio")
    window.mid = window.store.create(source, window.settings)
    window.store.update(window.mid, note=str(note), summary="{}")
    return window, app


def test_window_opens_the_note_through_the_registered_vault(tmp_path, monkeypatch):
    vault = tmp_path / "Documents" / "Samarizator"
    note = vault / "Meetings" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# заметка")
    monkeypatch.setenv("SAMARIZATOR_OBSIDIAN_CONFIG", str(config(tmp_path, [("vid1", vault)])))
    window, app = window_with_note(tmp_path, monkeypatch, note)

    opened = []
    monkeypatch.setattr(
        "samarizator.app.QDesktopServices.openUrl", lambda url: opened.append(url.toString()) or True
    )
    window.open_obsidian()
    assert opened == ["obsidian://open?vault=vid1&file=Meetings%2Fnote"]
    window.timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()


def test_window_explains_instead_of_firing_a_url_obsidian_cannot_resolve(tmp_path, monkeypatch):
    note = tmp_path / "Documents" / "Samarizator" / "Meetings" / "note.md"
    note.parent.mkdir(parents=True)
    note.write_text("# заметка")
    monkeypatch.setenv(
        "SAMARIZATOR_OBSIDIAN_CONFIG", str(config(tmp_path, [("other", tmp_path / "Other")]))
    )
    window, app = window_with_note(tmp_path, monkeypatch, note)

    opened = []
    monkeypatch.setattr(
        "samarizator.app.QDesktopServices.openUrl", lambda url: opened.append(url.toString()) or True
    )
    shown = {}

    def capture(box):
        shown["text"] = box.text()
        return 1  # the "Закрыть" button

    monkeypatch.setattr("samarizator.app.QMessageBox.exec", capture)
    window.open_obsidian()

    # No obsidian:// URL is fired at all, so the user never sees "Vault not found".
    assert opened == []
    assert "Открыть папку как хранилище" in shown["text"]
    assert str(tmp_path / "Other") in shown["text"]
    window.timer.stop()
    window.close()
    window.deleteLater()
    app.processEvents()
