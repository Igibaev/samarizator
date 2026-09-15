import AppKit
import SwiftUI

/// Управляет жизненным циклом единственной панели-капсулы: создание,
/// позиционирование над вырезом и переезд между экранами.
final class NotchWindowController {

    private var panel: NotchPanel?

    /// Экран, за которым сейчас "закреплена" капсула — нужен, чтобы не
    /// пересоздавать панель при каждом уведомлении, а только при реальном
    /// переезде.
    private var currentScreen: NSScreen?

    /// Действительно ли панель существует и показана — для отладочного вывода.
    var isPanelVisible: Bool { panel?.isVisible ?? false }

    /// Пропускает ли панель клики насквозь — для отладочного вывода.
    var panelIgnoresMouseEvents: Bool? { panel?.ignoresMouseEvents }

    /// Создаёт и показывает панель на переданном экране (или на экране с
    /// курсором мыши, если экран не передан).
    func show(on screen: NSScreen? = nil) {
        let targetScreen = screen ?? NSScreen.screenWithMouse ?? NSScreen.main

        guard let targetScreen else {
            // Теоретически недостижимо (macOS всегда сообщает хотя бы один
            // экран), но на всякий случай не падаем.
            return
        }

        if let panel {
            panel.close()
        }

        let frame = targetScreen.capsulePanelFrame
        let newPanel = NotchPanel(contentRect: frame)
        newPanel.contentView = NSHostingView(rootView: NotchRootView())

        // Повторно после contentView: NSHostingView добавляет свои tracking-области,
        // и флаг важнее любых настроек содержимого — меню-бар под капсулой обязан
        // остаться кликабельным.
        newPanel.ignoresMouseEvents = true

        // orderFrontRegardless(), а не makeKeyAndOrderFront(_:) — панель не
        // должна становиться key-окном и красть фокус у того, с чем работает
        // пользователь (см. canBecomeKey = false в NotchPanel).
        newPanel.orderFrontRegardless()

        panel = newPanel
        currentScreen = targetScreen
    }

    /// Пересчитывает целевой экран и переезжает, если он изменился.
    /// Вызывается при смене параметров экранов (подключение/отключение
    /// монитора, смена разрешения) и может вызываться при перемещении курсора.
    func reposition() {
        let targetScreen = NSScreen.screenWithMouse ?? NSScreen.main

        guard let targetScreen else { return }

        if targetScreen !== currentScreen || panel == nil {
            show(on: targetScreen)
        } else {
            // Тот же экран, но его геометрия могла измениться (например,
            // сменилось разрешение) — просто обновляем фрейм панели.
            panel?.setFrame(targetScreen.capsulePanelFrame, display: true)
        }
    }

    func moveToScreen(_ screen: NSScreen) {
        show(on: screen)
    }
}
