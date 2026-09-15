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
///
/// Фаза 3: размер, наклон и форма щелей читаются из `StateAppearance`
/// текущего состояния (множители к базовым `CharacterConfig.eyeWidth/Height`)
/// — `EyesViewModel` по-прежнему отвечает только за динамику (слежение,
/// моргание, дыхание и синхронное с ним "подпрыгивание"), не за статичную
/// форму глаз.
struct EyesView: View {
    // Обычная (не @Bindable) ссылка: двусторонний биндинг сюда не нужен,
    // а простого чтения @Observable-свойств в body достаточно, чтобы
    // SwiftUI отследил изменения и перерисовал вью.
    var model: EyesViewModel
    var appearance: StateAppearance

    var body: some View {
        HStack(spacing: CharacterConfig.eyeSpacing) {
            EyeView(
                width: CharacterConfig.eyeWidth * appearance.eyeWidthMultiplier,
                height: CharacterConfig.eyeHeight * appearance.eyeHeightMultiplier,
                scaleY: model.eyeScaleY,
                shape: appearance.eyeShape
            )
            EyeView(
                width: CharacterConfig.eyeWidth * appearance.eyeWidthMultiplier,
                height: CharacterConfig.eyeHeight * appearance.eyeHeightMultiplier,
                scaleY: model.eyeScaleY,
                shape: appearance.eyeShape
            )
        }
        .rotationEffect(.degrees(CharacterConfig.eyeTilt + appearance.eyeTiltDegrees))
        .offset(
            x: model.pupilOffset.x * CharacterConfig.gazeMaxShift,
            // bobOffsetY — "подпрыгивание" `.celebrating`, синхронное с
            // дыханием (см. EyesViewModel.startBreathing) — складывается со
            // сдвигом взгляда по той же оси, а не заменяет его.
            y: model.pupilOffset.y * CharacterConfig.gazeMaxShift + model.bobOffsetY
        )
        .scaleEffect(model.breathScale)
        .allowsHitTesting(false)
    }
}
