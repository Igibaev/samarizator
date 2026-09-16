import AppKit

enum SwipeDirection {
    case left
    case right
}

/// Распознаватель горизонтального свайпа двумя пальцами (design.md §8).
///
/// Правила, которые здесь реализованы дословно:
/// - сначала определяется ось жеста, затем она фиксируется до конца жеста;
/// - порог: накопленное смещение ≥40 pt и `abs(dx) ≥ 1.5 × abs(dy)`;
/// - один физический жест даёт максимум один переход;
/// - остаточная инерция (`momentumPhase`) не переключает следующую страницу.
///
/// Знак нормализуется с учётом системного «естественного» направления
/// прокрутки: «свайп влево» в документе — это физическое движение пальцев
/// влево, а не знак `deltaX`.
final class SwipeRecognizer {

    /// Накопленное горизонтальное смещение ≥ этого значения — переход.
    static let threshold: CGFloat = 40
    /// Во сколько раз горизонталь должна превышать вертикаль.
    static let axisRatio: CGFloat = 1.5

    private enum Axis {
        case undecided
        case horizontal
        case vertical
    }

    private var axis: Axis = .undecided
    private var accumulatedX: CGFloat = 0
    private var accumulatedY: CGFloat = 0
    private var firedInThisGesture = false

    var onSwipe: ((SwipeDirection) -> Void)?

    /// Возвращает `true`, если событие поглощено распознавателем и его не
    /// нужно отдавать обычной вертикальной прокрутке списка.
    @discardableResult
    func handle(_ event: NSEvent) -> Bool {
        switch event.phase {
        case .began:
            reset()
        case .ended, .cancelled:
            let wasHorizontal = axis == .horizontal
            reset()
            return wasHorizontal
        default:
            break
        }

        // Остаточная инерция не имеет права дать второй переход.
        if event.momentumPhase != [] {
            return axis == .horizontal
        }

        accumulatedX += event.scrollingDeltaX
        accumulatedY += event.scrollingDeltaY

        if axis == .undecided {
            let absX = abs(accumulatedX)
            let absY = abs(accumulatedY)
            if absX >= Self.axisRatio * absY && absX >= 8 {
                axis = .horizontal
            } else if absY > absX {
                axis = .vertical
            }
        }

        guard axis == .horizontal else { return false }
        guard !firedInThisGesture else { return true }
        guard abs(accumulatedX) >= Self.threshold,
              abs(accumulatedX) >= Self.axisRatio * abs(accumulatedY) else { return true }

        firedInThisGesture = true
        onSwipe?(Self.direction(forScrollingDeltaX: accumulatedX, isNatural: event.isDirectionInvertedFromDevice))
        return true
    }

    /// Физическое движение пальцев влево открывает следующую страницу.
    ///
    /// При «естественной» прокрутке движение пальцев влево даёт
    /// ОТРИЦАТЕЛЬНЫЙ `scrollingDeltaX`; при выключенной — знак обратный.
    static func direction(forScrollingDeltaX deltaX: CGFloat, isNatural: Bool) -> SwipeDirection {
        let fingersMovedLeft = isNatural ? (deltaX < 0) : (deltaX > 0)
        return fingersMovedLeft ? .left : .right
    }

    func reset() {
        axis = .undecided
        accumulatedX = 0
        accumulatedY = 0
        firedInThisGesture = false
    }
}
