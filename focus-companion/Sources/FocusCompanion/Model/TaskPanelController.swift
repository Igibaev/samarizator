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

    // MARK: - Дела со встречи (Samarizator → компаньон)

    private let inbox: HandoffInbox

    /// Все передачи из папки `inbox/`, старшая первой. Нужны странице
    /// «Записи»: короткие формулировки для кнопки «В фокус» (`proposedTitle`).
    private(set) var handoffs: [MeetingHandoff] = []

    /// Передача, о которой персонаж говорит прямо сейчас — реплика на
    /// компактной полоске под полкой задач с кнопкой «Открыть».
    private(set) var handoffNotice: MeetingHandoff?

    /// Передача, пришедшая пока человека не было за компьютером: реплика
    /// откладывается до его возвращения, а не показывается в пустоту.
    private var deferredHandoff: MeetingHandoff?

    /// «Открыть» на полоске: `NotchWindowController` открывает страницу
    /// «Записи» с этой встречей.
    var onOpenHandoff: ((String) -> Void)?

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

    /// Есть ли сейчас короткое сообщение для компактной полоски под полкой.
    var hasCompactNotice: Bool {
        recentlyExpired != nil
            || reminderCaption != nil
            || catchUpMessage != nil
            || slotsFullMessage != nil
            || handoffNotice != nil
    }

    enum AddOutcome: Equatable {
        case added
        case slotsFull
        case empty
    }

    /// `inbox` передаётся явно, а не default-аргументом: до Swift 5.10 вызов
    /// `@MainActor`-инициализатора в выражении default-аргумента — ошибка.
    init(store: TaskStore, stateMachine: CompanionStateMachine, inbox: HandoffInbox) {
        self.store = store
        self.stateMachine = stateMachine
        self.inbox = inbox
        catchUpAfterSleep()
        startDeadlineTicker()
        startReminderTicker()
        inbox.onChange = { [weak self] in
            self?.refreshInbox()
        }
        // Передачи, пришедшие пока приложение было закрыто, встречают так же,
        // как пришедшие только что: человек только что запустил компаньона,
        // то есть смотрит на экран.
        refreshInbox()
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

    // MARK: - Дела со встречи (Samarizator → компаньон)

    /// Перечитывает папку передач. Вызывается событием `HandoffInbox`, при
    /// старте и — запасным путём — тиком напоминаний раз в 30 секунд.
    func refreshInbox(now: Date = Date()) {
        handoffs = inbox.load(now: now)
        // Новая — созданная позже последней, о которой уже сказали.
        // Несколько новых разом (Samarizator догнал очередь сводок) — одна
        // реплика, о самой свежей: персонаж не говорит пять раз подряд.
        let seenAt = CompanionSettings.lastHandoffSeenAt
        guard let newest = handoffs.last(where: { $0.createdAt > seenAt }) else { return }
        CompanionSettings.lastHandoffSeenAt = newest.createdAt
        announce(newest)
    }

    /// Реплика показывается, только если человек сейчас за компьютером;
    /// иначе ждёт его возвращения (`tickReminders`).
    private func announce(_ handoff: MeetingHandoff) {
        guard IdleTimeProvider.secondsSinceLastEvent() < CharacterConfig.handoffIdleThreshold else {
            deferredHandoff = handoff
            return
        }
        presentHandoff(handoff)
    }

    /// Текст — на компактную полоску (`CompactNoticeView`), эмоция — глазам
    /// через обычный `react` с приоритетами design.md §12.2. Панели не
    /// открываются сами, системных уведомлений нет.
    private func presentHandoff(_ handoff: MeetingHandoff) {
        deferredHandoff = nil
        handoffNotice = handoff
        let line = handoff.line
        // Глаза реагируют только при заметной интенсивности и не в режиме
        // подавления (design.md §11.3: «Не беспокоить», набор текста, демонстрация).
        if let reaction = line.emotion.reaction,
           line.intensity >= CharacterConfig.handoffReactionThreshold,
           !isSuppressed {
            stateMachine.react(reaction)
        }
        taskBag.replace(.handoffNotice, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(CharacterConfig.handoffNoticeDuration * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.handoffNotice = nil
        })
    }

    func dismissHandoffNotice() {
        handoffNotice = nil
        taskBag.cancel(.handoffNotice)
    }

    /// «Открыть» на полоске: страница «Записи» с этой встречей.
    func openHandoff() {
        guard let handoff = handoffNotice else { return }
        dismissHandoffNotice()
        onOpenHandoff?(handoff.meetingId)
    }

    /// Короткая формулировка для кнопки «В фокус» у пункта саммари, если
    /// Samarizator передал её для этой записи. `nil` — брать сам пункт.
    func proposedTitle(recordingID: String?, sourceText: String) -> String? {
        guard let recordingID,
              let handoff = handoffs.last(where: { $0.meetingId == recordingID }) else { return nil }
        return handoff.proposals.first { $0.sourceText == sourceText }?.text
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
        // Дела со встречи: запасное перечитывание папки (если событие
        // `DispatchSource` не пришло) и отложенная реплика — как только человек
        // снова за компьютером. ДО правила «нет задач — молчать»: реплика не
        // напоминание, а список задач может быть пуст.
        refreshInbox(now: moment)
        if let deferred = deferredHandoff,
           IdleTimeProvider.secondsSinceLastEvent() < CharacterConfig.handoffIdleThreshold {
            presentHandoff(deferred)
        }

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
