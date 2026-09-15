import AppKit

/// Отслеживает глобальное положение курсора мыши для слежения глаз и саккад.
///
/// Используем `NSEvent.addGlobalMonitorForEvents`, а не таймер на 60 Гц:
/// монитор срабатывает только при реальном движении мыши, поэтому не жжёт
/// батарею круглосуточно, пока пользователь не двигает мышью (например,
/// печатает или читает). Прав Accessibility для событий мыши (в отличие от
/// клавиатуры) не требуется — глобальный монитор на mouseMoved доступен без
/// дополнительных разрешений.
///
/// Важно: глобальный монитор НЕ получает события, когда курсор находится над
/// окном самого приложения. Пока панель свёрнута, у неё `ignoresMouseEvents
/// = true`, и монитор видит движение над капсулой как обычно. Но в Фазе 3
/// панель при раскрытии переходит в `ignoresMouseEvents = false` — и в этот
/// момент монитор слепнет ровно там, где нужнее всего (курсор над самим
/// персонажем). Поэтому слежение глаз во время раскрытия питается НЕ этим
/// трекером, а опросом `NSEvent.mouseLocation`, который уже и так крутит
/// `HoverDetector` для определения наведения (см. `HoverDetector` и
/// `EyesViewModel.updateGaze`) — она передаёт координаты напрямую, без
/// участия `MouseTracker`.
@MainActor
final class MouseTracker {

    /// Последнее зафиксированное положение курсора в глобальных экранных
    /// координатах (`NSEvent.mouseLocation`: начало координат — левый нижний
    /// угол главного экрана, Y растёт вверх).
    private(set) var location: CGPoint = NSEvent.mouseLocation

    /// Момент последнего зафиксированного движения — по нему считается,
    /// сколько курсор простоял неподвижно (триггер саккад).
    private(set) var lastMovementDate: Date = Date()

    /// Вызывается при каждом обновлении позиции, уже на главном акторе.
    var onMove: ((CGPoint) -> Void)?

    /// Токен монитора лежит в отдельной Sendable-коробке по той же причине,
    /// что и таймеры в `TaskBag`: `deinit` у @MainActor-класса выполняется вне
    /// актора и не может читать изолированные свойства, а снимать монитор при
    /// освобождении обязательно — иначе он переживёт объект.
    private final class MonitorBox: @unchecked Sendable {
        var monitor: Any?
    }

    private let monitorBox = MonitorBox()

    init() {
        monitorBox.monitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.mouseMoved, .leftMouseDragged, .rightMouseDragged]
        ) { [weak self] _ in
            // Документация Apple не гарантирует явно, что обработчик глобального
            // монитора вызывается на главном потоке, поэтому перепрыгиваем на
            // MainActor сами, а не полагаемся на это.
            Task { @MainActor [weak self] in
                guard let self else { return }
                self.location = NSEvent.mouseLocation
                self.lastMovementDate = Date()
                self.onMove?(self.location)
            }
        }
    }

    deinit {
        if let monitor = monitorBox.monitor {
            NSEvent.removeMonitor(monitor)
        }
    }
}
