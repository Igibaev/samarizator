import Foundation

/// Передача со встречи от Samarizator — файл `inbox/<meetingId>.json` в папке
/// приложения (см. `HandoffInbox`). Пишет его Python-сторона
/// (`src/samarizator/handoff.py`) после создания сводки — и из GUI Samarizator,
/// и из headless-входа `samarizator.companion summarize`, потому что хук стоит
/// в самом `worker summary`. Формат — контракт между двумя программами: поля
/// менять только вместе с Python-стороной и с ростом `version`.
///
/// Файл живёт долго (см. `CharacterConfig.handoffRetentionDays`): кроме
/// одноразового сообщения «принёс дела» он даёт странице «Записи» короткие
/// формулировки для кнопки «В фокус» (`TaskPanelController.proposedTitle`).
struct MeetingHandoff: Codable, Identifiable, Equatable {

    var id: String { meetingId }

    let version: Int
    let meetingId: String
    let title: String
    let createdAt: Date

    /// Путь к заметке встречи в хранилище Obsidian, если экспорт был.
    /// Только хранится: открытие заметки из компаньона не входит в объём
    /// (design.md §10 — Obsidian вне подтверждённой спецификации).
    let note: String?

    /// Реплика персонажа, с которой он приносит дела.
    let line: CompanionLine

    /// Согласованные поручения с короткими формулировками.
    let proposals: [TaskProposal]
}

/// Поручение со встречи. `text` — формулировка для слота (её сочинила модель
/// или, если та была недоступна, это `sourceText` с именем ответственного
/// впереди); `sourceText` — дословный пункт сводки, по нему предложение
/// находится для строки саммари на странице «Записи».
struct TaskProposal: Codable, Identifiable, Equatable {
    let id: UUID
    let text: String
    let sourceText: String
    let owner: String?
    let due: String?
}

/// Структурированный ответ персонажа — `{ text, emotion, intensity }` из
/// библии персонажа (docs/focus-companion/CHARACTER-BIBLE.md).
struct CompanionLine: Codable, Equatable {
    let text: String
    let emotion: CompanionEmotion

    /// 0...1 — насколько сильно персонаж это чувствует. Ниже
    /// `CharacterConfig.handoffReactionThreshold` глаза не реагируют вовсе:
    /// остаётся только текст.
    let intensity: Double
}

/// Эмоции, которые персонаж может сообщить о себе в реплике. Это словарь
/// ПРОМТА, и он сознательно узкий: из библиотеки эмоций design.md §12 для
/// реплики уместны только две временные реакции — любопытство и радость.
/// Остальные (злость, грусть, подметание...) принадлежат утилизации задач и
/// в чужой сценарий не берутся.
enum CompanionEmotion: String, Codable {
    case calm
    case curious
    case happy

    /// Незнакомое значение от модели — не ошибка всей передачи: дела важнее,
    /// чем точность эмоции.
    init(from decoder: Decoder) throws {
        let raw = try decoder.singleValueContainer().decode(String.self)
        self = CompanionEmotion(rawValue: raw) ?? .curious
    }

    /// Временная реакция глаз (`CompanionStateMachine.react`). `nil` — без
    /// реакции, только текст на полоске.
    var reaction: CompanionState? {
        switch self {
        case .calm: return nil
        case .curious: return .curious
        case .happy: return .happy
        }
    }
}
