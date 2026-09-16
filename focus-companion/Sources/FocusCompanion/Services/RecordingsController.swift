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

    /// Найденная установка Samarizator. `nil` — управлять нечем, и кнопки
    /// честно показывают это, а не делают вид, что работают.
    private(set) var toolchain: SamarizatorToolchain?
    private(set) var toolchainFailure: SamarizatorToolchain.Failure?
    /// Что доступно по данным `samarizator.companion status`.
    private(set) var capabilities: CompanionCapabilities?

    /// Запущенная операция. Их не может быть две сразу для одной записи,
    /// а запись и сводка живут независимо друг от друга.
    private var captureProcess: CompanionProcess?
    private var summaryProcess: CompanionProcess?
    private(set) var recordings: [Recording] = []
    private(set) var selectedID: String?
    private(set) var loadError: String?

    var viewMode: RecordingViewMode = .summary
    var summaryFormat: SummaryFormat = .brief
    /// Позиция чтения по записям: сохраняется при переключении страниц.
    var readingOffsets: [String: CGFloat] = [:]

    private(set) var captureState: CaptureState = .idle {
        didSet { onActivityChange?() }
    }
    private(set) var summaryState: SummaryState = .unavailable {
        didSet { onActivityChange?() }
    }

    /// Сообщает наружу, что фоновая активность изменилась: персонаж слушает
    /// во время записи и думает во время РЕАЛЬНОЙ обработки — не по таймеру.
    var onActivityChange: (() -> Void)?

    /// Фоновое состояние персонажа, соответствующее текущей работе.
    var ambientEmotion: CompanionState {
        if captureState.isRecording { return .listening }
        if case .transcribing = captureState { return .thinking }
        if summaryState == .generating { return .thinking }
        return .idle
    }

    /// Данные взяты из демонстрационного набора design.md §15.
    private(set) var isDemo = false

    /// Что обрабатывается локально, а что уходит наружу. Метка ставится по
    /// факту, а не по умолчанию.
    private(set) var locality = SamarizatorBridge.ProcessingLocality.unknown

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
        locality = SamarizatorBridge.processingLocality()
        resolveToolchain()
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

    // MARK: - Подключение к Samarizator

    private func resolveToolchain() {
        switch SamarizatorToolchain.resolve() {
        case .success(let found):
            toolchain = found
            toolchainFailure = nil
            refreshCapabilities()
        case .failure(let failure):
            toolchain = nil
            toolchainFailure = failure
            capabilities = nil
        }
    }

    /// Указать папку Samarizator вручную — когда автопоиск её не нашёл.
    func selectToolchain(at url: URL) {
        switch SamarizatorToolchain.select(url) {
        case .success(let found):
            toolchain = found
            toolchainFailure = nil
            refreshCapabilities()
            reload()
        case .failure(let failure):
            toolchainFailure = failure
        }
    }

    /// Спрашивает у самого Samarizator, что он сейчас может.
    private func refreshCapabilities() {
        guard let toolchain else { return }
        let process = CompanionProcess(
            toolchain: toolchain,
            arguments: ["status"],
            onEvent: { [weak self] event in
                MainActor.assumeIsolated {
                    guard event.name == "status" else { return }
                    self?.capabilities = CompanionCapabilities(event: event)
                }
            },
            onFinish: { _ in }
        )
        do {
            try process.start()
        } catch {
            toolchainFailure = .environmentMissing(path: toolchain.root.path)
        }
    }

    // MARK: - Кнопки крыла

    /// Что произойдёт по нажатию кнопки записи прямо сейчас.
    enum RecordAction: Equatable {
        case start
        case stop
        /// Запускать нечем: не найден Samarizator, нет модели или нет захвата.
        case unavailable(reason: String)
    }

    var recordAction: RecordAction {
        if captureState.isRecording { return .stop }
        if isDemo { return .start }
        guard toolchain != nil else {
            return .unavailable(reason: toolchainFailure?.explanation
                ?? "Samarizator не найден.")
        }
        guard let capabilities else {
            return .unavailable(reason: "Проверяю, что доступно в Samarizator…")
        }
        guard capabilities.canRecord else {
            return .unavailable(reason: "Live-запись поддерживается только на macOS.")
        }
        guard capabilities.canTranscribe else {
            return .unavailable(reason: "Модель Whisper не установлена. Запустите ./start.sh "
                + "в папке Samarizator — он скачает модель.")
        }
        return .start
    }

    var recordTooltip: String {
        switch recordAction {
        case .stop: return "Остановить запись"
        case .start: return "Начать транскрибацию"
        case .unavailable(let reason): return reason
        }
    }

    var summaryTooltip: String {
        guard let recording = selectedRecording else { return "Сначала сделайте запись" }
        return "Сделать саммари: \(recording.title)"
    }

    /// Саммари недоступно, пока нет материала или не настроена модель.
    var canSummarize: Bool {
        guard let recording = selectedRecording else { return false }
        guard recording.hasTranscript || !recording.summaries.isEmpty else { return false }
        if isDemo { return true }
        guard summaryProcess == nil else { return false }
        return capabilities?.canSummarize ?? false
    }

    // MARK: - Запись

    func performRecordAction() {
        switch recordAction {
        case .start:
            startRecording()
        case .stop:
            stopRecording()
        case .unavailable(let reason):
            captureState = .needsPermission(reason)
        }
    }

    private func startRecording() {
        guard !isDemo else {
            // Демонстрационный режим ничего не пишет: он только показывает вид.
            captureState = .recording(startedAt: Date())
            return
        }
        guard let toolchain else { return }
        captureState = .recording(startedAt: Date())
        let process = CompanionProcess(
            toolchain: toolchain,
            arguments: ["record"],
            onEvent: { [weak self] event in
                MainActor.assumeIsolated { self?.handleCaptureEvent(event) }
            },
            onFinish: { [weak self] code in
                MainActor.assumeIsolated { self?.handleCaptureFinished(code: code) }
            }
        )
        do {
            try process.start()
            captureProcess = process
        } catch {
            captureProcess = nil
            captureState = .error("Не удалось запустить запись: \(error.localizedDescription)")
        }
    }

    private func stopRecording() {
        guard !isDemo else {
            captureState = .transcribing
            return
        }
        // Именно мягкая остановка: Samarizator должен закрыть файл и запустить
        // закрывающее распознавание. Грубое завершение потеряло бы запись.
        captureProcess?.requestStop()
        captureState = .transcribing
    }

    private func handleCaptureEvent(_ event: CompanionEvent) {
        switch event.name {
        case "recording":
            // Запись существует в базе с первой секунды — показываем её сразу.
            if let mid = event.mid { selectedID = mid }
            reloadRecordingsOnly()
        case "stopped":
            captureState = .transcribing
            reloadRecordingsOnly()
        case "transcribing":
            captureState = .transcribing
            // Персонаж думает только при РЕАЛЬНОЙ обработке.
            reloadRecordingsOnly()
        case "transcribed":
            captureState = .idle
            reload()
        case "discarded":
            captureState = .idle
            selectedID = nil
            reload()
        case "error":
            captureState = .error(event.message ?? "Запись прервалась.")
            reload()
        default:
            break
        }
    }

    private func handleCaptureFinished(code: Int32) {
        captureProcess = nil
        if captureState.isRecording {
            // Процесс умер, не сказав ни слова: делать вид, что запись идёт, нельзя.
            captureState = code == 0
                ? .idle
                : .error("Запись завершилась неожиданно (код \(code)).")
        }
        reload()
    }

    // MARK: - Саммаризация

    /// Саммаризация не останавливает запись и не показывает выдуманный прогресс.
    func requestSummary() {
        guard let recording = selectedRecording else {
            summaryState = .unavailable
            return
        }
        guard !isDemo else {
            summaryState = .generating
            return
        }
        guard summaryProcess == nil else { return }
        guard let toolchain else {
            summaryState = .error(toolchainFailure?.explanation ?? "Samarizator не найден.")
            return
        }
        guard capabilities?.canSummarize ?? false else {
            summaryState = .error("Модель для сводки не настроена. Откройте настройки "
                + "Samarizator и укажите адрес и название модели.")
            return
        }

        summaryState = .generating
        let process = CompanionProcess(
            toolchain: toolchain,
            arguments: ["summarize", recording.id],
            onEvent: { [weak self] event in
                MainActor.assumeIsolated { self?.handleSummaryEvent(event) }
            },
            onFinish: { [weak self] code in
                MainActor.assumeIsolated { self?.handleSummaryFinished(code: code) }
            }
        )
        do {
            try process.start()
            summaryProcess = process
        } catch {
            summaryProcess = nil
            summaryState = .error("Не удалось запустить саммаризацию: \(error.localizedDescription)")
        }
    }

    private func handleSummaryEvent(_ event: CompanionEvent) {
        switch event.name {
        case "summarizing":
            summaryState = .generating
        case "summarized":
            summaryState = .ready
            reload()
        case "error":
            // Ошибка сводки не трогает ни запись, ни распознанный текст.
            summaryState = .error(event.message ?? "Не удалось сделать сводку.")
            reload()
        default:
            break
        }
    }

    private func handleSummaryFinished(code: Int32) {
        summaryProcess = nil
        if summaryState == .generating {
            summaryState = code == 0
                ? .ready
                : .error("Саммаризация завершилась неожиданно (код \(code)).")
        }
        reload()
    }

    func finishDemoSummary() {
        guard isDemo else { return }
        summaryState = .ready
    }

    /// Перечитывает только список, не трогая выбранный режим и позицию чтения.
    private func reloadRecordingsOnly() {
        guard !isDemo, availability.isAvailable else { return }
        recordings = (try? SamarizatorBridge.loadRecordings()) ?? recordings
    }

    /// Текст честного состояния недоступности — он же виден в пустой панели.
    ///
    /// Порядок важен: сначала то, что вообще не даёт работать (не найден
    /// Samarizator), потом отсутствие базы, и только потом ошибка чтения.
    var unavailabilityMessage: String? {
        guard !isDemo else { return nil }
        if let toolchainFailure { return toolchainFailure.explanation }
        guard availability.isAvailable else { return availability.explanation }
        return loadError
    }

    /// Чего не хватает для работы кнопок. Пусто — всё на месте.
    var missingCapabilities: [String] {
        guard !isDemo, toolchain != nil, let capabilities else { return [] }
        var missing: [String] = []
        if !capabilities.canRecord {
            missing.append("Live-запись поддерживается только на macOS.")
        }
        if !capabilities.canTranscribe {
            missing.append("Модель Whisper не установлена: запустите ./start.sh в папке Samarizator.")
        }
        if !capabilities.canSummarize {
            missing.append("Модель для сводки не настроена: укажите адрес и название в настройках Samarizator.")
        }
        return missing
    }
}

/// Что headless-интерфейс Samarizator сообщил о своей готовности.
///
/// Всё это — факты, а не предположения: модель Whisper либо лежит на диске,
/// либо нет; адрес модели для сводки либо настроен, либо пуст.
struct CompanionCapabilities: Equatable {
    var captureSupported: Bool
    var whisperModelReady: Bool
    var whisperModelPath: String
    var summaryBaseURL: String
    var summaryModel: String
    var liveSource: String

    var canRecord: Bool { captureSupported }
    /// Распознавать нечем, пока нет файла модели.
    var canTranscribe: Bool { whisperModelReady }
    /// Сводку делать некому, пока не настроен адрес и название модели.
    var canSummarize: Bool { !summaryBaseURL.isEmpty && !summaryModel.isEmpty }

    init(event: CompanionEvent) {
        captureSupported = event.flag("captureSupported")
        whisperModelReady = event.flag("whisperModelReady")
        whisperModelPath = event.text("whisperModelPath") ?? ""
        summaryBaseURL = event.text("summaryBaseURL") ?? ""
        summaryModel = event.text("summaryModel") ?? ""
        liveSource = event.text("liveSource") ?? ""
    }
}
