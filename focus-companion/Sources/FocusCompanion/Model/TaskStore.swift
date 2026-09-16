import Foundation
import Observation

/// Хранилище трёх слотов фокуса и истории.
///
/// Жёсткое правило: активных задач не больше трёх, скрытого четвёртого слота
/// нет (design.md §2). История — отдельный архив, а не четвёртая задача.
@MainActor
@Observable
final class TaskStore {

    static let maxActiveSlots = 3
    /// Сколько завершённых/сгоревших записей держим в истории.
    private static let maxHistoryKept = 100

    private(set) var tasks: [CompanionTask]

    /// Активные задачи в закреплённом пользователем порядке.
    var activeTasks: [CompanionTask] {
        tasks.filter { $0.status == .active }.sorted { $0.order < $1.order }
    }

    /// История: выполненные, сгоревшие и убранные — новые сверху.
    var history: [CompanionTask] {
        tasks
            .filter { $0.status != .active }
            .sorted { lhs, rhs in
                (lhs.completedAt ?? lhs.expiredAt ?? lhs.expiresAt)
                    > (rhs.completedAt ?? rhs.expiredAt ?? rhs.expiresAt)
            }
    }

    var freeSlots: Int { max(0, Self.maxActiveSlots - activeTasks.count) }
    var hasFreeSlot: Bool { freeSlots > 0 }

    init(tasks: [CompanionTask] = TaskPersistence.load()) {
        self.tasks = tasks
    }

    // MARK: - Добавление

    /// Добавляет задачу в свободный слот. `nil` — слотов нет.
    /// Молча вытеснить существующую задачу нельзя (design.md §9.3).
    @discardableResult
    func add(title: String, note: String = "", duration: TimeInterval, now: Date = Date()) -> CompanionTask? {
        guard hasFreeSlot else { return nil }
        let trimmed = String(title.prefix(CharacterConfig.maxTaskTitleLength))
        let task = CompanionTask(
            title: trimmed,
            note: note,
            startedAt: now,
            expiresAt: now.addingTimeInterval(duration),
            status: .active,
            order: nextOrder()
        )
        tasks.append(task)
        persist()
        return task
    }

    private func nextOrder() -> Int {
        (tasks.filter { $0.status == .active }.map(\.order).max() ?? -1) + 1
    }

    // MARK: - Жизненный цикл

    func complete(_ id: UUID, now: Date = Date()) {
        mutate(id) { task in
            task.status = .completed
            task.completedAt = now
        }
    }

    /// Пользователь убрал задачу раньше срока — спокойный уход без вины.
    func archive(_ id: UUID, now: Date = Date()) {
        mutate(id) { task in
            task.status = .archived
            task.completedAt = now
        }
    }

    /// «Перенести на N минут»: обновляет НАЧАЛО интервала и новый срок,
    /// чтобы линия остатка снова считалась от полной длительности.
    func reschedule(_ id: UUID, by duration: TimeInterval, now: Date = Date()) {
        mutate(id) { task in
            task.startedAt = now
            task.expiresAt = now.addingTimeInterval(duration)
            task.lastRemindedAt = nil
        }
    }

    func setExpiresAt(_ id: UUID, to date: Date, now: Date = Date()) {
        mutate(id) { task in
            task.startedAt = now
            task.expiresAt = date
            task.lastRemindedAt = nil
        }
    }

    func markReminded(_ id: UUID, at date: Date) {
        mutate(id) { $0.lastRemindedAt = date }
    }

    /// Сначала СОХРАНИТЬ статус expired, и только потом играть эффект:
    /// сбой анимации не должен потерять задачу (design.md §11.2).
    /// Возвращает задачи, у которых только что вышел срок.
    @discardableResult
    func expireOverdue(now: Date = Date()) -> [CompanionTask] {
        let overdue = tasks.filter { $0.isExpired(now: now) }
        guard !overdue.isEmpty else { return [] }
        for task in overdue {
            mutate(task.id, persistNow: false) { item in
                item.status = .expired
                item.expiredAt = now
            }
        }
        trimHistory()
        persist()
        return overdue.map { task in
            var copy = task
            copy.status = .expired
            copy.expiredAt = now
            return copy
        }
    }

    // MARK: - Восстановление из истории

    enum RestoreOutcome: Equatable {
        case restored(CompanionTask)
        /// Слоты заняты: пользователь должен выбрать, кого заменить.
        case needsSlot
        case notFound
    }

    /// Восстановление требует НОВОГО срока и свободного слота (design.md §11.2).
    func restore(_ id: UUID, duration: TimeInterval, now: Date = Date()) -> RestoreOutcome {
        guard tasks.contains(where: { $0.id == id && $0.status != .active }) else { return .notFound }
        guard hasFreeSlot else { return .needsSlot }
        // Порядок считаем ДО мутации: `nextOrder()` читает `tasks`, а внутри
        // `mutate` этот же массив уже занят `inout`-доступом.
        let order = nextOrder()
        mutate(id) { task in
            task.status = .active
            task.startedAt = now
            task.expiresAt = now.addingTimeInterval(duration)
            task.completedAt = nil
            task.expiredAt = nil
            task.lastRemindedAt = nil
            task.order = order
        }
        guard let restored = tasks.first(where: { $0.id == id }) else { return .notFound }
        return .restored(restored)
    }

    /// Явная замена: указанная активная задача уходит в архив, а из истории
    /// возвращается выбранная. Четвёртой задачи не появляется.
    func restore(_ id: UUID, replacing victimID: UUID, duration: TimeInterval, now: Date = Date()) -> RestoreOutcome {
        archive(victimID, now: now)
        return restore(id, duration: duration, now: now)
    }

    func removeFromHistory(_ id: UUID) {
        tasks.removeAll { $0.id == id && $0.status != .active }
        persist()
    }

    // MARK: - Порядок

    func move(_ id: UUID, to index: Int) {
        var ordered = activeTasks
        guard let from = ordered.firstIndex(where: { $0.id == id }) else { return }
        let target = min(max(0, index), ordered.count - 1)
        let item = ordered.remove(at: from)
        ordered.insert(item, at: target)
        for (position, task) in ordered.enumerated() {
            mutate(task.id, persistNow: false) { $0.order = position }
        }
        persist()
    }

    // MARK: - Служебное

    private func mutate(_ id: UUID, persistNow: Bool = true, _ body: (inout CompanionTask) -> Void) {
        guard let index = tasks.firstIndex(where: { $0.id == id }) else { return }
        body(&tasks[index])
        if persistNow {
            trimHistory()
            persist()
        }
    }

    private func trimHistory() {
        let stale = history.dropFirst(Self.maxHistoryKept).map(\.id)
        guard !stale.isEmpty else { return }
        tasks.removeAll { stale.contains($0.id) }
    }

    private func persist() {
        TaskPersistence.save(tasks)
    }

    /// Для фикстур и debug-меню: заменить содержимое целиком, не трогая диск.
    func replaceForFixture(_ newTasks: [CompanionTask]) {
        tasks = newTasks
    }
}
