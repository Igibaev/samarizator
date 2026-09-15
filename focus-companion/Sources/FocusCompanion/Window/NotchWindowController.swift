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

    /// Машина состояний персонажа — Фаза 3. Живёт здесь, а не внутри
    /// `NotchRootView`, специально: `show()` пересоздаёт панель и вью при
    /// смене экрана, а текущая эмоция должна это пережить (иначе она
    /// сбрасывалась бы в `.idle` при каждом подключении/отключении
    /// монитора). `AppDelegate` обращается сюда напрямую для debug-меню.
    let stateMachine = CompanionStateMachine()

    /// Определитель наведения — тоже персистентен и по той же причине, но
    /// вдобавок он должен пережить пересоздание панели, чтобы его
    /// единственный `onExpansionChange` не пришлось перевешивать на новую
    /// панель при каждом переезде: колбэк ниже всегда обращается к
    /// `self.panel`, то есть к АКТУАЛЬНОЙ панели на момент срабатывания.
    private let hoverDetector = HoverDetector()

    /// Действительно ли панель существует и показана — для отладочного вывода.
    var isPanelVisible: Bool { panel?.isVisible ?? false }

    /// Пропускает ли панель клики насквозь — для отладочного вывода.
    var panelIgnoresMouseEvents: Bool? { panel?.ignoresMouseEvents }

    init() {
        hoverDetector.onExpansionChange = { [weak self] expanded in
            // Раскрыто → панель обязана перехватывать мышь (чекбоксы задач
            // появятся в Фазе 4); свёрнуто → снова пропускать клики насквозь,
            // чтобы меню-бар под капсулой оставался кликабельным (критерий
            // приёмки Фазы 1).
            self?.panel?.ignoresMouseEvents = !expanded
        }
    }

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
        newPanel.contentView = NSHostingView(
            rootView: NotchRootView(stateMachine: stateMachine, hoverDetector: hoverDetector)
        )

        // Повторно после contentView: NSHostingView добавляет свои tracking-области,
        // и порядок важнее любых настроек содержимого. Значение — по текущему
        // состоянию раскрытия, а не жёстко true: `hoverDetector` персистентен и
        // мог быть раскрыт уже до переезда панели на другой экран.
        newPanel.ignoresMouseEvents = !hoverDetector.isExpanded

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
