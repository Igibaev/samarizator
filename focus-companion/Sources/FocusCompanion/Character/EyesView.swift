import SwiftUI

/// Пара глаз персонажа.
///
/// Отступ между глазами берётся из `CharacterConfig`, дыхание (`breathScale`)
/// масштабирует пару целиком как один узел — капсулу под ней это не
/// затрагивает (см. решение №2 в PHASE-2-PROMPT.md: форма капсулы статична,
/// потому что жёстко привязана к физическому вырезу).
struct EyesView: View {
    // Обычная (не @Bindable) ссылка: двусторонний биндинг сюда не нужен,
    // а простого чтения @Observable-свойств в body достаточно, чтобы
    // SwiftUI отследил изменения и перерисовал вью.
    var model: EyesViewModel

    var body: some View {
        HStack(spacing: CharacterConfig.eyeSpacing) {
            EyeView(pupilOffset: model.pupilOffset, eyeScaleY: model.eyeScaleY)
            EyeView(pupilOffset: model.pupilOffset, eyeScaleY: model.eyeScaleY)
        }
        .scaleEffect(model.breathScale)
        .allowsHitTesting(false)
    }
}
