# ФАЗА 1 — Окно поверх notch (фундамент Focus Companion)

## Контекст проекта

Разрабатывается macOS-приложение **Focus Companion** для людей с СДВГ: маленький
персонаж, живущий в notch (вырезе) MacBook. Он раскрывается при наведении мыши,
имеет глаза, следящие за курсором, и выражает эмоции. Позже будет принимать
голосовые задачи и напоминать о них.

Работа идёт СТРОГО по фазам. Сейчас — только ФАЗА 1. Ничего из фаз 2-5 не
реализовывать, но не мешать им архитектурно.

Фазы (для понимания, куда всё идёт):
- **Фаза 1 (эта)** — borderless-окно над вырезом, статичная чёрная капсула.
- Фаза 2 — глаза, слежение за курсором, моргание, саккады, «дыхание».
- Фаза 3 — раскрытие по ховеру (morph collapsed↔expanded через
  matchedGeometryEffect), машина состояний эмоций.
- Фаза 4 — задачи: 3 слота, механика «сжигания».
- Фаза 5 — интеграция с проектом samarizator (транскрибация + саммаризация).

## Стек и ограничения

- Swift + SwiftUI + AppKit, macOS 14+
- **Ноль сторонних зависимостей.** Решение принято осознанно: DynamicNotchKit
  рассматривался и отклонён — он закрывает только Фазу 1, а на Фазах 2-3 его
  контейнер мешает (matchedGeometryEffect между collapsed/expanded, своя машина
  состояний, подсветка по эмоциям, пульсация всей капсулы).
- Архитектура MVVM; состояние персонажа позже поедет в отдельный StateMachine.
- Комментарии в коде — **по-русски** там, где логика неочевидна. Не комментировать
  очевидное.

## КРИТИЧЕСКИ ВАЖНО: среда сборки

Ты работаешь в **Linux-контейнере. Swift-тулчейна и macOS SDK здесь нет.**
`swift build` / `xcodebuild` выполнить невозможно — не пытайся, не трать на это
время и НЕ придумывай, что сборка прошла.

Следствие: цена ошибки в API высокая, проверить её некому кроме автора на Mac.
Поэтому:
- Используй только те API, в которых уверен. Не выдумывай сигнатуры методов.
- Где API неочевиден (`auxiliaryTopLeftArea`, `safeAreaInsets` на `NSScreen`,
  `NSPanel.collectionBehavior`) — сверяйся с референсом ниже, он взят из рабочего
  опенсорс-проекта.
- В конце отчёта честно напиши: «сборка не проверялась, нужен запуск на macOS».

## Куда класть код

Новая папка `focus-companion/` в корне репозитория (репозиторий — samarizator).
**Ничего за пределами `focus-companion/` и `docs/focus-companion/` не трогать.**
Python-код samarizator (`src/`, `tests/`, `scripts/`, `native/`) — не твой, руки прочь.

## Формат проекта: SwiftPM + скрипт сборки .app

Xcode-проект (`.xcodeproj`) генерировать НЕ надо — его формат плохо пишется руками
и ты не сможешь его проверить. Вместо этого:

- `Package.swift` — executable target, platform `.macOS(.v14)`.
- Скрипт `focus-companion/build-app.sh`: запускает `swift build -c release`,
  собирает `.app`-бандл вручную (создаёт `FocusCompanion.app/Contents/MacOS/`,
  `Contents/Info.plist`, кладёт бинарник), и `open`-ит его.
- LSUIElement задаётся ДВУМЯ способами (пояс и подтяжки): ключ `LSUIElement`
  в `Info.plist` И `NSApplication.shared.setActivationPolicy(.accessory)` в коде.
  Второе гарантирует отсутствие иконки в Dock и в Cmd+Tab даже при запуске
  голого бинарника без бандла.

## Файлы, которые нужно создать

```
focus-companion/
├── Package.swift
├── build-app.sh                         # сборка + упаковка в .app + запуск
├── README.md                            # как собрать и запустить, что уже работает
├── Resources/Info.plist                 # LSUIElement, bundle id, min system version
└── Sources/FocusCompanion/
    ├── App/
    │   ├── main.swift                   # точка входа: NSApplication, delegate, run()
    │   └── AppDelegate.swift            # жизненный цикл + подписка на смену экранов
    ├── Window/
    │   ├── NotchPanel.swift             # подкласс NSPanel
    │   └── NotchWindowController.swift  # создание, позиционирование, переезд
    ├── Geometry/
    │   └── ScreenGeometry.swift         # extension NSScreen: метрики выреза
    ├── UI/
    │   ├── NotchShape.swift             # SwiftUI Shape капсулы
    │   └── NotchRootView.swift          # корневая вью: пока чёрная капсула
    └── Config/
        └── AppearanceConfig.swift       # ВСЕ числа (радиусы, размеры, отступы)
```

## Требования по файлам

### `Config/AppearanceConfig.swift`
Единая структура со статическими константами. Сюда выносятся ВСЕ магические числа:
радиус верхних углов, радиус нижних углов, размеры виртуальной капсулы для машин
без выреза, цвет капсулы. Задел на Фазу 2, где добавятся параметры анимаций
(автор должен крутить внешний вид, не трогая логику).

### `Geometry/ScreenGeometry.swift`
`extension NSScreen` со свойствами. Референс из рабочего проекта
(DynamicNotchKit, MIT) — логика верна, можешь брать за основу, но оформи в своём
стиле и с русскими комментариями:

```swift
static var screenWithMouse: NSScreen? {
    let mouseLocation = NSEvent.mouseLocation
    return NSScreen.screens.first { NSMouseInRect(mouseLocation, $0.frame, false) }
}

var hasNotch: Bool {
    auxiliaryTopLeftArea?.width != nil && auxiliaryTopRightArea?.width != nil
}

var notchSize: NSSize? {
    guard let leftPad = auxiliaryTopLeftArea?.width,
          let rightPad = auxiliaryTopRightArea?.width else { return nil }
    // Высота выреза = верхний safe area inset.
    // Ширина = ширина экрана минус «уши» меню-бара слева и справа от выреза.
    return NSSize(width: frame.width - leftPad - rightPad, height: safeAreaInsets.top)
}

var notchFrame: NSRect? {
    guard let notchSize else { return nil }
    return NSRect(x: frame.midX - notchSize.width / 2,
                  y: frame.maxY - notchSize.height,
                  width: notchSize.width, height: notchSize.height)
}

var menubarHeight: CGFloat { frame.maxY - visibleFrame.maxY }
```

Плюс главное свойство — **фрейм с запасным вариантом**: если выреза нет (MacBook Air
без выреза, внешний монитор), вернуть виртуальную капсулу по центру верха экрана
шириной из конфига (ориентир — 200-300pt) и высотой = `menubarHeight`.
Приложение должно одинаково работать на трёх конфигурациях: MacBook с вырезом,
MacBook без выреза, внешний монитор.

### `Window/NotchPanel.swift`
Подкласс `NSPanel`:
- `styleMask`: `[.borderless, .nonactivatingPanel]`
- `backgroundColor = .clear`, `isOpaque = false`, `hasShadow = false`
- `level` — выше меню-бара. В референсе используется `.screenSaver`; это рабочий
  вариант. Если считаешь, что `.statusBar` достаточно — обоснуй в комментарии.
- `collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]`
- **`canBecomeKey` → `false`, `canBecomeMain` → `false`.** Окно не должно забирать
  фокус у того, с чем работает пользователь. (В референсе `canBecomeKey = true` —
  им нужен ввод, нам на Фазе 1 нет. На Фазе 3 это пересмотрим.)
- `ignoresMouseEvents = true` на Фазе 1. Hover появится в Фазе 3; пока панель не
  должна перехватывать ни одного клика.

### `Window/NotchWindowController.swift`
- Создаёт панель с `NSHostingView(rootView: NotchRootView())`.
- Позиционирует по `notchFrameWithFallback` нужного экрана.
- Метод `moveToScreen(_:)` / `reposition()` для переезда.
- Показывает панель через `orderFrontRegardless()` (не `makeKeyAndOrderFront` —
  фокус не забираем).

### `App/AppDelegate.swift`
- `applicationDidFinishLaunching`: `setActivationPolicy(.accessory)`, создать контроллер.
- Подписка на `NSApplication.didChangeScreenParametersNotification` — пересчитать
  и переехать при подключении/отключении монитора или смене разрешения.
- Переезд за активным дисплеем: при изменении параметров экранов выбирать
  `NSScreen.screenWithMouse ?? NSScreen.main`.
- Способ выйти из приложения: `NSStatusItem` в меню-баре с пунктом «Выход» ИЛИ
  обработка Cmd+Q. Без этого приложение без Dock-иконки невозможно закрыть
  иначе как через Activity Monitor — это обязательно, не опционально.

### `UI/NotchShape.swift`
`Shape` с `animatableData` (AnimatablePair двух радиусов — понадобится на Фазе 3
для анимации morph). Форма: верхние углы капсулы загнуты наружу-вверх (стыкуются
с краем экрана без шва), нижние углы скруглены внутрь — так вырез визуально
продолжается вниз. Референс реализации через `addQuadCurve` есть в
DynamicNotchKit/Views/NotchShape.swift — логика пути там правильная.
Внимание на систему координат SwiftUI (y растёт вниз).

### `UI/NotchRootView.swift`
Пока просто: `NotchShape` залитый чёрным (`Color.black`), на весь доступный размер.
Никаких глаз — это Фаза 2.

## Критерий приёмки

1. `./build-app.sh` собирает и запускает приложение.
2. Над вырезом появляется статичная чёрная капсула, **визуально неотличимая от
   продолжения железного выреза** — без шва, без светлой каймы, без тени.
3. Иконки в Dock нет, в Cmd+Tab приложения нет.
4. **Меню-бар под капсулой и вокруг неё остаётся полностью кликабельным.**
   (Достигается тем, что панель ограничена шириной выреза + `ignoresMouseEvents`.)
5. При подключении внешнего монитора / переносе мыши на другой дисплей капсула
   переезжает.
6. На машине без выреза приложение не падает — рисует виртуальную капсулу.
7. Приложение можно нормально закрыть.

## Что сделать в конце

1. Написать `focus-companion/README.md`: как собрать, как запустить, как закрыть,
   что сделано в Фазе 1, что осознанно отложено.
2. Закоммитить в ветку `claude/hopeful-brahmagupta-x9vwyb` с внятным сообщением
   на русском и запушить (`git push -u origin claude/hopeful-brahmagupta-x9vwyb`).
   Pull request НЕ создавать.
3. В отчёте: список созданных файлов, все принятые решения, все места, где ты не
   уверен в API и что автору стоит проверить первым делом на Mac.

## Чего НЕ делать

- Не реализовывать глаза, hover, состояния эмоций, задачи — это Фазы 2-4.
- Не добавлять зависимости в `Package.swift`.
- Не трогать Python-код samarizator.
- Не создавать pull request.
- Не утверждать, что сборка проверена.
