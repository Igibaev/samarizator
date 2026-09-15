import Foundation

/// Пресет длительности фитиля — Фаза 4б.
///
/// Поджиг в `ExpandedPanelView` всегда идёт через явный выбор ОДНОГО из
/// этих трёх пунктов меню, а не одиночный клик по иконке: отпускание задачи
/// в итоге необратимо, и случайное нажатие не должно его запускать (прямое
/// требование PHASE-4B-PROMPT.md).
enum FusePreset: CaseIterable, Identifiable {
    case fifteenMinutes
    case oneHour
    case endOfDay

    var id: Self { self }

    /// Подпись пункта меню — без цифр секунд/минут отсчёта после поджига,
    /// только здесь, в момент выбора длительности.
    var label: String {
        switch self {
        case .fifteenMinutes: return "15 минут"
        case .oneHour: return "Час"
        case .endOfDay: return "До конца дня"
        }
    }

    /// Длительность фитиля от момента `now`, секунды.
    ///
    /// `.endOfDay` — единственный пресет без фиксированного числа: конец
    /// дня всегда считается от текущего момента до ближайшей полуночи по
    /// календарю пользователя, поэтому принимает `now`, а не хранит готовое
    /// значение, как два других пресета (там просто отдаёт константу из
    /// `CharacterConfig`).
    func duration(from now: Date, calendar: Calendar = .current) -> TimeInterval {
        switch self {
        case .fifteenMinutes:
            return CharacterConfig.fuseShortDuration
        case .oneHour:
            return CharacterConfig.fuseLongDuration
        case .endOfDay:
            let startOfNextDay = calendar.startOfDay(for: now).addingTimeInterval(24 * 60 * 60)
            // Подстраховка на случай поджига в последнюю минуту суток —
            // фитиль не должен быть короче тика таймера (1с).
            return max(CharacterConfig.burnTickInterval, startOfNextDay.timeIntervalSince(now))
        }
    }
}
