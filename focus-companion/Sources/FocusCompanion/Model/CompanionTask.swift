import Foundation

/// Задача компаньона.
///
/// Обычная Codable-структура, а НЕ SwiftData-модель: макрос `@Model`
/// реализован плагином компилятора (`SwiftDataMacros`), которого нет при
/// сборке через `swift build` из терминала — ровно та же болезнь, что у
/// `#Preview` (см. HANDOFF.md). SwiftData в нашей схеме сборки, без
/// Xcode-проекта, не работает в принципе.
///
/// Для трёх активных задач это и не потеря: JSON-файл проще, не требует
/// миграций схемы и целиком читается глазами при отладке.
///
/// Имя `CompanionTask`, а не `Task` — последнее занято `Swift.Task`, который
/// в проекте используется активно (циклы моргания, саккад, дыхания).
struct CompanionTask: Identifiable, Codable, Equatable {

    let id: UUID
    var text: String
    var createdAt: Date
    var isDone: Bool

    /// Момент выполнения. `nil`, пока задача активна.
    var completedAt: Date?

    /// Поле под будущий «фитиль» (Фаза 4б: механика сжигания — осознанное
    /// отпускание задачи без вины). Заложено сейчас, чтобы потом не менять
    /// формат файла: старые записи без этого поля декодируются как `nil`.
    var fuseDate: Date?

    init(
        id: UUID = UUID(),
        text: String,
        createdAt: Date = Date(),
        isDone: Bool = false,
        completedAt: Date? = nil,
        fuseDate: Date? = nil
    ) {
        self.id = id
        self.text = text
        self.createdAt = createdAt
        self.isDone = isDone
        self.completedAt = completedAt
        self.fuseDate = fuseDate
    }
}
