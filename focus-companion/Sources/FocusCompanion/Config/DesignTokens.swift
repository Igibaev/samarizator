import SwiftUI

/// Единственный источник правды для внешнего вида компаньона.
///
/// Числа и цвета взяты из `design.md` (раздел 5 «Дизайн-токены» и раздел 4
/// «Геометрия и расположение»). Правило простое: если значение встречается
/// в двух местах кода — оно должно жить здесь, а не дублироваться.
enum DesignTokens {

    // MARK: - Цвет (design.md §5.1)

    enum Palette {
        /// Корпус: правое крыло notch и полка задач под ним.
        static let companionBase = Color(hex: 0x08090B)
        /// Плотная основа панелей.
        static let surfaceSolid = Color(hex: 0x17181C)
        /// Базовый fallback «дымчатого стекла».
        static let surfaceGlass = Color(hex: 0x17181C).opacity(0.92)
        /// Выбранная или приподнятая строка.
        static let surfaceRaised = Color(hex: 0x222328)
        /// Наведение на элемент.
        static let surfaceHover = Color.white.opacity(0.07)
        /// Граница и разделители.
        static let strokeHairline = Color.white.opacity(0.10)

        static let textPrimary = Color(hex: 0xF5F5F7)
        static let textSecondary = Color(hex: 0xA1A1A8)
        static let textTertiary = Color(hex: 0x8E8E96)

        /// Активный переключатель, клавиатурный фокус.
        static let accentSelection = Color(hex: 0x0A84FF)
        /// Срок близко, тлеющий край.
        static let accentWarning = Color(hex: 0xFFB340)
        /// Индикатор записи и истечение срока.
        static let accentRecording = Color(hex: 0xFF6159)
        /// Короткое подтверждение выполнения.
        static let accentSuccess = Color(hex: 0x63D69B)
    }

    // MARK: - Шкала отступов (design.md §5.2)

    enum Spacing {
        static let xxs: CGFloat = 4
        static let xs: CGFloat = 8
        static let s: CGFloat = 12
        static let m: CGFloat = 16
        static let l: CGFloat = 20
        static let xl: CGFloat = 24
        static let xxl: CGFloat = 32
    }

    // MARK: - Радиусы (design.md §5.2)

    enum Radius {
        static let button: CGFloat = 16
        /// Самостоятельная капсула на экране без выреза — скругляется целиком.
        static let standaloneCapsule: CGFloat = 16
        static let row: CGFloat = 10
        static let shelf: CGFloat = 20
        static let focus: CGFloat = 24
        static let drawer: CGFloat = 24
    }

    // MARK: - Типографика (design.md §5.2)

    enum Typography {
        static func recordingTitle() -> Font { .system(size: 20, weight: .semibold) }
        static func panelTitle() -> Font { .system(size: 16, weight: .semibold) }
        static func transcript() -> Font { .system(size: 14, weight: .regular) }
        static func taskTitle() -> Font { .system(size: 13, weight: .medium) }
        static func compactTask() -> Font { .system(size: 11, weight: .medium) }
        /// Таймеры и метаданные. Табличные цифры, чтобы ширина не прыгала.
        static func meta() -> Font { .system(size: 11, weight: .regular).monospacedDigit() }
        static func caption() -> Font { .system(size: 10, weight: .medium) }
    }

    // MARK: - Motion-токены (design.md §12.2)

    enum Motion {
        static let hoverOpenDelay: Double = 0.120
        static let hoverCloseDelay: Double = 0.450
        static let focusExpand: Double = 0.220
        static let focusCollapse: Double = 0.180
        static let drawerOpen: Double = 0.280
        static let drawerClose: Double = 0.220
        static let pageChange: Double = 0.240
        static let feedbackPress: Double = 0.080
        /// Потолок любой анимации утилизации задачи.
        static let disposalMaximum: Double = 1.600
        /// Всплытие tooltip у кнопок.
        static let tooltipDelay: Double = 0.600

        /// Кривая `standard.easing` как SwiftUI-анимация заданной длительности.
        static func standard(_ duration: Double) -> Animation {
            .timingCurve(0.2, 0.8, 0.2, 1, duration: duration)
        }

        /// Reduce Motion: вместо сдвига — короткий crossfade (design.md §12.2).
        static func respectful(_ duration: Double, reduceMotion: Bool) -> Animation {
            reduceMotion ? .easeInOut(duration: 0.120) : standard(duration)
        }
    }

    // MARK: - Стекло (design.md §5.1)

    enum Glass {
        static let blurRadius: CGFloat = 28
        static let shadowRadius: CGFloat = 40
        static let shadowY: CGFloat = 14
        static let shadowColor = Color.black.opacity(0.24)
    }
}

extension Color {
    /// Цвет из 24-битного RRGGBB, как записано в design.md.
    init(hex: UInt32) {
        self.init(
            .sRGB,
            red: Double((hex >> 16) & 0xFF) / 255,
            green: Double((hex >> 8) & 0xFF) / 255,
            blue: Double(hex & 0xFF) / 255,
            opacity: 1
        )
    }
}
