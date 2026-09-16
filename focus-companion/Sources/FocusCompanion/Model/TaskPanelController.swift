import Foundation
import Observation

/// Логика панели фокуса: три слота, сроки, напоминания, утилизация, история.
@MainActor
@Observable
final class TaskPanelController {

    let store: TaskStore
    private let stateMachine: CompanionStateMachine
    private let taskBag = TaskBag()

    /// Текущее время, которым считаются остатки. Обновляется тиком раз в
    /// секунду — так линия срока не требует непрерывной перерисовки.
    private(set) var now: Date = Date()

    /// Сообщение «слоты заняты» и связанный с ним выбор замены.
    private(set) var slotsFullMessage: String?

    /// Задача, которая только что сгорела, и окно «Вернуть» на 8 секунд.
    private(set) var recentlyExpired: CompanionTask?
    /// Эффект, который сейчас играется. `nil` — ничего не играется.
    private(set) var runningDisposal: (taskID: UUID, effect: DisposalEffect)?

    /// Подпись напоминания, живёт 4 секунды.
    private(set) var reminderCaption: String?

    /// Одно спокойное уведомление после сна: «Пока вас не было, истёк срок N задач».
    private(set) var catchUpMessage: String?

    /// Замена при восстановлении из истории: какую задачу возвращаем.
    var pendingRestoreID: UUID?

    var mood: CharacterMood = CompanionSettings.mood {
        didSet { CompanionSettings.mood = mood }
    }

    var preferredDisposal: DisposalEffect = CompanionSettings.disposalEffect {
        didSet { CompanionSettings.disposalEffect = preferredDisposal }
    }

    var reduceMotion = false

    /// Сколько напоминаний уже было показано — для лимита в режиме «Чаще».
    private var reminderTimestamps: [Date] = []
    private var lastNoticeableReminder: Date?
    /// Фикстура заморозила данные: не трогаем диск и не гасим задачи по сроку.
    private(set) var isFixtureActive = false

    enum AddOutcome: Equatable {
        case added
        case slotsFull
        case empty
    }

    init(store: TaskStore, stateMachine: CompanionStateMachine) {
        self.store = store
        self.stateMachine = stateMachine
        catchUpAfterSleep()
        startDeadlineTicker()
        startReminderTicker()
    }

    deinit { taskBag.cancelAll() }

    // MARK: - Добавление и правка

    @discardableResult
    func addTask(title: String, note: String = "", minutes: Int = CharacterConfig.defaultDeadlineMinutes) -> AddOutcome {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return .empty }
        guard store.add(title: trimmed, note: note, duration: Double(minutes) * 60) != nil else {
            slotsFullMessage = "Три дела уже в фокусе. Освободите слот, чтобы добавить новое."
            return .slotsFull
        }
        slotsFullMessage = nil
        return .added
    }

    /// Единственное базовое подтверждение выполнения — нажатие пользователя.
    /// Сам компаньон задачу выполненной не отмечает.
    func complete(_ task: CompanionTask) {
        store.complete(task.id)
        slotsFullMessage = nil
        stateMachine.react(.happy)
    }

    func archive(_ task: CompanionTask) {
        // Спокойный уход в историю, без наказательной эмоции.
        store.archive(task.id)
        slotsFullMessage = nil
    }

    /// «Перенести на N минут» — видимое действие, а не скрытая правка.
    func reschedule(_ task: CompanionTask, byMinutes minutes: Int) {
        store.reschedule(task.id, by: Double(minutes) * 60)
    }

    func setDeadline(_ task: CompanionTask, to date: Date) {
        store.setExpiresAt(task.id, to: date)
    }

    func move(_ task: CompanionTask, to index: Int) {
        store.move(task.id, to: index)
    }

    // MARK: - История и восстановление

    /// Восстановление требует нового срока и свободного слота.
    @discardableResult
    func restore(_ task: CompanionTask, minutes: Int = CharacterConfig.defaultDeadlineMinutes) -> TaskStore.RestoreOutcome {
        let outcome = store.restore(task.id, duration: Double(minutes) * 60)
        if case .needsSlot = outcome {
            pendingRestoreID = task.id
            slotsFullMessage = "Все три слота заняты. Выберите, какую задачу заменить."
        }
        if case .restored = outcome {
            pendingRestoreID = nil
            slotsFullMessage = nil
            recentlyExpired = nil
        }
        return outcome
    }

    @discardableResult
    func restore(_ task: CompanionTask, replacing victim: CompanionTask, minutes: Int = CharacterConfig.defaultDeadlineMinutes) -> TaskStore.RestoreOutcome {
        let outcome = store.restore(task.id, replacing: victim.id, duration: Double(minutes) * 60)
        pendingRestoreID = nil
        slotsFullMessage = nil
        return outcome
    }

    func dismissRestorePrompt() {
        pendingRestoreID = nil
        slotsFullMessage = nil
    }

    // MARK: - Сроки и утилизация

    private func startDeadlineTicker() {
        taskBag.replace(.deadlineTicker, with: Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(CharacterConfig.deadlineTickInterval * 1_000_000_000))
                guard !Task.isCancelled, let self else { return }
                self.tickDeadlines(now: Date())
            }
        })
    }

    /// Один шаг проверки сроков. Сначала СОХРАНЯЕТСЯ статус, потом играется
    /// эффект: сбой анимации не имеет права потерять задачу.
    func tickDeadlines(now moment: Date) {
        now = moment
        guard !isFixtureActive else { return }
        let expired = store.expireOverdue(now: moment)
        guard !expired.isEmpty else { return }
        slotsFullMessage = nil
        // Одновременно истёкшие задачи меняют статус вместе, но эффект —
        // ОДИН эпизод до 1.6 с, а не три наказания подряд.
        playDisposal(for: expired)
    }

    /// Запускает ровно один эффект на событие.
    func playDisposal(for tasks: [CompanionTask]) {
        guard let first = tasks.first else { return }
        recentlyExpired = first
        startUndoWindow()

        guard let effect = mood.effect(preferred: preferredDisposal), !reduceMotion else {
            // «Без эмоций» и Reduce Motion: обычное изменение статуса.
            // Кнопка «Вернуть» и запись в истории остаются в обоих случаях.
            return
        }
        runningDisposal = (taskID: first.id, effect: effect)
        stateMachine.react(effect.emotion, duration: effect.duration)
        taskBag.replace(.disposal, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(effect.duration * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.runningDisposal = nil
        })
    }

    private func startUndoWindow() {
        taskBag.replace(.undoWindow, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(CharacterConfig.undoWindow * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.recentlyExpired = nil
        })
    }

    func dismissExpiredNotice() {
        recentlyExpired = nil
        taskBag.cancel(.undoWindow)
    }

    func dismissCatchUpMessage() {
        catchUpMessage = nil
    }

    /// После сна или перезапуска сверяем абсолютные сроки, а не досчитываем
    /// таймеры: накопившиеся вспышки, слёзы и огонь не воспроизводятся.
    private func catchUpAfterSleep() {
        let expired = store.expireOverdue(now: Date())
        guard !expired.isEmpty else { return }
        catchUpMessage = "Пока вас не было, истёк срок \(expired.count) "
            + Self.plural(expired.count, one: "задачи", few: "задач", many: "задач") + "."
    }

    private static func plural(_ count: Int, one: String, few: String, many: String) -> String {
        let mod100 = count % 100
        if (11...14).contains(mod100) { return many }
        switch count % 10 {
        case 1: return one
        case 2, 3, 4: return few
        default: return many
        }
    }

    // MARK: - Напоминания (design.md §11.3)

    private func startReminderTicker() {
        taskBag.replace(.reminderTicker, with: Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(CharacterConfig.reminderTickInterval * 1_000_000_000))
                guard !Task.isCancelled, let self else { return }
                self.tickReminders(now: Date())
            }
        })
    }

    /// Первый тихий сигнал на 50% интервала, второй — за 5 минут до срока.
    /// Пропущенные сигналы объединяются, а не ставятся в очередь.
    func tickReminders(now moment: Date) {
        guard !store.activeTasks.isEmpty else { return }
        guard !isQuietHour(moment) else { return }
        guard !isSuppressed else { return }

        let cooldown = CompanionSettings.frequentReminders
            ? CharacterConfig.reminderFrequentCooldown
            : CharacterConfig.reminderGlobalCooldown
        if let last = lastNoticeableReminder, moment.timeIntervalSince(last) < cooldown { return }
        if CompanionSettings.frequentReminders {
            reminderTimestamps.removeAll { moment.timeIntervalSince($0) > 3600 }
            guard reminderTimestamps.count < CharacterConfig.reminderFrequentHourlyLimit else { return }
        }

        guard let (task, index, text) = dueReminder(now: moment) else { return }
        lastNoticeableReminder = moment
        reminderTimestamps.append(moment)
        store.markReminded(task.id, at: moment)
        reminderCaption = text
        // Взгляд вниз к нужной строке — по её позиции в полке.
        stateMachine.react(
            .reminder,
            gazeOffset: CGSize(width: 0, height: 2 + CGFloat(index)),
            duration: CharacterConfig.reminderDuration
        )
        taskBag.replace(.reminderCaption, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(CharacterConfig.reminderCaptionDuration * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.reminderCaption = nil
        })
    }

    /// Ищет задачу, которой пора напомнить. Возвращает её, номер строки и текст.
    private func dueReminder(now moment: Date) -> (CompanionTask, Int, String)? {
        for (index, task) in store.activeTasks.enumerated() {
            let total = task.expiresAt.timeIntervalSince(task.startedAt)
            let remaining = task.remainingSeconds(now: moment)
            guard remaining > 0 else { continue }

            let secondDue = remaining <= CharacterConfig.reminderSecondLeadTime
            let firstDue = total > 0 && remaining <= total * CharacterConfig.reminderFirstFraction

            // Второй сигнал имеет право повториться после первого, поэтому
            // сравниваем с моментом последнего напоминания именно этой задачи.
            if secondDue, task.lastRemindedAt.map({ $0 < task.expiresAt.addingTimeInterval(-CharacterConfig.reminderSecondLeadTime) }) ?? true {
                return (task, index, "До срока 5 минут")
            }
            if firstDue, task.lastRemindedAt == nil {
                return (task, index, "Вернёмся к задаче «\(task.title)»?")
            }
        }
        return nil
    }

    /// Громкие реакции подавляются в «Не беспокоить», при наборе текста в
    /// панели и при демонстрации экрана — если состояние достоверно известно.
    var isSuppressed = false

    private func isQuietHour(_ date: Date) -> Bool {
        let hour = Calendar.current.component(.hour, from: date)
        let start = CharacterConfig.reminderQuietHourStart
        let end = CharacterConfig.reminderQuietHourEnd
        if start > end { return hour >= start || hour < end }
        return hour >= start && hour < end
    }

    /// «Не напоминать 30 минут»: подавляет сигналы, но НЕ меняет срок задачи.
    func snoozeReminders(minutes: Int = 30) {
        lastNoticeableReminder = Date().addingTimeInterval(
            Double(minutes) * 60 - CharacterConfig.reminderGlobalCooldown
        )
    }

    // MARK: - Фикстуры

    /// Загружает воспроизводимое состояние: данные замораживаются, диск не
    /// трогается, реальных сроков ждать не нужно.
    func loadFixture(_ fixture: CompanionFixture) {
        isFixtureActive = true
        now = Date()
        store.replaceForFixture(fixture.tasks(now: now))
        recentlyExpired = nil
        runningDisposal = nil
        slotsFullMessage = nil
        reminderCaption = nil
        catchUpMessage = nil
        if fixture == .expiredWithUndo {
            recentlyExpired = store.history.first { $0.status == .expired }
            if let expired = recentlyExpired {
                playDisposal(for: [expired])
            }
        }
    }

    /// Возврат к настоящим данным пользователя.
    func leaveFixture() {
        isFixtureActive = false
        store.replaceForFixture(TaskPersistence.load())
        now = Date()
    }
}
