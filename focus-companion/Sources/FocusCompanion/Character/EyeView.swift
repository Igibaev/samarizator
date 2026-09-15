import SwiftUI

/// Форма глаза для конкретного `CompanionState` (см. `State/StateAppearance.swift`).
enum EyeShape {
    /// Обычная щель — состояние по умолчанию (Фаза 2, `.idle` и большинство
    /// остальных состояний).
    case slit
    /// Дуга-полумесяц (закрытый довольный глаз, "^‿^") — используется в
    /// `.celebrating`: см. таблицу ориентиров в PHASE-3-PROMPT.md
    /// ("дуги-полумесяцы (перевёрнутые)").
    case crescent
}

/// Один глаз — светлая щель (капсула со скруглением по короткой стороне)
/// либо, в `.celebrating`, дуга-полумесяц.
///
/// Моргание по-прежнему реализовано как схлопывание по вертикальной оси
/// (`scaleY`) — этим по-прежнему управляет `EyesViewModel`, эта вью лишь
/// применяет готовое значение поверх любой из двух форм. Размер (`width`,
/// `height`) и форма (`shape`) приходят из `StateAppearance` текущего
/// состояния — см. `EyesView`.
struct EyeView: View {
    var width: CGFloat
    var height: CGFloat
    var scaleY: CGFloat
    var shape: EyeShape

    var body: some View {
        Group {
            switch shape {
            case .slit:
                Capsule(style: .continuous)
                    .fill(CharacterConfig.eyeColor)
                    .frame(width: width, height: height)

            case .crescent:
                // Толщина дуги — короткая ось обычной щели (width), размах —
                // длинная (height). Ориентировочные пропорции, автор
                // подгонит на живой машине.
                CrescentShape()
                    .stroke(CharacterConfig.eyeColor, style: StrokeStyle(lineWidth: width, lineCap: .round))
                    .frame(width: height, height: max(width * 1.6, 4))
            }
        }
        .scaleEffect(x: 1, y: scaleY, anchor: .center)
    }
}

/// Дуга-полумесяц для `.crescent`: гладкая кривая от нижнего левого угла к
/// нижнему правому через контрольную точку сверху — визуально читается как
/// "⌒", закрытый довольный глаз.
private struct CrescentShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.maxY),
            control: CGPoint(x: rect.midX, y: rect.minY)
        )
        return path
    }
}
