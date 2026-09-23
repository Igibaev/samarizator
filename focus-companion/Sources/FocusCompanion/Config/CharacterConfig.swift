import SwiftUI

/// Всё про самого персонажа: размеры глаз, слежение, моргание, тайминги
/// эмоций. Числа — из design.md §6.1 и §12.
enum CharacterConfig {

    // MARK: - Геометрия глаз (design.md §6.1)

    /// Область пары глаз внутри крыла.
    static let eyesAreaWidth: CGFloat = 48
    static let eyesAreaHeight: CGFloat = 28

    /// Каждая капсула 9 × 18 pt, между ними 8 pt:
    /// левый глаз x=8, правый x=25 внутри собственной области пары 42 × 26 pt.
    static let eyeWidth: CGFloat = 9
    static let eyeHeight: CGFloat = 18
    static let eyeSpacing: CGFloat = 8

    static let eyeColor: Color = DesignTokens.Palette.textPrimary

    /// Цвет слезы. Единственное цветное пятно на лице — и это не глаз:
    /// сами капсулы остаются белыми во всех состояниях (design.md §3).
    static let tearColor: Color = Color(hex: 0x6CB8FF)

    // MARK: - Слежение за курсором (design.md §12)

    /// Общий сдвиг пары: до ±3 pt по x и ±2 pt по y.
    static let gazeMaxShiftX: CGFloat = 3
    static let gazeMaxShiftY: CGFloat = 2
    /// Сглаживание слежения.
    static let gazeSmoothing: Double = 0.120
    /// Курсор считается «рядом с корпусом» в этом радиусе от центра крыла.
    /// Дальше глаза не следят — иначе персонаж елозит весь день.
    static let gazeNearRadius: CGFloat = 420
    /// Внутри мёртвой зоны взгляд стоит ровно.
    static let gazeDeadZoneRadius: CGFloat = 24

    // MARK: - Моргание (design.md §12)

    /// Высота 18 → 2 → 18 pt.
    static let blinkDuration: Double = 0.110
    static let blinkClosedHeight: CGFloat = 2
    static let blinkMinInterval: Double = 4
    static let blinkMaxInterval: Double = 7
    /// Фиксированный seed нужен для воспроизводимых скриншотов (design.md §14).
    static let blinkSeedEnvironmentKey = "FOCUS_BLINK_SEED"

    // MARK: - Тайминги эмоций (design.md §12)

    static let curiousDuration: Double = 0.280
    static let thinkingCycle: Double = 1.200
    static let reminderDuration: Double = 0.650
    static let happyDuration: Double = 0.700
    static let angryDuration: Double = 0.450
    static let sadDuration: Double = 0.800
    static let sweepingDuration: Double = 1.100
    static let discardingDuration: Double = 1.300
    static let errorDuration: Double = 0.300
    /// Плавность перехода между эмоциями.
    static let stateTransitionDuration: Double = 0.220

    // MARK: - Напоминания (design.md §11.3)

    /// Первый тихий сигнал — на 50% выбранного интервала.
    static let reminderFirstFraction: Double = 0.5
    /// Второй сигнал — за 5 минут до срока.
    static let reminderSecondLeadTime: Double = 5 * 60
    /// Не более одного заметного напоминания на весь компаньон за 20 минут.
    static let reminderGlobalCooldown: Double = 20 * 60
    /// Режим «Чаще»: интервал 10 минут, максимум 4 сигнала в час.
    static let reminderFrequentCooldown: Double = 10 * 60
    static let reminderFrequentHourlyLimit = 4
    /// Сколько секунд держится подпись напоминания.
    static let reminderCaptionDuration: Double = 4.0
    /// Частота проверки сроков и напоминаний.
    static let deadlineTickInterval: Double = 1.0
    static let reminderTickInterval: Double = 30.0
    static let reminderQuietHourStart: Int = 23
    static let reminderQuietHourEnd: Int = 8

    // MARK: - Сроки задач (design.md §11.1, §11.2)

    /// Пресеты срока и значение по умолчанию.
    static let deadlinePresets: [Int] = [30, 60, 90]
    static let defaultDeadlineMinutes: Int = 60
    static let maxTaskTitleLength = 80
    /// `dueSoon`: до срока ≤10 минут или ≤20% исходного интервала.
    static let dueSoonAbsolute: Double = 10 * 60
    static let dueSoonFraction: Double = 0.2
    /// Сколько секунд висит кнопка «Вернуть» после сгорания.
    static let undoWindow: Double = 8

    // MARK: - Дела со встречи (Samarizator → компаньон)

    /// Сколько секунд держится реплика «принёс дела» на компактной полоске.
    /// Дольше подписи напоминания (4 с): у неё есть кнопка «Открыть», и
    /// человеку нужно успеть её нажать.
    static let handoffNoticeDuration: Double = 12.0
    /// Ниже этой интенсивности эмоции реплики глаза не реагируют вовсе —
    /// остаётся только текст.
    static let handoffReactionThreshold: Double = 0.3
    /// Бездействие (сек), после которого реплику откладывают до возвращения
    /// человека: реакция в пустоту никому не нужна.
    static let handoffIdleThreshold: Double = 4 * 60
    /// Сколько дней хранить файлы передач: они дают короткие формулировки
    /// кнопке «В фокус» на странице «Записи».
    static let handoffRetentionDays: Int = 30

    // MARK: - Живость (переключатель в меню, по умолчанию выключен)

    /// Сколько секунд без ввода — и персонаж засыпает.
    static let asleepAfter: Double = 5 * 60
    /// Поздний вечер: сонные глаза с этого часа до `drowsyHourEnd`.
    static let drowsyHourStart: Int = 23
    static let drowsyHourEnd: Int = 6
    /// Как часто проверять присутствие человека. Вызов дешёвый —
    /// `CGEventSource`, — а возвращение хочется заметить сразу.
    static let presenceTickInterval: Double = 2
    static let wakingDuration: Double = 0.600
    /// Человек «активен», если трогал мышь или клавиатуру не дольше этого.
    static let activeWithin: Double = 60
    /// Мелкие движения в покое: не чаще раза в полторы минуты.
    static let fidgetMinInterval: Double = 90
    static let fidgetMaxInterval: Double = 240
    static let glanceShiftX: CGFloat = 2.5
    static let glanceShiftY: CGFloat = 1.5
    static let glanceHold: Double = 0.9
    /// Дыхание во сне: подъём на 1 pt и обратно за этот цикл.
    static let breathingCycle: Double = 2.8
    static let breathingDepth: CGFloat = 1
    /// Ритм дня: утренняя реплика — при первой активности с 5:00 до вечера,
    /// вечерняя — после 18:00, если утром он уже здоровался.
    static let morningFromHour: Int = 5
    static let eveningFromHour: Int = 18
    static let rhythmNoticeDuration: Double = 10.0

    // MARK: - Отладка и фикстуры

    /// FOCUS_DEBUG=1 — крыло красное и вытянуто вниз, иначе его не отличить
    /// от «приложение не запустилось».
    static let isDebug = ProcessInfo.processInfo.environment["FOCUS_DEBUG"] == "1"
    static var debugExtraHeight: CGFloat { isDebug ? 28 : 0 }
    static let debugScale: CGFloat = 1
}
