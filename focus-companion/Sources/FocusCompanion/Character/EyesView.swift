import SwiftUI

/// Пара глаз внутри области 48 × 28 pt (design.md §6.1).
///
/// Лицо — это ровно две белые капсулы во всех состояниях. Ни рта, ни носа,
/// ни радужки, ни значков рядом: эмоция читается формой и веками.
struct EyesView: View {
    var model: EyesViewModel
    var appearance: StateAppearance
    /// Дополнительный сдвиг взгляда к конкретной строке задачи.
    var gazeTargetOffset: CGSize

    var body: some View {
        HStack(spacing: CharacterConfig.eyeSpacing) {
            eye(isRight: false)
            eye(isRight: true)
        }
        .frame(width: CharacterConfig.eyesAreaWidth, height: CharacterConfig.eyesAreaHeight)
        .offset(x: totalOffset.width, y: totalOffset.height)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Открыть фокус")
        .accessibilityAddTraits(.isButton)
    }

    private func eye(isRight: Bool) -> some View {
        EyeView(
            width: CharacterConfig.eyeWidth
                * appearance.widthMultiplier
                * (isRight ? appearance.rightEyeWidthMultiplier : 1),
            height: CharacterConfig.eyeHeight * appearance.heightMultiplier,
            openFraction: model.openFraction,
            upperLid: appearance.upperLid + model.thinkingSquint(isRight: isRight),
            lowerLid: appearance.lowerLid,
            lidTiltDegrees: isRight ? -appearance.lidTiltDegrees : appearance.lidTiltDegrees,
            shape: appearance.shape,
            showsTear: appearance.showsTear && isRight
        )
        .offset(y: isRight ? -appearance.rightEyeOffsetY : 0)
    }

    private var totalOffset: CGSize {
        CGSize(
            width: appearance.offsetX + gazeTargetOffset.width
                + (appearance.tracksCursor ? model.gazeOffset.width : 0)
                + model.jolt,
            height: appearance.offsetY + gazeTargetOffset.height
                + (appearance.tracksCursor ? model.gazeOffset.height : 0)
        )
    }
}
