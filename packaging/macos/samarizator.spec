# PyInstaller spec for Samarizator.app. Run through packaging/macos/build.sh.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules, copy_metadata

ROOT = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
VERSION = os.environ.get("SAMARIZATOR_VERSION", "0.1.0")
ICON = os.environ.get("SAMARIZATOR_ICON") or None

# Experimental MLX engine (Apple Silicon): MLX with its Metal library, mlx-lm with every model
# architecture (imported by name at load time), transformers' tokenizers and llguidance.
mlx_hidden, mlx_datas, mlx_binaries = [], [], []
try:
    import mlx_lm  # noqa: F401

    mlx_hidden = (
        collect_submodules("mlx")
        + collect_submodules("mlx_lm")
        + collect_submodules("llguidance")
        + collect_submodules("transformers")
        + ["jinja2"]
    )
    mlx_datas = collect_data_files("mlx") + collect_data_files("mlx_lm") + collect_data_files("transformers")
    # These packages read their own (and their dependencies') metadata when imported.
    for package in ("mlx-lm", "llguidance", "transformers"):
        mlx_datas += copy_metadata(package, recursive=True)
    mlx_binaries = collect_dynamic_libs("mlx")
except ImportError:
    pass

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[os.path.join(ROOT, "src")],
    # Workers run as `Samarizator --run-module samarizator.worker`: every module must be frozen.
    hiddenimports=collect_submodules("samarizator")
    + collect_submodules("pymorphy3")
    + ["truststore", "pymorphy3_dicts_ru", "dawg_python"]
    + mlx_hidden,
    # Russian morphology dictionary for pseudonymisation (privacy.py).
    datas=collect_data_files("pymorphy3_dicts_ru") + mlx_datas,
    binaries=mlx_binaries,
    excludes=[
        "tkinter",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtPdf",
        "PySide6.QtWebEngineCore",
        # transformers imports these only when present; the app never uses them.
        "torch",
        "tensorflow",
        "jax",
        "flax",
    ],
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
