import SwiftUI

/// Один глаз — светлая щель (капсула со скруглением по короткой стороне).
///
/// Моргание реализовано как схлопывание щели по её длинной оси: щель
/// сжимается в тонкую чёрточку и раскрывается обратно. Именно так закрывается
/// глаз у минималистичных персонажей — накладывать сверху прямоугольник-веко
/// здесь нечего, тело персонажа и так однотонное.
struct EyeView: View {
    var eyeScaleY: CGFloat

    var body: some View {
        Capsule(style: .continuous)
            .fill(CharacterConfig.eyeColor)
            .frame(width: CharacterConfig.eyeWidth, height: CharacterConfig.eyeHeight)
            .scaleEffect(x: 1, y: eyeScaleY, anchor: .center)
    }
}
