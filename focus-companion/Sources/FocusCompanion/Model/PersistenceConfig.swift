import Foundation
import SwiftData

/// Создание единственного `ModelContainer` приложения.
///
/// Вызывается один раз в `AppDelegate` и живёт на уровне приложения (ровно
/// как того требует PHASE-4A-PROMPT.md) — не пересоздаётся при переезде
/// панели между экранами, в отличие от `NotchPanel`/`NSHostingView`.
enum PersistenceConfig {

    /// Совпадает с `CFBundleIdentifier` из `Resources/Info.plist`.
    ///
    /// Задаём его строкой, а не читаем из `Bundle.main.bundleIdentifier`:
    /// приложение может быть запущено и голым бинарником (`swift run`,
    /// см. HANDOFF.md, грабли №6) — тогда бандла с Info.plist нет вообще, и
    /// `Bundle.main.bundleIdentifier` будет `nil`. Хранилище обязано быть
    /// одним и тем же в обоих случаях запуска.
    static let bundleIdentifier = "dev.samarizator.focuscompanion"

    /// Создаёт контейнер с ЯВНО заданным путём хранения в
    /// `~/Library/Application Support/<bundle id>/`, а не полагается на
    /// путь по умолчанию — приложение собрано вручную (`build-app.sh`,
    /// без Xcode-проекта), и есть риск, что путь по умолчанию для такого
    /// бандла окажется непредсказуемым (см. PHASE-4A-PROMPT.md).
    static func makeContainer() -> ModelContainer {
        let schema = Schema([CompanionTask.self])
        let configuration = ModelConfiguration(schema: schema, url: storeURL())

        do {
            return try ModelContainer(for: schema, configurations: [configuration])
        } catch {
            // SwiftData — самая рискованная часть проекта на данный момент:
            // ни макрос `@Model`, ни создание контейнера ни разу не
            // проверялись компилятором (Linux-контейнер, нет Swift-тулчейна,
            // см. PHASE-4A-PROMPT.md). Падаем с понятным сообщением вместо
            // того, чтобы молча остаться без хранилища задач.
            fatalError("Не удалось создать SwiftData ModelContainer: \(error)")
        }
    }

    private static func storeURL() -> URL {
        let fileManager = FileManager.default
        let base = (try? fileManager.url(
            for: .applicationSupportDirectory,
            in: .userDomainMask,
            appropriateFor: nil,
            create: true
        )) ?? fileManager.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support")

        let directory = base.appendingPathComponent(bundleIdentifier, isDirectory: true)
        try? fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory.appendingPathComponent("FocusCompanion.sqlite")
    }
}
