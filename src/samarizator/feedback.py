"""Where users report a bug or suggest an idea: GitHub issue forms of the project.

The bug form is opened with the version, macOS, memory and models already filled in
(issue forms take field values from the URL), so a report says what it is about.
Nothing is sent by the app itself: the browser opens the form, the user writes and submits.
"""

import platform
from pathlib import Path
from urllib.parse import urlencode

from . import __version__

REPO = "https://github.com/Igibaev/samarizator"
PAGE = "https://igibaev.github.io/samarizator/"


def mac_line():
    from .local_llm import ram_gb

    return f"{platform.machine() or 'Mac'}, {round(ram_gb())} ГБ"


def bug_url(settings):
    """The bug form with what the app knows about itself already filled in."""
    models = [Path(path).name for path in (settings.whisper_model, settings.llm_model) if path]
    fields = dict(
        template="bug.yml",
        version=__version__,
        macos=platform.mac_ver()[0] or platform.platform(),
        mac=mac_line(),
        model=", ".join(models) or "не выбраны",
    )
    return f"{REPO}/issues/new?" + urlencode(fields)


def idea_url():
    return f"{REPO}/issues/new?" + urlencode(dict(template="feature.yml", version=__version__))
