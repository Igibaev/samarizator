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

    /// Момент, когда догоревший фитиль отпустит задачу саму. `nil`, пока
    /// фитиль не подожжён. Фаза 4б: механика сжигания — осознанное
    /// отпускание задачи без вины (см. HANDOFF.md, «Тон персонажа»).
    var fuseDate: Date?

    /// Момент поджига — нужен только чтобы посчитать ДОЛЮ оставшегося
    /// фитиля для полоски прогресса в строке задачи (`ExpandedPanelView`):
    /// одного `fuseDate` достаточно, чтобы понять, сколько СЕКУНД осталось,
    /// но не сколько это в процентах от исходной длительности пресета.
    /// `nil` вместе с `fuseDate == nil` — фитиль не горит.
    var fuseStartedAt: Date?

    init(
        id: UUID = UUID(),
        text: String,
        createdAt: Date = Date(),
        isDone: Bool = false,
        completedAt: Date? = nil,
        fuseDate: Date? = nil,
        fuseStartedAt: Date? = nil
    ) {
        self.id = id
        self.text = text
        self.createdAt = createdAt
        self.isDone = isDone
        self.completedAt = completedAt
        self.fuseDate = fuseDate
        self.fuseStartedAt = fuseStartedAt
    }
}
