"""Сверка исходников компаньона со спецификацией design.md.

Swift-тулчейна и macOS SDK в среде разработки нет, поэтому компилятор не
видит ни опечаток в именах, ни разъехавшихся чисел раскладки. Эти проверки
закрывают два класса ошибок, которые иначе всплывут только на живой машине:

1. структурные (`focus-companion/Tools/check_sources.py`);
2. дрейф чисел раскладки относительно таблиц design.md §4.1 и §5.

Это НЕ компиляция и НЕ замена сборке на macOS.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
COMPANION = ROOT / "focus-companion"
GEOMETRY = COMPANION / "Sources/FocusCompanion/Geometry/CompanionGeometry.swift"
TOKENS = COMPANION / "Sources/FocusCompanion/Config/DesignTokens.swift"
CHARACTER = COMPANION / "Sources/FocusCompanion/Config/CharacterConfig.swift"


def constant(path: Path, name: str) -> str:
    match = re.search(
        r"static let " + re.escape(name) + r"\s*(?::\s*[A-Za-z<>, ]+)?\s*=\s*([^\n/]+)",
        path.read_text(encoding="utf-8"),
    )
    assert match, f"{name} не найдена в {path.name}"
    return match.group(1).strip()


def number(path: Path, name: str) -> float:
    return float(constant(path, name))


def test_structural_check_passes():
    """Скобки сходятся, все типы и статические члены существуют."""
    result = subprocess.run(
        [sys.executable, str(COMPANION / "Tools/check_sources.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    "name,expected",
    [
        # design.md §4.1, таблица «Элемент — Размер и положение».
        ("wingWidth", 164),
        ("wingWidthWithTimer", 212),
        ("wingInset", 12),
        ("shelfWidth", 288),
        ("shelfVerticalPadding", 8),
        ("shelfRowHeight", 24),
        ("focusWidth", 376),
        ("focusHeight", 260),
        ("bodyTopGap", 4),
        ("drawerInset", 12),
        ("drawerPadding", 20),
        ("recordingsListWidth", 180),
        ("recordingsListGap", 16),
        # design.md §4.3 «Адаптация».
        ("drawerMinComfortableWidth", 520),
        ("drawerNarrowThreshold", 680),
        ("sideBySideMinimumWidth", 640),
        ("standaloneWidth", 164),
    ],
)
def test_layout_metrics_match_spec(name, expected):
    assert number(GEOMETRY, name) == expected


@pytest.mark.parametrize(
    "name,expected",
    [
        # design.md §6.1 «Глаза».
        ("eyesAreaWidth", 48),
        ("eyesAreaHeight", 28),
        ("eyeWidth", 9),
        ("eyeHeight", 18),
        ("eyeSpacing", 8),
        # design.md §12 «Живые глаза».
        ("blinkDuration", 0.110),
        ("blinkClosedHeight", 2),
        ("blinkMinInterval", 4),
        ("blinkMaxInterval", 7),
        ("gazeMaxShiftX", 3),
        ("gazeMaxShiftY", 2),
        ("curiousDuration", 0.280),
        ("thinkingCycle", 1.200),
        ("reminderDuration", 0.650),
        ("happyDuration", 0.700),
        ("angryDuration", 0.450),
        ("sadDuration", 0.800),
        ("sweepingDuration", 1.100),
        ("discardingDuration", 1.300),
        ("errorDuration", 0.300),
        # design.md §11.
        ("dueSoonAbsolute", 600),
        ("dueSoonFraction", 0.2),
        ("undoWindow", 8),
        ("defaultDeadlineMinutes", 60),
        ("maxTaskTitleLength", 80),
        ("reminderSecondLeadTime", 300),
        ("reminderGlobalCooldown", 1200),
        ("reminderFrequentCooldown", 600),
    ],
)
def test_character_metrics_match_spec(name, expected):
    raw = constant(CHARACTER, name)
    value = eval(raw.replace("*", "*"), {"__builtins__": {}}, {})  # только арифметика вида 10 * 60
    assert value == pytest.approx(expected)


@pytest.mark.parametrize(
    "name,expected",
    [
        # design.md §12.2 «Motion-токены».
        ("hoverOpenDelay", 0.120),
        ("hoverCloseDelay", 0.450),
        ("focusExpand", 0.220),
        ("focusCollapse", 0.180),
        ("drawerOpen", 0.280),
        ("drawerClose", 0.220),
        ("pageChange", 0.240),
        ("feedbackPress", 0.080),
        ("disposalMaximum", 1.600),
        ("tooltipDelay", 0.600),
    ],
)
def test_motion_tokens_match_spec(name, expected):
    assert number(TOKENS, name) == pytest.approx(expected)


@pytest.mark.parametrize(
    "name,expected",
    [
        # design.md §5.1 «Цвет, контраст, поверхность».
        ("companionBase", "0x08090B"),
        ("surfaceSolid", "0x17181C"),
        ("surfaceRaised", "0x222328"),
        ("textPrimary", "0xF5F5F7"),
        ("textSecondary", "0xA1A1A8"),
        ("textTertiary", "0x8E8E96"),
        ("accentSelection", "0x0A84FF"),
        ("accentWarning", "0xFFB340"),
        ("accentRecording", "0xFF6159"),
        ("accentSuccess", "0x63D69B"),
    ],
)
def test_palette_matches_spec(name, expected):
    text = TOKENS.read_text(encoding="utf-8")
    match = re.search(r"static let " + re.escape(name) + r"\s*=\s*Color\(hex:\s*(0x[0-9A-Fa-f]{6})\)", text)
    assert match, f"цвет {name} не найден"
    assert match.group(1).upper() == expected.upper()


@pytest.mark.parametrize(
    "name,expected",
    [
        # design.md §5.2 «Радиусы».
        ("button", 16),
        ("standaloneCapsule", 16),
        ("row", 10),
        ("shelf", 20),
        ("focus", 24),
        ("drawer", 24),
    ],
)
def test_radii_match_spec(name, expected):
    assert number(TOKENS, name) == expected


def test_three_active_slots_is_hard_limit():
    store = (COMPANION / "Sources/FocusCompanion/Model/TaskStore.swift").read_text(encoding="utf-8")
    assert "static let maxActiveSlots = 3" in store
    # Добавление обязано упираться в лимит, а не «почти всегда» упираться.
    assert "guard hasFreeSlot else { return nil }" in store


def test_shelf_height_formula_matches_spec():
    """8 pt суммарных отступов + 24 pt на активную задачу → 32 / 56 / 80."""
    padding = number(GEOMETRY, "shelfVerticalPadding")
    row = number(GEOMETRY, "shelfRowHeight")
    assert [padding + row * n for n in (1, 2, 3)] == [32, 56, 80]


def test_focus_panel_vertical_grid_adds_up():
    """40 заголовок + 3 × 56 + 12 промежуток + 40 подвал = 260 pt."""
    assert 40 + 3 * 56 + 12 + 40 == number(GEOMETRY, "focusHeight")


def test_wing_group_fits_inside_wing():
    """Глаза 48 + 20 + кнопка 32 + 8 + кнопка 32 = 140 pt в крыле 164 pt."""
    group = (
        number(CHARACTER, "eyesAreaWidth") + 20 + 32 + 8 + 32
    )
    assert group == 140
    assert group + 2 * number(GEOMETRY, "wingInset") <= number(GEOMETRY, "wingWidth")
