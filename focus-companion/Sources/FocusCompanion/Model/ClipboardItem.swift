import Foundation

/// Тип элемента буфера (design.md §9.2, §14).
enum ClipboardItemType: String, Equatable {
    case text
    case link
    case image

    var symbolName: String {
        switch self {
        case .text: return "text.alignleft"
        case .link: return "link"
        case .image: return "photo"
        }
    }

    var displayName: String {
        switch self {
        case .text: return "Текст"
        case .link: return "Ссылка"
        case .image: return "Изображение"
        }
    }
}

/// `id, type, contentReference, capturedAt, pinned, sourceLabel?`
///
/// Для изображения хранится ССЫЛКА на локальный файл, а не сам бинарник:
/// большой объект в состоянии интерфейса — это подвисания при каждой
/// перерисовке списка.
struct ClipboardItem: Identifiable, Equatable {
    let id: UUID
    var type: ClipboardItemType
    /// Текст, URL или путь к локальному ресурсу.
    var contentReference: String
    var capturedAt: Date
    var pinned: Bool
    /// Источник показывается, только если он реально известен.
    var sourceLabel: String?
    /// Заголовок ссылки, если он реально известен; иначе показывается URL.
    var linkTitle: String?

    init(
        id: UUID = UUID(),
        type: ClipboardItemType,
        contentReference: String,
        capturedAt: Date = Date(),
        pinned: Bool = false,
        sourceLabel: String? = nil,
        linkTitle: String? = nil
    ) {
        self.id = id
        self.type = type
        self.contentReference = contentReference
        self.capturedAt = capturedAt
        self.pinned = pinned
        self.sourceLabel = sourceLabel
        self.linkTitle = linkTitle
    }

    /// Заголовок строки: для ссылки — домен и заголовок, при отсутствии
    /// заголовка — сам URL.
    var primaryLine: String {
        switch type {
        case .link:
            if let linkTitle, !linkTitle.isEmpty { return linkTitle }
            return contentReference
        case .image:
            return (contentReference as NSString).lastPathComponent
        case .text:
            return contentReference
                .trimmingCharacters(in: .whitespacesAndNewlines)
                .replacingOccurrences(of: "\n", with: " ")
        }
    }

    var domain: String? {
        guard type == .link, let url = URL(string: contentReference) else { return nil }
        return url.host
    }

    func matches(query: String) -> Bool {
        guard !query.isEmpty else { return true }
        let needle = query.lowercased()
        return contentReference.lowercased().contains(needle)
            || (linkTitle?.lowercased().contains(needle) ?? false)
            || (sourceLabel?.lowercased().contains(needle) ?? false)
    }
}


/// «2 мин назад», «12 мин назад», «вчера» — подпись строки буфера.
enum RelativeTime {
    static func label(for date: Date, now: Date = Date()) -> String {
        let seconds = max(0, now.timeIntervalSince(date))
        if seconds < 60 { return "только что" }
        let minutes = Int(seconds / 60)
        if minutes < 60 { return "\(minutes) мин назад" }
        let hours = minutes / 60
        if hours < 24 { return "\(hours) ч назад" }
        let days = hours / 24
        return days == 1 ? "вчера" : "\(days) дн назад"
    }
}
