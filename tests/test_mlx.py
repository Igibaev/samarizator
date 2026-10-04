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
