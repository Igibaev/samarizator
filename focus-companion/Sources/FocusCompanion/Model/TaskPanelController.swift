import Foundation
import Observation

/// Связывает данные (`TaskStore`) с реакциями персонажа
/// (`CompanionStateMachine`) — Фаза 4а, механика фитиля — Фаза 4б.
///
/// Отдельный класс, а не логика прямо в `ExpandedPanelView`: реакция на
/// выполнение задачи держится несколько секунд и должна САМА вернуться в
/// фоновое состояние по таймеру — а таймер обязан быть отменяемым и не
/// зависеть от жизненного цикла SwiftUI-вью, которая пересоздаётся при
/// переезде панели между экранами (тот же принцип, что у
/// `EyesViewModel`/`TaskBag`, см. HANDOFF.md). Фаза 4б добавляет второй
/// такой же отменяемый таймер — общий тик горения всех фитилей разом.
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
    /// (см. HANDOFF.md, раздел «Тон персонажа»).
    private(set) var slotsFullMessage: String?

    /// Текущий момент, обновляется общим таймером горения раз в секунду
    /// (`startBurnTicker`). Единственная причина, по которой
    /// `ExpandedPanelView` вообще перерисовывается каждую секунду, пока
    /// что-то горит: полоска остатка фитиля не хранит готовое число, а
    /// считает долю на лету от `now` и `CompanionTask.fuseDate/fuseStartedAt`
    /// (`remainingFuseFraction`).
    private(set) var now: Date = Date()

    enum AddOutcome {
        case added
        case slotsFull
        case empty
    }

    init(store: TaskStore, stateMachine: CompanionStateMachine) {
        self.store = store
        self.stateMachine = stateMachine
        // На случай, если при запуске что-то ЕЩЁ горит (не догорело, просто
        // приложение открыли заново) — фоновое состояние должно это сразу
        // отражать. Задачи, чей фитиль уже истёк, к этому моменту тихо
        // отпущены внутри `TaskStore.init`, так что здесь никакого всплеска
        // эмоций не будет — только ровный переход в уже идущее горение.
        syncAmbientState()
        startBurnTicker()
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
        scheduleReturnToRestingState(after: CharacterConfig.taskCompletedCelebrationDuration)
    }

    // MARK: - Фитиль (Фаза 4б)

    /// Поджигает фитиль на выбранный пресет длительности. Вызывается только
    /// из явного выбора пункта меню в `ExpandedPanelView` — см. `FusePreset`
    /// и комментарий там про то, почему это не одиночный клик.
    func igniteFuse(_ task: CompanionTask, preset: FusePreset) {
        store.igniteFuse(task, duration: preset.duration(from: Date()))
        syncAmbientState()
    }

    /// Гасит фитиль — пользователь передумал отпускать задачу по дороге.
    /// Тот же жест (нажатие на иконку фитиля), что и поджиг, но без меню:
    /// передумать проще, чем решиться, и это сознательно НЕ требует
    /// подтверждения (необратим только финал, не сам поджиг).
    func extinguishFuse(_ task: CompanionTask) {
        store.extinguishFuse(task)
        syncAmbientState()
    }

    /// Доля оставшегося фитиля (0 — почти догорел, 1 — только подожгли) для
    /// полоски прогресса в строке задачи. `nil`, если фитиль не горит.
    func remainingFuseFraction(for task: CompanionTask) -> Double? {
        guard let fuseDate = task.fuseDate, let startedAt = task.fuseStartedAt else { return nil }
        let total = fuseDate.timeIntervalSince(startedAt)
        guard total > 0 else { return 0 }
        let remaining = fuseDate.timeIntervalSince(now)
        return min(1, max(0, remaining / total))
    }

    /// Общий таймер горения — ОДИН на все горящие задачи разом, не по
    /// таймеру на задачу (прямое требование PHASE-4B-PROMPT.md). Живёт в
    /// `TaskBag`, а не в обычном `Task`-свойстве, по той же причине, что и
    /// циклы `EyesViewModel`: `deinit` `@MainActor`-класса не видит
    /// изолированные свойства (см. HANDOFF.md, «Грабли», п.2).
    private func startBurnTicker() {
        taskBag.replace(.burnTicker, with: Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(CharacterConfig.burnTickInterval * 1_000_000_000))
                guard !Task.isCancelled, let self else { return }
                self.handleBurnTick()
            }
        })
    }

    private func handleBurnTick() {
        now = Date()
        let releasedCount = store.releaseBurnedOut(now: now)
        if releasedCount > 0 {
            // Слот(ы) освободились — заботливое сообщение о трёх занятых
            // больше не актуально, как и при обычном выполнении задачи.
            slotsFullMessage = nil
        }
        // Реакция персонажа на догоревший фитиль — СОЗНАТЕЛЬНО без
        // отдельной анимации-вспышки: не `.celebrating` (это не достижение)
        // и не что-то мрачное. Задача просто тихо исчезает из списка, а
        // персонаж оседает в фоновое состояние — `.idle` ("Спокоен") само
        // по себе и есть та самая спокойная, принимающая реакция, без
        // всякого назидания. См. отчёт — это решение, не забытая реакция.
        syncAmbientState()
    }

    /// Фоновое (не временное, в отличие от `.celebrating`/`.listening`)
    /// состояние персонажа: `.burning`, пока горит хотя бы одна задача,
    /// иначе `.idle`. НЕ перекрывает идущую временную реакцию — та сама
    /// вернётся сюда по своему таймеру (`scheduleReturnToRestingState`),
    /// пересчитав то же правило заново в момент срабатывания, а не то,
    /// каким оно было на момент запуска таймера.
    private func syncAmbientState() {
        guard stateMachine.state != .celebrating, stateMachine.state != .listening else { return }
        stateMachine.setState(store.hasBurningTasks ? .burning : .idle)
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
        scheduleReturnToRestingState(after: CharacterConfig.taskAddedReactionDuration)
    }

    /// Возврат из временной реакции — не жёстко в `.idle`, а в ТЕКУЩЕЕ
    /// фоновое состояние на момент срабатывания таймера (Фаза 4б): если
    /// что-то горит прямо сейчас, персонаж осядет в `.burning`, а не
    /// мигнёт обратно в `.idle` на долю секунды перед тем, как
    /// `syncAmbientState` в другом месте снова переключит его. Именно
    /// поэтому здесь `store.hasBurningTasks`, а не захваченное на момент
    /// запуска значение.
    private func scheduleReturnToRestingState(after seconds: Double) {
        taskBag.replace(.stateReset, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.stateMachine.setState(self.store.hasBurningTasks ? .burning : .idle)
        })
    }
}
