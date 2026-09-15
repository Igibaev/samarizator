import Foundation
import Observation

/// Связывает данные (`TaskStore`) с реакциями персонажа
/// (`CompanionStateMachine`) — Фаза 4а.
///
/// Отдельный класс, а не логика прямо в `ExpandedPanelView`: реакция на
/// выполнение задачи держится несколько секунд и должна САМА вернуться в
/// `.idle` по таймеру — а таймер обязан быть отменяемым и не зависеть от
/// жизненного цикла SwiftUI-вью, которая пересоздаётся при переезде панели
/// между экранами (тот же принцип, что у `EyesViewModel`/`TaskBag`, см.
/// HANDOFF.md).
///
/// Живёт в `NotchWindowController` рядом со `stateMachine` — персистентно,
/// не в `@State` вью.
@MainActor
@Observable
final class TaskPanelController {

    let store: TaskStore
    private let stateMachine: CompanionStateMachine
    private let taskBag = TaskBag()

    /// Заботливое сообщение о занятых трёх слотах — НЕ ошибка и не выговор
    /// (см. HANDOFF.md, раздел «Тон персонажа»). Формулировка сознательно не
    /// обещает кнопку отпускания задачи — этой механики ещё нет (Фаза 4б).
    private(set) var slotsFullMessage: String?

    enum AddOutcome {
        case added
        case slotsFull
        case empty
    }

    init(store: TaskStore, stateMachine: CompanionStateMachine) {
        self.store = store
        self.stateMachine = stateMachine
    }

    /// Добавляет задачу и включает короткую заметную реакцию персонажа.
    @discardableResult
    func addTask(text: String) -> AddOutcome {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .empty }

        guard store.add(text: trimmed) != nil else {
            slotsFullMessage = "Трёх задач хватит — давай сначала разберёмся с этими."
            return .slotsFull
        }

        slotsFullMessage = nil
        briefReaction()
        return .added
    }

    /// Отмечает задачу выполненной и включает позитивную реакцию —
    /// `.celebrating` держится заметно дольше и ярче, чем реакция на
    /// добавление: позитив должен быть громче негатива, прямое требование
    /// автора (см. HANDOFF.md).
    func complete(_ task: CompanionTask) {
        store.complete(task)
        slotsFullMessage = nil // слот освободился — сообщение больше не актуально
        stateMachine.setState(.celebrating)
        scheduleReturnToIdle(after: CharacterConfig.taskCompletedCelebrationDuration)
    }

    /// Короткая, заметная, но не праздничная реакция на добавление задачи.
    ///
    /// Переиспользует состояние `.listening` ("шире раскрытые глаза") как
    /// самое близкое по смыслу "заметил, внимание" из уже готовой таблицы
    /// `StateAppearance` — специально не заводили новое состояние ради
    /// одной короткой реакции. Если Фаза 5 (интеграция с samarizator)
    /// закрепит за `.listening` буквальный смысл "идёт запись", этот выбор
    /// стоит пересмотреть — см. отчёт.
    private func briefReaction() {
        stateMachine.setState(.listening)
        scheduleReturnToIdle(after: CharacterConfig.taskAddedReactionDuration)
    }

    private func scheduleReturnToIdle(after seconds: Double) {
        taskBag.replace(.stateReset, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.stateMachine.setState(.idle)
        })
    }
}
