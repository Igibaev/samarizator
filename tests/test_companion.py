import json
from datetime import datetime

import httpx
import pytest

from samarizator.assistant_store import CompanionStore
from samarizator.companion_service import CompanionService
from samarizator.config import Settings
from samarizator.obsidian_index import context_for, parse_note, sync_vault
from samarizator.ollama import OllamaClient, local_ollama_url


def test_parse_note_extracts_tasks_and_due_dates():
    title, chunks, tasks = parse_note(
        "Projects/demo.md",
        """# Demo

Контекст проекта.

- [ ] Подготовить релиз 📅 2026-09-20
- [x] Проверить миграцию
## Решения
Выбрали SQLite.
""",
    )
    assert title == "Demo"
    assert [heading for heading, _ in chunks] == ["Demo", "Решения"]
    assert tasks[0]["title"] == "Подготовить релиз"
    assert datetime.fromisoformat(tasks[0]["due_at"]).astimezone().date().isoformat() == "2026-09-20"
    assert tasks[0]["status"] == "open"
    assert tasks[1]["status"] == "done"


def test_vault_sync_is_incremental_and_searchable(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "plan.md"
    note.write_text("# План\n\nОбновить карту событий.\n- [ ] Исправить ранжирование", encoding="utf-8")
    hidden = vault / ".obsidian"
    hidden.mkdir()
    (hidden / "cache.md").write_text("секретный внутренний кэш", encoding="utf-8")
    store = CompanionStore(tmp_path / "assistant.sqlite3")

    first = sync_vault(vault, store)
    second = sync_vault(vault, store)

    assert first.scanned == 1 and first.updated == 1
    assert second.scanned == 1 and second.updated == 0
    assert "карту событий" in context_for(store, "карта")
    assert [task.title for task in store.tasks()] == ["Исправить ранжирование"]


def test_task_state_survives_unchanged_obsidian_checkbox(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "todo.md"
    note.write_text("- [ ] Важная задача", encoding="utf-8")
    store = CompanionStore(tmp_path / "assistant.sqlite3")
    sync_vault(vault, store)
    task = store.tasks()[0]
    store.toggle_pin(task.id)
    store.set_task_status(task.id, "done")
    note.write_text("# Новая шапка\n- [ ] Важная задача", encoding="utf-8")
    sync_vault(vault, store)
    saved = store.tasks(include_done=True)[0]
    assert saved.status == "done"
    assert saved.pinned


def test_switching_vault_removes_old_unpinned_context(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "note.md").write_text("Старый секрет\n- [ ] Старая задача", encoding="utf-8")
    (second / "note.md").write_text("Новый проект\n- [ ] Новая задача", encoding="utf-8")
    store = CompanionStore(tmp_path / "assistant.sqlite3")
    sync_vault(first, store)
    sync_vault(second, store)
    assert context_for(store, "секрет") == ""
    assert [task.title for task in store.tasks()] == ["Новая задача"]


def test_ollama_is_local_only_and_disables_thinking():
    with pytest.raises(ValueError):
        local_ollama_url("https://example.com")
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "Готово"}})

    client = OllamaClient(transport=httpx.MockTransport(handler))
    answer = client.chat("qwen3:4b", [{"role": "user", "content": "Привет"}], keep_alive="0")
    assert answer == "Готово"
    assert seen["think"] is False
    assert seen["keep_alive"] == "0"
    assert seen["options"]["num_ctx"] == 16_384


def test_service_sends_only_retrieved_context(tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "work.md").write_text("# Работа\nПроект Atlas нужно выпустить в пятницу.", encoding="utf-8")
    store = CompanionStore(tmp_path / "assistant.sqlite3")
    sync_vault(vault, store)

    class Client:
        def chat(self, model, messages, keep_alive):
            assert model == "qwen3:4b"
            assert keep_alive == "0"
            prompt = messages[-1]["content"]
            assert "work.md" in prompt and "Atlas" in prompt
            assert "<reference_data>" in prompt
            assert "</reference_data>" in prompt
            return "Проверь релиз Atlas. [источник: work.md]"

    service = CompanionService(store, Client(), "qwen3:4b", vault)
    result = service.answer("Что с Atlas?")
    assert "Atlas" in result
    assert store.recent_messages() == [
        {"role": "user", "content": "Что с Atlas?"},
        {"role": "assistant", "content": result},
    ]


def test_companion_window_is_floating_and_reminds_once(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from samarizator.companion_ui import CompanionWindow

    app = QApplication.instance() or QApplication([])
    store = CompanionStore(tmp_path / "assistant.sqlite3")
    task_id = store.add_task("Срочная задача", due_at="2000-01-01T00:00:00+00:00")
    settings = Settings(vault=str(tmp_path / "vault"))
    window = CompanionWindow(settings, store=store, client=object())
    assert window.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    window.task_input.setText("Новая задача")
    window.add_task()
    assert {task.title for task in store.tasks()} == {"Срочная задача", "Новая задача"}
    notices = []
    window.reminder.connect(lambda title, body: notices.append((title, body)))
    window.check_reminders()
    window.check_reminders()
    assert notices == [("Напоминание", "Срочная задача")]
    assert task_id in window.notified
    window.allow_close = True
    window.close()
    app.processEvents()


def test_settings_reject_remote_companion_endpoint():
    settings = Settings(assistant_url="https://remote.example")
    with pytest.raises(ValueError):
        settings.validate()
