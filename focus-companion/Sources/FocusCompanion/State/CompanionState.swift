import Foundation

/// Эмоциональное/функциональное состояние персонажа.
///
/// Фаза 3 переключает состояние только вручную, из debug-подменю в
/// `AppDelegate` (см. критерий приёмки в PHASE-3-PROMPT.md). Реальные
/// триггеры — начало записи, обработка, напоминание о задаче — появятся в
/// Фазах 4-5, когда возникнет что триггерить.
enum CompanionState: String, CaseIterable {
    case idle
    case listening
    case thinking
    case reminding
    case annoyed
    case celebrating
    case burning

    /// Человекочитаемое имя — для debug-меню и заготовки раскрытой панели
    /// (`ExpandedPanelView`).
    ///
    /// `.annoyed` намеренно назван "Скучает", а не "Раздражён"/"Недоволен" —
    /// см. раздел «Тон персонажа» в PHASE-3-PROMPT.md: это самоироничная
    /// скука персонажа, а не укор пользователю.
    var displayName: String {
        switch self {
        case .idle: return "Спокоен"
        case .listening: return "Слушает"
        case .thinking: return "Думает"
        case .reminding: return "Напоминает"
        case .annoyed: return "Скучает"
        case .celebrating: return "Радуется"
        case .burning: return "Горит фитиль"
        }
    }
}
