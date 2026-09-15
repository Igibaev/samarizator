import AppKit

/// `@MainActor` на всём классе: делегат напрямую владеет и пользуется
/// изолированными объектами (`CompanionStateMachine`, `HoverDetector`,
/// `NotchWindowController`), а весь его жизненный цикл и так протекает на
/// главном потоке. Без этой пометки каждое обращение к ним — обращение из
/// nonisolated-контекста, то есть ошибка компиляции.
@MainActor
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
        menu.addItem(makeStateSubmenuItem())
        menu.addItem(.separator())
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

    /// Debug-подменю переключения состояний персонажа — критерий приёмки
    /// Фазы 3. Текущее состояние отмечено галочкой; выбор пункта переключает
    /// `CompanionStateMachine` (живёт в `NotchWindowController`, переживает
    /// переезды между экранами) с анимацией.
    private func makeStateSubmenuItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Состояние (debug)", action: nil, keyEquivalent: "")
        let submenu = NSMenu()
        let currentState = windowController.stateMachine.state

        for state in CompanionState.allCases {
            let item = NSMenuItem(
                title: state.displayName,
                action: #selector(selectState(_:)),
                keyEquivalent: ""
            )
            item.target = self
            item.representedObject = state
            item.state = (state == currentState) ? .on : .off
            submenu.addItem(item)
        }

        parent.submenu = submenu
        return parent
    }

    @objc private func selectState(_ sender: NSMenuItem) {
        guard let state = sender.representedObject as? CompanionState else { return }
        windowController.stateMachine.setState(state)

        // NSMenu не обновляет галочки соседних пунктов сам — проходим по
        // всему подменю и выставляем `.on` только у выбранного.
        sender.menu?.items.forEach { menuItem in
            let itemState = menuItem.representedObject as? CompanionState
            menuItem.state = (itemState == state) ? .on : .off
        }
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
        print("стартовое состояние персонажа: \(windowController.stateMachine.state.displayName)")
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
            // Фаза 3: окно панели теперь ВСЕГДА в размере раскрытого
            // состояния (capsulePanelFrame) — печатаем отдельно от размера
            // самой капсулы в свёрнутом виде (collapsedCapsuleFrame), иначе
            // цифры легко перепутать при подборе AppearanceConfig.expandedWidth/Height.
            print("фрейм окна панели (раскрытый размер): \(screen.capsulePanelFrame)")
            print("фрейм капсулы (свёрнутый размер): \(screen.collapsedCapsuleFrame)")
        }
        print("=======================")
        fflush(stdout)
    }
}
