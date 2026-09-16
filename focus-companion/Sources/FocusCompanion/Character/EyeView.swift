import SwiftUI

enum EyeShape: Equatable {
    /// Обычная вертикальная капсула 9 × 18 pt.
    case capsule
    /// Короткая улыбчивая дуга — только в радости.
    case arc
}

/// Один глаз: белая вертикальная капсула с веками.
///
/// Веки рисуются прямоугольниками цвета корпуса поверх капсулы и обрезаются
/// её же формой. Это даёт настоящее «опущенное верхнее веко» с наклоном —
/// именно им читаются злость и грусть, — и при этом не требует ни маски с
/// альфой, ни отдельного слоя размытия.
struct EyeView: View {
    var width: CGFloat
    var height: CGFloat
    /// Доля высоты, закрытая морганием: 1 — открыт, 0 — закрыт.
    var openFraction: CGFloat
    var upperLid: CGFloat
    var lowerLid: CGFloat
    var lidTiltDegrees: Double
    var shape: EyeShape
    var showsTear: Bool

    private var lidColor: Color { DesignTokens.Palette.companionBase }

    var body: some View {
        Group {
            switch shape {
            case .capsule:
                capsuleEye
            case .arc:
                arcEye
            }
        }
        .frame(width: width, height: height)
        .overlay(alignment: .bottom) { tear }
    }

    private var capsuleEye: some View {
        let form = Capsule(style: .continuous)
        return form
            .fill(CharacterConfig.eyeColor)
            .overlay(alignment: .top) {
                // Верхнее веко: наклон внутрь читается как нахмуренная бровь.
                Rectangle()
                    .fill(lidColor)
                    .frame(height: max(0, height * clampedUpperLid))
                    .rotationEffect(.degrees(lidTiltDegrees), anchor: .center)
                    .offset(y: -height * 0.02)
            }
            .overlay(alignment: .bottom) {
                Rectangle()
                    .fill(lidColor)
                    .frame(height: max(0, height * min(max(lowerLid, 0), 1)))
            }
            .clipShape(form)
            // Моргание — сжатие по высоте от ЦЕНТРА капсулы: 18 → 2 → 18 pt.
            .scaleEffect(x: 1, y: max(0.02, openFraction), anchor: .center)
    }

    /// Радость: капсула превращается в короткую дугу той же толщины.
    private var arcEye: some View {
        SmileArc()
            .stroke(
                CharacterConfig.eyeColor,
                style: StrokeStyle(lineWidth: width, lineCap: .round)
            )
            .frame(width: height * 0.78, height: max(width * 1.4, 4))
            .frame(width: width, height: height, alignment: .center)
    }

    @ViewBuilder
    private var tear: some View {
        if showsTear {
            TearShape()
                .fill(CharacterConfig.eyeColor.opacity(0.45))
                .frame(width: width * 0.45, height: width * 0.7)
                .offset(y: width * 0.9)
                .allowsHitTesting(false)
        }
    }

    private var clampedUpperLid: CGFloat {
        min(max(upperLid, 0), 1)
    }
}

/// Дуга улыбки: выпуклостью вверх, концы опущены.
private struct SmileArc: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.maxY),
            control: CGPoint(x: rect.midX, y: rect.minY - rect.height)
        )
        return path
    }
}

/// Одна маленькая полупрозрачная слеза.
private struct TearShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        path.move(to: CGPoint(x: rect.midX, y: rect.minY))
        path.addQuadCurve(
            to: CGPoint(x: rect.midX, y: rect.maxY),
            control: CGPoint(x: rect.maxX + rect.width * 0.3, y: rect.midY)
        )
        path.addQuadCurve(
            to: CGPoint(x: rect.midX, y: rect.minY),
            control: CGPoint(x: rect.minX - rect.width * 0.3, y: rect.midY)
        )
        path.closeSubpath()
        return path
    }
}
