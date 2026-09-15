import Foundation
import Observation

/// Хранилище задач — Фаза 4а: три активных слота, чекбокс выполнения,
/// лёгкая ретенция выполненных. Фаза 4б добавляет фитиль: поджиг/гашение и
/// уборку догоревших задач.
///
/// Держит все задачи одним массивом в памяти и целиком пишет его в JSON при
/// каждом изменении. Для трёх активных задач это заведомо дешевле, чем любая
/// база, и не требует ни схемы, ни миграций.
@MainActor
@Observable
final class TaskStore {

    /// Правило трёх слотов — прямое требование ТЗ.
    static let maxActiveSlots = 3

    /// Сколько последних выполненных задач держать, чтобы список не копился
    /// бесконечно. Это простая уборка, а НЕ механика «сжигания» (та — Фаза
    /// 4б, у активных задач и с другим смыслом: осознанное отпускание).
    private static let maxCompletedKept = 8

    private var tasks: [CompanionTask]

    var activeTasks: [CompanionTask] {
        tasks.filter { !$0.isDone }.sorted { $0.createdAt < $1.createdAt }
    }

    var completedTasks: [CompanionTask] {
        tasks.filter(\.isDone).sorted { $0.createdAt > $1.createdAt }
    }

    /// Горит ли фитиль хотя бы у одной задачи — читает `TaskPanelController`,
    /// чтобы решить фоновое состояние персонажа (`.burning` vs `.idle`,
    /// Фаза 4б).
    var hasBurningTasks: Bool {
        tasks.contains { !$0.isDone && $0.fuseDate != nil }
    }

    init(tasks: [CompanionTask] = TaskPersistence.load()) {
        self.tasks = tasks
        // Обязательный случай из PHASE-4B-PROMPT.md: приложение могло быть
        // закрыто, пока фитиль горел. Такие задачи отпускаются ЗДЕСЬ, ДО
        // того как до них доберётся UI или машина состояний — тихо, без
        // единой эмоции персонажа и без сообщения о пропущенном. Человек
        // вернулся к компьютеру, а не к отчёту о потерях.
        releaseBurnedOut(now: Date())
    }

    /// Добавляет задачу. `nil`, если все три слота заняты — вызывающая
    /// сторона (`TaskPanelController`) отвечает за заботливое сообщение об
    /// этом в UI, здесь только правило.
    @discardableResult
    func add(text: String) -> CompanionTask? {
        guard activeTasks.count < Self.maxActiveSlots else { return nil }

        let task = CompanionTask(text: text)
        tasks.append(task)
        persist()
        return task
    }

    func complete(_ task: CompanionTask) {
        guard let index = tasks.firstIndex(where: { $0.id == task.id }) else { return }
        tasks[index].isDone = true
        tasks[index].completedAt = Date()
        // Выполненная задача не может одновременно ещё и гореть — фитиль
        // сброшен, чтобы `releaseBurnedOut` её не подобрал по ошибке (она и
        // так уже отфильтрована через `!isDone`, это просто гигиена данных).
        tasks[index].fuseDate = nil
        tasks[index].fuseStartedAt = nil
        trimCompleted()
        persist()
    }

    // MARK: - Фитиль (Фаза 4б)

    /// Поджигает фитиль — задаче назначается момент, когда она уйдёт сама.
    func igniteFuse(_ task: CompanionTask, duration: TimeInterval) {
        guard let index = tasks.firstIndex(where: { $0.id == task.id }), !tasks[index].isDone else { return }
        let now = Date()
        tasks[index].fuseStartedAt = now
        tasks[index].fuseDate = now.addingTimeInterval(duration)
        persist()
    }

    /// Гасит фитиль — пользователь передумал отпускать задачу по дороге.
    /// Необратим только сам финал (задача ушла), не поджиг как таковой —
    /// прямое требование PHASE-4B-PROMPT.md.
    func extinguishFuse(_ task: CompanionTask) {
        guard let index = tasks.firstIndex(where: { $0.id == task.id }) else { return }
        tasks[index].fuseDate = nil
        tasks[index].fuseStartedAt = nil
        persist()
    }

    /// Убирает задачи, чей фитиль догорел к моменту `now`. Задача при этом
    /// НЕ становится "выполненной" — она целиком уходит из хранилища, минуя
    /// `completedTasks`: отпускание не достижение и не должно попадать ни в
    /// какую статистику (см. HANDOFF.md: "никаких счётчиков отпущенных
    /// задач"). Возвращает число отпущенных — вызывающая сторона
    /// (`TaskPanelController`) решает, нужна ли (и какая) реакция персонажа.
    @discardableResult
    func releaseBurnedOut(now: Date) -> Int {
        let burnedOutIDs = tasks
            .filter { !$0.isDone && ($0.fuseDate.map { $0 <= now } ?? false) }
            .map(\.id)
        guard !burnedOutIDs.isEmpty else { return 0 }
        tasks.removeAll { burnedOutIDs.contains($0.id) }
        persist()
        return burnedOutIDs.count
    }

    private func trimCompleted() {
        let stale = completedTasks.dropFirst(Self.maxCompletedKept).map(\.id)
        guard !stale.isEmpty else { return }
        tasks.removeAll { stale.contains($0.id) }
    }

    private func persist() {
        TaskPersistence.save(tasks)
    }
}
