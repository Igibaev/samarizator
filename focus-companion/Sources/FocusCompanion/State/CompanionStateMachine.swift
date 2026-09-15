import SwiftUI
import Observation

/// Хранит текущее состояние персонажа (`CompanionState`) и анимированно
/// переключает его визуальные параметры (`StateAppearance`).
///
/// Живёт на уровне `NotchWindowController` (переживает переезд панели между
/// экранами — иначе текущая эмоция сбрасывалась бы при каждом
/// подключении/отключении монитора) и передаётся в `NotchRootView`, откуда
/// её же держит `EyesViewModel` для чтения множителей к своим циклам.
///
/// Важно: эта машина НЕ заводит собственных таймеров и не дублирует циклы
/// `EyesViewModel` (моргание/саккады/дыхание) — она только публикует
/// целевые параметры, которые `EyesViewModel` читает при планировании
/// следующего шага своих уже существующих циклов. Именно это разделение
/// прямо требуется PHASE-3-PROMPT.md ("влиять на параметры, а не дублировать
/// таймеры").
@MainActor
@Observable
final class CompanionStateMachine {

    private(set) var state: CompanionState = .idle
    private(set) var appearance: StateAppearance = StateAppearance.table[.idle] ?? StateAppearance()

    /// Переключает состояние с анимацией. Вызов с уже активным состоянием —
    /// no-op: иначе выбор того же пункта в debug-меню перезапускал бы
    /// анимацию перехода без всякой причины.
    func setState(_ newState: CompanionState) {
        guard newState != state else { return }
        state = newState

        let target = StateAppearance.table[newState] ?? StateAppearance()
        withAnimation(.easeInOut(duration: CharacterConfig.stateTransitionDuration)) {
            appearance = target
        }
    }
}
