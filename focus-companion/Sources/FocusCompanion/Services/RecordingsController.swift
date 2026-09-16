import AppKit
import Observation

/// Состояние захвата (design.md §14, область `capture`).
enum CaptureState: Equatable {
    case idle
    case selectingSource
    case needsPermission(String)
    case recording(startedAt: Date)
    case transcribing
    case error(String)

    var isRecording: Bool {
        if case .recording = self { return true }
        return false
    }
}

/// Состояние саммаризации (design.md §14, область `summary`).
/// Независимо от `CaptureState`: ошибка модели не останавливает запись и
/// не удаляет исходный материал.
enum SummaryState: Equatable {
    case unavailable
    case readyToGenerate
    case generating
    case ready
    case stale
    case error(String)
}

/// Записи, транскрипты и саммари.
///
/// Единственный реальный источник — база Samarizator, и только на чтение.
/// Всё, чего в ней нет, показывается как недоступное, а не подменяется
/// правдоподобной заглушкой.
@MainActor
@Observable
final class RecordingsController {

    private(set) var availability: SamarizatorBridge.Availability = .databaseMissing(expectedPath: "")
    private(set) var recordings: [Recording] = []
    private(set) var selectedID: String?
    private(set) var loadError: String?

    var viewMode: RecordingViewMode = .summary
    var summaryFormat: SummaryFormat = .brief
    /// Позиция чтения по записям: сохраняется при переключении страниц.
    var readingOffsets: [String: CGFloat] = [:]

    private(set) var captureState: CaptureState = .idle
    private(set) var summaryState: SummaryState = .unavailable

    /// Данные взяты из демонстрационного набора design.md §15.
    private(set) var isDemo = false

    var selectedRecording: Recording? {
        guard let selectedID else { return recordings.first }
        return recordings.first { $0.id == selectedID } ?? recordings.first
    }

    init() {
        reload()
    }

    // MARK: - Загрузка

    func reload() {
        if CompanionSettings.demoMode {
            loadDemo()
            return
        }
        isDemo = false
        availability = SamarizatorBridge.availability()
        guard availability.isAvailable else {
            recordings = []
            loadError = nil
            captureState = .idle
            summaryState = .unavailable
            return
        }
        do {
            recordings = try SamarizatorBridge.loadRecordings()
            loadError = nil
            if selectedID == nil { selectedID = recordings.first?.id }
            loadSegmentsForSelection()
            refreshSummaryState()
        } catch {
            recordings = []
            loadError = error.localizedDescription
            summaryState = .error(error.localizedDescription)
        }
    }

    func loadDemo() {
        isDemo = true
        availability = .available(databasePath: "демонстрационный набор")
        recordings = DemoFixtures.recordings
        selectedID = recordings.first?.id
        loadError = nil
        captureState = .idle
        summaryState = .ready
    }

    func select(_ id: String) {
        selectedID = id
        loadSegmentsForSelection()
        refreshSummaryState()
    }

    private func loadSegmentsForSelection() {
        guard !isDemo, let id = selectedID,
              let index = recordings.firstIndex(where: { $0.id == id }),
              recordings[index].segments.isEmpty else { return }
        do {
            recordings[index].segments = try SamarizatorBridge.loadSegments(recordingID: id)
        } catch {
            recordings[index].processingError = error.localizedDescription
        }
    }

    private func refreshSummaryState() {
        guard let recording = selectedRecording else {
            summaryState = .unavailable
            return
        }
        if let error = recording.processingError, recording.status == .failed {
            summaryState = .error(error)
            return
        }
        if recording.summaries.isEmpty {
            summaryState = recording.hasTranscript ? .readyToGenerate : .unavailable
        } else {
            summaryState = (recording.summary(for: summaryFormat)?.isStale ?? false) ? .stale : .ready
        }
    }

    // MARK: - Кнопки крыла

    /// Что произойдёт по нажатию кнопки записи прямо сейчас.
    enum RecordAction: Equatable {
        case start
        case stop
        /// Управлять записью из компаньона нельзя — нужен сам Samarizator.
        case openSamarizator(reason: String)
    }

    var recordAction: RecordAction {
        if captureState.isRecording { return .stop }
        if isDemo { return .start }
        return .openSamarizator(
            reason: "Начать запись можно в приложении Samarizator: внешнего управления записью "
                + "у него пока нет. Компаньон читает готовые записи, но не запускает их."
        )
    }

    var recordTooltip: String {
        switch recordAction {
        case .stop: return "Остановить запись"
        case .start: return "Начать транскрибацию"
        case .openSamarizator: return "Открыть Samarizator — запись начинается там"
        }
    }

    var summaryTooltip: String {
        guard let recording = selectedRecording else { return "Сначала сделайте запись" }
        return "Сделать саммари: \(recording.title)"
    }

    /// Саммари недоступно, пока нет материала.
    var canSummarize: Bool {
        guard let recording = selectedRecording else { return false }
        return recording.hasTranscript || !recording.summaries.isEmpty
    }

    func performRecordAction() {
        switch recordAction {
        case .start:
            // Демонстрационный режим: запись имитируется явно и подписана.
            captureState = .recording(startedAt: Date())
        case .stop:
            captureState = .transcribing
        case .openSamarizator(let reason):
            captureState = .needsPermission(reason)
            openSamarizatorApp()
        }
    }

    /// Саммаризация не останавливает запись и не показывает выдуманный прогресс.
    func requestSummary() {
        guard canSummarize else {
            summaryState = .unavailable
            return
        }
        guard isDemo else {
            summaryState = .error(
                "Саммаризацию запускает Samarizator. Компаньон показывает уже готовый результат "
                    + "из его базы, но не может создать новый."
            )
            return
        }
        summaryState = .generating
    }

    func finishDemoSummary() {
        guard isDemo else { return }
        summaryState = .ready
    }

    private func openSamarizatorApp() {
        let candidates = [
            URL(fileURLWithPath: "/Applications/Samarizator.app"),
            FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Applications/Samarizator.app"),
        ]
        for url in candidates where FileManager.default.fileExists(atPath: url.path) {
            NSWorkspace.shared.open(url)
            return
        }
    }

    /// Текст честного состояния недоступности — он же виден в пустой панели.
    var unavailabilityMessage: String? {
        guard !isDemo else { return nil }
        guard !availability.isAvailable else { return loadError }
        return availability.explanation
    }
}
