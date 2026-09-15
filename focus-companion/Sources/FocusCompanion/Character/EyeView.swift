import SwiftUI

/// Один глаз: склера (белок) и зрачок со смещением внутри неё.
///
/// Моргание реализовано как схлопывание высоты всего глаза (`scaleEffect`
/// по Y), а не наложением прямоугольника-века поверх — так закрытие выглядит
/// органично (веко как бы "часть" самого глаза), а не как отдельный слой,
/// наезжающий сверху.
struct EyeView: View {
    var pupilOffset: CGPoint
    var eyeScaleY: CGFloat

    var body: some View {
        let size = CharacterConfig.eyeSize
        let pupilSize = CharacterConfig.pupilSize
        let maxShift = (size - pupilSize) / 2 * CharacterConfig.pupilMaxShiftFraction

        ZStack {
            Circle()
                .fill(CharacterConfig.scleraColor)

            Circle()
                .fill(CharacterConfig.pupilColor)
                .frame(width: pupilSize, height: pupilSize)
                .offset(x: pupilOffset.x * maxShift, y: pupilOffset.y * maxShift)
        }
        .frame(width: size, height: size)
        .scaleEffect(x: 1, y: eyeScaleY, anchor: .center)
    }
}
