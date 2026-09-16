import Foundation

/// Эмоции персонажа — ровно тот набор, что задан в design.md §14 («emotion»).
///
/// Эмоция читается по форме, положению и векам самих глаз: отдельных значков
/// рядом с глазами вместо эмоции не бывает (design.md §5 задания).
enum CompanionState: String, CaseIterable, Identifiable {
    /// Спокойствие и естественное моргание.
    case idle
    /// Любопытство при наведении.
    case curious
    /// Идёт запись.
    case listening
    /// Реальная обработка: транскрибация или генерация саммари.
    case thinking
    /// Напоминание со взглядом в сторону задачи.
    case reminder
    /// Радость после выполнения.
    case happy
    /// Злость — перед сгоранием.
    case angry
    /// Грусть со слезой.
    case sad
    /// Подметает крошки задачи.
    case sweeping
    /// Комкает и выбрасывает.
    case discarding
    /// Ошибка, требующая внимания.
    case error

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .idle: return "Спокоен"
        case .curious: return "Любопытен"
        case .listening: return "Записывает"
        case .thinking: return "Думает"
        case .reminder: return "Напоминает"
        case .happy: return "Радуется"
        case .angry: return "Злится"
        case .sad: return "Грустит"
        case .sweeping: return "Подметает"
        case .discarding: return "Выбрасывает"
        case .error: return "Ошибка"
        }
    }

    /// Приоритет эмоций из design.md §12.2:
    /// ошибка → прямое действие пользователя → истечение срока → выполнение →
    /// напоминание → фоновая активность. Больше — важнее.
    var priority: Int {
        switch self {
        case .error: return 60
        case .curious: return 50
        case .angry, .sad, .sweeping, .discarding: return 40
        case .happy: return 30
        case .reminder: return 20
        case .listening, .thinking: return 10
        case .idle: return 0
        }
    }

    /// Сколько держится временная реакция. `nil` — состояние фоновое.
    var duration: Double? {
        switch self {
        case .idle, .listening, .thinking: return nil
        case .curious: return CharacterConfig.curiousDuration
        case .reminder: return CharacterConfig.reminderDuration
        case .happy: return CharacterConfig.happyDuration
        case .angry: return CharacterConfig.angryDuration
        case .sad: return CharacterConfig.sadDuration
        case .sweeping: return CharacterConfig.sweepingDuration
        case .discarding: return CharacterConfig.discardingDuration
        case .error: return CharacterConfig.errorDuration
        }
    }
}
