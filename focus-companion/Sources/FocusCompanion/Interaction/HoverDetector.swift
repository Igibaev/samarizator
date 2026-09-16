import AppKit
import SwiftUI
import Observation

/// Отслеживание курсора над компаньоном (design.md §7.2).
///
/// Опрос `NSEvent.mouseLocation` по таймеру, а не `NSTrackingArea` и не
/// глобальный монитор: глобальный монитор слепнет над собственным окном, как
/// только панель перестаёт пропускать мышь, а `NSTrackingArea` не видит
/// курсор, пока окно клики пропускает. Опрос работает одинаково в обоих
/// режимах — это уже проверено на живой машине в Фазе 3.
@MainActor
@Observable
final class HoverDetector {

    /// Курсор внутри области глаз (крыла).
    private(set) var isOverWing = false
    /// Курсор внутри объединения корпуса, панели и коридора между ними.
    private(set) var isOverUnion = false
    /// Текущая геометрия — её же читает и вью, чтобы раскладка была одна.
    private(set) var geometry: CompanionGeometry?
    private(set) var mouseLocation: CGPoint = .zero

    /// Требуется ли панели принимать клики прямо сейчас.
    private(set) var wantsMouseEvents = false

    /// Ввод, drag или открытое контекстное меню удерживают панель открытой.
    var holdsOpen = false

    /// Пока идёт перетаскивание корпуса, панель обязана принимать события
    /// мыши, даже если курсор убежал за границу капсулы: иначе жест
    /// оборвётся на первом же резком движении.
    var isDraggingBody = false

    var onOpenFocus: (() -> Void)?
    var onCloseFocus: (() -> Void)?
    var onMouseLocation: ((CGPoint) -> Void)?
    var onWantsMouseEventsChange: ((Bool) -> Void)?
    var onWingHoverChange: ((Bool) -> Void)?

    /// Поставщик актуального состояния: сколько задач и раскрыт ли focus.
    var currentStateProvider: (() -> CompanionRuntimeState)?

    private var openPendingSince: Date?
    private var closePendingSince: Date?
    private let taskBag = TaskBag()
    /// 20 Гц: достаточно для ощущения мгновенности и не греет процессор.
    private let pollInterval: Double = 1.0 / 20.0

    init() {
        startPolling()
    }

    deinit {
        taskBag.cancelAll()
    }

    private func startPolling() {
        taskBag.replace(.hoverPoll, with: Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.tick(now: Date())
                try? await Task.sleep(nanoseconds: UInt64(self.pollInterval * 1_000_000_000))
            }
        })
    }

    /// Один шаг опроса. Вынесен отдельно от цикла, чтобы логику задержек
    /// можно было прогнать вручную, не дожидаясь реального таймера.
    func tick(now: Date) {
        guard let screen = NSScreen.screenWithMouse ?? NSScreen.main else { return }
        let state = currentStateProvider?() ?? CompanionRuntimeState()
        let geometry = screen.companionGeometry(state)
        self.geometry = geometry

        let location = NSEvent.mouseLocation
        mouseLocation = location
        onMouseLocation?(location)

        let overWing = geometry.wingRect.contains(location)
        if overWing != isOverWing {
            isOverWing = overWing
            onWingHoverChange?(overWing)
        }

        let overUnion = geometry.hoverUnionRect.contains(location)
        isOverUnion = overUnion

        // Клики панель должна принимать, когда курсор над реально
        // нарисованными поверхностями: крылом, полкой или раскрытым focus.
        // Коридор в эту проверку НЕ входит — прозрачные участки не имеют
        // права перехватывать нажатия у других приложений.
        var interactive = geometry.wingRect.contains(location)
        if state.isFocusOpen {
            interactive = interactive || geometry.focusRect.contains(location)
        } else {
            if let shelf = geometry.shelfRect {
                interactive = interactive || shelf.contains(location)
            }
            // У сообщения есть кнопка «Вернуть» — оно обязано принимать клики.
            if let notice = geometry.compactNoticeRect {
                interactive = interactive || notice.contains(location)
            }
        }
        if isDraggingBody { interactive = true }
        if interactive != wantsMouseEvents {
            wantsMouseEvents = interactive
            onWantsMouseEventsChange?(interactive)
        }

        evaluateOpen(overWing: overWing, now: now)
        evaluateClose(overUnion: overUnion, isFocusOpen: state.isFocusOpen, now: now)
    }

    /// Курсор вошёл в область глаз → через 120 мс раскрыть focus.
    /// Ушёл раньше — открытие отменяется.
    private func evaluateOpen(overWing: Bool, now: Date) {
        guard overWing else {
            openPendingSince = nil
            return
        }
        guard let since = openPendingSince else {
            openPendingSince = now
            return
        }
        guard now.timeIntervalSince(since) >= DesignTokens.Motion.hoverOpenDelay else { return }
        openPendingSince = nil
        onOpenFocus?()
    }

    /// Курсор покинул ВСЁ объединение корпуса и панели → таймер 450 мс.
    /// Возврат до истечения таймера отменяет закрытие.
    private func evaluateClose(overUnion: Bool, isFocusOpen: Bool, now: Date) {
        guard isFocusOpen, !holdsOpen else {
            closePendingSince = nil
            return
        }
        guard !overUnion else {
            closePendingSince = nil
            return
        }
        guard let since = closePendingSince else {
            closePendingSince = now
            return
        }
        guard now.timeIntervalSince(since) >= DesignTokens.Motion.hoverCloseDelay else { return }
        closePendingSince = nil
        onCloseFocus?()
    }
}
