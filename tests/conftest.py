import pytest

from samarizator.config import Settings
from samarizator.store import Store


@pytest.fixture
def meeting(tmp_path, monkeypatch):
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    source = tmp_path / "recording.wav"
    source.write_bytes(b"fixture")
    settings = Settings(vault=str(tmp_path / "vault"))
    store = Store()
    mid = store.create(source, settings)
    return store, mid, settings


@pytest.fixture(autouse=True)
def plain_final_document(request, monkeypatch):
    """Pipeline tests check map/review/reduce; the final text stage has its own tests."""
    if request.node.get_closest_marker("real_final"):
        return
    monkeypatch.setattr(
        "samarizator.summary.final_document",
        lambda *a, **k: dict(title="Протокол встречи", format="protocol", text="", warning=""),
    )
