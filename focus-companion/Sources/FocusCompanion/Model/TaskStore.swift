import Foundation
import SwiftData
import Observation

/// Хранилище задач — Фаза 4а: три активных слота, чекбокс выполнения,
/// лёгкая ретенция выполненных задач.
///
/// Работает через голый `ModelContext`, а не через `@Query`.
///
/// Так надёжнее: `@Query` рассчитан на то, что нужный `ModelContext` придёт
/// через `@Environment` из `.modelContainer(_:)`, и формально это работает
/// на любой `View`, а не только внутри `App`/`Scene` — но здесь SwiftUI-вью
/// (`NotchRootView`/`ExpandedPanelView`) живёт внутри ВРУЧНУЮ созданного
/// `NSHostingView` без App-лайфцикла вообще (см. `NotchWindowController`),
/// а такое сочетание никем и никогда не проверялось на живой машине. Ручной
/// `fetch` после каждой мутации даёт тот же результат предсказуемее и без
/// зависимости от того, как именно SwiftData прокидывает environment в этой
/// нестандартной иерархии — см. заметку о неуверенности в отчёте фазы.
@MainActor
@Observable
final class TaskStore {

    /// Правило трёх слотов — прямое требование ТЗ.
    static let maxActiveSlots = 3

    /// Сколько последних выполненных задач держать (и в UI, и в базе), чтобы
    /// список не копился бесконечно и не превращал раскрытую панель в
    /// свалку. Это отдельное, более простое правило ретенции — не механика
    /// «сжигания» (та — Фаза 4б, у активных задач и с другим смыслом:
    /// осознанное отпускание, а не автоматическая уборка).
    private static let maxCompletedKept = 8

    private let context: ModelContext

    private(set) var activeTasks: [CompanionTask] = []
    private(set) var completedTasks: [CompanionTask] = []

    init(container: ModelContainer) {
        self.context = ModelContext(container)
        refresh()
    }

    /// Добавляет задачу. `nil`, если все три слота заняты — вызывающая
    /// сторона (`TaskPanelController`) отвечает за заботливое сообщение об
    /// этом в UI, здесь только правило.
    @discardableResult
    func add(text: String) -> CompanionTask? {
        guard activeTasks.count < Self.maxActiveSlots else { return nil }

        let task = CompanionTask(text: text)
        context.insert(task)
        save()
        refresh()
        return task
    }

    /// Отмечает задачу выполненной и подчищает старые выполненные сверх
    /// `maxCompletedKept`.
    func complete(_ task: CompanionTask) {
        task.isDone = true
        task.completedAt = Date()
        save()
        trimCompletedIfNeeded()
        refresh()
    }

    private func trimCompletedIfNeeded() {
        let descriptor = FetchDescriptor<CompanionTask>(
            predicate: #Predicate { $0.isDone },
            sortBy: [SortDescriptor(\.createdAt, order: .reverse)]
        )
        guard let done = try? context.fetch(descriptor), done.count > Self.maxCompletedKept else { return }
        for stale in done.dropFirst(Self.maxCompletedKept) {
            context.delete(stale)
        }
        save()
    }

    private func refresh() {
        // Сортировка по `createdAt` (не по `completedAt`) даже для
        // выполненных — `completedAt` опциональный, и лишний раз полагаться
        // на поведение `SortDescriptor` с `Date?` не хочется без компилятора
        // под рукой (см. отчёт).
        let activeDescriptor = FetchDescriptor<CompanionTask>(
            predicate: #Predicate { !$0.isDone },
            sortBy: [SortDescriptor(\.createdAt)]
        )
        let completedDescriptor = FetchDescriptor<CompanionTask>(
            predicate: #Predicate { $0.isDone },
            sortBy: [SortDescriptor(\.createdAt, order: .reverse)]
        )
        activeTasks = (try? context.fetch(activeDescriptor)) ?? []
        completedTasks = (try? context.fetch(completedDescriptor)) ?? []
    }

    private func save() {
        try? context.save()
    }
}
