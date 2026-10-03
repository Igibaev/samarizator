"""Questions answered only from the summary; models taken from the repository first."""

import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QLabel

from samarizator import model_bundle, qa
from samarizator.config import Settings

TRANSCRIPT_ONLY = "Секретная реплика, которой нет в сводке"


def summary_fixture():
    decision = dict(kind="decision", text="Запуск тарифа в ноябре", evidence=[2], status="agreed")
    task = dict(
        kind="action",
        text="Ирина считает скидки",
        evidence=[3],
        status="agreed",
        owner="Ирина",
        due="пятница",
    )
    ledger = [
        dict(kind="point", text="План на квартал не меняется", evidence=[1], status="unspecified"),
        decision,
        task,
        dict(kind="risk", text="Поддержка не справится с нагрузкой", evidence=[4], status="unspecified"),
    ]
    brief = dict(overview="Обсудили тариф и скидки.", items=[decision], topics=["Тариф"])
    return dict(
        **brief, brief=brief, detailed=dict(overview="", items=ledger, topics=[], resolved=[decision, task])
    )


class Client:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def complete_json(self, system, prompt, max_tokens):
        self.prompts.append((system, prompt))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


TIMES = {1: 5.0, 2: 610.0, 3: 1220.0, 4: 1830.0}


def test_model_sees_only_numbered_summary_points_never_the_transcript():
    client = Client(dict(found=True, answer="Запуск в ноябре [1].", refs=[1]))
    result = qa.ask(client, summary_fixture(), TIMES, "Когда запуск?", title="Планёрка")
    system, prompt = client.prompts[0]
    assert "СТРОГО по её сводке" in system
    assert "[1]" in prompt and "Запуск тарифа в ноябре" in prompt
    assert TRANSCRIPT_ONLY not in prompt
    assert result["found"] and result["sources"][0]["evidence"]


def test_answers_without_valid_references_are_replaced_by_the_fixed_reply():
    summary = summary_fixture()
    cases = [
        dict(found=False, answer="", refs=[]),  # the model says it is not in the summary
        dict(found=True, answer="Столица Франции — Париж.", refs=[]),  # outside knowledge, no refs
        dict(found=True, answer="Да [99].", refs=[99]),  # references a point that does not exist
        dict(found=True, answer="Да [1], и ещё [42].", refs=[1]),  # quotes a missing point in the text
        "not a dict",
    ]
    for answer in cases:
        result = qa.ask(Client(answer, answer), summary, TIMES, "Вопрос")
        assert result == dict(q="Вопрос", a=qa.NOT_FOUND, found=False, sources=[])


def test_bad_json_is_retried_once_then_refused():
    result = qa.ask(
        Client(ValueError("broken"), ValueError("broken")), summary_fixture(), TIMES, "Кто считает скидки?"
    )
    assert not result["found"] and result["a"] == qa.NOT_FOUND
    good = qa.ask(
        Client(ValueError("broken"), dict(found=True, answer="Ирина, к пятнице [3].", refs=[3])),
        summary_fixture(),
        TIMES,
        "Кто считает скидки?",
    )
    assert good["found"] and good["sources"][0]["text"] == "Ирина считает скидки"


def test_history_carries_only_grounded_answers_and_material_fits_the_budget():
    history = [
        dict(q="Что решили?", a="Запуск в ноябре [1].", found=True),
        dict(q="Как погода?", a=qa.NOT_FOUND, found=False),
    ]
    client = Client(dict(found=True, answer="Ирина [2].", refs=[2]))
    qa.ask(client, summary_fixture(), TIMES, "А кто отвечает?", history)
    prompt = client.prompts[0][1]
    assert "Что решили?" in prompt and "Как погода?" not in prompt
    # Points are numbered in the order of the recording, final statuses come first in the budget.
    overview, lines, numbered = qa.material(summary_fixture(), TIMES, "нагрузка поддержки", 230)
    texts = [item["text"] for item in numbered]
    assert "Запуск тарифа в ноябре" in texts and "Ирина считает скидки" in texts
    assert "Поддержка не справится с нагрузкой" in texts and "План на квартал не меняется" not in texts
    assert [line[:3] for line in lines] == ["[1]", "[2]", "[3]"]


def test_build_copies_a_local_model_and_downloads_the_rest(tmp_path):
    source, target = tmp_path / "models", tmp_path / "out"
    source.mkdir()
    (source / "whole.bin").write_bytes(b"W" * 4096)
    downloads = []

    def fake_download(url, path):
        downloads.append(url)
        path.write_bytes(b"D" * 2048)

    assert model_bundle.provide("whole.bin", source, target) == "local copy"
    assert model_bundle.provide("whole.bin", source, target) == "cached"
    how = model_bundle.provide(
        "absent.bin",
        source,
        target,
        {"url": "https://example.test/p.bin"},
        log=lambda *_: None,
        downloader=fake_download,
    )
    assert how == "download" and downloads == ["https://example.test/p.bin"]
    with pytest.raises(ValueError, match="контрольная сумма"):
        model_bundle.provide(
            "bad.bin",
            source,
            target,
            {"url": "u", "sha256": "0" * 64},
            log=lambda *_: None,
            downloader=fake_download,
        )
    with pytest.raises(ValueError, match="адреса"):
        model_bundle.provide("unknown.bin", source, target, log=lambda *_: None)
    # By default the app carries only the VAD model; the rest is chosen in the app.
    manifest = model_bundle.load(Path(__file__).parents[1] / "models" / "bundle.json")
    assert manifest["whisper"] == manifest["llm"] == "" and manifest["vad"]


def test_every_model_states_its_memory_and_whether_it_fits_this_mac():
    from samarizator import local_llm
    from samarizator.settings_dialog import gb, preset_facts
    from samarizator.setup_models import WHISPER_PRESETS

    settings = Settings()
    gemma = local_llm.PRESETS["gemma3-12b"]
    # Weights + context + server: the same estimate the summary job checks before it starts.
    assert local_llm.preset_ram_gb(gemma, settings) > gemma.size_gb + 1
    assert local_llm.fit(gemma, settings, total_gb=16) == "ok"
    assert local_llm.fit(gemma, settings, total_gb=8) == "no"
    assert local_llm.fit(local_llm.PRESETS["qwen3-30b-a3b"], settings, total_gb=24) == "no"
    assert local_llm.fit(WHISPER_PRESETS["large-v3"], settings, total_gb=16) == "slow"
    assert preset_facts(gemma, settings).startswith("7,3 ГБ на диске · ≈ ")
    assert gb(0.55) == "0,6" and gb(18.6) == "19" and gb(2.0) == "2"
    for presets in (local_llm.PRESETS, WHISPER_PRESETS):
        for preset in presets.values():
            assert local_llm.preset_ram_gb(preset, settings) > 0 and preset.note


def test_first_run_on_an_8_gb_mac_fits_the_small_summary_model(tmp_path, monkeypatch):
    from samarizator import local_llm

    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(local_llm, "ram_gb", lambda: 8.0)
    first = Settings.first_run()
    assert first.whisper_model == ""  # nothing downloaded yet: the app offers a model
    assert local_llm.fit(local_llm.PRESETS["qwen3-4b"], first, total_gb=8) != "no"


def test_download_dialog_shows_cards_and_blocks_models_that_do_not_fit(tmp_path, monkeypatch, qapp):
    from samarizator import local_llm
    from samarizator.settings_dialog import ModelDownloadDialog, ModelOption

    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(local_llm, "ram_gb", lambda: 16.0)
    dialog = ModelDownloadDialog(settings=Settings())
    cards = dialog.findChildren(ModelOption)
    assert [card.key for card in cards] == list(local_llm.PRESETS)
    by_key = {card.key: card for card in cards}
    assert not by_key["qwen3-30b-a3b"].isEnabled()
    assert not by_key["gemma4-26b-a4b"].isEnabled()
    assert dialog.selected() == "gigachat3.1-lightning"  # the fast Russian model on a 16 GB Mac
    assert dialog.start_button.text() == "Скачать 6,4 ГБ"
    by_key["qwen3-4b"].radio.setChecked(True)
    assert dialog.selected() == "qwen3-4b" and dialog.start_button.text() == "Скачать 2,5 ГБ"
    texts = " ".join(label.text() for label in by_key["qwen3-4b"].findChildren(QLabel))
    assert "памяти при работе" in texts and "Подходит этому Mac" in texts
    dialog.deleteLater()


def test_app_starts_with_the_models_it_carries(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    resources = tmp_path / "Resources"
    (resources / "models").mkdir(parents=True)
    for name in ["ggml-large-v3-turbo.bin", "ggml-silero-v6.2.0.bin"]:
        (resources / "models" / name).write_bytes(b"x" * 2048)
    (resources / "models" / "summary.gguf").write_bytes(b"GGUF" + b"\0" * 2048)
    (resources / "models" / "bundle.json").write_text(
        json.dumps(dict(whisper="ggml-large-v3-turbo.bin", vad="ggml-silero-v6.2.0.bin", llm="summary.gguf"))
    )
    monkeypatch.setenv("SAMARIZATOR_RESOURCES", str(resources))
    first = Settings.first_run()
    assert first.whisper_model.endswith("ggml-large-v3-turbo.bin")
    assert first.llm_model.endswith("summary.gguf") and first.vad
    # A user who already has settings gets the bundled summary model until choosing another.
    assert Settings(llm_model="").resolved().llm_model.endswith("summary.gguf")
    assert Settings(llm_model=str(tmp_path / "mine.gguf")).resolved().llm_model.endswith("mine.gguf")


def test_chat_panel_shows_grounded_answers_with_sources(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window
    from samarizator.summary_page import EvidenceButton

    app = QApplication.instance() or QApplication([])
    w = Window()
    source = tmp_path / "a.wav"
    source.write_bytes(b"x")
    mid = w.store.create(source, w.settings)
    w.store.save_chunk(
        mid,
        0,
        [dict(start=i * 610.0 + 5, end=i * 610.0 + 9, speaker="Речь", text=f"Реплика {i}") for i in range(4)],
    )
    ids = [r["id"] for r in w.store.segments(mid)]
    summary = summary_fixture()
    for item in summary["detailed"]["items"]:
        item["evidence"] = [ids[e - 1] for e in item["evidence"]]
    w.store.update(mid, summary=json.dumps(summary), status="done")
    w.mid = mid
    w.refresh_list()
    assert w.ask_button.isVisibleTo(w)
    w.ask_button.setChecked(True)
    assert w.chat_panel.isVisibleTo(w)

    asked = []

    def fake_ask(question):
        asked.append(question)
        w.chat_answered(
            mid,
            dict(
                q=question,
                a="Запуск в ноябре [1].",
                found=True,
                sources=[dict(n=1, text="Запуск", evidence=[ids[1]])],
            ),
        )

    monkeypatch.setattr(w, "ask_question", fake_ask)
    w.chat_panel.ask.disconnect()
    w.chat_panel.ask.connect(w.ask_question)
    w.chat_panel.field.setText("Когда запуск?")
    w.chat_panel.send()
    assert asked == ["Когда запуск?"]
    buttons = w.chat_panel.findChildren(EvidenceButton)
    assert len(buttons) == 1 and buttons[0].text().startswith("[1] ▶")
    assert w.store.checkpoint(mid, "chat", 0)[0]["q"] == "Когда запуск?"
    # Reopening the record restores the conversation; clearing forgets it.
    w.load_detail()
    assert len(w.chat_panel.findChildren(EvidenceButton)) == 1
    w.clear_chat()
    assert w.store.checkpoint(mid, "chat", 0) == [] and not w.chat_panel.findChildren(EvidenceButton)
    w.timer.stop()
    w.close()
    w.deleteLater()
    app.processEvents()


class FakeHub:
    """Hugging Face API and file downloads, without the network."""

    def __init__(self, trees, files):
        self.trees, self.files, self.urls = trees, files, []

    def open(self, request, timeout=None):
        import io

        url = request.full_url
        self.urls.append(url)
        for repo, entries in self.trees.items():
            if url.startswith(f"https://huggingface.co/api/models/{repo}/tree/main"):
                body = json.dumps(entries).encode()
                break
        else:
            body = self.files[url]
        response = io.BytesIO(body)
        response.status = 200
        response.headers = {"Content-Length": str(len(body))}
        return response


def test_russian_whisper_is_taken_from_the_repository_listing(tmp_path):
    from samarizator import local_llm
    from samarizator.setup_models import PODLODKA, WHISPER_PRESETS

    preset = WHISPER_PRESETS["podlodka-turbo"]
    assert preset.url == PODLODKA and list(WHISPER_PRESETS)[0] == "podlodka-turbo"
    mb = 1024**2
    coreml_only = [
        {"type": "directory", "path": "ggml-podlodka-turbo-encoder.mlmodelc"},
        {"type": "file", "path": "ggml-podlodka-turbo-encoder.mlmodelc/weights/weight.bin", "size": 600 * mb},
        {"type": "file", "path": "README.md", "size": 2000},
    ]
    ggml = [
        {"type": "file", "path": "ggml-model-q5_0.bin", "size": 500 * mb},
        {"type": "file", "path": "ggml-model.bin", "lfs": {"size": 1600 * mb}, "size": 134},
        {"type": "file", "path": "ggml-model-q8_0.bin", "size": 870 * mb},
    ]
    assert local_llm.pick_ggml(coreml_only) is None
    assert local_llm.pick_ggml(ggml) == "ggml-model.bin"  # full precision wins
    weights = local_llm.GGML_MAGIC + b"\0" * 4096
    file_url = "https://huggingface.co/JoaoZaokk/whisper-podlodka-turbo-ggml/resolve/main/ggml-model.bin"
    hub = FakeHub(
        {"smkrv/whisper-podlodka-turbo-coreml": coreml_only, "JoaoZaokk/whisper-podlodka-turbo-ggml": ggml},
        {file_url: weights},
    )
    target = local_llm.download(preset.url, tmp_path / preset.file, opener=hub)
    assert target.read_bytes() == weights and hub.urls[-1] == file_url
    assert not list(tmp_path.glob("*.url"))
    # A file that is not whisper.cpp weights is rejected instead of being used.
    hub.files[file_url] = b"<html>" + b"x" * 4096
    with pytest.raises(ValueError, match="whisper.cpp"):
        local_llm.download(preset.url, tmp_path / "other.bin", opener=hub)
    with pytest.raises(ValueError, match="Не удалось найти"):
        local_llm.download("hf-repo:a/b", tmp_path / "x.bin", opener=FakeHub({"a/b": coreml_only}, {}))


def test_summary_models_are_picked_from_their_repositories(tmp_path):
    from samarizator import local_llm

    gb = 1024**3
    listing = [
        {"type": "file", "path": "mmproj-F16.gguf", "size": 1 * gb},
        {"type": "file", "path": "gemma-4-12b-it-Q8_0.gguf", "size": 12 * gb},
        {"type": "file", "path": "gemma-4-12b-it-UD-Q4_K_XL.gguf", "size": 7 * gb},
        {"type": "file", "path": "gemma-4-12b-it-Q4_K_M.gguf", "lfs": {"size": 7 * gb}, "size": 135},
        {"type": "file", "path": "BF16/gemma-4-12b-it-BF16-00001-of-00002.gguf", "size": 20 * gb},
        {"type": "file", "path": "README.md", "size": 3000},
    ]
    assert local_llm.pick_gguf(listing) == "gemma-4-12b-it-Q4_K_M.gguf"
    assert local_llm.pick_gguf(listing[:2]) == "gemma-4-12b-it-Q8_0.gguf"  # the closest available
    assert local_llm.pick_gguf(listing[:1]) is None
    preset = local_llm.PRESETS["gemma4-12b"]
    weights = b"GGUF" + b"\0" * 4096
    file_url = "https://huggingface.co/unsloth/gemma-4-12b-it-GGUF/resolve/main/gemma-4-12b-it-Q4_K_M.gguf"
    hub = FakeHub({"unsloth/gemma-4-12b-it-GGUF": listing}, {file_url: weights})
    target = local_llm.download(preset.url, tmp_path / preset.file, opener=hub)
    assert target.name == preset.file and target.read_bytes() == weights
    for key in ("gigachat3.1-lightning", "gemma4-12b", "gemma4-26b-a4b"):
        assert local_llm.PRESETS[key].url.startswith(local_llm.HF_REPO)
