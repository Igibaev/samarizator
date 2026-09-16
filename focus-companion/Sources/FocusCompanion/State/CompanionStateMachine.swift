import SwiftUI
import Observation

/// Машина эмоций с приоритетами из design.md §12.2.
///
/// Правило: более важная эмоция перебивает менее важную, менее важная — нет.
/// Фоновое состояние (idle / listening / thinking) — то, куда возвращается
/// персонаж после временной реакции.
@MainActor
@Observable
final class CompanionStateMachine {

    private(set) var state: CompanionState = .idle
    private(set) var appearance: StateAppearance = StateAppearance.forState(.idle)

    /// Горизонтальное смещение взгляда к конкретной строке задачи, в pt.
    /// Задаётся при напоминании: «взгляд к строке» — это не общий сдвиг пары.
    private(set) var gazeTargetOffset: CGSize = .zero

    /// Фоновое состояние, в которое персонаж оседает сам по себе.
    private(set) var ambientState: CompanionState = .idle

    /// Reduce Motion — переходы без сдвигов и частиц.
    var reduceMotion: Bool = false

    private let taskBag = TaskBag()

    deinit {
        taskBag.cancelAll()
    }

    /// Прямая установка состояния — debug-меню и фикстуры.
    func setState(_ newState: CompanionState) {
        apply(newState)
        if newState.duration == nil {
            ambientState = newState
        }
    }

    /// Смена фонового состояния. Не перебивает текущую временную реакцию.
    func setAmbient(_ newState: CompanionState) {
        ambientState = newState
        guard state.duration == nil else { return }
        apply(newState)
    }

    /// Короткая реакция: играет, если её приоритет не ниже текущей,
    /// и возвращает персонажа в фоновое состояние по окончании.
    func react(_ newState: CompanionState, gazeOffset: CGSize = .zero, duration: Double? = nil) {
        guard newState.priority >= state.priority || state.duration == nil else { return }
        gazeTargetOffset = reduceMotion ? .zero : gazeOffset
        apply(newState)
        let seconds = duration ?? newState.duration ?? CharacterConfig.stateTransitionDuration
        taskBag.replace(.stateReset, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(0, seconds) * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.returnToAmbient()
        })
    }

    func returnToAmbient() {
        gazeTargetOffset = .zero
        apply(ambientState)
    }

    private func apply(_ newState: CompanionState) {
        guard newState != state else { return }
        state = newState
        let target = StateAppearance.forState(newState)
        let duration = reduceMotion ? 0.12 : CharacterConfig.stateTransitionDuration
        withAnimation(.easeInOut(duration: duration)) {
            appearance = target
        }
    }
}
