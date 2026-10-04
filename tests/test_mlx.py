"""The experimental MLX engine, run on the CPU build of MLX with a tiny random model."""

import json
import socket
import threading
import time

import pytest

from samarizator import local_llm
from samarizator.config import Settings
from samarizator.summary import JSON_GRAMMAR, LocalClient, SummaryTooLong

mx = pytest.importorskip("mlx.core")
pytest.importorskip("mlx_lm")
pytest.importorskip("llguidance")


@pytest.fixture(autouse=True)
def cpu_only(monkeypatch):
    """CI runners may have no usable GPU: the tiny model runs on the CPU everywhere."""
    monkeypatch.setenv("SAMARIZATOR_MLX_DEVICE", "cpu")
    mx.set_default_device(mx.cpu)


@pytest.fixture(scope="module")
def tiny_model(tmp_path_factory):
    """A one-layer llama with random weights and a small byte-level BPE tokenizer."""
    from mlx.utils import tree_flatten
    from mlx_lm.models import llama
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers, trainers
    from transformers import PreTrainedTokenizerFast

    target = tmp_path_factory.mktemp("tiny-mlx")
    corpus = [
        '{"overview": "Обсудили бюджет", "items": [], "topics": ["бюджет"]}',
        "Бюджет 17 миллионов.",
    ] * 40
    tokenizer = Tokenizer(models.BPE(unk_token="<unk>"))
    tokenizer.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False)
    tokenizer.decoder = decoders.ByteLevel()
    tokenizer.train_from_iterator(
        corpus,
        trainers.BpeTrainer(
            vocab_size=400,
            special_tokens=["<unk>", "<s>", "</s>"],
            initial_alphabet=pre_tokenizers.ByteLevel.alphabet(),
        ),
    )
    fast = PreTrainedTokenizerFast(
        tokenizer_object=tokenizer, bos_token="<s>", eos_token="</s>", unk_token="<unk>"
    )
    fast.chat_template = "{% for m in messages %}{{ m['role'] }}: {{ m['content'] }}\n{% endfor %}assistant:"
    fast.save_pretrained(target)
    config = dict(
        model_type="llama",
        hidden_size=32,
        num_hidden_layers=1,
        intermediate_size=64,
        num_attention_heads=2,
        num_key_value_heads=2,
        rms_norm_eps=1e-5,
        vocab_size=len(fast),
        tie_word_embeddings=True,
    )
    model = llama.Model(llama.ModelArgs.from_dict(config))
    mx.eval(model.parameters())
    mx.save_safetensors(str(target / "model.safetensors"), dict(tree_flatten(model.parameters())))
    (target / "config.json").write_text(json.dumps(config))
    (target / local_llm.MLX_MARK).write_text("test")
    return target


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def engine_url(tiny_model):
    from samarizator.mlx_server import serve

    port, holder = free_port(), {}
    thread = threading.Thread(
        target=serve, args=(tiny_model, port, "key", 2048), kwargs=dict(ready=lambda s: holder.update(s=s))
    )
    thread.start()
    deadline = time.monotonic() + 60
    while "s" not in holder and time.monotonic() < deadline:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    holder["s"].shutdown()
    holder["s"].server_close()
    thread.join(10)


def test_mlx_engine_speaks_the_llama_server_api(engine_url):
    client = LocalClient(Settings(), engine_url, "key")
    assert client.count_tokens("Бюджет 17 миллионов") > 0 and client.tokenizer is True
    request = dict(
        messages=[dict(role="system", content="S"), dict(role="user", content="Верни JSON")],
        max_tokens=40,
        grammar=JSON_GRAMMAR,
        temperature=0.9,
    )
    first = client.client.post(client.url, headers=client.headers(), json=request).json()
    answer = first["choices"][0]
    # Whatever a random model wants to say, the grammar lets through only a JSON object.
    if answer["finish_reason"] == "stop":
        assert isinstance(json.loads(answer["message"]["content"]), dict)
    else:
        assert answer["message"]["content"].lstrip().startswith("{")
    second = client.client.post(client.url, headers=client.headers(), json=request).json()
    assert second["usage"]["cached_tokens"] >= second["usage"]["prompt_tokens"] - 1  # the prompt is reused
    # A request larger than the context is refused the way llama-server refuses it.
    client.n_ctx = 10**6
    with pytest.raises(SummaryTooLong):
        client.complete_text("S", "x" * 50, 5000)
    unauthorised = client.client.post(client.url, json=request)
    assert unauthorised.status_code == 401


def test_mlx_runs_as_a_child_process_and_failures_fall_back_to_llama_cpp(tiny_model, tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_MLX_ANYWHERE", "1")
    settings = Settings(llm_engine="mlx", mlx_model=str(tiny_model), input_chars=4000)
    assert local_llm.mlx_usable(settings)
    server = local_llm.start_summary_server(settings, tmp_path / "w")
    try:
        assert isinstance(server, local_llm.MlxServer) and server.slots == 1
        client = LocalClient(settings, server.url, server.key)
        text, _ = client.complete_text("S", "Привет", 8)
        assert isinstance(text, str)
    finally:
        server.__exit__(None, None, None)
    assert server.proc is None
    # A broken MLX model: the job goes on with llama.cpp.
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / local_llm.MLX_MARK).write_text("x")
    started, notes = [], []
    monkeypatch.setattr(local_llm.LlamaServer, "__enter__", lambda self: started.append(self) or self)
    fallback = local_llm.start_summary_server(
        Settings(llm_engine="mlx", mlx_model=str(broken)), tmp_path, notes.append
    )
    assert started and type(fallback) is local_llm.LlamaServer
    assert any("llama.cpp" in note for note in notes)
    # Not chosen, or not on Apple Silicon: llama.cpp from the start.
    monkeypatch.delenv("SAMARIZATOR_MLX_ANYWHERE")
    if not local_llm.mlx_supported():
        assert not local_llm.mlx_usable(settings)


def test_mlx_models_are_downloaded_as_folders(tmp_path):
    from test_chat_and_models import FakeHub

    repo = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
    listing = [
        dict(type="file", path="config.json", size=10),
        dict(type="file", path="model.safetensors", size=20),
        dict(type="file", path="tokenizer.json", size=30),
        dict(type="file", path="README.md", size=5),
        dict(type="file", path=".gitattributes", size=5),
        dict(type="file", path="modeling_custom.py", size=5),
    ]
    files = {f"https://huggingface.co/{repo}/resolve/main/{e['path']}": b"x" * e["size"] for e in listing}
    hub = FakeHub({repo: listing}, files)
    preset = local_llm.MLX_PRESETS["qwen3-4b"]
    target = tmp_path / preset.file
    seen = []
    local_llm.download(
        preset.url, target, progress=lambda done, total: seen.append((done, total)), opener=hub
    )
    assert sorted(p.name for p in target.iterdir()) == sorted(
        ["config.json", "model.safetensors", "tokenizer.json", local_llm.MLX_MARK]
    )
    assert local_llm.model_present(target) and seen[-1] == (60, 60)
    assert local_llm.path_size(target) >= 60
    # A listing without weights is not a model.
    assert local_llm.snapshot_files([dict(type="file", path="config.json", size=1)]) == []


def test_mlx_folders_are_listed_and_deleted_with_the_other_models(tmp_path, monkeypatch, qapp):
    from samarizator.settings_dialog import ModelsDialog, downloaded_models

    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    folder = local_llm.models_dir() / local_llm.MLX_PRESETS["gemma4-12b"].file
    folder.mkdir(parents=True)
    (folder / "model.safetensors").write_bytes(b"x" * 4096)
    (folder / local_llm.MLX_MARK).write_text("repo")
    assert [path for path, _ in downloaded_models()] == [folder]
    manager = ModelsDialog({"mlx_model": str(folder)})
    monkeypatch.setattr(ModelsDialog, "confirm", staticmethod(lambda box, button: True))
    manager.remove(folder)
    assert not folder.exists() and manager.removed_in_use == ["mlx_model"]
    manager.deleteLater()


def grammar_matcher(tiny_model):
    import llguidance
    import llguidance.hf
    from transformers import PreTrainedTokenizerFast

    tokenizer = PreTrainedTokenizerFast.from_pretrained(str(tiny_model))
    lltokenizer = llguidance.hf.from_tokenizer(tokenizer, eos_token=[tokenizer.eos_token_id])

    def accepts(grammar, text):
        matcher = llguidance.LLMatcher(lltokenizer, llguidance.grammar_from("gbnf", grammar))
        assert not matcher.is_error(), matcher.get_error()
        return matcher.consume_tokens(tokenizer.encode(text, add_special_tokens=False)) and matcher.is_accepting()

    return accepts


def test_line_grammars_accept_exactly_what_the_parsers_read(tiny_model):
    from samarizator import summary_notes as notes

    accepts = grammar_matcher(tiny_model)
    answer = (
        "ТЕМА: Бюджет квартала\nТЕМЫ: бюджет; сроки\nОБЗОР: Обсудили бюджет.\n"
        "- Решение [12, 14] Бюджет — 17 млн | отв: Анна | срок: к пятнице | статус: согласовано\n"
        "- Тезис [15] Поставщик меняется\nКОНЕЦ\n"
    )
    assert accepts(notes.NOTES_GRAMMAR, answer)
    # Without the end line the answer is not finished; after it nothing more can be written,
    # so a model that cannot emit its own end-of-turn token still stops.
    assert not accepts(notes.NOTES_GRAMMAR, answer.replace("КОНЕЦ\n", ""))
    assert not accepts(notes.NOTES_GRAMMAR, answer + "- Тезис [16] Ещё\n")
    assert not accepts(notes.NOTES_GRAMMAR, answer.replace("Обсудили бюджет.", "Обсудили<|message_sep|>"))
    assert len(notes.parse_notes(answer)["items"]) == 2
    assert accepts(notes.NOTES_GRAMMAR, "ТЕМА: Связь\nТЕМЫ: связь\nОБЗОР: Проверка звука.\nПУНКТОВ НЕТ\n")
    for broken in [
        answer.replace("[12, 14]", "[12?]"),  # only digits in the brackets
        answer.replace("Решение", "Мысль"),  # unknown kind
        answer.replace("статус: согласовано", "статус: почти"),
        answer.replace("ТЕМЫ: бюджет; сроки\n", ""),
        '{"overview": "JSON"}',
    ]:
        assert not accepts(notes.NOTES_GRAMMAR, broken)
    check = "Исправить 1, 3: Задача [12] Отчёт | отв: Олег\nУдалить 2: дубль пункта 1\nДобавить: Риск [14] Курс\nКОНЕЦ\n"
    assert accepts(notes.CHECK_GRAMMAR, check) and accepts(notes.CHECK_GRAMMAR, "ВСЁ ВЕРНО\n")
    assert not accepts(notes.CHECK_GRAMMAR, check.replace("КОНЕЦ\n", ""))
    assert not accepts(notes.CHECK_GRAMMAR, "Всё нормально\n")
    assembly = (
        "ОБЗОР: Встреча о бюджете.\nТЕМЫ: бюджет\nТЕЗИСЫ\n- Решение [12] Бюджет — 20 млн | статус: согласовано\n"
        "ПЕРЕСМОТРЫ\n- 1, 4 → Решение [12, 30] Бюджет — 20 млн вместо 17 | статус: согласовано\nКОНЕЦ\n"
    )
    assert accepts(notes.ASSEMBLE_GRAMMAR, assembly)
    assert accepts(notes.ASSEMBLE_GRAMMAR, assembly.split("ПЕРЕСМОТРЫ")[0] + "ПЕРЕСМОТРЫ\nНЕТ\n")
    ten = "ОБЗОР: О.\nТЕМЫ: т\nТЕЗИСЫ\n" + "- Тезис [1] x\n" * 10 + "ПЕРЕСМОТРЫ\nНЕТ\n"
    assert not accepts(notes.ASSEMBLE_GRAMMAR, ten)  # at most nine theses
    assert accepts(notes.ASSEMBLE_GRAMMAR, ten.replace("- Тезис [1] x\n", "", 1))


def test_mlx_engine_writes_notes_in_the_line_format(engine_url):
    from samarizator import summary_notes as notes

    client = LocalClient(Settings(), engine_url, "key")
    text, truncated = client.complete_text("S", "Конспект", 60, 8, grammar=notes.NOTES_GRAMMAR)
    # Whatever a random model wants to say, the grammar starts the answer with the title line.
    assert text.startswith("ТЕМА: ")


def test_mlx_engine_honours_stop_strings(engine_url):
    from samarizator import summary_notes as notes

    client = LocalClient(Settings(), engine_url, "key")
    body = dict(
        messages=[dict(role="user", content="Конспект")],
        max_tokens=40,
        grammar=notes.NOTES_GRAMMAR,
        stop=["ЕМА"],
    )
    answer = client.client.post(client.url, headers=client.headers(), json=body).json()["choices"][0]
    assert answer["finish_reason"] == "stop" and answer["message"]["content"] == "Т"
