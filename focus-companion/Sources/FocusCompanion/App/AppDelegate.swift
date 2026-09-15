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
        item.button?.image = NSImage(
            systemSymbolName: "circle.fill",
            accessibilityDescription: "Focus Companion"
        )

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
        print("иконка в меню-баре: \(statusItem?.button != nil ? "создана" : "НЕ СОЗДАНА")")
        print("панель показана: \(windowController.isPanelVisible ? "да" : "НЕТ")")
        print("экранов: \(NSScreen.screens.count)")
        for screen in NSScreen.screens {
            print(screen.geometryDescription)
        }
        print("=======================")
        fflush(stdout)
    }
}
