import Foundation

/// Статусы, которые реально сохраняются (design.md §11.2, §14).
enum TaskStatus: String, Codable, CaseIterable {
    /// В одном из трёх слотов.
    case active
    /// Пользователь отметил выполнение.
    case completed
    /// Срок наступил, задача не завершена.
    case expired
    /// Пользователь убрал задачу раньше срока — без наказательной эмоции.
    case archived

    var displayName: String {
        switch self {
        case .active: return "В фокусе"
        case .completed: return "Выполнена"
        case .expired: return "Сгорела"
        case .archived: return "Убрана"
        }
    }
}

/// Минимальные данные задачи из design.md §14.
///
/// Срок абсолютный (`expiresAt`), а не «сколько осталось»: после сна или
/// перезапуска состояние восстанавливается сверкой с текущим временем,
/// а не досчитыванием таймера.
struct CompanionTask: Identifiable, Codable, Equatable {
    let id: UUID
    var title: String
    var note: String
    var startedAt: Date
    var expiresAt: Date
    var status: TaskStatus
    /// Порядок закреплён пользователем: автоматической перестановки при
    /// приближении срока нет (design.md §6.3).
    var order: Int
    var completedAt: Date?
    var expiredAt: Date?
    var lastRemindedAt: Date?
    /// Встреча Samarizator, с которой пришло дело, и исходная формулировка
    /// пункта сводки. По ним `samarizator.journal` находит поручение в
    /// «Поручения.md»: заголовок персонаж переписывает, по нему не сверить.
    var meetingId: String?
    var sourceText: String?

    init(
        id: UUID = UUID(),
        title: String,
        note: String = "",
        startedAt: Date = Date(),
        expiresAt: Date,
        status: TaskStatus = .active,
        order: Int = 0,
        completedAt: Date? = nil,
        expiredAt: Date? = nil,
        lastRemindedAt: Date? = nil,
        meetingId: String? = nil,
        sourceText: String? = nil
    ) {
        self.id = id
        self.title = title
        self.note = note
        self.startedAt = startedAt
        self.expiresAt = expiresAt
        self.status = status
        self.order = order
        self.completedAt = completedAt
        self.expiredAt = expiredAt
        self.lastRemindedAt = lastRemindedAt
        self.meetingId = meetingId
        self.sourceText = sourceText
    }

    // MARK: - Производные признаки

    /// Доля ОСТАВШЕГОСЯ времени: `clamp((expiresAt − now) / (expiresAt − startedAt), 0, 1)`.
    /// Это не оценка готовности работы (design.md §11.2).
    func remainingFraction(now: Date) -> Double {
        let total = expiresAt.timeIntervalSince(startedAt)
        guard total > 0 else { return 0 }
        let remaining = expiresAt.timeIntervalSince(now)
        return min(1, max(0, remaining / total))
    }

    func remainingSeconds(now: Date) -> TimeInterval {
        expiresAt.timeIntervalSince(now)
    }

    /// `dueSoon` — вычисляемый визуальный признак, не отдельный статус.
    func isDueSoon(now: Date) -> Bool {
        guard status == .active else { return false }
        let remaining = remainingSeconds(now: now)
        guard remaining > 0 else { return false }
        let total = expiresAt.timeIntervalSince(startedAt)
        let byFraction = total > 0 && remaining <= total * CharacterConfig.dueSoonFraction
        return remaining <= CharacterConfig.dueSoonAbsolute || byFraction
    }

    func isExpired(now: Date) -> Bool {
        status == .active && expiresAt <= now
    }

    /// Человеческий остаток: «18 мин», «1 ч 20 мин», «срок вышел».
    func remainingLabel(now: Date) -> String {
        let remaining = remainingSeconds(now: now)
        guard remaining > 0 else { return "срок вышел" }
        let minutes = Int((remaining / 60).rounded(.up))
        if minutes < 60 { return "\(minutes) мин" }
        let hours = minutes / 60
        let rest = minutes % 60
        return rest == 0 ? "\(hours) ч" : "\(hours) ч \(rest) мин"
    }
}

// MARK: - Совместимость со старым файлом задач

extension CompanionTask {
    private enum CodingKeys: String, CodingKey {
        case id, title, note, startedAt, expiresAt, status, order
        case completedAt, expiredAt, lastRemindedAt
        case meetingId, sourceText
        // Ключи Фаз 4а/4б — читаются, но больше не пишутся.
        case text, createdAt, isDone, fuseDate, fuseStartedAt
    }

    /// Ручной декодер вместо синтезированного: на диске у пользователя может
    /// лежать файл прошлой схемы (`text` / `isDone` / `fuseDate`), и терять
    /// его задачи из-за смены модели нельзя.
    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decodeIfPresent(UUID.self, forKey: .id) ?? UUID()
        note = try container.decodeIfPresent(String.self, forKey: .note) ?? ""
        order = try container.decodeIfPresent(Int.self, forKey: .order) ?? 0
        completedAt = try container.decodeIfPresent(Date.self, forKey: .completedAt)
        expiredAt = try container.decodeIfPresent(Date.self, forKey: .expiredAt)
        lastRemindedAt = try container.decodeIfPresent(Date.self, forKey: .lastRemindedAt)
        meetingId = try container.decodeIfPresent(String.self, forKey: .meetingId)
        sourceText = try container.decodeIfPresent(String.self, forKey: .sourceText)

        if let title = try container.decodeIfPresent(String.self, forKey: .title) {
            self.title = title
        } else {
            self.title = try container.decodeIfPresent(String.self, forKey: .text) ?? "Без названия"
        }

        let legacyCreatedAt = try container.decodeIfPresent(Date.self, forKey: .createdAt)
        let legacyFuseStart = try container.decodeIfPresent(Date.self, forKey: .fuseStartedAt)
        let started = try container.decodeIfPresent(Date.self, forKey: .startedAt)
        startedAt = started ?? legacyFuseStart ?? legacyCreatedAt ?? Date()

        let legacyFuseDate = try container.decodeIfPresent(Date.self, forKey: .fuseDate)
        if let expires = try container.decodeIfPresent(Date.self, forKey: .expiresAt) {
            expiresAt = expires
        } else {
            // У старой задачи без подожжённого фитиля срока не было вовсе.
            // Срок обязателен, поэтому назначаем стандартный час от начала —
            // и это видно пользователю в списке, а не происходит молча.
            expiresAt = legacyFuseDate
                ?? startedAt.addingTimeInterval(Double(CharacterConfig.defaultDeadlineMinutes) * 60)
        }

        if let status = try container.decodeIfPresent(TaskStatus.self, forKey: .status) {
            self.status = status
        } else {
            let wasDone = try container.decodeIfPresent(Bool.self, forKey: .isDone) ?? false
            self.status = wasDone ? .completed : .active
        }
    }

    /// Пишем только новую схему — старые ключи не воскрешаем.
    func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(title, forKey: .title)
        try container.encode(note, forKey: .note)
        try container.encode(startedAt, forKey: .startedAt)
        try container.encode(expiresAt, forKey: .expiresAt)
        try container.encode(status, forKey: .status)
        try container.encode(order, forKey: .order)
        try container.encodeIfPresent(completedAt, forKey: .completedAt)
        try container.encodeIfPresent(expiredAt, forKey: .expiredAt)
        try container.encodeIfPresent(lastRemindedAt, forKey: .lastRemindedAt)
        try container.encodeIfPresent(meetingId, forKey: .meetingId)
        try container.encodeIfPresent(sourceText, forKey: .sourceText)
    }
}
