# PyInstaller spec for Samarizator.app. Run through packaging/macos/build.sh.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
VERSION = os.environ.get("SAMARIZATOR_VERSION", "0.1.0")
ICON = os.environ.get("SAMARIZATOR_ICON") or None

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    # Workers run as `Samarizator --run-module samarizator.worker`: every module must be frozen.
    hiddenimports=collect_submodules("samarizator")
    + collect_submodules("pymorphy3")
    + ["truststore", "pymorphy3_dicts_ru", "dawg_python"],
    # Russian morphology dictionary for pseudonymisation (privacy.py).
    datas=collect_data_files("pymorphy3_dicts_ru"),
    excludes=["tkinter", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtPdf", "PySide6.QtWebEngineCore"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Samarizator",
    console=False,
    argv_emulation=False,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Samarizator")
app = BUNDLE(
    coll,
    name="Samarizator.app",
    icon=ICON,
    bundle_identifier="app.samarizator.desktop",
    version=VERSION,
    info_plist={
        "CFBundleName": "Samarizator",
        "CFBundleDisplayName": "Samarizator",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "LSMinimumSystemVersion": "13.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": (
            "Samarizator записывает микрофон для live-записи встречи. Запись и расшифровка "
            "остаются на этом Mac."
        ),
        "NSHumanReadableCopyright": "Samarizator",
    },
)
