import Foundation

/// Достоверный статус обработки записи (design.md §9.3).
/// Ничего похожего на «87% готово» здесь нет и быть не может.
enum RecordingStatus: String, Equatable {
    case recording
    case processing
    case ready
    case failed

    var displayName: String {
        switch self {
        case .recording: return "Запись"
        case .processing: return "Обработка"
        case .ready: return "Готово"
        case .failed: return "Ошибка"
        }
    }

    /// Перевод статусов Samarizator (таблица `meetings.status`).
    static func fromSamarizator(_ raw: String) -> RecordingStatus {
        switch raw {
        case "recording", "live": return .recording
        case "transcribing", "summarizing", "retrying", "new", "queued": return .processing
        case "error", "failed": return .failed
        default: return .ready
        }
    }
}

/// Сегмент транскрипта. Неизвестные поля остаются отсутствующими:
/// метки спикеров показываются только при наличии реальных данных.
struct TranscriptSegment: Identifiable, Equatable {
    let id: Int
    let start: Double
    let end: Double
    let text: String
    let speaker: String?
    let uncertain: Bool

    var timecode: String { TimecodeFormatter.string(from: start) }
}

/// Один пункт саммари.
enum SummaryItemKind: String, Equatable {
    case point
    case decision
    case action
    case risk
    case question

    var sectionTitle: String {
        switch self {
        case .point: return "Главное"
        case .decision: return "Решения"
        case .action: return "Следующие шаги"
        case .risk: return "Риски"
        case .question: return "Вопросы"
        }
    }
}

struct SummaryItem: Identifiable, Equatable {
    let id: String
    let kind: SummaryItemKind
    let text: String
    /// ID реплик-источников. Пусто — вывод не привязан к тексту, и в интерфейсе
    /// он подписывается нейтральным «Предположение» (design.md §9.3).
    let evidence: [String]

    var isAssumption: Bool { evidence.isEmpty }
}

/// Форматы саммари (design.md §9.3).
enum SummaryFormat: String, CaseIterable, Identifiable {
    case brief
    case detailed
    case actions

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .brief: return "Кратко"
        case .detailed: return "Подробно"
        case .actions: return "Действия"
        }
    }
}

/// Режимы просмотра записи.
enum RecordingViewMode: String, CaseIterable, Identifiable {
    case summary
    case transcript
    case sideBySide

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .summary: return "Саммари"
        case .transcript: return "Текст"
        case .sideBySide: return "Рядом"
        }
    }
}

struct RecordingSummary: Equatable {
    var overview: String
    var items: [SummaryItem]
    /// Отметка «По состоянию на 12:34» для саммари по снимку живой записи.
    var snapshotLabel: String?
    /// Саммари устарело: после него в транскрипт добавились новые фрагменты.
    var isStale: Bool

    func items(for format: SummaryFormat) -> [SummaryItem] {
        switch format {
        case .actions: return items.filter { $0.kind == .action }
        default: return items
        }
    }

    static let empty = RecordingSummary(overview: "", items: [], snapshotLabel: nil, isStale: false)
}

/// Запись: минимальные данные из design.md §14.
struct Recording: Identifiable, Equatable {
    let id: String
    var title: String
    var createdAt: Date
    /// Длительность в секундах. `nil` — пока неизвестна, и тогда в интерфейсе
    /// не пишется «0:00», а не пишется ничего.
    var duration: Double?
    var source: String
    var status: RecordingStatus
    var segments: [TranscriptSegment]
    var summaries: [SummaryFormat: RecordingSummary]
    var processingError: String?
    /// Данные получены из демонстрационного набора, а не из реальной базы.
    var isDemo: Bool = false

    var durationLabel: String? {
        guard let duration, duration > 0 else { return nil }
        return TimecodeFormatter.string(from: duration)
    }

    var hasTranscript: Bool { !segments.isEmpty }

    func summary(for format: SummaryFormat) -> RecordingSummary? {
        summaries[format] ?? summaries[.brief]
    }
}

enum TimecodeFormatter {
    /// «24 мин», «1:04:20» — табличные цифры не должны прыгать по ширине.
    static func string(from seconds: Double) -> String {
        let total = Int(seconds.rounded())
        let hours = total / 3600
        let minutes = (total % 3600) / 60
        let secs = total % 60
        if hours > 0 {
            return String(format: "%d:%02d:%02d", hours, minutes, secs)
        }
        return String(format: "%d:%02d", minutes, secs)
    }

    static func minutesLabel(from seconds: Double) -> String {
        let minutes = Int((seconds / 60).rounded())
        return "\(minutes) мин"
    }

    static func dayLabel(for date: Date, now: Date = Date(), calendar: Calendar = .current) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ru_RU")
        if calendar.isDate(date, inSameDayAs: now) {
            formatter.dateFormat = "'Сегодня', HH:mm"
        } else if let yesterday = calendar.date(byAdding: .day, value: -1, to: now),
                  calendar.isDate(date, inSameDayAs: yesterday) {
            formatter.dateFormat = "'Вчера', HH:mm"
        } else {
            formatter.dateFormat = "d MMMM, HH:mm"
        }
        return formatter.string(from: date)
    }

    static func clockLabel(for date: Date) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "ru_RU")
        formatter.dateFormat = "HH:mm"
        return formatter.string(from: date)
    }
}
