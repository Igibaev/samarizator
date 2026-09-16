import Foundation

/// Sendable-контейнер отменяемых задач.
///
/// Нужен потому, что `deinit` у `@MainActor`-класса выполняется вне актора и
/// не может трогать изолированные свойства — а отменить фоновые циклы при
/// уничтожении владельца надо.
final class TaskBag: @unchecked Sendable {
    enum Key {
        case blink
        case thinkingCycle
        case gaze
        case hoverPoll
        case stateReset
        case deadlineTicker
        case reminderTicker
        case edgeResistance
        case gestureHint
        case reminderCaption
        case disposal
        case undoWindow
        case clipboardPoll
        case livePoll
        case copyConfirmation
    }

    private let lock = NSLock()
    private var tasks: [Key: Task<Void, Never>] = [:]

    func replace(_ key: Key, with task: Task<Void, Never>) {
        lock.lock()
        defer { lock.unlock() }
        tasks[key]?.cancel()
        tasks[key] = task
    }

    func cancel(_ key: Key) {
        lock.lock()
        defer { lock.unlock() }
        tasks[key]?.cancel()
        tasks[key] = nil
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
