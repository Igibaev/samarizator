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
        newPanel.contentView = Self.makeContentView(size: frame.size)

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
            let newFrame = targetScreen.capsulePanelFrame
            panel?.setFrame(newFrame, display: true)
            // Маска стекла нарисована под конкретный размер — при смене
            // разрешения её надо перерисовать, иначе форма разъедется.
            if let effectView = panel?.contentView as? NSVisualEffectView {
                effectView.maskImage = NotchShapeMask.image(size: newFrame.size)
            }
        }
    }

    func moveToScreen(_ screen: NSScreen) {
        show(on: screen)
    }

    /// Собирает содержимое панели: матовое стекло снизу, SwiftUI-персонаж сверху.
    ///
    /// Стекло — это `NSVisualEffectView` в роли contentView, а не подложка
    /// внутри SwiftUI. Причина: `blendingMode = .behindWindow` размывает то,
    /// что позади окна, и работает только пока вью рисуется самим оконным
    /// сервером. Любая обрезка средствами SwiftUI (`clipShape`) уводит его в
    /// отдельный слой, размытие пропадает и остаётся плоская заливка. Поэтому
    /// форму стеклу задаёт его собственный `maskImage`.
    private static func makeContentView(size: NSSize) -> NSView {
        let effectView = NSVisualEffectView(frame: NSRect(origin: .zero, size: size))
        effectView.material = AppearanceConfig.capsuleMaterial
        effectView.blendingMode = .behindWindow
        // .active явно: панель никогда не бывает key-окном (canBecomeKey = false),
        // и в автоматическом режиме стекло навсегда осталось бы "неактивным".
        effectView.state = .active
        effectView.isEmphasized = false
        effectView.maskImage = NotchShapeMask.image(size: size)
        effectView.autoresizingMask = [.width, .height]

        let hostingView = NSHostingView(rootView: NotchRootView())
        hostingView.frame = effectView.bounds
        hostingView.autoresizingMask = [.width, .height]
        effectView.addSubview(hostingView)

        return effectView
    }
}
