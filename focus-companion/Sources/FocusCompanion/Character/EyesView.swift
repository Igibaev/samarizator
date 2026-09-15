import SwiftUI

/// Пара глаз персонажа.
///
/// Взгляд передаётся сдвигом пары целиком по телу персонажа, а не движением
/// зрачков внутри склер: у минималистичного лица без белка это единственный
/// способ показать направление взгляда, и он же читается как лёгкий поворот
/// головы.
///
/// Наклон применяется к паре целиком, а не к каждой щели отдельно — иначе
/// глаза расходятся веером и лицо теряет собранность.
struct EyesView: View {
    // Обычная (не @Bindable) ссылка: двусторонний биндинг сюда не нужен,
    // а простого чтения @Observable-свойств в body достаточно, чтобы
    // SwiftUI отследил изменения и перерисовал вью.
    var model: EyesViewModel

    var body: some View {
        HStack(spacing: CharacterConfig.eyeSpacing) {
            EyeView(eyeScaleY: model.eyeScaleY)
            EyeView(eyeScaleY: model.eyeScaleY)
        }
        .rotationEffect(.degrees(CharacterConfig.eyeTilt))
        .offset(
            x: model.pupilOffset.x * CharacterConfig.gazeMaxShift,
            y: model.pupilOffset.y * CharacterConfig.gazeMaxShift
        )
        .scaleEffect(model.breathScale)
        .allowsHitTesting(false)
    }
}
