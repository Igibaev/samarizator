"""The allowed-by-security build reads files only: no microphone, no system audio, no screen."""

import importlib.util
import plistlib
from pathlib import Path

import pytest

from samarizator.config import Settings
from samarizator.launcher import ALLOWED

ROOT = Path(__file__).resolve().parents[1]
PACKAGING = ROOT / "packaging" / "macos"


@pytest.mark.parametrize("module", ["samarizator.live", "samarizator.screencapture"])
def test_capture_modules_are_gone(module):
    assert importlib.util.find_spec(module) is None
    assert module not in ALLOWED


def test_source_has_no_way_to_open_a_capture_device():
    for path in (ROOT / "src").rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for marker in ("avfoundation", "screencapturekit", "samarizator-system-audio"):
            assert marker not in text, f"{path.name}: {marker}"
    assert not (ROOT / "native").exists()


def test_bundle_asks_for_no_device_permission():
    entitlements = plistlib.loads((PACKAGING / "entitlements.plist").read_bytes())
    assert not any(key.startswith("com.apple.security.device.") for key in entitlements)
    spec = (PACKAGING / "samarizator.spec").read_text(encoding="utf-8")
    for key in ("NSMicrophoneUsageDescription", "NSCameraUsageDescription", "NSScreenCaptureUsageDescription"):
        assert key not in spec


def test_bundled_ffmpeg_is_built_without_capture_devices():
    build = (PACKAGING / "build.sh").read_text(encoding="utf-8")
    assert "--disable-indevs" in build and "--disable-outdevs" in build
    assert "--enable-avfoundation" not in build
    assert "swiftc" not in build


def test_live_settings_of_older_versions_are_dropped():
    old = dict(live_source="both", live_system_backend="screencapturekit", live_mix="gentle")
    settings = Settings.from_dict(old)
    assert not any(name.startswith("live_") for name in vars(settings))
