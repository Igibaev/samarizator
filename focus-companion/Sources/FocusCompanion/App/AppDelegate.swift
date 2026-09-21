import AppKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    private let windowController = NotchWindowController()
    private var statusItem: NSStatusItem?

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        setUpStatusItem()
        windowController.show()
        applyStartupFixtureIfRequested()
        logDiagnostics()
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(screenParametersDidChange),
            name: NSApplication.didChangeScreenParametersNotification,
            object: nil
        )
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    @objc private func screenParametersDidChange() {
        windowController.reposition()
    }

    /// FOCUS_FIXTURE=<имя> открывает воспроизводимое состояние сразу при старте.
    private func applyStartupFixtureIfRequested() {
        guard let raw = ProcessInfo.processInfo.environment["FOCUS_FIXTURE"],
              let fixture = CompanionFixture(rawValue: raw) else { return }
        windowController.applyFixture(fixture)
    }

    // MARK: - Меню в строке состояния

    private func setUpStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        item.button?.title = "FC"
        item.button?.toolTip = "ИИ-компаньон"
        item.behavior = []
        item.isVisible = true

        let menu = NSMenu()
        menu.addItem(makeMenuItem(title: "Фокус", action: #selector(openFocus)))
        menu.addItem(makeMenuItem(title: "Буфер", action: #selector(openClipboard)))
        menu.addItem(makeMenuItem(title: "Записи", action: #selector(openRecordings)))
        menu.addItem(.separator())
        menu.addItem(makeFixtureSubmenuItem())
        menu.addItem(makeStateSubmenuItem())
        menu.addItem(makeMenuItem(
            title: "Вернуться к моим данным",
            action: #selector(leaveFixture)
        ))
        menu.addItem(.separator())
        menu.addItem(withTitle: "Выход", action: #selector(quit), keyEquivalent: "q")
        item.menu = menu
        statusItem = item
    }

    private func makeMenuItem(title: String, action: Selector) -> NSMenuItem {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: "")
        item.target = self
        return item
    }

    @objc private func openFocus() { windowController.navigation.show(page: .focus) }
    @objc private func openClipboard() { windowController.navigation.show(page: .clipboard) }
    @objc private func openRecordings() { windowController.navigation.show(page: .recordings) }
    @objc private func leaveFixture() { windowController.leaveFixture() }
    @objc private func quit() { NSApp.terminate(nil) }

    /// Воспроизводимые состояния для проверки без ожидания реальных сроков.
    private func makeFixtureSubmenuItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Состояния для проверки", action: nil, keyEquivalent: "")
        let submenu = NSMenu()
        for fixture in CompanionFixture.allCases {
            let item = NSMenuItem(
                title: fixture.displayName,
                action: #selector(selectFixture(_:)),
                keyEquivalent: ""
            )
            item.target = self
            item.representedObject = fixture.rawValue
            submenu.addItem(item)
        }
        parent.submenu = submenu
        return parent
    }

    @objc private func selectFixture(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let fixture = CompanionFixture(rawValue: raw) else { return }
        windowController.applyFixture(fixture)
    }

    private func makeStateSubmenuItem() -> NSMenuItem {
        let parent = NSMenuItem(title: "Эмоция (debug)", action: nil, keyEquivalent: "")
        let submenu = NSMenu()
        for state in CompanionState.allCases {
            let item = NSMenuItem(
                title: state.displayName,
                action: #selector(selectState(_:)),
                keyEquivalent: ""
            )
            item.target = self
            item.representedObject = state.rawValue
            item.state = (state == windowController.stateMachine.state) ? .on : .off
            submenu.addItem(item)
        }
        parent.submenu = submenu
        return parent
    }

    @objc private func selectState(_ sender: NSMenuItem) {
        guard let raw = sender.representedObject as? String,
              let state = CompanionState(rawValue: raw) else { return }
        windowController.stateMachine.setState(state)
        sender.menu?.items.forEach { menuItem in
            menuItem.state = (menuItem.representedObject as? String) == raw ? .on : .off
        }
    }

    // MARK: - Диагностика при старте

    /// Компаньон внешне почти неотличим от незапустившегося: крыло чёрное и
    /// точно по высоте выреза. Поэтому метрики печатаются в терминал, а
    /// FOCUS_DEBUG=1 красит крыло красным.
    private func logDiagnostics() {
        print("=== ИИ-компаньон ===")
        print("debug-режим: \(CharacterConfig.isDebug ? "ВКЛ (крыло красное)" : "выкл")")
        print("демо-режим: \(CompanionSettings.demoMode ? "ВКЛ" : "выкл")")
        print("панель показана: \(windowController.isPanelVisible ? "да" : "НЕТ")")
        print("панель пропускает клики: "
            + (windowController.panelIgnoresMouseEvents.map(String.init(describing:)) ?? "панели нет"))
        print("записи: \(windowController.recordings.availability.explanation)")
        print("передачи со встреч: \(HandoffInbox.directoryURL.path), файлов: \(windowController.taskPanel.handoffs.count)")
        print("экранов: \(NSScreen.screens.count)")
        for screen in NSScreen.screens {
            print(screen.geometryDescription)
        }
        print("фикстуры: FOCUS_FIXTURE=" + CompanionFixture.allCases.map(\.rawValue).joined(separator: "|"))
        print("====================")
        fflush(stdout)
    }
}
