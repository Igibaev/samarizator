import Foundation

/// Держатель фоновых задач, НЕ привязанный ни к какому актору.
///
/// Зачем нужен: `deinit` у `@MainActor`-класса выполняется в nonisolated-
/// контексте и потому не может обращаться к изолированным свойствам — а
/// отменять таймеры при освобождении объекта надо обязательно, иначе циклы
/// моргания и дыхания переживут свою вью (например, при переезде панели на
/// другой экран, где `NSHostingView` пересоздаётся).
///
/// Выход: хранить задачи в `let`-свойстве Sendable-типа. Неизменяемые
/// `let` Sendable-свойства доступны из любого контекста, включая `deinit`,
/// а внутренняя синхронизация здесь на замке.
final class TaskBag: @unchecked Sendable {

    enum Key {
        case blink
        case saccade
        case breath
        /// Опрос `NSEvent.mouseLocation` в `HoverDetector` (Фаза 3) — тот же
        /// generic-контейнер, что и у циклов персонажа, чтобы не заводить
        /// под таймеры новую инфраструктуру отмены.
        case hoverPoll
        /// Отложенный возврат в фоновое состояние (`.idle`/`.burning`) после
        /// временной реакции на событие задачи (добавлена/выполнена) —
        /// `TaskPanelController` (Фаза 4а, фоновое состояние — Фаза 4б).
        case stateReset
        /// Общий таймер горения фитилей, тик раз в секунду — ОДИН на все
        /// горящие задачи разом, не по таймеру на задачу (Фаза 4б,
        /// `TaskPanelController`).
        case burnTicker
    }

    private let lock = NSLock()
    private var tasks: [Key: Task<Void, Never>] = [:]

    /// Кладёт задачу под ключ, отменяя предыдущую с тем же ключом.
    func replace(_ key: Key, with task: Task<Void, Never>) {
        lock.lock()
        defer { lock.unlock() }
        tasks[key]?.cancel()
        tasks[key] = task
    }

    func cancelAll() {
        lock.lock()
        defer { lock.unlock() }
        for task in tasks.values {
            task.cancel()
        }
        tasks.removeAll()
    }
}
