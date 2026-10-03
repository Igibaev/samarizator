import json
import os
import shutil
import subprocess
import sys
import threading
from collections import deque
from dataclasses import asdict
from pathlib import Path

from PySide6.QtCore import QLockFile, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QKeySequence,
    QPainter,
    QPalette,
    QShortcut,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import local_llm, obsidian, screencapture
from .bundle import python_command, tool
from .config import FINAL_PROMPT_LIMIT, INSTRUCTIONS_LIMIT, Settings, data_dir
from .knowledge import stamp
from .live import (
    BOTH,
    DEVICE,
    MICROPHONE,
    NATIVE,
    SOURCE_LABELS,
    SYSTEM,
    LiveCaptureError,
    LiveRecorder,
    audio_devices,
    capture_supported,
    describe_tracks,
    recording_title,
    system_audio_devices,
)
from .playback import evidence_intervals
from .process import supervise
from .store import Store
from .summary import summary_views
from .summary_browser import SummaryBrowser
from .summary_prompts import DEFAULT_FORMAT, FINAL_FORMATS

STATUS = {
    "new": "Новая",
    "recording": "Идёт запись",
    "transcribing": "Распознавание",
    "review": "Готова к проверке",
    "retrying": "Повторный проход по сомнительным репликам",
    "summarizing": "Создание сводки",
    "done": "Готово",
    "error": "Ошибка",
    "interrupted": "Приостановлена",
}


NEXT_STEP = {
    "new": "Нажмите «1. Распознать» — текст создаётся на этом Mac, аудио никуда не уходит.",
    "recording": "Идёт запись, готовые фрагменты распознаются по ходу. "
    "Нажмите «Live: остановить», когда встреча закончится.",
    "transcribing": "Идёт распознавание. Кнопки шагов включатся, когда оно закончится.",
    "retrying": "Идёт повторный проход по отмеченным репликам.",
    "review": "Проверьте отмеченные реплики, при необходимости исправьте текст — "
    "затем «2. Создать сводку».",
    "summarizing": "Локальная модель составляет сводку на этом Mac. Длинная запись — это десятки минут.",
    "done": "Готово. Сводки — во вкладках выше, «Открыть в Obsidian» — внизу.",
    "error": "Шаг не выполнен. Причина ниже; после исправления запустите его заново.",
    "interrupted": "Обработка остановлена. Тот же шаг продолжит с места остановки, "
    "уже готовые фрагменты сохранены.",
}


def explain(button, enabled, reason=""):
    """Enable a button and say why when it stays off: a dead control must not be a riddle."""
    button.setEnabled(bool(enabled))
    if not enabled and reason:
        button.setToolTip(reason)
    elif not enabled:
        button.setToolTip("")
    return enabled


def final_markdown(final):
    """Final document as Markdown, with its warnings on top; a hint for older summaries."""
    if not final:
        return (
            "Итогового текста у этой сводки нет — она создана до его появления. "
            "Нажмите «Пересоздать итоговый текст»."
        )
    head = f"*Формат: {final.get('title', '')}*\n\n"
    if final.get("warning"):
        head += "> " + " ".join(final["warning"].split()) + "\n\n"
    return head + (final.get("text") or "")


class ElidedLabel(QLabel):
    """Shortens its own text with an ellipsis to whatever width it ends up with.

    Measuring at build time is wrong: the sidebar has no final width yet, and it
    changes again whenever the splitter moves.
    """

    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self._full = text

    def setText(self, text):
        self._full = text
        super().setText(text)

    def paintEvent(self, event):
        painter = QPainter(self)
        elided = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, self.width())
        painter.drawText(self.rect(), int(self.alignment()) | int(Qt.AlignmentFlag.AlignVCenter), elided)


class Job(QThread):
    memory = Signal(float)
    result = Signal(str)

    def __init__(self, phase, mid, budget):
        super().__init__()
        self.phase, self.mid, self.budget = phase, mid, budget
        self.stop = threading.Event()

    def run(self):
        try:
            code = supervise(
                python_command("samarizator.worker", self.phase, self.mid),
                self.budget,
                callback=lambda rss: self.memory.emit(rss / 1024**3),
                cancelled=self.stop.is_set,
            )
            self.result.emit("" if code == 0 else "worker-error")
        except Exception as exc:
            self.result.emit(str(exc))


class DownloadJob(QThread):
    progress = Signal(float, float)
    done = Signal(str)

    def __init__(self, url, target):
        super().__init__()
        self.url, self.target = url, target
        self.stop = threading.Event()

    def run(self):
        try:
            local_llm.download(
                self.url,
                self.target,
                progress=lambda done, total: self.progress.emit(float(done), float(total)),
                cancelled=self.stop.is_set,
            )
            self.done.emit("")
        except local_llm.DownloadCancelled:
            self.done.emit("Загрузка остановлена. Следующая продолжит с того же места.")
        except Exception as exc:
            self.done.emit(str(exc) if isinstance(exc, (ValueError, OSError)) else "Загрузка не удалась.")


def recommended(presets):
    """Largest preset this Mac's memory allows (presets are ordered largest first)."""
    total = round(local_llm.ram_gb())
    return next((key for key, preset in presets.items() if total >= preset.min_ram_gb), list(presets)[-1])


class ModelDownloadDialog(QDialog):
    """One-time resumable download of a model, with a recommendation by RAM."""

    def __init__(self, parent=None, presets=None, title="Модель сводок", intro=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(620, 360)
        self.presets = presets or local_llm.PRESETS
        self.path = None
        self.preset = ""
        self.job = None
        layout = QVBoxLayout(self)
        intro = QLabel(
            intro
            or "Сводки составляет локальная модель. Её нужно скачать один раз — дальше всё работает "
            "без интернета. Модель сохраняется в ~/Library/Application Support/Samarizator/models."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        self.choice = QComboBox()
        best = recommended(self.presets)
        for key, preset in self.presets.items():
            mark = " · рекомендуется" if key == best else ""
            have = " · скачана" if self.target(key).is_file() else ""
            self.choice.addItem(f"{preset.label} — {preset.size_gb:g} ГБ{mark}{have}", key)
        self.choice.setCurrentIndex(max(0, self.choice.findData(best)))
        self.choice.currentIndexChanged.connect(self.describe)
        layout.addWidget(self.choice)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.state = QLabel()
        self.state.setWordWrap(True)
        layout.addWidget(self.state)
        layout.addStretch(1)
        row = QHBoxLayout()
        self.start_button = QPushButton("Скачать")
        self.start_button.setProperty("primary", True)
        self.start_button.clicked.connect(self.start)
        self.close_button = QPushButton("Закрыть")
        self.close_button.clicked.connect(self.close_or_stop)
        row.addStretch(1)
        row.addWidget(self.start_button)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.describe()

    def target(self, key):
        return local_llm.models_dir() / self.presets[key].file

    def describe(self, *_):
        preset = self.presets[self.choice.currentData()]
        free = shutil.disk_usage(local_llm.models_dir()).free / 1024**3
        target = self.target(preset.key)
        self.note.setText(
            f"{preset.note}\nНужно {preset.size_gb:g} ГБ на диске, свободно {free:.0f} ГБ. "
            f"Памяти в этом Mac: {local_llm.ram_gb():.0f} ГБ."
        )
        self.start_button.setText("Выбрать" if target.is_file() else "Скачать")

    def start(self):
        preset = self.presets[self.choice.currentData()]
        target = self.target(preset.key)
        if target.is_file():
            self.finish(preset.key, target)
            return
        if shutil.disk_usage(target.parent).free < preset.size_gb * 1024**3 * 1.05:
            QMessageBox.warning(self, "Мало места", "На диске не хватает места для этой модели.")
            return
        self.job = DownloadJob(preset.url, target)
        self.job.progress.connect(self.show_progress)
        self.job.done.connect(lambda error: self.downloaded(error, preset.key, target))
        self.start_button.setEnabled(False)
        self.choice.setEnabled(False)
        self.close_button.setText("Остановить")
        self.state.setText("Подключение…")
        self.job.start()

    def show_progress(self, done, total):
        if total:
            self.bar.setValue(int(done / total * 1000))
            self.state.setText(f"Скачано {done / 1024**3:.2f} из {total / 1024**3:.2f} ГБ")
        else:
            self.state.setText(f"Скачано {done / 1024**3:.2f} ГБ")

    def downloaded(self, error, key, target):
        self.job.wait()
        self.job.deleteLater()
        self.job = None
        self.start_button.setEnabled(True)
        self.choice.setEnabled(True)
        self.close_button.setText("Закрыть")
        if error:
            self.state.setText(error)
            return
        self.finish(key, target)

    def finish(self, key, target):
        self.path, self.preset = target, key
        self.accept()

    def close_or_stop(self):
        if self.job:
            self.job.stop.set()
            self.state.setText("Останавливаю…")
        else:
            self.reject()

    def reject(self):
        if self.job:
            self.job.stop.set()
            self.job.wait()
        super().reject()


class SettingsDialog(QDialog):
    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Настройки Samarizator")
        self.resize(700, 650)
        self.settings = settings
        outer = QVBoxLayout(self)
        tabs = QTabWidget()
        outer.addWidget(tabs)
        self.fields = {}
        local = QWidget()
        form = QFormLayout(local)
        tabs.addTab(local, "Распознавание речи · локально")
        intro = QLabel(
            "Whisper работает на вашем Mac и превращает аудио в текст. Аудио никуда не отправляется.\n"
            "Сводку по готовому тексту составляет локальная модель — вкладка «Сводка · локальная модель»."
        )
        intro.setWordWrap(True)
        form.addRow(intro)
        for key, label in [
            ("whisper_model", "Модель Whisper (.bin)"),
            ("vault", "Папка Obsidian"),
        ]:
            line = QLineEdit(str(getattr(settings, key)))
            row = QHBoxLayout()
            row.addWidget(line)
            button = QPushButton("Выбрать…")
            button.clicked.connect(lambda checked=False, k=key, w=line: self.pick(k, w))
            row.addWidget(button)
            form.addRow(label, row)
            self.fields[key] = line
        whisper_download = QPushButton("Скачать более точную модель распознавания…")
        whisper_download.clicked.connect(self.download_whisper)
        form.addRow(whisper_download)
        memory = QDoubleSpinBox()
        memory.setRange(2, 64)
        memory.setSuffix(" ГиБ")
        memory.setValue(settings.memory_gb)
        form.addRow("Бюджет памяти", memory)
        self.fields["memory_gb"] = memory
        for key, label, lo, hi in [
            ("threads", "Потоки CPU", 1, 16),
            ("chunk_seconds", "Фрагмент, секунд", 30, 300),
        ]:
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(getattr(settings, key))
            self.fields[key] = spin
            form.addRow(label, spin)
        language = QLineEdit(settings.language)
        language.setPlaceholderText("ru, en, kk или auto")
        self.fields["language"] = language
        form.addRow("Язык Whisper", language)
        gpu = QCheckBox("Считать на GPU (Metal) вместо CPU")
        gpu.setChecked(settings.gpu)
        self.fields["gpu"] = gpu
        form.addRow("Ускорение", gpu)
        hint = QLabel(
            "Контроль RSS останавливает обработку на 90% бюджета. Это не жёсткая квота ОС.\n"
            "GPU может ускорить распознавание, но память Metal "
            "не попадает в этот подсчёт: на длинных записях бюджет перестаёт быть точной оценкой."
        )
        hint.setWordWrap(True)
        form.addRow(hint)
        form.addRow(QLabel("<b>Live-запись</b>"))
        source = QComboBox()
        for value, label in [
            (MICROPHONE, "Только микрофон"),
            (SYSTEM, "Только системный звук"),
            (BOTH, "Микрофон и системный звук — раздельными каналами"),
        ]:
            source.addItem(label, value)
        source.setCurrentIndex(max(0, source.findData(settings.live_source)))
        self.fields["live_source"] = source
        form.addRow("Источник", source)
        backend = QComboBox()
        for value, label in [
            (NATIVE, "Штатный macOS · ScreenCaptureKit, без драйверов"),
            (DEVICE, "Устройство петли · BlackHole, Loopback, интерфейс"),
        ]:
            backend.addItem(label, value)
        backend.setCurrentIndex(max(0, backend.findData(settings.live_system_backend)))
        self.fields["live_system_backend"] = backend
        form.addRow("Захват системного звука", backend)
        self.helper = screencapture.status()
        helper_state = QLabel(
            ("✓ " if self.helper["available"] else "⚠ ") + self.helper["message"]
        )
        helper_state.setWordWrap(True)
        form.addRow("Состояние helper'а", helper_state)
        self.inputs, self.loopback = self.live_devices()
        for key, label, only_loopback in [
            ("live_microphone_device", "Устройство микрофона", False),
            ("live_system_device", "Устройство системного звука", True),
        ]:
            box = QComboBox()
            box.setEditable(True)
            box.addItem("По умолчанию", "")
            for _, name in (self.loopback if only_loopback else self.inputs):
                box.addItem(name, name)
            saved = getattr(settings, key)
            found = box.findData(saved)
            if saved and found < 0:
                box.addItem(saved, saved)
                found = box.count() - 1
            box.setCurrentIndex(max(0, found))
            self.fields[key] = box
            form.addRow(label, box)
        mixing = QComboBox()
        for value, label in [
            ("gentle", "Мягкое выравнивание — по умолчанию"),
            ("no-resample", "Без выравнивания — дорожки как есть"),
            ("stretch", "Жёсткое выравнивание темпом"),
            ("hard-stuff", "Выравнивание вставкой тишины"),
            ("legacy-pan", "Старое поведение до 14.09.2026"),
        ]:
            mixing.addItem(label, value)
        mixing.setCurrentIndex(max(0, mixing.findData(settings.live_mix)))
        self.fields["live_mix"] = mixing
        form.addRow("Сведение двух дорожек", mixing)
        mix_hint = QLabel(
            "Микрофон и системный звук идут от разных часов, и расхождение приходится "
            "компенсировать. Вставка тишины слышна как прерывание, растяжение темпа — как "
            "лёгкое плавание звука. Сравнить на своих устройствах:\n"
            "python -m samarizator.live compare-mix"
        )
        mix_hint.setWordWrap(True)
        form.addRow(mix_hint)
        live_hint = QLabel(
            "Штатный захват берёт системный звук через ScreenCaptureKit: сторонний драйвер не нужен, "
            "разрешение — «Запись экрана и системного звука», отдельное от микрофонного. Оно "
            "выдаётся приложению-хозяину: при запуске из Terminal в списке нужно включить Terminal. "
            "Экран при этом не записывается, helper берёт только звук.\n"
            "Устройство петли — запасной путь, если штатный захват запрещён политикой компании: "
            "BlackHole, Loopback или интерфейс с аппаратным loopback, выбранный выходом звука. "
            "Обход запрета приложение не выполняет — согласуйте вариант с IT.\n"
            "Оба источника пишутся в один WAV: канал 1 — микрофон, канал 2 — системный звук, "
            "на общей шкале времени. Whisper сводит каналы в моно, оба голоса попадают в текст.\n"
            "При выводе в динамики микрофон повторно захватит удалённую речь — используйте наушники."
        )
        live_hint.setWordWrap(True)
        form.addRow(live_hint)
        if self.inputs and not self.loopback:
            missing = QLabel(
                "Устройства петли сейчас не видно. Оно нужно только для запасного пути: "
                "установите его и переоткройте настройки либо впишите имя вручную."
            )
            missing.setWordWrap(True)
            form.addRow(missing)
        quality = QWidget()
        qform = QFormLayout(quality)
        tabs.addTab(quality, "Качество и термины")
        profile = QPushButton("Применить профиль M4 Pro · 48 ГБ")
        profile.clicked.connect(self.quality_profile)
        qform.addRow(profile)
        for key, label in [
            ("pause_boundaries", "Сдвигать границы фрагментов к паузам"),
            ("vad", "Локальный VAD · выделение речи"),
        ]:
            field = QCheckBox(label)
            field.setChecked(getattr(settings, key))
            self.fields[key] = field
            qform.addRow(field)
        for key, label in [("vad_model", "Модель VAD (.bin)"), ("glossary", "Термины, имена, аббревиатуры")]:
            field = QLineEdit(getattr(settings, key))
            self.fields[key] = field
            qform.addRow(label, field)
        self.fields["glossary"].setMaxLength(800)
        self.fields["glossary"].setPlaceholderText("Samarizator, Иванов, EBITDA, названия ваших проектов")
        cleanup = QComboBox()
        for value, label in [
            ("off", "Без обработки — как записано"),
            ("light", "Лёгкая — срез гула и выравнивание громкости"),
            ("strong", "Сильная — плюс подавление шипения"),
        ]:
            cleanup.addItem(label, value)
        cleanup.setCurrentIndex(max(0, cleanup.findData(settings.audio_cleanup)))
        self.fields["audio_cleanup"] = cleanup
        qform.addRow("Обработка звука перед Whisper", cleanup)
        cleanup_hint = QLabel(
            "Обрабатывается только то, что слышит Whisper: сама запись на диске не меняется, "
            "и профиль можно поменять и распознать заново. Сравнить на своей записи:\n"
            "python -m samarizator.live clean <файл записи>"
        )
        cleanup_hint.setWordWrap(True)
        qform.addRow(cleanup_hint)
        beam = QSpinBox()
        beam.setRange(1, 8)
        beam.setValue(settings.beam_size)
        self.fields["beam_size"] = beam
        qform.addRow("Ширина поиска Whisper", beam)
        hint = QLabel(
            "Профиль: 16 ГиБ, 8 потоков CPU, фрагменты около 90 секунд, VAD и границы по паузам. "
            "Выбранные модели распознавания и сводок сохраняются. VAD входит в приложение.\n\n"
            "Словарь — короткий список ожидаемых слов, а не инструкция. "
            "Он помогает с написанием терминов, но может смещать распознавание. "
            "Если VAD пропускает тихую речь, сравните запись с отключённым VAD."
        )
        hint.setWordWrap(True)
        qform.addRow(hint)
        llm = QWidget()
        lform = QFormLayout(llm)
        tabs.addTab(llm, "Сводка · локальная модель")
        llm_intro = QLabel(
            "Сводку составляет локальная языковая модель (llama.cpp) прямо на этом Mac. "
            "Текст и аудио никуда не отправляются; интернет нужен только один раз — скачать модель."
        )
        llm_intro.setWordWrap(True)
        lform.addRow(llm_intro)
        model_row = QHBoxLayout()
        self.fields["llm_model"] = QLineEdit(settings.llm_model)
        self.fields["llm_model"].setPlaceholderText("Файл .gguf — скачайте модель кнопкой ниже")
        model_row.addWidget(self.fields["llm_model"])
        choose = QPushButton("Выбрать…")
        choose.clicked.connect(self.pick_llm)
        model_row.addWidget(choose)
        lform.addRow("Модель сводок (.gguf)", model_row)
        self.fields["llm_preset"] = QLineEdit(settings.llm_preset)
        self.fields["llm_preset"].setVisible(False)
        download = QPushButton("Скачать или сменить модель…")
        download.clicked.connect(self.download_llm)
        lform.addRow(download)
        recommended = local_llm.PRESETS[local_llm.recommended_preset()]
        self.llm_state = QLabel()
        self.llm_state.setWordWrap(True)
        lform.addRow(self.llm_state)
        self.fields["llm_model"].textChanged.connect(self.describe_llm)
        self.describe_llm()
        ram_hint = QLabel(
            f"В этом Mac {local_llm.ram_gb():.0f} ГБ памяти — рекомендуется «{recommended.label}»."
        )
        ram_hint.setWordWrap(True)
        lform.addRow(ram_hint)
        llm_gpu = QCheckBox("Считать сводку на GPU (Metal) — в разы быстрее CPU")
        llm_gpu.setChecked(settings.llm_gpu)
        self.fields["llm_gpu"] = llm_gpu
        lform.addRow("Ускорение", llm_gpu)
        for key, label, lo, hi in [
            ("input_chars", "Символов текста на запрос", 4000, 48000),
            ("max_output_tokens", "Лимит токенов ответа", 512, 64000),
        ]:
            spin = QSpinBox()
            spin.setRange(lo, hi)
            spin.setValue(getattr(settings, key))
            self.fields[key] = spin
            lform.addRow(label, spin)
        llm_hint = QLabel(
            "Запись любой длины делится на блоки: каждый блок разбирается и затем проверяется по "
            "расшифровке, потом всё сводится в краткую сводку и итоговый текст. Двухчасовая встреча — "
            "это 20–40 запросов к модели: на Mac с M-процессором обычно 15–60 минут в зависимости от "
            "модели. Меньший блок — точнее у небольших моделей, но дольше.\n"
            "Контекст и память для модели рассчитываются автоматически из размера блока."
        )
        llm_hint.setWordWrap(True)
        lform.addRow(llm_hint)
        fmt = QWidget()
        fform = QFormLayout(fmt)
        tabs.addTab(fmt, "Формат сводки")
        instructions = QPlainTextEdit(settings.summary_instructions)
        instructions.setPlaceholderText(
            "Например: «Сводка для отдела продаж. Выделяй цены, сроки поставки и возражения клиентов. "
            "QBS — название нашей методики, не расшифровывай»."
        )
        instructions.setFixedHeight(90)
        self.fields["summary_instructions"] = instructions
        fform.addRow("Указания для всей сводки", instructions)
        final_format = QComboBox()
        for key, (label, _) in FINAL_FORMATS.items():
            final_format.addItem(label, key)
        final_format.setCurrentIndex(max(0, final_format.findData(settings.final_format)))
        final_format.activated.connect(self.pick_format)
        self.fields["final_format"] = final_format
        fform.addRow("Итоговый текст", final_format)
        template = QPlainTextEdit(
            settings.final_prompt or FINAL_FORMATS.get(settings.final_format, FINAL_FORMATS[DEFAULT_FORMAT])[1]
        )
        template.setMinimumHeight(220)
        self.fields["final_prompt"] = template
        fform.addRow("Шаблон итогового текста", template)
        reset = QPushButton("Вернуть стандартный шаблон")
        reset.clicked.connect(lambda: self.pick_format(final_format.currentIndex()))
        fform.addRow(reset)
        format_hint = QLabel(
            "Шаблон описывает разделы, порядок и стиль документа обычными словами — модель "
            "следует ему. Указания для всей сводки влияют на все шаги: что считать важным и как "
            "называть вещи. Правила точности (только факты из записи, статусы решений, ссылки на "
            "реплики) встроены и не отключаются.\n"
            "Сменили шаблон у готовой записи — нажмите «Пересоздать итоговый текст»: проверенный "
            "реестр пунктов переиспользуется, повторного разбора записи не будет."
        )
        format_hint.setWordWrap(True)
        fform.addRow(format_hint)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def quality_profile(self):
        profile = self.settings.quality_profile()
        for key in ["memory_gb", "threads", "chunk_seconds", "beam_size"]:
            self.fields[key].setValue(getattr(profile, key))
        for key in ["gpu", "pause_boundaries", "vad"]:
            self.fields[key].setChecked(getattr(profile, key))
        if not self.fields["vad_model"].text().strip():
            self.fields["vad_model"].setText(profile.vad_model)

    @staticmethod
    def live_devices():
        """Enumerate macOS inputs for the pickers; elsewhere the fields stay free text."""
        if not capture_supported():
            return [], []
        try:
            devices = audio_devices(tool("ffmpeg"))
        except LiveCaptureError:
            return [], []
        return devices, system_audio_devices(devices)

    def pick_format(self, index):
        key = self.fields["final_format"].itemData(index)
        self.fields["final_prompt"].setPlainText(FINAL_FORMATS[key][1])

    def pick_llm(self):
        path = QFileDialog.getOpenFileName(self, "Модель сводок", "", "Модель GGUF (*.gguf);;Все файлы (*)")[0]
        if path:
            self.fields["llm_model"].setText(path)
            self.fields["llm_preset"].setText("")

    def download_llm(self):
        dialog = ModelDownloadDialog(self)
        if dialog.exec() and dialog.path:
            self.fields["llm_model"].setText(str(dialog.path))
            self.fields["llm_preset"].setText(dialog.preset)

    def download_whisper(self):
        from .setup_models import WHISPER_PRESETS, whisper_memory_gb

        dialog = ModelDownloadDialog(
            self,
            WHISPER_PRESETS,
            "Модель распознавания",
            "Встроенная модель small-q5_1 быстрая, но крупные модели Whisper заметно точнее на "
            "живой речи. Скачивается один раз; новые записи распознаются выбранной моделью, а "
            "старые — кнопкой «Распознать заново».",
        )
        if dialog.exec() and dialog.path:
            self.fields["whisper_model"].setText(str(dialog.path))
            # The transcription refuses a model that does not fit the memory budget.
            need = whisper_memory_gb(dialog.path)
            if self.fields["memory_gb"].value() < need:
                self.fields["memory_gb"].setValue(need)

    def describe_llm(self):
        path = Path(self.fields["llm_model"].text().strip()).expanduser()
        if self.fields["llm_model"].text().strip() and path.is_file():
            self.llm_state.setText(f"✓ Модель на месте: {path.name}, {path.stat().st_size / 1024**3:.1f} ГБ.")
        else:
            self.llm_state.setText("⚠ Модель сводок ещё не выбрана — скачайте её, это нужно один раз.")

    def pick(self, key, widget):
        path = (
            QFileDialog.getExistingDirectory(self, "Папка базы знаний")
            if key == "vault"
            else QFileDialog.getOpenFileName(self, "Выберите локальную модель")[0]
        )
        if path:
            widget.setText(path)

    def save(self):
        try:
            values = asdict(self.settings)
            for key, field in self.fields.items():
                values[key] = (
                    field.currentData()
                    if isinstance(field, QComboBox)
                    else field.isChecked()
                    if isinstance(field, QCheckBox)
                    else field.value()
                    if isinstance(field, (QSpinBox, QDoubleSpinBox))
                    else field.toPlainText().strip()
                    if isinstance(field, QPlainTextEdit)
                    else field.text()
                )
            # An untouched standard template is stored empty, so improved templates in
            # future versions reach the user automatically.
            preset = FINAL_FORMATS.get(values["final_format"], FINAL_FORMATS[DEFAULT_FORMAT])[1]
            if values["final_prompt"] == preset.strip():
                values["final_prompt"] = ""
            updated = Settings(**values)
            if len(updated.summary_instructions) > INSTRUCTIONS_LIMIT:
                raise ValueError(f"Указания для сводки: не более {INSTRUCTIONS_LIMIT} символов.")
            if len(updated.final_prompt) > FINAL_PROMPT_LIMIT:
                raise ValueError(f"Шаблон итогового текста: не более {FINAL_PROMPT_LIMIT} символов.")
            updated.validate(llm=bool(updated.llm_model.strip()))
            updated.save()
            self.settings = updated
            self.accept()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Настройки", str(exc))


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings.load()
        self.store = Store()
        self.store.recover()
        from .worker import cleanup

        cleanup()
        self.job = None
        self.pending_phase = ""
        self.active_id = None
        self.mid = None
        self.page = 0
        self.player_proc = None
        self.playback_queue = deque()
        self.playback_total = 0
        self.playback_mid = None
        self.live_recorder = None
        from .build import build_label

        self.build_label = build_label()
        self.setWindowTitle("Samarizator · " + self.build_label)
        self.resize(1200, 800)
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        top = QHBoxLayout()
        title = QLabel("Samarizator")
        title.setObjectName("brand")
        top.addWidget(title)
        top.addWidget(QLabel("Локальное аудио  ·  Кратко + подробно  ·  " + self.build_label))
        top.addStretch()
        self.model_button = QPushButton("Скачать модель сводок")
        self.model_button.setProperty("primary", True)
        self.model_button.setToolTip("Один раз: после загрузки сводки создаются без интернета.")
        self.model_button.clicked.connect(self.download_model)
        top.addWidget(self.model_button)
        self.settings_button = QPushButton("Настройки")
        self.settings_button.clicked.connect(self.configure)
        top.addWidget(self.settings_button)
        layout.addLayout(top)
        split = QSplitter()
        layout.addWidget(split, 1)
        sidebar = QWidget()
        side = QVBoxLayout(sidebar)
        self.add_button = QPushButton("+ Добавить аудио или видео")
        self.add_button.clicked.connect(self.add_file)
        side.addWidget(self.add_button)
        self.live_button = QPushButton("● Live: начать запись")
        self.live_button.setToolTip(
            "Записывает локально выбранный в настройках источник: микрофон, системный звук или оба "
            "раздельными каналами. Готовые фрагменты распознаются прямо во время записи, поэтому "
            "после остановки остаётся только хвост."
        )
        self.live_button.clicked.connect(self.toggle_live)
        side.addWidget(self.live_button)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск в записях и сводках…")
        self.search.textChanged.connect(self.refresh_list)
        side.addWidget(self.search)
        self.list = QListWidget()
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentItemChanged.connect(self.select)
        remove = QShortcut(QKeySequence.StandardKey.Delete, self.list)
        remove.setContext(Qt.ShortcutContext.WidgetShortcut)
        remove.activated.connect(lambda: self.delete_meeting())
        side.addWidget(self.list)
        split.addWidget(sidebar)
        detail = QWidget()
        body = QVBoxLayout(detail)
        self.heading = QLabel("Добавьте запись встречи, лекции или интервью")
        self.heading.setObjectName("heading")
        self.heading.setWordWrap(True)
        body.addWidget(self.heading)
        self.info = QLabel("1. Распознайте локально → 2. Проверьте текст → 3. Создайте сводку")
        self.info.setWordWrap(True)
        body.addWidget(self.info)
        self.error_detail = QLabel()
        self.error_detail.setWordWrap(True)
        self.error_detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_detail.setStyleSheet("color: #a52d27;")
        body.addWidget(self.error_detail)
        actions = QHBoxLayout()
        self.transcribe = QPushButton("1. Распознать / продолжить")
        self.transcribe.clicked.connect(lambda: self.start("transcribe"))
        self.retry = QPushButton("Повторить сомнительные реплики")
        self.retry.setToolTip(
            "Второй проход Whisper только по репликам, отмеченным для проверки, "
            "с запасом аудио по краям. Ничего не заменяет автоматически — "
            "вариант нужно принять вручную ниже."
        )
        self.retry.clicked.connect(lambda: self.start("retry"))
        self.summarize = QPushButton("2. Создать сводку")
        self.summarize.clicked.connect(lambda: self.start("summary"))
        self.cancel = QPushButton("Остановить")
        self.cancel.clicked.connect(self.cancel_job)
        for button in [self.transcribe, self.retry, self.summarize, self.cancel]:
            actions.addWidget(button)
        body.addLayout(actions)
        maintenance = QHBoxLayout()
        self.rerun_button = QPushButton("Распознать заново")
        self.rerun_button.setToolTip(
            "Новая запись с текущей моделью и настройками. Старый результат сохранится для сравнения."
        )
        self.rerun_button.clicked.connect(self.rerun_transcription)
        self.copy_error_button = QPushButton("Скопировать ошибку")
        self.copy_error_button.clicked.connect(
            lambda: QApplication.clipboard().setText(self.error_detail.text())
        )
        for button in [self.rerun_button, self.copy_error_button]:
            maintenance.addWidget(button)
        maintenance.addStretch(1)
        body.addLayout(maintenance)
        self.tabs = QTabWidget()
        body.addWidget(self.tabs, 1)
        transcript = QWidget()
        tbox = QVBoxLayout(transcript)
        self.uncertain_only = QCheckBox("Только требующие проверки")
        self.uncertain_only.setToolTip(
            "Показывать только реплики, отмеченные для проверки: граница фрагмента, "
            "низкая уверенность Whisper или возможный повтор на стыке."
        )
        self.uncertain_only.stateChanged.connect(self.toggle_uncertain_filter)
        tbox.addWidget(self.uncertain_only)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Время", "Текст", "Проверка"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setSectionResizeMode(
            1, self.table.horizontalHeader().ResizeMode.Stretch
        )
        self.table.itemSelectionChanged.connect(self.selected_segment)
        tbox.addWidget(self.table)
        nav = QHBoxLayout()
        prev = QPushButton("← Назад")
        prev.clicked.connect(lambda: self.turn_page(-1))
        nxt = QPushButton("Дальше →")
        nxt.clicked.connect(lambda: self.turn_page(1))
        self.page_label = QLabel()
        for widget in [prev, self.page_label, nxt]:
            nav.addWidget(widget)
        tbox.addLayout(nav)
        playback = QHBoxLayout()
        self.play_button = QPushButton("▶ Прослушать реплику")
        self.play_button.setToolTip(
            "Открывает исходную запись в ffplay на выбранной реплике, с запасом по 2 с с каждой "
            "стороны. Нужен ffplay (обычно ставится вместе с ffmpeg)."
        )
        self.play_button.clicked.connect(self.play_segment)
        self.stop_button = QPushButton("■ Стоп")
        self.stop_button.clicked.connect(self.stop_playback)
        self.playback_label = QLabel()
        playback.addWidget(self.play_button)
        tbox.addLayout(playback)
        edit = QHBoxLayout()
        self.text = QLineEdit()
        self.text.setPlaceholderText("Исправить выбранную реплику")
        self.save_segment = QPushButton("Сохранить реплику")
        self.save_segment.clicked.connect(self.edit_segment)
        edit.addWidget(self.text, 1)
        edit.addWidget(self.save_segment)
        tbox.addLayout(edit)
        retry_row = QHBoxLayout()
        self.retry_label = QLabel()
        self.retry_label.setWordWrap(True)
        self.accept_retry_button = QPushButton("Принять повторный вариант")
        self.accept_retry_button.clicked.connect(self.accept_retry)
        retry_row.addWidget(self.retry_label, 1)
        retry_row.addWidget(self.accept_retry_button)
        self.undo_retry_button = QPushButton("Отменить принятие")
        self.undo_retry_button.clicked.connect(self.undo_retry)
        retry_row.addWidget(self.undo_retry_button)
        tbox.addLayout(retry_row)
        self.tabs.addTab(transcript, "Расшифровка")
        final = QWidget()
        fbox = QVBoxLayout(final)
        self.final_text = QTextBrowser()
        self.final_text.setOpenExternalLinks(False)
        fbox.addWidget(self.final_text, 1)
        final_actions = QHBoxLayout()
        self.copy_final_button = QPushButton("Копировать текст")
        self.copy_final_button.setToolTip("Копирует итоговый текст в Markdown.")
        self.copy_final_button.clicked.connect(self.copy_final)
        self.regenerate_button = QPushButton("Пересоздать итоговый текст")
        self.regenerate_button.setToolTip(
            "Только итоговый текст по текущему формату из настроек. Проверенный реестр пунктов "
            "переиспользуется — это быстрее полной сводки."
        )
        self.regenerate_button.clicked.connect(lambda: self.start("final"))
        final_actions.addWidget(self.copy_final_button)
        final_actions.addWidget(self.regenerate_button)
        final_actions.addStretch(1)
        fbox.addLayout(final_actions)
        self.tabs.addTab(final, "Итоговый текст")
        self.summary = SummaryBrowser(self.store.segment)
        self.tabs.addTab(self.summary, "Кратко · тезисы")
        self.detailed_summary = SummaryBrowser(self.store.segment)
        self.tabs.addTab(self.detailed_summary, "Подробная сводка")
        self.resolved_summary = SummaryBrowser(self.store.segment)
        self.resolved_summary.setToolTip(
            "Итоговый статус решений и задач с учётом более поздних правок и отмен, отдельно "
            "от полной истории в «Подробной сводке» — там ничего не удаляется и не заменяется."
        )
        self.tabs.addTab(self.resolved_summary, "Итог по решениям")
        self.audio_report = QTextBrowser()
        self.tabs.addTab(self.audio_report, "Качество записи")
        for browser in [self.summary, self.detailed_summary, self.resolved_summary]:
            browser.playEvidence.connect(self.play_evidence)
            browser.playGroup.connect(self.play_evidence_group)
        shared_playback = QHBoxLayout()
        shared_playback.addWidget(self.stop_button)
        shared_playback.addWidget(self.playback_label, 1)
        body.addLayout(shared_playback)
        bottom = QHBoxLayout()
        source = QPushButton("Открыть исходную запись")
        source.clicked.connect(self.open_source)
        self.obsidian = QPushButton("Открыть в Obsidian")
        self.obsidian.clicked.connect(self.open_obsidian)
        vault = QPushButton("Папка базы знаний")
        vault.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.settings.vault)))
        self.reexport = QPushButton("Экспортировать снова")
        self.reexport.clicked.connect(lambda: self.start("export"))
        for widget in [source, self.obsidian, vault, self.reexport]:
            bottom.addWidget(widget)
        body.addLayout(bottom)
        split.addWidget(detail)
        split.setSizes([290, 910])
        self.progress = QLabel("Готов к работе. Медиа обрабатывается на этом компьютере.")
        self.progress.setWordWrap(True)
        layout.addWidget(self.progress)
        self.ram = QLabel("Бюджет: " + str(self.settings.memory_gb) + " ГиБ · CPU")
        layout.addWidget(self.ram)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.poll)
        self.timer.start(800)
        self.refresh_list()
        self.controls()

    def configure(self):
        dialog = SettingsDialog(self.settings, self)
        if dialog.exec():
            self.settings = dialog.settings
            self.ram.setText(f"Бюджет: {self.settings.memory_gb:g} ГиБ · CPU")
        self.controls()

    def has_llm(self):
        return bool(self.settings.llm_model.strip()) and Path(self.settings.llm_model).expanduser().is_file()

    def download_model(self):
        """Pick and fetch the summary model; returns True when one is ready."""
        dialog = ModelDownloadDialog(self)
        if not (dialog.exec() and dialog.path):
            return self.has_llm()
        self.settings.llm_model, self.settings.llm_preset = str(dialog.path), dialog.preset
        try:
            self.settings.save()
        except (ValueError, OSError) as exc:
            QMessageBox.warning(self, "Настройки", str(exc))
        self.controls()
        return self.has_llm()

    def copy_final(self):
        if not self.mid or not (summary := self.store.meeting(self.mid)["summary"]):
            return
        QApplication.clipboard().setText((json.loads(summary).get("final") or {}).get("text", ""))

    def add_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Добавить запись",
            "",
            "Аудио и видео (*.mp3 *.mp4 *.m4a *.wav *.mov *.mkv *.webm *.ogg *.flac *.aac);;Все файлы (*)",
        )
        if path:
            self.mid = self.store.create(path, self.settings)
            self.refresh_list()

    def toggle_live(self):
        # Stopping comes first: catch-up recognition runs as a job during the whole
        # recording, and it must never block the button that ends the meeting.
        if self.live_recorder is not None:
            self.stop_live()
            return
        if self.job:
            return
        try:
            recorder = LiveRecorder(
                source=self.settings.live_source,
                microphone_device=self.settings.live_microphone_device,
                system_device=self.settings.live_system_device,
                system_backend=self.settings.live_system_backend,
                mix=self.settings.live_mix,
            )
            recorder.start()
        except LiveCaptureError as exc:
            QMessageBox.warning(self, "Live-запись", str(exc))
            return
        self.live_recorder = recorder
        self.stop_playback()
        # The meeting exists from the first second so recognition can run alongside
        # the recording; its source moves to the finished file when capture stops.
        self.mid = self.store.create(recorder.partial, self.settings)
        self.store.update(
            self.mid,
            title=recording_title(recorder.source, recorder.partial.stem),
            status="recording",
        )
        self.page = 0
        self.refresh_list()
        self.start_catchup()
        self.progress.setText(
            f"Live-запись началась ({SOURCE_LABELS[recorder.source]}). "
            "Аудио сохраняется только на этом Mac, распознавание идёт по ходу записи."
        )
        self.controls()

    def start_catchup(self):
        """Recognise finished fragments while the meeting is still being recorded."""
        if self.job or not self.mid:
            return
        self.active_id = self.mid
        self.job = Job("catchup", self.mid, self.settings.memory_gb)
        self.job.memory.connect(
            lambda rss: self.ram.setText(
                f"RSS приложения и обработчиков: {rss:.2f} ГиБ / {self.settings.memory_gb:g} ГиБ · CPU"
            )
        )
        self.job.result.connect(self.job_result)
        self.job.finished.connect(self.job_finished)
        self.job.start()

    def stop_live(self, start_transcription=True):
        recorder = self.live_recorder
        mid = self.mid
        if recorder is None:
            return
        self.live_recorder = None
        try:
            path = recorder.stop()
        except (LiveCaptureError, OSError, ValueError) as exc:
            self.end_catchup(mid)
            # An empty recording leaves nothing to keep unless catch-up already saved text.
            if mid and not self.store.segments(mid, limit=1):
                self.store.delete(mid)
                self.mid = None
            self.refresh_list()
            self.progress.setText(str(exc))
            QMessageBox.warning(self, "Live-запись", str(exc))
            self.controls()
            return
        self.store.update(mid, source=str(path), status="transcribing")
        self.end_catchup(mid)
        self.mid, self.page = mid, 0
        self.refresh_list()
        self.progress.setText("Live-запись сохранена локально.")
        self.controls()
        self.report_live_tracks(path, recorder.tracks, [e.name for e in recorder.inputs])
        if start_transcription:
            # Catch-up is still finishing its last fragment; chain the closing pass to it.
            if self.job is not None and self.job.phase == "catchup":
                self.pending_phase = "transcribe"
            else:
                self.start("transcribe")

    def end_catchup(self, mid):
        """Tell the catch-up worker the recording is over, so it stops after the tail."""
        if mid:
            self.store.save_checkpoint(mid, "live-stopped", 0, True)

    def report_live_tracks(self, path, tracks, names=()):
        """Say which source made it into the file, instead of leaving it to the ear."""
        if len(tracks) < 2:
            return
        try:
            report, silent = describe_tracks(path, tracks, names)
        except (OSError, ValueError, RuntimeError) as exc:
            self.progress.setText(f"Запись сохранена, проверить дорожки не удалось: {exc}")
            return
        self.progress.setText("Live-запись сохранена локально. " + report.replace("\n", " · "))
        if silent:
            QMessageBox.warning(
                self,
                "Live-запись",
                "В записи нет звука на дорожке: "
                + ", ".join(silent)
                + ".\n\n"
                + report
                + "\n\nЗапись сохранена, распознавание продолжится. Измерены первые "
                "30 секунд: если источник молчал в начале, предупреждение ложное.",
            )

    def refresh_list(self):
        current = self.mid
        self.list.blockSignals(True)
        self.list.clear()
        found = False
        for meeting in self.store.meetings(self.search.text()):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, meeting["id"])
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(10, 6, 6, 6)
            text = QVBoxLayout()
            text.setSpacing(1)
            # One line per record keeps a long list scannable; the full title is in the tooltip.
            label = ElidedLabel(meeting["title"])
            state = QLabel(STATUS.get(meeting["status"], meeting["status"]))
            state.setObjectName("rowStatus")
            text.addWidget(label)
            text.addWidget(state)
            row_layout.addLayout(text, 1)
            row.setToolTip(meeting["title"])
            trash = QPushButton("🗑")
            trash.setObjectName("trashButton")
            trash.setToolTip("Удалить запись")
            trash.setFixedWidth(30)
            trash.clicked.connect(lambda checked=False, mid=meeting["id"]: self.delete_meeting(mid))
            row_layout.addWidget(trash)
            item.setSizeHint(row.sizeHint())
            self.list.addItem(item)
            self.list.setItemWidget(item, row)
            if meeting["id"] == current:
                self.list.setCurrentItem(item)
                found = True
        self.list.blockSignals(False)
        if current and found:
            self.load_detail()
        else:
            self.mid = None
            self.clear_detail()

    def clear_detail(self):
        self.stop_playback()
        self.heading.setText("Добавьте запись встречи, лекции или интервью")
        self.info.setText("1. Распознайте локально → 2. Проверьте текст → 3. Создайте сводку")
        self.error_detail.clear()
        self.table.setRowCount(0)
        self.visible_rows = []
        self.summary.setPlainText("")
        self.detailed_summary.clear()
        self.resolved_summary.clear()
        self.final_text.clear()
        self.audio_report.clear()
        self.controls()

    def delete_meeting(self, mid=None):
        mid = mid or self.mid
        if self.job or self.live_recorder is not None or not mid:
            return
        meeting = self.store.meeting(mid)
        confirm = QMessageBox.question(
            self,
            "Удалить запись",
            f"Удалить «{meeting['title']}» из приложения? Расшифровка и сводка будут удалены безвозвратно.\n"
            "Исходный аудио/видео файл и уже экспортированные заметки в Obsidian не удаляются.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(mid)
        if mid == self.mid:
            self.mid = None
        self.refresh_list()

    def select(self, item, previous=None):
        if item:
            self.stop_playback()
            self.mid = item.data(Qt.ItemDataRole.UserRole)
            self.page = 0
            self.load_detail()

    def load_detail(self):
        if not self.mid:
            return
        meeting = self.store.meeting(self.mid)
        self.heading.setText(meeting["title"])
        state = STATUS.get(meeting["status"], meeting["status"])
        length = f" · {stamp(meeting['duration'])}" if meeting["duration"] else ""
        self.info.setText(f"{state}{length} · {NEXT_STEP.get(meeting['status'], '')}")
        self.error_detail.setText(
            "Причина: " + meeting["error"]
            if meeting["error"] and meeting["status"] not in {"transcribing", "summarizing", "retrying"}
            else ""
        )
        self.load_rows()
        if meeting["summary"]:
            result = json.loads(meeting["summary"])
            self.final_text.setMarkdown(final_markdown(result.get("final")))
            brief, detailed = summary_views(result)
            refs = {r["id"]: stamp(r["start"]) for r in self.store.iter_segments(self.mid)}
            self.summary.show_summary(self.mid, brief, refs)
            self.detailed_summary.show_summary(self.mid, detailed, refs)
            resolved = detailed.get("resolved") or []
            if resolved:
                self.resolved_summary.show_summary(
                    self.mid,
                    dict(
                        overview=detailed.get("resolution_warning")
                        or "Финальный статус с учётом более поздних правок и отмен.",
                        items=resolved,
                    ),
                    refs,
                )
            else:
                self.resolved_summary.setPlainText(
                    detailed.get("resolution_warning")
                    or "В записи нет решений или задач для согласования, либо сводка создана "
                    "до появления этого раздела — пересоздайте сводку, чтобы получить его."
                )
        else:
            self.summary.setPlainText(
                "После распознавания проверьте текст. Затем нажмите «Создать сводку».\n\n"
                "Сводку составит локальная модель на этом Mac — текст никуда не отправляется. "
                "Результат автоматически сохранится в базе знаний."
            )
            self.final_text.setPlainText(
                "Здесь появится итоговый документ в формате, выбранном в Настройки → «Формат сводки»."
            )
            self.detailed_summary.setPlainText(self.summary.toPlainText())
            self.resolved_summary.setPlainText(self.summary.toPlainText())
        report = []
        plan = self.store.checkpoint(self.mid, "asr-plan", 0) or []
        for i, (start, end) in enumerate(plan):
            for d in self.store.checkpoint(self.mid, "audio-quality", i) or []:
                channel = f" · канал {d['channel'] + 1}" if d["channel"] is not None else ""
                report.append(
                    f"{stamp(start)}–{stamp(end)}{channel}: RMS {d['rms_dbfs']} dBFS; "
                    + (", ".join(d["warnings"]) or "нет предупреждений об уровне сигнала")
                )
        self.audio_report.setPlainText(
            "Диагностика уровня звука. Это не оценка точности текста и не измерение шума.\n\n"
            + ("\n".join(report) or "Диагностика появится для новой обработки записи.")
        )
        self.controls()

    def load_rows(self):
        rows = self.store.segments(
            self.mid, self.page * 200, 200, uncertain_only=self.uncertain_only.isChecked()
        )
        # A new page/meeting invalidates the old selection and displayed proposal.
        self.table.blockSignals(True)
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.visible_rows = rows
        self.table.setRowCount(len(rows))
        for i, row in enumerate(rows):
            for col, value in enumerate(
                [
                    stamp(row["start"]),
                    row["text"],
                    self.review_label(row),
                ]
            ):
                self.table.setItem(i, col, QTableWidgetItem(value))
        self.table.blockSignals(False)
        self.selected_segment()
        self.table.resizeRowsToContents()
        self.page_label.setText(f"Страница {self.page + 1} · до 200 реплик")

    def review_label(self, row):
        if not row["uncertain"]:
            return ""
        reasons = [r.strip() for r in (row.get("review") or "").split(",") if r.strip() != "говорящий"]
        label = ", ".join(reasons) or "Проверить"
        if row.get("retry_text"):
            label += "; есть повторный вариант"
        return label

    def turn_page(self, delta):
        if not self.mid:
            return
        new = max(0, self.page + delta)
        only = self.uncertain_only.isChecked()
        if new == 0 or self.store.segments(self.mid, new * 200, 1, uncertain_only=only):
            self.page = new
            self.load_rows()

    def toggle_uncertain_filter(self):
        if not self.mid:
            return
        self.page = 0
        self.load_rows()

    def selected_segment(self):
        index = self.table.currentRow()
        if hasattr(self, "visible_rows") and 0 <= index < len(self.visible_rows):
            row = self.visible_rows[index]
            self.text.setText(row["text"])
            self.retry_label.setText(
                f"Повторный вариант: {row['retry_text']}" if row.get("retry_text") else ""
            )
        else:
            self.text.clear()
            self.retry_label.setText("")
        self.controls()

    def edit_segment(self):
        index = self.table.currentRow()
        if self.job or not self.mid or not 0 <= index < len(self.visible_rows):
            return
        row = self.visible_rows[index]
        self.store.edit_segment(self.mid, row["id"], self.text.text())
        self.load_detail()

    def accept_retry(self):
        index = self.table.currentRow()
        if self.job or not self.mid or not 0 <= index < len(self.visible_rows):
            return
        row = self.visible_rows[index]
        if not row.get("retry_text"):
            return
        try:
            self.store.accept_retry(self.mid, row["id"], expected_text=row["retry_text"])
        except ValueError as exc:
            QMessageBox.warning(self, "Повторный вариант", str(exc))
        self.load_detail()

    def undo_retry(self):
        index = self.table.currentRow()
        if self.job or not self.mid or not 0 <= index < len(self.visible_rows):
            return
        try:
            self.store.undo_retry(self.mid, self.visible_rows[index]["id"])
        except ValueError as exc:
            QMessageBox.information(self, "Отмена принятия", str(exc))
        self.load_detail()

    def play_segment(self):
        index = self.table.currentRow()
        if not self.mid or not 0 <= index < len(self.visible_rows):
            return
        self.play_row(self.visible_rows[index], pad=2.0)

    def play_evidence(self, mid, sid):
        if mid != self.mid:
            return
        row = self.store.segment(mid, sid)
        if row is None:
            QMessageBox.information(self, "Реплика недоступна", "Исходная реплика больше не найдена.")
            return
        self.play_row(row, pad=0.0)

    def play_row(self, row, pad=0.0):
        start = max(0, row["start"] - pad)
        self.start_playback([dict(row, start=start, end=row["end"] + pad)])

    def play_evidence_group(self, mid, ids):
        if mid != self.mid:
            return
        rows = [self.store.segment(mid, sid) for sid in dict.fromkeys(ids)]
        if not rows or any(row is None for row in rows):
            QMessageBox.information(self, "Реплика недоступна", "Одна из исходных реплик больше не найдена.")
            return
        intervals = evidence_intervals(rows, duration=self.store.meeting(mid)["duration"])
        self.start_playback(intervals)

    def start_playback(self, intervals):
        if not intervals:
            return
        source = Path(self.store.meeting(self.mid)["source"])
        if not source.is_file():
            QMessageBox.information(
                self, "Запись недоступна", "Исходный аудио- или видеофайл перемещён либо удалён."
            )
            return
        # ffplay comes with Homebrew FFmpeg; the portable app plays a cut excerpt with the
        # system afplay instead.
        player = shutil.which("ffplay") or shutil.which("afplay")
        if not player:
            QMessageBox.information(
                self,
                "Прослушивание недоступно",
                "Не найден проигрыватель (ffplay или afplay). "
                "Пока можно открыть всю запись кнопкой «Открыть исходную запись» ниже.",
            )
            return
        self.stop_playback()
        self.playback_mid = self.mid
        self.playback_queue = deque(intervals)
        self.playback_total = len(intervals)
        self.playback_source = str(source)
        self.playback_player = player
        self.play_next_excerpt()

    def play_next_excerpt(self):
        if not self.playback_queue or self.playback_mid != self.mid:
            self.stop_playback()
            return
        row = self.playback_queue.popleft()
        start = row["start"]
        duration = max(0.1, row["end"] - start)
        try:
            if Path(self.playback_player).name == "afplay":
                excerpt = data_dir() / "work" / "excerpt.wav"
                excerpt.parent.mkdir(exist_ok=True)
                subprocess.run(
                    [tool("ffmpeg"), "-nostdin", "-v", "error", "-y", "-ss", str(start), "-t", str(duration),
                     "-i", self.playback_source, "-vn", str(excerpt)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=60,
                    check=True,
                )
                args = [self.playback_player, str(excerpt)]
            else:
                args = [
                    self.playback_player,
                    "-nodisp",
                    "-autoexit",
                    "-ss",
                    str(start),
                    "-t",
                    str(duration),
                    self.playback_source,
                ]
            self.player_proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except (OSError, subprocess.SubprocessError):
            self.stop_playback()
            QMessageBox.information(self, "Прослушивание недоступно", "Не удалось запустить проигрыватель.")
            return
        part = self.playback_total - len(self.playback_queue)
        position = f" · {part}/{self.playback_total}" if self.playback_total > 1 else ""
        self.playback_label.setText(f"Играет {stamp(start)}–{stamp(start + duration)}{position}…")
        self.controls()

    def stop_playback(self):
        self.playback_queue.clear()
        self.playback_mid = None
        if self.player_proc and self.player_proc.poll() is None:
            self.player_proc.terminate()
        self.player_proc = None
        self.playback_label.setText("")
        self.stop_button.setEnabled(False)

    def rerun_transcription(self):
        if self.job or not self.mid:
            return
        old = self.store.meeting(self.mid)
        try:
            new_id = self.store.create(old["source"], self.settings)
        except (OSError, ValueError):
            QMessageBox.warning(self, "Запись недоступна", "Исходный файл перемещён. Добавьте его заново.")
            return
        self.store.update(new_id, title=old["title"] + " · заново")
        self.mid, self.page = new_id, 0
        self.refresh_list()
        self.start("transcribe")

    def start(self, phase):
        if self.job or self.live_recorder is not None or not self.mid:
            return
        try:
            meeting = self.store.meeting(self.mid)
            original = Settings.from_dict(json.loads(meeting["settings"]))
            # Preserve transcription chunk geometry on resume.
            if phase == "transcribe":
                if not self.store.checkpoint(self.mid, "source", 0):
                    original = Settings(**asdict(self.settings))
                else:
                    for key in ["memory_gb", "threads", "gpu"]:
                        setattr(original, key, getattr(self.settings, key))
            elif phase == "retry":
                if not self.store.checkpoint(self.mid, "asr_complete", 0):
                    raise ValueError("Сначала завершите распознавание всей записи.")
                # Same rule as resuming transcription: the ASR model/VAD/beam stay
                # exactly as recorded, only memory/threads/GPU follow current settings.
                for key in ["memory_gb", "threads", "gpu"]:
                    setattr(original, key, getattr(self.settings, key))
            else:
                for key in [
                    "llm_model",
                    "llm_preset",
                    "llm_gpu",
                    "summary_instructions",
                    "final_format",
                    "final_prompt",
                    "input_chars",
                    "max_output_tokens",
                    "vault",
                    "memory_gb",
                    "threads",
                ]:
                    setattr(original, key, getattr(self.settings, key))
                if phase in {"summary", "final"}:
                    if not self.has_llm() and not self.download_model():
                        return
                    original.llm_model, original.llm_preset = self.settings.llm_model, self.settings.llm_preset
                    original.validate(llm=True)
                    local_llm.check_fits(original)
                    if not self.store.checkpoint(self.mid, "asr_complete", 0):
                        raise ValueError("Сначала завершите распознавание всей записи.")
                    if phase == "final" and not meeting["summary"]:
                        raise ValueError("Сначала создайте сводку.")
            budget = local_llm.job_budget_gb(original) if phase in {"summary", "final"} else original.memory_gb
            self.store.update(
                self.mid,
                settings=json.dumps(asdict(original)),
                status={"transcribe": "transcribing", "retry": "retrying"}.get(phase, "summarizing"),
                error=None,
            )
            self.active_id = self.mid
            self.job = Job(phase, self.mid, budget)
            self.job.memory.connect(
                lambda rss: self.ram.setText(f"RSS приложения и обработчиков: {rss:.2f} ГиБ / {budget:g} ГиБ")
            )
            self.job.result.connect(self.job_result)
            self.job.finished.connect(self.job_finished)
            self.job.start()
            self.controls()
        except Exception as exc:
            QMessageBox.warning(self, "Не удалось запустить", str(exc))

    def job_result(self, error):
        if error and error != "worker-error":
            self.store.update(self.active_id, status="interrupted", error=error)
        elif error == "worker-error":
            meeting = self.store.meeting(self.active_id)
            if meeting["status"] != "error":
                self.store.update(
                    self.active_id,
                    status="error",
                    error="Обработчик аварийно завершился. Можно повторить запуск.",
                )

    def job_finished(self):
        meeting = self.store.meeting(self.active_id)
        pending, self.pending_phase = self.pending_phase, ""
        if self.job.phase in {"summary", "final"} and meeting["summary"] and self.mid == self.active_id:
            self.tabs.setCurrentIndex(1)
        self.progress.setText(meeting["error"] or "Готово. Результат сохранён.")
        self.job.deleteLater()
        self.job = None
        self.active_id = None
        self.refresh_list()
        self.controls()
        if pending:
            self.start(pending)

    def cancel_job(self):
        if self.job:
            self.job.stop.set()
            self.progress.setText("Остановка обработчиков…")

    def poll(self):
        if self.live_recorder is not None:
            if self.live_recorder.recording:
                # One line during a meeting: the timer plus whatever catch-up is doing.
                note = (self.store.meeting(self.active_id)["error"] or "") if self.active_id else ""
                self.progress.setText(
                    f"● Live-запись · {SOURCE_LABELS[self.live_recorder.source]} · "
                    f"{stamp(self.live_recorder.elapsed)} · "
                    + (note.strip() or "нажмите кнопку ещё раз для остановки")
                )
            else:
                self.stop_live(start_transcription=False)
        elif self.active_id:
            meeting = self.store.meeting(self.active_id)
            self.progress.setText(meeting["error"] or "Подготовка…")
        if self.player_proc and self.player_proc.poll() is not None:
            code = self.player_proc.poll()
            self.player_proc = None
            if code != 0:
                self.stop_playback()
                self.playback_label.setText("Ошибка воспроизведения. Проверьте исходный файл и аудиовыход.")
            elif self.playback_queue:
                self.play_next_excerpt()
            else:
                self.stop_playback()
            self.controls()

    def controls(self):
        recording = self.live_recorder is not None
        busy = self.job is not None or recording
        ready = self.mid is not None
        working = "Дождитесь конца текущей обработки." if self.job else "Идёт запись."
        pick = "Выберите запись в списке слева."
        explain(self.settings_button, not busy, working)
        explain(self.add_button, not busy, working)
        self.search.setEnabled(not busy)
        self.list.setEnabled(not busy)
        # Stopping must stay possible while catch-up recognition is running.
        explain(self.live_button, self.job is None or recording, working)
        self.live_button.setText("■ Live: остановить и распознать" if recording else "● Live: начать запись")
        complete = ready and bool(self.store.checkpoint(self.mid, "asr_complete", 0))
        explain(
            self.transcribe,
            ready and not busy and not complete,
            "Распознавание уже завершено. Для нового прохода — «Распознать заново»."
            if complete
            else (working if busy else pick),
        )
        explain(self.rerun_button, ready and not busy, working if busy else pick)
        # An error button with nothing to copy is noise; it appears only with an error.
        self.copy_error_button.setVisible(bool(self.error_detail.text()))
        explain(self.copy_error_button, bool(self.error_detail.text()))
        explain(
            self.retry,
            ready and not busy and complete,
            working if busy else ("Сначала распознайте запись целиком." if ready else pick),
        )
        explain(
            self.summarize,
            bool(complete) and not busy,
            working if busy else ("Сначала распознайте запись целиком." if ready else pick),
        )
        explain(self.cancel, busy, "Сейчас нечего останавливать.")
        self.model_button.setVisible(not self.has_llm())
        explain(self.model_button, not busy, working)
        summarized = ready and bool(self.store.meeting(self.mid)["summary"])
        explain(
            self.regenerate_button,
            summarized and not busy,
            working if busy else "Сначала создайте сводку.",
        )
        explain(self.copy_final_button, summarized, "Итогового текста пока нет.")
        explain(self.save_segment, ready and not busy, working if busy else pick)
        # Exactly one step is highlighted, so the next action is never a guess.
        step = self.summarize if complete else self.transcribe
        for button in (self.transcribe, self.summarize):
            primary = button is step and button.isEnabled()
            if button.property("primary") != primary:
                button.setProperty("primary", primary)
                button.style().unpolish(button)
                button.style().polish(button)
        index = self.table.currentRow()
        has_retry = (
            ready
            and hasattr(self, "visible_rows")
            and 0 <= index < len(self.visible_rows)
            and bool(self.visible_rows[index].get("retry_text"))
        )
        self.accept_retry_button.setEnabled(has_retry and not busy)
        has_row = ready and hasattr(self, "visible_rows") and 0 <= index < len(self.visible_rows)
        self.undo_retry_button.setEnabled(bool(has_row) and not busy)
        self.play_button.setEnabled(has_row)
        playing = bool(self.player_proc) and self.player_proc.poll() is None
        self.stop_button.setEnabled(playing)
        self.stop_button.setVisible(playing)
        self.playback_label.setVisible(playing)
        self.obsidian.setEnabled(
            ready
            and bool(self.store.meeting(self.mid)["note"])
            and bool(self.store.meeting(self.mid)["summary"])
        )
        self.reexport.setEnabled(ready and not busy and bool(self.store.meeting(self.mid)["summary"]))

    def open_source(self):
        if self.mid:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self.store.meeting(self.mid)["source"]))

    def open_obsidian(self):
        if not self.mid or not (note := self.store.meeting(self.mid)["note"]):
            return
        # Ask Obsidian which vaults it knows rather than guessing a name from the
        # settings folder: the name only matches if the user opened that exact folder
        # as a vault, and a note can also live inside a vault registered higher up.
        url = obsidian.note_url(note)
        if url and QDesktopServices.openUrl(QUrl(url)):
            return
        folder = Path(self.settings.vault).expanduser()
        registered = "\n".join(f"• {location}" for _, location in obsidian.vaults())
        message = (
            "Obsidian не знает хранилища с этой заметкой.\n\n"
            f"Заметка лежит в {folder}. Откройте Obsidian → «Открыть папку как хранилище» "
            "и выберите эту папку — одного наличия папки на диске недостаточно, "
            "ссылка obsidian:// работает только с зарегистрированным хранилищем."
        )
        if registered:
            message += "\n\nСейчас Obsidian знает такие хранилища:\n" + registered
        elif not obsidian.config_path().is_file():
            message = (
                "Не найден конфиг Obsidian — похоже, приложение не установлено или ни разу "
                f"не запускалось. Заметки лежат в {folder} и открываются любым "
                "Markdown-редактором."
            )
        box = QMessageBox(self)
        box.setWindowTitle("Не удалось открыть в Obsidian")
        box.setText(message)
        box.addButton("Показать папку", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Закрыть", QMessageBox.ButtonRole.RejectRole)
        if box.exec() == 0:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(note).parent)))

    def closeEvent(self, event):
        if self.live_recorder is not None:
            self.stop_live(start_transcription=False)
        if self.job:
            self.job.stop.set()
            if not self.job.wait(5000):
                event.ignore()
                return
        self.stop_playback()
        event.accept()


def apply_theme(app):
    """Palette and stylesheet. Separate from main() so a rendered window can be checked."""
    palette = QPalette()
    for role, color in [
        (QPalette.ColorRole.Window, "#f4f5f7"),
        (QPalette.ColorRole.WindowText, "#223e35"),
        (QPalette.ColorRole.Base, "#ffffff"),
        (QPalette.ColorRole.AlternateBase, "#f0f4f1"),
        (QPalette.ColorRole.Text, "#223e35"),
        (QPalette.ColorRole.Button, "#e3ece8"),
        (QPalette.ColorRole.ButtonText, "#173e35"),
        (QPalette.ColorRole.Highlight, "#d9e9e2"),
        (QPalette.ColorRole.HighlightedText, "#163f33"),
    ]:
        palette.setColor(role, QColor(color))
    app.setPalette(palette)
    app.setStyleSheet("""
        QWidget { font-size: 13px; }
        QMainWindow { background: #f4f5f7; }
        QLabel#brand { font-size: 25px; font-weight: 700; color: #185c50; padding: 14px 10px; }
        QLabel#heading { font-size: 21px; font-weight: 600; padding: 14px 0; }
        QLabel#step { color: #3c5a51; }
        QPushButton { padding: 8px 12px; border-radius: 6px; background: #e3ece8; color: #173e35; }
        QPushButton:hover { background: #ccded5; }
        QPushButton:disabled { color: #87968f; background: #edf0ee; }
        QPushButton[primary="true"] { background: #185c50; color: #ffffff; font-weight: 600; }
        QPushButton[primary="true"]:hover { background: #145046; }
        QPushButton[primary="true"]:disabled { background: #cfd8d4; color: #8a9a94; }
        QLineEdit { padding: 7px; }
        QListWidget, QTableWidget, QTextBrowser { background: white; border: 1px solid #d9dfdb; }
        QListWidget::item:selected { background: #d9e9e2; color: #163f33; }
        QLabel#rowStatus { color: #6b7d76; font-size: 12px; }
        QPushButton#trashButton { padding: 4px; background: transparent; border-radius: 4px; }
        QPushButton#trashButton:hover { background: #f0d3d3; }
    """)


def main():
    os.umask(0o077)
    app = QApplication(sys.argv)
    app.setApplicationName("Samarizator")
    app.setStyle("Fusion")
    lock = QLockFile(str(data_dir() / "app.lock"))
    lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None, "Samarizator", "Приложение уже запущено.")
        return 0
    apply_theme(app)
    window = Window()
    window.show()
    code = app.exec()
    lock.unlock()
    return code


if __name__ == "__main__":
    sys.exit(main())
