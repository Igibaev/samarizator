import Foundation

/// Заметка дня и «Поручения» в хранилище Obsidian.
///
/// Сам текст собирает `python -m samarizator.companion journal` из фактов —
/// базы Samarizator и `tasks.json`, — поэтому лишний вызов ничего не портит,
/// а пропущенный догоняется следующим. Здесь только «когда»: через пару
/// секунд после изменения в слотах (серия нажатий — один вызов), при запуске
/// и раз в час, чтобы после полуночи появилась заметка нового дня.
@MainActor
final class JournalSync {

    /// Пауза после последнего изменения: «Готово» и сразу новое дело —
    /// один запуск Python, а не два.
    private static let debounce: TimeInterval = 3
    private static let hourly: TimeInterval = 3600

    /// Откуда взять Samarizator. Задаётся владельцем после сборки
    /// контроллеров: папку ищет и запоминает `RecordingsController`.
    var toolchain: () -> SamarizatorToolchain? = { nil }
    private let taskBag = TaskBag()
    private var running: CompanionProcess?
    /// Изменение пришло, пока предыдущий вызов ещё шёл: повторить по его окончании.
    private var pending = false

    /// Последняя ошибка — для лога; в интерфейс дневник не пишет.
    private(set) var lastError: String?

    deinit { taskBag.cancelAll() }

    func start() {
        schedule()
        taskBag.replace(.journalHourly, with: Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(Self.hourly * 1_000_000_000))
                guard !Task.isCancelled, let self else { return }
                self.runNow()
            }
        })
    }

    func schedule() {
        taskBag.replace(.journalDebounce, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Self.debounce * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            self.runNow()
        })
    }

    private func runNow() {
        guard running == nil else {
            pending = true
            return
        }
        guard let toolchain = toolchain() else { return }
        let process = CompanionProcess(
            toolchain: toolchain,
            arguments: ["journal"],
            onEvent: { [weak self] event in
                MainActor.assumeIsolated {
                    guard event.name == "error" else { return }
                    self?.lastError = event.message
                    NSLog("Дневник: %@", event.message ?? "ошибка без текста")
                }
            },
            onFinish: { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self else { return }
                    self.running = nil
                    if self.pending {
                        self.pending = false
                        self.schedule()
                    }
                }
            }
        )
        do {
            try process.start()
            running = process
        } catch {
            lastError = error.localizedDescription
        }
    }
}
