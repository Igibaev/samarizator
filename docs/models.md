# Зависимости и модели

Python-пакеты зафиксированы с хешами в `uv.lock`; `start.sh` использует `uv sync --frozen`. FFmpeg и Whisper.cpp устанавливаются Homebrew из его актуальных формул. Веса загружаются отдельно, не включаются в Git. `download-manifest.json` фиксирует SHA-256 полученных файлов для локального учёта; это не независимая криптографическая проверка опубликованного эталона. Корпоративный проверенный файл можно разместить вручную и выбрать в настройках.

| Компонент | Назначение | Первичный источник |
|---|---|---|
| Whisper.cpp, модель small-q5_1 | Локальное распознавание речи, CPU | https://github.com/ggml-org/whisper.cpp |
| GGML Whisper models | Мультиязычные веса | https://huggingface.co/ggerganov/whisper.cpp |
| sherpa-onnx | Локальное выполнение диаризации | https://k2-fsa.github.io/sherpa/onnx/speaker-diarization/index.html |
| pyannote segmentation 3.0 ONNX | Поиск участков и смены говорящих | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-segmentation-models |
| 3D-Speaker ERes2Net | Векторы голоса для группировки | https://github.com/k2-fsa/sherpa-onnx/releases/tag/speaker-recongition-models |
| PySide6 | Десктопный интерфейс Qt | https://doc.qt.io/qtforpython-6/ |
| FFmpeg | Извлечение первой аудиодорожки / каналов | https://ffmpeg.org/ |
| Homebrew whisper-cpp | Установка CLI на Mac | https://formulae.brew.sh/formula/whisper-cpp |

Каждый сторонний компонент и набор весов имеет собственные условия лицензирования. Установка зависимостей не перелицензирует их. Для корпоративного распространения проверьте условия конкретных используемых версий, моделей, Qt/PySide6 и сборки FFmpeg. Этот репозиторий не включает и не распространяет веса моделей.
