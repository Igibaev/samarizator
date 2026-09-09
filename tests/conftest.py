import pytest

from samarizator.config import Settings
from samarizator.store import Store


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    source = tmp_path / "recording.wav"
    source.write_bytes(b"fixture")
    settings = Settings(
        endpoint="https://company.example/v1/chat/completions",
        model="corporate",
        vault=str(tmp_path / "vault"),
        diarization="manual",
    )
    store = Store()
    mid = store.create(source, settings)
    return store, mid, settings
