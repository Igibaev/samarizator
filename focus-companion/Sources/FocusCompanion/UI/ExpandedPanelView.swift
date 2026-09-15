import SwiftUI

/// Содержимое раскрытой панели ниже "головы" персонажа.
///
/// Фаза 3 задач ещё не заводит (список задач и "сжигание" — Фаза 4). Это
/// осознанная заготовка: подпись текущего состояния и плейсхолдер-текст,
/// чтобы пустая область под персонажем не выглядела как ошибка отрисовки
/// (просто чёрный прямоугольник ни о чём). Фаза 4 заменит `body` на список.
struct ExpandedPanelView: View {
    var stateMachine: CompanionStateMachine

    var body: some View {
        VStack(spacing: 6) {
            Text(stateMachine.state.displayName)
                .font(.system(size: 12, weight: .medium))
                .foregroundStyle(.white.opacity(0.85))

            Text("Список задач появится здесь в следующей фазе")
                .font(.system(size: 10))
                .foregroundStyle(.white.opacity(0.5))
                .multilineTextAlignment(.center)
                .padding(.horizontal, 20)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Своей интерактивности здесь пока нет (Фаза 4 добавит чекбоксы) —
        // не перехватываем клики поверх родителя, который сам решает,
        // включать ли hit-testing (см. NotchRootView.capsuleGroup).
        .allowsHitTesting(false)
    }
}
