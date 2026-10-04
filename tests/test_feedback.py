"""Bug reports and ideas go to the project's GitHub issue forms; the app fills in what it knows."""

from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from samarizator import __version__, feedback
from samarizator.config import Settings

FORMS = Path(__file__).resolve().parents[1] / ".github" / "ISSUE_TEMPLATE"


def form_ids(name):
    return {field["id"] for field in yaml.safe_load((FORMS / name).read_text())["body"] if "id" in field}


def test_bug_link_fills_the_fields_of_the_bug_form():
    settings = Settings(
        whisper_model="/m/ggml-podlodka-turbo.bin", llm_model="/m/GigaChat3.1-10B-A1.8B-Q4_K_M.gguf"
    )
    url = urlparse(feedback.bug_url(settings))
    assert url.geturl().startswith(feedback.REPO + "/issues/new?")
    fields = {key: values[0] for key, values in parse_qs(url.query).items()}
    assert fields.pop("template") == "bug.yml"
    assert set(fields) <= form_ids("bug.yml")  # every prefilled value lands in a real field
    assert fields["version"] == __version__ and "ГБ" in fields["mac"]
    assert fields["model"] == "ggml-podlodka-turbo.bin, GigaChat3.1-10B-A1.8B-Q4_K_M.gguf"
    assert parse_qs(urlparse(feedback.bug_url(Settings())).query)["model"] == ["не выбраны"]
    idea = parse_qs(urlparse(feedback.idea_url()).query)
    assert idea["template"] == ["feature.yml"] and set(idea) - {"template"} <= form_ids("feature.yml")


def test_forms_are_valid_and_ask_not_to_attach_personal_data():
    for name in ["bug.yml", "feature.yml"]:
        form = yaml.safe_load((FORMS / name).read_text())
        assert form["name"] and form["description"] and form["body"]
        assert any(field.get("validations", {}).get("required") for field in form["body"])
    assert "персональными данными" in (FORMS / "bug.yml").read_text()
    assert yaml.safe_load((FORMS / "config.yml").read_text())["contact_links"][0]["url"] == feedback.PAGE


def test_about_window_shows_the_version_and_the_feedback_links(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setenv("SAMARIZATOR_HOME", str(tmp_path / "home"))
    from PySide6.QtWidgets import QApplication

    from samarizator.app import Window

    app = QApplication.instance() or QApplication([])
    w = Window()
    text = w.about_text()
    assert f"Samarizator {__version__}" in text and feedback.PAGE in text
    assert "template=bug.yml" in text and "template=feature.yml" in text
    opened = []
    monkeypatch.setattr(w, "open_url", opened.append)
    w.report_bug()
    assert opened and opened[0].startswith(feedback.REPO + "/issues/new?template=bug.yml")
    w.close()
    app.processEvents()


def test_download_page_offers_feedback_and_kaspi_support():
    page = (Path(__file__).resolve().parents[1] / "site" / "index.html").read_text()
    assert "issues/new?template=bug.yml" in page and "issues/new?template=feature.yml" in page
    assert "+7 777 756 78 04" in page and "Kaspi" in page and "{{" not in page
