import Foundation
import Observation

/// Хранилище задач — Фаза 4а: три активных слота, чекбокс выполнения,
/// лёгкая ретенция выполненных.
///
/// Держит все задачи одним массивом в памяти и целиком пишет его в JSON при
/// каждом изменении. Для трёх активных задач это заведомо дешевле, чем любая
/// база, и не требует ни схемы, ни миграций.
@MainActor
@Observable
final class TaskStore {

    /// Правило трёх слотов — прямое требование ТЗ.
    static let maxActiveSlots = 3

    /// Сколько последних выполненных задач держать, чтобы список не копился
    /// бесконечно. Это простая уборка, а НЕ механика «сжигания» (та — Фаза
    /// 4б, у активных задач и с другим смыслом: осознанное отпускание).
    private static let maxCompletedKept = 8

    private var tasks: [CompanionTask]

    var activeTasks: [CompanionTask] {
        tasks.filter { !$0.isDone }.sorted { $0.createdAt < $1.createdAt }
    }

    var completedTasks: [CompanionTask] {
        tasks.filter(\.isDone).sorted { $0.createdAt > $1.createdAt }
    }

    init(tasks: [CompanionTask] = TaskPersistence.load()) {
        self.tasks = tasks
    }

    /// Добавляет задачу. `nil`, если все три слота заняты — вызывающая
    /// сторона (`TaskPanelController`) отвечает за заботливое сообщение об
    /// этом в UI, здесь только правило.
    @discardableResult
    func add(text: String) -> CompanionTask? {
        guard activeTasks.count < Self.maxActiveSlots else { return nil }

        let task = CompanionTask(text: text)
        tasks.append(task)
        persist()
        return task
    }

    func complete(_ task: CompanionTask) {
        guard let index = tasks.firstIndex(where: { $0.id == task.id }) else { return }
        tasks[index].isDone = true
        tasks[index].completedAt = Date()
        trimCompleted()
        persist()
    }

    private func trimCompleted() {
        let stale = completedTasks.dropFirst(Self.maxCompletedKept).map(\.id)
        guard !stale.isEmpty else { return }
        tasks.removeAll { stale.contains($0.id) }
    }

    private func persist() {
        TaskPersistence.save(tasks)
    }
}
