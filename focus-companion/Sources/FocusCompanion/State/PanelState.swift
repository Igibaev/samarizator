import SwiftUI
import Observation

/// Страницы в неизменном порядке Фокус → Буфер → Записи (design.md §8).
enum CompanionPage: Int, CaseIterable, Identifiable {
    case focus = 0
    case clipboard = 1
    case recordings = 2

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .focus: return "Фокус"
        case .clipboard: return "Буфер"
        case .recordings: return "Записи"
        }
    }

    /// Живёт ли страница в правой панели.
    var isDrawerPage: Bool { self != .focus }
}

/// Состояние оболочки: collapsed / focusHover / focusPinned / clipboard / recordings
/// (design.md §14, область `panel`).
enum PanelPresentation: String, Equatable {
    case collapsed
    case focusHover
    case focusPinned
    case clipboard
    case recordings

    var isFocusOpen: Bool { self == .focusHover || self == .focusPinned }
    var isDrawerOpen: Bool { self == .clipboard || self == .recordings }

    var page: CompanionPage {
        switch self {
        case .clipboard: return .clipboard
        case .recordings: return .recordings
        default: return .focus
        }
    }
}

/// Навигация компаньона: одна оболочка, одна позиция, один механизм.
@MainActor
@Observable
final class NavigationController {

    private(set) var presentation: PanelPresentation = .collapsed

    /// Закрепление правой панели. Закреплённая остаётся при клике в другое
    /// приложение; незакреплённая закрывается по внешнему клику, сохраняя
    /// страницу, выделение и позицию чтения.
    var drawerPinned: Bool = CompanionSettings.drawerPinned {
        didSet { CompanionSettings.drawerPinned = drawerPinned }
    }

    /// Сколько раз показывали подсказку про жесты (первые два открытия).
    private(set) var gestureHintVisible = false

    /// Сопротивление на границе списка страниц: до 6 pt, без циклического перехода.
    private(set) var edgeResistanceOffset: CGFloat = 0

    var onPresentationChange: ((PanelPresentation) -> Void)?

    var isDrawerOpen: Bool { presentation.isDrawerOpen }
    var isFocusOpen: Bool { presentation.isFocusOpen }
    var page: CompanionPage { presentation.page }

    private let taskBag = TaskBag()

    deinit { taskBag.cancelAll() }

    // MARK: - Фокус

    func openFocusHover() {
        guard presentation == .collapsed else { return }
        set(.focusHover)
    }

    func closeFocusHover() {
        guard presentation == .focusHover else { return }
        set(.collapsed)
    }

    /// Клик по глазам: закрывает правую панель и открывает закреплённый focus.
    func toggleFocusPinned() {
        switch presentation {
        case .focusPinned:
            set(.collapsed)
        default:
            set(.focusPinned)
        }
    }

    func collapse() {
        set(.collapsed)
    }

    // MARK: - Страницы

    func show(page: CompanionPage) {
        switch page {
        case .focus: set(.focusPinned)
        case .clipboard: set(.clipboard)
        case .recordings: set(.recordings)
        }
    }

    func closeDrawer() {
        guard presentation.isDrawerOpen else { return }
        set(.collapsed)
    }

    /// Один физический жест — максимум один переход.
    /// Свайп влево: Фокус → Буфер → Записи. Вправо — назад, из Буфера в
    /// закреплённый Фокус.
    func advance(_ direction: SwipeDirection) {
        let current = page
        let nextIndex = current.rawValue + (direction == .left ? 1 : -1)
        guard let next = CompanionPage(rawValue: nextIndex) else {
            applyEdgeResistance()
            return
        }
        show(page: next)
    }

    private func applyEdgeResistance() {
        withAnimation(.easeOut(duration: 0.12)) { edgeResistanceOffset = 6 }
        taskBag.replace(.edgeResistance, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: 120_000_000)
            guard !Task.isCancelled, let self else { return }
            withAnimation(.easeOut(duration: 0.18)) { self.edgeResistanceOffset = 0 }
        })
    }

    // MARK: - Подсказка про жесты

    func noteDrawerOpenedForHint() {
        guard CompanionSettings.gestureHintsShown < 2 else { return }
        CompanionSettings.gestureHintsShown += 1
        gestureHintVisible = true
        taskBag.replace(.gestureHint, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            guard !Task.isCancelled, let self else { return }
            self.gestureHintVisible = false
        })
    }

    func dismissGestureHint() {
        gestureHintVisible = false
        CompanionSettings.gestureHintsShown = 2
    }

    private func set(_ new: PanelPresentation) {
        guard new != presentation else { return }
        let wasDrawerOpen = presentation.isDrawerOpen
        presentation = new
        if new.isDrawerOpen && !wasDrawerOpen {
            noteDrawerOpenedForHint()
        }
        onPresentationChange?(new)
    }
}
