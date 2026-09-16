import SwiftUI

/// Как выглядит пара глаз в каждом состоянии.
///
/// Все величины — множители и СМЕЩЕНИЯ В PT относительно базовой капсулы
/// 9 × 18 pt. Веки — доля высоты глаза, закрытая сверху/снизу: именно ими
/// читается злость и грусть, а не цветным значком рядом.
struct StateAppearance: Equatable {
    /// Множители размера капсулы.
    var widthMultiplier: CGFloat = 1
    var heightMultiplier: CGFloat = 1
    /// Асимметрия: применяется только к правому глазу (любопытство).
    var rightEyeWidthMultiplier: CGFloat = 1
    var rightEyeOffsetY: CGFloat = 0

    /// Общий сдвиг пары в pt.
    var offsetX: CGFloat = 0
    var offsetY: CGFloat = 0

    /// Верхнее веко: доля высоты, закрытая сверху (0 — открыт, 1 — закрыт).
    var upperLid: CGFloat = 0
    /// Наклон верхнего века внутрь, в градусах. Положительное — «злой» наклон.
    var lidTiltDegrees: Double = 0
    /// Нижнее веко — им читается «прищур».
    var lowerLid: CGFloat = 0

    /// Форма: обычная вертикальная капсула или улыбчивая дуга.
    var shape: EyeShape = .capsule

    /// Слеза видна только в грусти.
    var showsTear: Bool = false

    /// Множители фоновых циклов.
    var blinkRateMultiplier: Double = 1
    /// Следят ли глаза за курсором в этом состоянии.
    var tracksCursor: Bool = true
}

extension StateAppearance {
    /// Таблица состояний. Значения — из design.md §12.
    static let table: [CompanionState: StateAppearance] = [
        // Спокоен: две капсулы 9 × 18, без свечения и без непрерывной пульсации.
        .idle: StateAppearance(),

        // Любопытен: один глаз на 2 pt выше, другой слегка уже.
        .curious: StateAppearance(
            rightEyeWidthMultiplier: 0.86,
            rightEyeOffsetY: 2,
            offsetY: -1
        ),

        // Записывает: спокойный устойчивый взгляд, минимальная реакция.
        .listening: StateAppearance(
            heightMultiplier: 0.96,
            blinkRateMultiplier: 0.8
        ),

        // Думает: поочерёдное сужение и короткий взгляд в сторону.
        // Сама поочерёдность — в EyesViewModel, здесь базовая форма.
        .thinking: StateAppearance(
            heightMultiplier: 0.72,
            offsetX: 1.5,
            lowerLid: 0.12,
            tracksCursor: false
        ),

        // Напоминает: взгляд вниз к нужной задаче, короткий подъём века.
        // Горизонтальное смещение к строке задаётся отдельно, из контроллера.
        .reminder: StateAppearance(
            offsetY: 2,
            upperLid: -0.08,
            tracksCursor: false
        ),

        // Радуется: капсулы превращаются в две короткие дуги, подъём на 2 pt.
        .happy: StateAppearance(
            offsetY: -2,
            shape: .arc,
            blinkRateMultiplier: 0.4,
            tracksCursor: false
        ),

        // Злится: верхние веки наклонены внутрь, глаза ниже и уже.
        .angry: StateAppearance(
            widthMultiplier: 0.88,
            heightMultiplier: 0.78,
            offsetY: 1.5,
            upperLid: 0.42,
            lidTiltDegrees: 18,
            tracksCursor: false
        ),

        // Грустит: верхние края слегка опущены, одна маленькая слеза.
        .sad: StateAppearance(
            heightMultiplier: 0.86,
            offsetY: 1,
            upperLid: 0.26,
            lidTiltDegrees: -14,
            showsTear: true,
            tracksCursor: false
        ),

        // Подметает: взгляд следует за щёткой вниз.
        .sweeping: StateAppearance(
            heightMultiplier: 0.82,
            offsetY: 2,
            lowerLid: 0.18,
            tracksCursor: false
        ),

        // Комкает и выбрасывает: взгляд провожает бумагу.
        .discarding: StateAppearance(
            heightMultiplier: 0.84,
            offsetX: 2,
            offsetY: 1.5,
            upperLid: 0.16,
            tracksCursor: false
        ),

        // Ошибка: короткая асимметрия взгляда, затем спокойный вид.
        .error: StateAppearance(
            rightEyeWidthMultiplier: 1.15,
            rightEyeOffsetY: -2,
            offsetX: -2,
            upperLid: 0.2,
            tracksCursor: false
        ),
    ]

    static func forState(_ state: CompanionState) -> StateAppearance {
        table[state] ?? StateAppearance()
    }
}
