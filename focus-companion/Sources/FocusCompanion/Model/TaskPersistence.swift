import Foundation

/// Чтение и запись задач в JSON-файл.
///
/// Путь задан явно, а не отдан на откуп фреймворку: приложение собирается
/// вручную в `.app` (`build-app.sh`) и может запускаться голым бинарником,
/// когда `Bundle.main.bundleIdentifier` вообще `nil`. Хранилище обязано быть
/// одним и тем же в обоих случаях запуска.
enum TaskPersistence {

    /// Совпадает с `CFBundleIdentifier` из `Resources/Info.plist`. Задан
    /// строкой намеренно — см. комментарий выше.
    static let bundleIdentifier = "dev.samarizator.focuscompanion"

    static var fileURL: URL {
        let fileManager = FileManager.default
        let base = (try? fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )) ?? fileManager.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support")

        let directory = base.appendingPathComponent(bundleIdentifier, isDirectory: true)
        try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("tasks.json")
    }

    /// Пустой список при любой ошибке — отсутствие файла при первом запуске
    /// это норма, а не сбой.
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
        // .atomic: запись через временный файл. Приложение живёт неделями и
        // может быть убито в любой момент — недописанный JSON означал бы
        // потерю всех задач разом.
        try? data.write(to: fileURL, options: .atomic)
    }
}
