import Foundation
import SwiftData

/// Задача персонажа — Фаза 4а.
///
/// Имя `Task` сознательно НЕ используется: конфликтует с `Swift.Task`,
/// который в проекте активно применяется (`Character/TaskBag.swift`, циклы
/// моргания/саккад/дыхания в `EyesViewModel`, опрос `HoverDetector`) — см.
/// PHASE-4A-PROMPT.md.
///
/// `@Model` автоматически делает класс `Identifiable` (через
/// `persistentModelID`) и `Observable` в контексте SwiftData — отдельно
/// объявлять эти протоколы не нужно (см. заметку о неуверенности в отчёте).
@Model
final class CompanionTask {
    var text: String
    var createdAt: Date
    var isDone: Bool

    /// Момент выполнения. `nil`, пока задача активна.
    var completedAt: Date?

    /// Поле под будущий «фитиль» (Фаза 4б: механика сжигания — задача,
    /// отпущенная без вины по истечении срока). Само сжигание сюда не
    /// входит, поле закладывается заранее, чтобы не мигрировать схему
    /// SwiftData отдельным кругом — миграции необратимо теряют данные при
    /// ошибке, а проверить их здесь всё равно нельзя (нет тулчейна).
    var fuseDate: Date?

    init(
        text: String,
        createdAt: Date = Date(),
        isDone: Bool = false,
        completedAt: Date? = nil,
        fuseDate: Date? = nil
    ) {
        self.text = text
        self.createdAt = createdAt
        self.isDone = isDone
        self.completedAt = completedAt
        self.fuseDate = fuseDate
    }
}
