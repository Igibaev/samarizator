# Сборка портативного приложения

Результат — `dist/Samarizator-<версия>-arm64.dmg` с `Samarizator.app`, которому на Mac
пользователя ничего не нужно: ни Homebrew, ни Python, ни Terminal.

## Что внутри `.app`

| Путь | Что |
|---|---|
| `Contents/MacOS/Samarizator` | Python + PySide6, замороженные PyInstaller. Тот же исполняемый файл запускает фоновые задачи: `Samarizator --run-module samarizator.worker …` |
| `Contents/Resources/bin/ffmpeg`, `ffprobe` | FFmpeg, статически, только системные фреймворки macOS (AVFoundation для микрофона) |
| `Contents/Resources/bin/whisper-cli` | whisper.cpp, статически, Metal встроен |
| `Contents/Resources/bin/llama-server` | llama.cpp, статически, Metal встроен — модель сводок |
| `Contents/Resources/bin/samarizator-system-audio` | helper ScreenCaptureKit (`native/macos-capture`) |
| `Contents/Resources/models/` | модели из `models/bundle.json` и сам манифест — по нему первый запуск выбирает модели |
| `Contents/Resources/build.txt` | версия и коммит, видны в заголовке окна |

Пользовательские данные по-прежнему в `~/Library/Application Support/Samarizator`, туда же
скачивается модель сводок. Приложение можно переносить: пути к встроенным моделям
переопределяются при запуске.

## Сборка на GitHub (рекомендуется)

Actions → **Build macOS app** → **Run workflow**. Через ~30–60 минут (первый раз дольше —
собираются FFmpeg, whisper.cpp и llama.cpp; дальше они берутся из кэша) в артефактах
появится `Samarizator-macOS-arm64` с `.dmg`. Тег `v*` дополнительно публикует `.dmg` в
Releases. Перед сборкой прогоняются тесты и Ruff.

Поле `bundle_llm` встраивает модель сводок в приложение (например `qwen3-4b`, +2,5 ГБ):
пользователю тогда не нужен даже первый интернет. Ограничение GitHub Releases — 2 ГБ на
файл, поэтому такой `.dmg` забирайте из артефактов.

### Подпись и нотаризация

Без секретов приложение подписывается ad-hoc: работает, но при первом запуске macOS
просит подтвердить его в «Конфиденциальность и безопасность» (инструкция лежит в `.dmg`).
Чтобы приложение открывалось сразу, нужен аккаунт Apple Developer и секреты репозитория:

| Секрет | Значение |
|---|---|
| `MACOS_CERTIFICATE` | сертификат «Developer ID Application» в `.p12`, base64 |
| `MACOS_CERTIFICATE_PASSWORD` | пароль `.p12` |
| `MACOS_SIGN_IDENTITY` | `Developer ID Application: Имя (TEAMID)` |
| `APPLE_ID`, `APPLE_TEAM_ID`, `APPLE_APP_PASSWORD` | для `notarytool`; пароль — app-specific |

С ними сборка подписывает всё содержимое с hardened runtime, нотаризует приложение и
`.dmg` и прикрепляет тикеты (`stapler`).

## Сборка на своём Mac

```bash
xcode-select --install          # один раз
brew install cmake uv           # только на машине сборки
./packaging/macos/build.sh
```

Переменные: `SAMARIZATOR_SIGN_IDENTITY`, `APPLE_ID`/`APPLE_TEAM_ID`/`APPLE_APP_PASSWORD`,
`SAMARIZATOR_BUNDLE_LLM`, `SAMARIZATOR_BUILD_DIR` — см. начало скрипта. Сборка для Intel
делается тем же скриптом на Intel Mac (для FFmpeg желателен `brew install nasm`).

## Модели в сборке

`models/bundle.json` перечисляет, что встроить: `whisper`, `vad`, `llm`. Для каждой модели
сборка берёт файл из `models/` (Git LFS), иначе склеивает его части `.part-NNN`, иначе
скачивает, и проверяет `sha256`, если он записан. Как положить модели в репозиторий и
ограничения GitHub (место в LFS, диск раннера, размер релиза) — [models/README.md](../models/README.md).
`SAMARIZATOR_BUNDLE_LLM` добавляет модель сводок поверх манифеста для одной сборки.

## Проверки внутри сборки

- `otool -L` для каждого бинарника: ссылки только на `/usr/lib` и `/System`. Иначе сборка
  падает — значит, что-то подтянулось из Homebrew и не запустится у пользователя.
- `codesign --verify --deep --strict`.
- `Samarizator --run-module samarizator.selfcheck`: запускает каждый инструмент из
  приложения, проверяет модели и Qt. Ту же команду можно дать пользователю при проблеме.

## Закреплённые версии

`FFMPEG_TAG`, `WHISPER_TAG`, `LLAMA_TAG` в начале `build.sh`. Флаги `whisper-cli` и
`llama-server`, которые использует приложение, проверены на этих версиях. При обновлении
llama.cpp проверьте, что `--skip-chat-parsing`, `--reasoning-budget`, `--parallel` и поле
`grammar` в запросе по-прежнему поддерживаются (`llama-server --help`), и прогоните
`tests/test_local_llm.py`.
