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
| `Contents/Resources/models/` | модели из `models/bundle.json` (по умолчанию только VAD, ~1 МБ) и сам манифест |
| `Contents/Resources/build.txt` | версия и коммит, видны в заголовке окна |

Пользовательские данные по-прежнему в `~/Library/Application Support/Samarizator`, туда же
скачиваются модель распознавания и модель сводок. Приложение можно переносить: пути к встроенным моделям
переопределяются при запуске.

## Выпуск релиза

Основная ветка — `main`. Релиз собирается на GitHub (macOS-раннер с Apple Silicon):

1. Поднимите версию в `pyproject.toml` и `src/samarizator/__init__.py` (и `uv lock`).
2. Закоммитьте в `main` с меткой **`[release-macos]`** в сообщении коммита, например
   `git commit -m "Релиз 0.2.0 [release-macos]"`, и отправьте. Либо отправьте тег
   `v0.2.0` — результат тот же.
3. Workflow **Build macOS app** прогоняет тесты и Ruff, собирает приложение и `.dmg`, ставит
   тег `v<версия>` и публикует GitHub Release с `.dmg` и заметками
   `packaging/macos/RELEASE_NOTES.md` (обновите их перед релизом).
4. [Страница загрузки](https://igibaev.github.io/samarizator/) сама берёт последний релиз
   через GitHub API — её перевыкладывать не нужно.

Первый раз сборка идёт ~30–60 минут (собираются FFmpeg, whisper.cpp и llama.cpp), дальше
они берутся из кэша и сборка занимает около 10 минут.

**Сборка без релиза** — метка `[build-macos]` в сообщении коммита (в `main` или рабочей
ветке `claude/**`) или Actions → Build macOS app → Run workflow: `.dmg` появится в
артефактах запуска `Samarizator-macOS-arm64`. Коммиты без меток сборку не запускают.

Поле `bundle_llm` встраивает модель сводок в приложение (например `qwen3-4b`, +2,5 ГБ) —
для Mac без интернета. Ограничение GitHub Releases — 2 ГБ на файл, поэтому такой `.dmg`
забирайте из артефактов.

## Страница загрузки

`site/index.html` — одна страница без зависимостей. Workflow **Download page**
(`.github/workflows/pages.yml`) публикует её на GitHub Pages при изменении `site/` в `main`.
Один раз нужно включить Pages: Settings → Pages → Build and deployment → Source:
**GitHub Actions**, затем Actions → Download page → Run workflow. Кнопка «Скачать» ведёт на
страницу последнего релиза, а когда GitHub API доступен — прямо на `.dmg` с версией,
размером и датой.

## Подпись и нотаризация

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

Модели в репозитории не хранятся. По умолчанию сборка встраивает только VAD, а модели
распознавания и сводок пользователь выбирает и скачивает в приложении. Чтобы встроить
модель, впишите её в `models/bundle.json` (`whisper`, `llm`) или задайте
`SAMARIZATOR_BUNDLE_LLM`: сборка возьмёт файл из `models/` на машине сборки, иначе скачает,
и проверит `sha256`, если он записан. Подробнее — [models/README.md](../models/README.md).

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
