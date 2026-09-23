import Foundation

/// «Живость»: персонаж замечает человека и помнит его день.
///
/// - Человека нет дольше 5 минут — засыпает (щёлочки и дыхание), вернулся —
///   коротко просыпается. Поздно вечером глаза сонные.
/// - В покое изредка смотрит в сторону или дважды моргает.
/// - Одна реплика при первой активности дня и одна вечером — текст даёт
///   `python -m samarizator.companion rhythm`, из дневника и слотов.
///
/// Всё это выключено, пока `CompanionSettings.liveliness` не включат в меню:
/// базовое поведение сначала проверяется без него (CHECKLIST.md).
@MainActor
final class LivelinessController {

    /// Фоновое состояние покоя: `idle`, `drowsy` или `asleep`. Запись и
    /// обработка важнее — это решает `NotchWindowController.updateAmbient`.
    private(set) var resting: CompanionState = .idle
    var onRestingChange: (() -> Void)?

    /// Откуда взять Samarizator для реплик — как у `JournalSync`.
    var toolchain: () -> SamarizatorToolchain? = { nil }

    private let stateMachine: CompanionStateMachine
    private let eyes: EyesViewModel
    private let taskPanel: TaskPanelController
    private let taskBag = TaskBag()
    private var nextFidget = Date()
    private var lineProcess: CompanionProcess?

    init(stateMachine: CompanionStateMachine, eyes: EyesViewModel, taskPanel: TaskPanelController) {
        self.stateMachine = stateMachine
        self.eyes = eyes
        self.taskPanel = taskPanel
    }

    deinit { taskBag.cancelAll() }

    func start() {
        scheduleFidget(from: Date())
        taskBag.replace(.presence, with: Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(CharacterConfig.presenceTickInterval * 1_000_000_000))
                guard !Task.isCancelled, let self else { return }
                self.tick(now: Date())
            }
        })
    }

    // MARK: - Один шаг

    func tick(now: Date) {
        guard CompanionSettings.liveliness, !taskPanel.isFixtureActive else {
            setResting(.idle)
            return
        }
        let idle = IdleTimeProvider.secondsSinceLastEvent()
        let wasAsleep = resting == .asleep
        let next: CompanionState
        if idle >= CharacterConfig.asleepAfter {
            next = .asleep
        } else if isDrowsyHour(now) {
            next = .drowsy
        } else {
            next = .idle
        }
        setResting(next)

        guard next != .asleep else { return }
        if wasAsleep {
            // Одна короткая реакция на возвращение, без текста: «где ты был»
            // запрещено библией персонажа (§11).
            stateMachine.react(.waking)
        }
        let isActive = idle < CharacterConfig.activeWithin
        if isActive {
            speakIfDue(now: now)
        }
        if isActive, now >= nextFidget {
            scheduleFidget(from: now)
            let eyes = self.eyes
            Task { await eyes.fidget() }
        }
    }

    private func setResting(_ state: CompanionState) {
        guard state != resting else { return }
        resting = state
        onRestingChange?()
    }

    private func isDrowsyHour(_ date: Date) -> Bool {
        let hour = Calendar.current.component(.hour, from: date)
        return hour >= CharacterConfig.drowsyHourStart || hour < CharacterConfig.drowsyHourEnd
    }

    private func scheduleFidget(from date: Date) {
        let delay = Double.random(in: CharacterConfig.fidgetMinInterval...CharacterConfig.fidgetMaxInterval)
        nextFidget = date.addingTimeInterval(delay)
    }

    // MARK: - Ритм дня

    private func speakIfDue(now: Date) {
        // Не перебиваем другое сообщение и не говорим, когда просили тишины.
        guard lineProcess == nil, !taskPanel.hasCompactNotice, !taskPanel.isSuppressed else { return }
        // Samarizator ещё не найден — день не отмечаем, скажем, когда найдётся.
        guard toolchain() != nil else { return }
        let hour = Calendar.current.component(.hour, from: now)
        let today = Self.dayKey(now)
        if hour >= CharacterConfig.morningFromHour,
           hour < CharacterConfig.eveningFromHour,
           CompanionSettings.lastMorningDay != today {
            CompanionSettings.lastMorningDay = today
            requestLine(kind: "morning")
        } else if hour >= CharacterConfig.eveningFromHour,
                  hour < CharacterConfig.drowsyHourStart,
                  CompanionSettings.lastMorningDay == today,
                  CompanionSettings.lastEveningDay != today {
            // Вечером подводит итог только тот день, в который здоровался:
            // иначе первым словом после запуска в 19:00 был бы итог пустого дня.
            CompanionSettings.lastEveningDay = today
            requestLine(kind: "evening")
        }
    }

    private func requestLine(kind: String) {
        guard let toolchain = toolchain() else { return }
        let process = CompanionProcess(
            toolchain: toolchain,
            arguments: ["rhythm", kind],
            onEvent: { [weak self] event in
                MainActor.assumeIsolated {
                    guard let self, event.name == "line", let text = event.text("text"), !text.isEmpty else { return }
                    let emotion = CompanionEmotion(rawValue: event.text("emotion") ?? "") ?? .calm
                    let intensity = Double(event.text("intensity") ?? "") ?? 0.4
                    self.taskPanel.presentCompanionLine(text, emotion: emotion, intensity: intensity)
                }
            },
            onFinish: { [weak self] _ in
                MainActor.assumeIsolated {
                    self?.lineProcess = nil
                }
            }
        )
        do {
            try process.start()
            lineProcess = process
        } catch {
            NSLog("Живость: реплику не получить — %@", error.localizedDescription)
        }
    }

    private static func dayKey(_ date: Date) -> String {
        let parts = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04ld-%02ld-%02ld", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }
}
