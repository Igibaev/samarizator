import Foundation

/// Задачи и история лежат в обычном JSON рядом с настройками приложения.
///
/// SwiftData здесь принципиально не используется: её `@Model` — макрос,
/// реализованный плагином компилятора Xcode, а приложение собирается
/// `swift build` из терминала (см. docs/focus-companion/HANDOFF.md).
enum TaskPersistence {
    static let bundleIdentifier = "dev.samarizator.focuscompanion"

    static var directoryURL: URL {
        let fileManager = FileManager.default
        let base = (try? fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )) ?? fileManager.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support")
        let directory = base.appendingPathComponent(bundleIdentifier, isDirectory: true)
        try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    static var fileURL: URL { directoryURL.appendingPathComponent("tasks.json") }

    static func load() -> [CompanionTask] {
        guard let data = try? Data(contentsOf: fileURL) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return (try? decoder.decode([CompanionTask].self, from: data)) ?? []
    }

    static func save(_ tasks: [CompanionTask]) {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        guard let data = try? encoder.encode(tasks) else { return }
        try? data.write(to: fileURL, options: .atomic)
    }
}

/// Пользовательские настройки компаньона, которые нельзя терять между запусками.
enum CompanionSettings {
    private static let defaults = UserDefaults.standard

    private enum Key {
        static let mood = "companion.mood"
        static let disposal = "companion.disposalEffect"
        static let clipboardHistory = "companion.clipboardHistoryEnabled"
        static let drawerPinned = "companion.drawerPinned"
        static let gestureHintsShown = "companion.gestureHintsShown"
        static let frequentReminders = "companion.frequentReminders"
        static let demoMode = "companion.demoMode"
        static let lastHandoffSeenAt = "companion.lastHandoffSeenAt"
        static let liveliness = "companion.liveliness"
        static let lastMorningDay = "companion.lastMorningDay"
        static let lastEveningDay = "companion.lastEveningDay"
    }

    static var mood: CharacterMood {
        get { CharacterMood(rawValue: defaults.string(forKey: Key.mood) ?? "") ?? .expressive }
        set { defaults.set(newValue.rawValue, forKey: Key.mood) }
    }

    static var disposalEffect: DisposalEffect {
        get { DisposalEffect(rawValue: defaults.string(forKey: Key.disposal) ?? "") ?? .burn }
        set { defaults.set(newValue.rawValue, forKey: Key.disposal) }
    }

    /// История буфера включается ЯВНЫМ действием пользователя (design.md §9.2).
    static var clipboardHistoryEnabled: Bool {
        get { defaults.bool(forKey: Key.clipboardHistory) }
        set { defaults.set(newValue, forKey: Key.clipboardHistory) }
    }

    /// Закрепление правой панели включено по умолчанию (design.md §9.1).
    static var drawerPinned: Bool {
        get { defaults.object(forKey: Key.drawerPinned) as? Bool ?? true }
        set { defaults.set(newValue, forKey: Key.drawerPinned) }
    }

    /// Подсказка про жесты показывается первые два раза, потом скрывается.
    static var gestureHintsShown: Int {
        get { defaults.integer(forKey: Key.gestureHintsShown) }
        set { defaults.set(newValue, forKey: Key.gestureHintsShown) }
    }

    static var frequentReminders: Bool {
        get { defaults.bool(forKey: Key.frequentReminders) }
        set { defaults.set(newValue, forKey: Key.frequentReminders) }
    }

    /// Момент создания последней передачи со встречи (`MeetingHandoff`), о
    /// которой персонаж уже сказал. Всё, что создано позже, — новое.
    static var lastHandoffSeenAt: Date {
        get { defaults.object(forKey: Key.lastHandoffSeenAt) as? Date ?? .distantPast }
        set { defaults.set(newValue, forKey: Key.lastHandoffSeenAt) }
    }

    /// «Живость»: сон, пробуждение, мелкие движения и реплики утром и вечером.
    /// Выключена по умолчанию, пока базовое поведение не проверено на Mac.
    static var liveliness: Bool {
        get { defaults.bool(forKey: Key.liveliness) }
        set { defaults.set(newValue, forKey: Key.liveliness) }
    }

    /// День (ГГГГ-ММ-ДД), в который уже прозвучала утренняя / вечерняя реплика.
    static var lastMorningDay: String {
        get { defaults.string(forKey: Key.lastMorningDay) ?? "" }
        set { defaults.set(newValue, forKey: Key.lastMorningDay) }
    }

    static var lastEveningDay: String {
        get { defaults.string(forKey: Key.lastEveningDay) ?? "" }
        set { defaults.set(newValue, forKey: Key.lastEveningDay) }
    }

    /// Демонстрационный режим: FOCUS_DEMO=1 или переключатель в меню.
    /// Данные из него всегда подписаны как демонстрационные.
    static var demoMode: Bool {
        get {
            if ProcessInfo.processInfo.environment["FOCUS_DEMO"] == "1" { return true }
            return defaults.bool(forKey: Key.demoMode)
        }
        set { defaults.set(newValue, forKey: Key.demoMode) }
    }
}
