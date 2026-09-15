import AppKit

final class AppDelegate: NSObject, NSApplicationDelegate {

    private let windowController = NotchWindowController()
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Дублирует LSUIElement из Info.plist: гарантирует отсутствие иконки
        // в Dock и Cmd+Tab даже при запуске голого бинарника без .app-бандла
        // (например, напрямую из `swift run`).
        NSApp.setActivationPolicy(.accessory)

        setUpStatusItem()

        windowController.show()

        logDiagnostics()

        // У приложения без Dock-иконки и без стандартного меню Cmd+Q не работает
        // "из коробки" — единственный способ выйти без Activity Monitor это
        // пункт меню в NSStatusItem, который мы и заводим выше.
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(screenParametersDidChange),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        // У нас нет "обычных" окон, закрытие которых должно завершать приложение —
        // капсула управляется контроллером напрямую, а не системой жизненного
        // цикла окон.
        false
    }

    @objc private func screenParametersDidChange() {
        // Переезжаем за активным дисплеем: экран с курсором мыши в приоритете,
        // иначе — основной экран. Срабатывает при подключении/отключении
        // монитора и при смене разрешения.
        windowController.reposition()
    }

    private func setUpStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)

        // Текстовый заголовок, а не SF Symbol: символ мог не отрисоваться и дать
        // кнопку нулевой ширины — то есть невидимый пункт меню-бара. Текст
        // виден гарантированно. Иконку вернём, когда убедимся, что пункт на месте.
        item.button?.title = "FC"
        item.button?.toolTip = "Focus Companion"

        // Пункт не должен прятаться под вырез, если в меню-баре много иконок.
        item.behavior = []
        item.isVisible = true

        let menu = NSMenu()
        menu.addItem(
            withTitle: "Выход",
            action: #selector(quit),
            keyEquivalent: "q"
        )
        item.menu = menu

        statusItem = item
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }

    /// Отладочный вывод при старте.
    ///
    /// Нужен потому, что в обычном режиме работающее приложение внешне
    /// неотличимо от незапустившегося: капсула чёрная и точно по размеру выреза.
    /// Видно только при запуске бинарника из терминала.
    private func logDiagnostics() {
        print("=== Focus Companion ===")
        print("debug-режим: \(AppearanceConfig.isDebug ? "ВКЛ (капсула красная и вытянута вниз)" : "выкл")")
        if let button = statusItem?.button {
            print("иконка в меню-баре: создана, isVisible=\(statusItem?.isVisible ?? false), "
                  + "ширина кнопки=\(button.frame.width), окно кнопки=\(String(describing: button.window?.frame))")
        } else {
            print("иконка в меню-баре: НЕ СОЗДАНА")
        }
        print("панель показана: \(windowController.isPanelVisible ? "да" : "НЕТ")")
        print("панель пропускает клики (ignoresMouseEvents): \(windowController.panelIgnoresMouseEvents.map(String.init(describing:)) ?? "панели нет")")
        print("экранов: \(NSScreen.screens.count)")
        for screen in NSScreen.screens {
            print(screen.geometryDescription)
        }
        // Фаза 2: капсула теперь шире и глубже физического выреза
        // (AppearanceConfig.capsuleExtraWidthPerSide/capsuleExtraDepth) —
        // печатаем итоговый фрейм отдельной строкой, чтобы цифры не терялись
        // среди остальной геометрии экрана.
        print("расширение капсулы: +\(AppearanceConfig.capsuleExtraWidthPerSide)pt на сторону по ширине, "
              + "+\(AppearanceConfig.capsuleExtraDepth)pt в глубину"
              + (AppearanceConfig.isDebug ? " (+\(AppearanceConfig.debugExtraHeight)pt debug-довесок)" : ""))
        if let screen = NSScreen.screenWithMouse ?? NSScreen.main {
            print("итоговый фрейм капсулы: \(screen.capsulePanelFrame)")
        }
        print("=======================")
        fflush(stdout)
    }
}
