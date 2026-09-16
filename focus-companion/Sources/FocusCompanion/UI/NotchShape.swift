import SwiftUI

/// Правое крыло notch: бесшовное продолжение выреза.
///
/// Левая сторона ПРЯМАЯ и визуально сливается с вырезом — скругляются только
/// внешний правый верхний и правый нижний углы. Между вырезом и крылом нет
/// ни бордера, ни блика, ни тени, ни разницы прозрачности: это одна фигура,
/// просто нарисованная двумя разными слоями системы.
struct WingShape: Shape {
    /// Радиус верхнего правого угла — повторяет кривизну выреза.
    var topCornerRadius: CGFloat
    /// Радиус нижнего правого угла — продолжает форму выреза вниз.
    var bottomCornerRadius: CGFloat
    /// Скруглять ли левую сторону. `true` — самостоятельная капсула на
    /// экране без выреза, где скругляются все четыре угла.
    var roundsLeftSide: Bool

    func path(in rect: CGRect) -> Path {
        let top = max(0, min(topCornerRadius, rect.width / 2, rect.height / 2))
        let bottom = max(0, min(bottomCornerRadius, rect.height - top, rect.width / 2))
        var path = Path()

        if roundsLeftSide {
            // Самостоятельная капсула: полноценное скругление всех углов.
            let radius = max(0, min(DesignTokens.Radius.standaloneCapsule, rect.height / 2, rect.width / 2))
            path.addRoundedRect(in: rect, cornerSize: CGSize(width: radius, height: radius), style: .continuous)
            return path
        }

        // Левый край прямой, вплотную к вырезу.
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX - top, y: rect.minY))
        // Верхний правый угол «выгибается наружу» — так же, как правый край выреза.
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY + top),
            control: CGPoint(x: rect.maxX, y: rect.minY)
        )
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - bottom))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX - bottom, y: rect.maxY),
            control: CGPoint(x: rect.maxX, y: rect.maxY)
        )
        path.addLine(to: CGPoint(x: rect.minX, y: rect.maxY))
        path.closeSubpath()
        return path
    }
}

/// Полка задач и панель фокуса: прямоугольник со скруглением снизу,
/// растущий вниз от нижней границы выреза.
struct ShelfShape: Shape {
    var cornerRadius: CGFloat

    func path(in rect: CGRect) -> Path {
        let radius = max(0, min(cornerRadius, rect.width / 2, rect.height))
        var path = Path()
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.minY))
        path.addLine(to: CGPoint(x: rect.maxX, y: rect.maxY - radius))
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX - radius, y: rect.maxY),
            control: CGPoint(x: rect.maxX, y: rect.maxY)
        )
        path.addLine(to: CGPoint(x: rect.minX + radius, y: rect.maxY))
        path.addQuadCurve(
            to: CGPoint(x: rect.minX, y: rect.maxY - radius),
            control: CGPoint(x: rect.minX, y: rect.maxY)
        )
        path.closeSubpath()
        return path
    }
}
