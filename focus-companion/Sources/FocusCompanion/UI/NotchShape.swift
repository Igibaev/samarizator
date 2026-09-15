import SwiftUI

/// Форма капсулы, визуально продолжающей вырез экрана вниз.
///
/// Верхние углы скруглены "наружу" — вогнутой кривой, повторяющей скругление
/// самого физического выреза, так что капсула стыкуется с краем экрана без
/// видимого шва. Нижние углы, наоборот, скруглены обычным (выпуклым) образом,
/// как скругление низа настоящего выреза.
///
/// Важно: в SwiftUI координата Y растёт ВНИЗ (в отличие от AppKit), поэтому
/// `rect.minY` — это верх капсулы (примыкает к краю экрана), а `rect.maxY` — низ.
struct NotchShape: Shape {
    private var topCornerRadius: CGFloat
    private var bottomCornerRadius: CGFloat

    init(topCornerRadius: CGFloat, bottomCornerRadius: CGFloat) {
        self.topCornerRadius = topCornerRadius
        self.bottomCornerRadius = bottomCornerRadius
    }

    /// Оба радиуса анимируемы — задел на Фазу 3, где капсула будет морфиться
    /// между collapsed- и expanded-состояниями через изменение этих радиусов.
    var animatableData: AnimatablePair<CGFloat, CGFloat> {
        get { AnimatablePair(topCornerRadius, bottomCornerRadius) }
        set {
            topCornerRadius = newValue.first
            bottomCornerRadius = newValue.second
        }
    }

    func path(in rect: CGRect) -> Path {
        // Радиусы зажимаются по размеру капсулы. Без этого достаточно
        // задать нижний радиус больше половины высоты — и скругления с двух
        // сторон встречаются, съедая боковые стенки: форма перестаёт быть
        // капсулой и превращается в бесформенное пятно.
        let topCornerRadius = max(0, min(self.topCornerRadius, rect.width / 2, rect.height / 2))
        let bottomCornerRadius = max(0, min(
            self.bottomCornerRadius,
            rect.height - topCornerRadius,
            rect.width / 2 - topCornerRadius
        ))

        var path = Path()

        // Начинаем от верхнего левого угла и идём по часовой стрелке.
        path.move(to: CGPoint(x: rect.minX, y: rect.minY))

        // Левый верхний угол: вогнутая кривая (control-точка лежит на том же
        // верхнем ребре, что и старт) — она "подрезает" угол внутрь капсулы,
        // повторяя скругление корпуса экрана вокруг выреза.
        path.addQuadCurve(
            to: CGPoint(x: rect.minX + topCornerRadius, y: rect.minY + topCornerRadius),
            control: CGPoint(x: rect.minX + topCornerRadius, y: rect.minY)
        )

        // Левая сторона вниз, до начала нижнего скругления.
        path.addLine(to: CGPoint(x: rect.minX + topCornerRadius, y: rect.maxY - bottomCornerRadius))

        // Левый нижний угол: обычная выпуклая кривая.
        path.addQuadCurve(
            to: CGPoint(x: rect.minX + topCornerRadius + bottomCornerRadius, y: rect.maxY),
            control: CGPoint(x: rect.minX + topCornerRadius, y: rect.maxY)
        )

        // Низ капсулы.
        path.addLine(to: CGPoint(x: rect.maxX - topCornerRadius - bottomCornerRadius, y: rect.maxY))

        // Правый нижний угол — зеркально левому.
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX - topCornerRadius, y: rect.maxY - bottomCornerRadius),
            control: CGPoint(x: rect.maxX - topCornerRadius, y: rect.maxY)
        )

        // Правая сторона вверх.
        path.addLine(to: CGPoint(x: rect.maxX - topCornerRadius, y: rect.minY + topCornerRadius))

        // Правый верхний угол — зеркально левому, снова вогнутая кривая.
        path.addQuadCurve(
            to: CGPoint(x: rect.maxX, y: rect.minY),
            control: CGPoint(x: rect.maxX - topCornerRadius, y: rect.minY)
        )

        path.closeSubpath()
        return path
    }
}
