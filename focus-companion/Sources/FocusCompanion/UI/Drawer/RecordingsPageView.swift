import AppKit
import SwiftUI

/// Записи: список слева, чтение справа (design.md §9.3).
struct RecordingsPageView: View {
    var recordings: RecordingsController
    var taskPanel: TaskPanelController
    var geometry: CompanionGeometry

    @State private var showsAllRecordings = false
    @State private var replacementFor: SummaryItem?

    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.s) {
            if let message = recordings.unavailabilityMessage {
                unavailable(message)
            } else {
                missingCapabilities
                HStack(alignment: .top, spacing: CompanionGeometry.Metrics.recordingsListGap) {
                    if geometry.showsRecordingsList {
                        recordingsList
                            .frame(width: CompanionGeometry.Metrics.recordingsListWidth)
                    } else if showsAllRecordings {
                        // На узкой панели список открывается поверх чтения.
                        recordingsList
                            .frame(width: CompanionGeometry.Metrics.recordingsListWidth)
                    }
                    reader
                        .frame(maxWidth: .infinity, alignment: .topLeading)
                }
            }
        }
        .padding(.top, DesignTokens.Spacing.s)
    }

    // MARK: - Честная недоступность

    private func unavailable(_ message: String) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
            Text("Записи недоступны")
                .font(DesignTokens.Typography.panelTitle())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
            Text(message)
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: DesignTokens.Spacing.m) {
                Button("Указать папку Samarizator…") { chooseSamarizatorFolder() }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(DesignTokens.Palette.accentSelection)
                Button("Проверить ещё раз") { recordings.reload() }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
                Button("Демонстрационный набор") { recordings.loadDemo() }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
            }
        }
    }

    /// Папка Samarizator — та, в которой лежит `start.sh`.
    private func chooseSamarizatorFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.allowsMultipleSelection = false
        panel.prompt = "Выбрать"
        panel.message = "Выберите папку Samarizator — ту, в которой лежит start.sh"
        guard panel.runModal() == .OK, let url = panel.url else { return }
        recordings.selectToolchain(at: url)
    }

    /// Чего не хватает, чтобы кнопки работали. Показывается над содержимым,
    /// а не вместо него: готовые записи читаются и без модели Whisper.
    @ViewBuilder
    private var missingCapabilities: some View {
        let missing = recordings.missingCapabilities
        if !missing.isEmpty {
            VStack(alignment: .leading, spacing: 2) {
                ForEach(missing, id: \.self) { line in
                    HStack(alignment: .top, spacing: DesignTokens.Spacing.xxs) {
                        Image(systemName: "exclamationmark.triangle")
                            .font(.system(size: 10))
                            .foregroundStyle(DesignTokens.Palette.accentWarning)
                        Text(line)
                            .font(DesignTokens.Typography.caption())
                            .foregroundStyle(DesignTokens.Palette.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            .padding(DesignTokens.Spacing.xs)
            .background(
                RoundedRectangle(cornerRadius: DesignTokens.Radius.row, style: .continuous)
                    .fill(DesignTokens.Palette.accentWarning.opacity(0.10))
            )
        }
    }

    // MARK: - Список записей

    private var recordingsList: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("Записи")
                .font(DesignTokens.Typography.panelTitle())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
                .padding(.bottom, DesignTokens.Spacing.xs)
            ScrollView {
                VStack(alignment: .leading, spacing: 2) {
                    ForEach(recordings.recordings) { recording in
                        listRow(recording)
                    }
                }
            }
        }
    }

    private func listRow(_ recording: Recording) -> some View {
        Button {
            recordings.select(recording.id)
        } label: {
            VStack(alignment: .leading, spacing: 2) {
                Text(recording.title)
                    .font(DesignTokens.Typography.compactTask())
                    .foregroundStyle(DesignTokens.Palette.textPrimary)
                    .lineLimit(1)
                HStack(spacing: DesignTokens.Spacing.xxs) {
                    Text(TimecodeFormatter.dayLabel(for: recording.createdAt))
                    if let duration = recording.durationLabel {
                        Text("·")
                        Text(duration)
                    }
                }
                .font(DesignTokens.Typography.meta())
                .foregroundStyle(DesignTokens.Palette.textTertiary)
                statusBadge(recording.status)
            }
            .padding(.vertical, DesignTokens.Spacing.xs)
            .padding(.horizontal, DesignTokens.Spacing.xs)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(
                RoundedRectangle(cornerRadius: DesignTokens.Radius.row, style: .continuous)
                    .fill(recordings.selectedRecording?.id == recording.id
                        ? DesignTokens.Palette.accentSelection.opacity(0.18)
                        : Color.clear)
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }

    /// Достоверный статус: смысл не передаётся одним цветом — рядом есть текст.
    private func statusBadge(_ status: RecordingStatus) -> some View {
        HStack(spacing: 4) {
            Circle()
                .fill(statusColor(status))
                .frame(width: 5, height: 5)
            Text(status.displayName)
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
        }
    }

    private func statusColor(_ status: RecordingStatus) -> Color {
        switch status {
        case .recording: return DesignTokens.Palette.accentRecording
        case .processing: return DesignTokens.Palette.accentWarning
        case .ready: return DesignTokens.Palette.accentSuccess
        case .failed: return DesignTokens.Palette.accentRecording
        }
    }

    // MARK: - Чтение

    @ViewBuilder
    private var reader: some View {
        if let recording = recordings.selectedRecording {
            VStack(alignment: .leading, spacing: DesignTokens.Spacing.s) {
                HStack(alignment: .top, spacing: DesignTokens.Spacing.s) {
                    readerHeader(recording)
                    Spacer(minLength: 0)
                    if !geometry.showsRecordingsList {
                        // Ширины на колонку списка нет — её заменяет кнопка.
                        Button(showsAllRecordings ? "Скрыть список" : "Все записи") {
                            showsAllRecordings.toggle()
                        }
                        .buttonStyle(.plain)
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.accentSelection)
                    }
                }
                modeSwitcher
                if recordings.isDemo {
                    Text("Демонстрационный набор — это не результат обработки записи.")
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.accentWarning)
                }
                if let error = recording.processingError {
                    errorBlock(error, recording: recording)
                }
                readerBody(recording)
                Spacer(minLength: 0)
                localityFooter
            }
        } else {
            Text("Спокойное пустое состояние: записей ещё нет.")
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textTertiary)
        }
    }

    /// Подвал с меткой обработки. В макете он один и говорит «Локальная
    /// обработка»; в рабочем продукте так писать можно только про то, что
    /// действительно считается локально (design.md §10). Распознавание в
    /// Samarizator локальное, саммаризация — по адресу из настроек.
    @ViewBuilder
    private var localityFooter: some View {
        if recordings.isDemo {
            EmptyView()
        } else {
            HStack(spacing: DesignTokens.Spacing.xs) {
                Image(systemName: "lock.shield")
                    .font(.system(size: 11, weight: .regular))
                    .foregroundStyle(recordings.locality.transcriptIsLocal
                        ? DesignTokens.Palette.accentSuccess
                        : DesignTokens.Palette.textTertiary)
                Text(recordings.locality.transcriptIsLocal
                    ? "Текст: локальная обработка"
                    : "Текст: обработка не подтверждена")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
                Text("·")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
                Text("Саммари: " + recordings.locality.summaryLabel)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(recordings.locality.summaryIsLocal
                        ? DesignTokens.Palette.textTertiary
                        : DesignTokens.Palette.accentWarning)
                Spacer(minLength: 0)
            }
            .padding(.top, DesignTokens.Spacing.xs)
        }
    }

    private func readerHeader(_ recording: Recording) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(recording.title)
                .font(DesignTokens.Typography.recordingTitle())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
                .lineLimit(1)
            HStack(spacing: DesignTokens.Spacing.xxs) {
                Text(TimecodeFormatter.dayLabel(for: recording.createdAt))
                if let duration = recording.durationLabel {
                    Text("·")
                    Text(duration)
                }
                if !recording.source.isEmpty {
                    Text("·")
                    Text(recording.source)
                }
            }
            .font(DesignTokens.Typography.meta())
            .foregroundStyle(DesignTokens.Palette.textSecondary)
        }
    }

    private var modeSwitcher: some View {
        HStack(spacing: DesignTokens.Spacing.s) {
            Picker("Режим", selection: Binding(
                get: { recordings.viewMode },
                set: { recordings.viewMode = $0 }
            )) {
                ForEach(RecordingViewMode.allCases) { mode in
                    // «Рядом» требует минимум 640 pt области чтения.
                    if mode != .sideBySide || geometry.allowsSideBySide {
                        Text(mode.displayName).tag(mode)
                    }
                }
            }
            .pickerStyle(.segmented)
            .fixedSize()

            if recordings.viewMode != .transcript {
                Picker("Формат", selection: Binding(
                    get: { recordings.summaryFormat },
                    set: { recordings.summaryFormat = $0 }
                )) {
                    ForEach(SummaryFormat.allCases) { Text($0.displayName).tag($0) }
                }
                .pickerStyle(.segmented)
                .fixedSize()
            }
            Spacer(minLength: 0)
        }
    }

    @ViewBuilder
    private func readerBody(_ recording: Recording) -> some View {
        switch recordings.viewMode {
        case .summary:
            summaryColumn(recording)
        case .transcript:
            transcriptColumn(recording)
        case .sideBySide:
            if geometry.allowsSideBySide {
                HStack(alignment: .top, spacing: DesignTokens.Spacing.m) {
                    transcriptColumn(recording)
                    summaryColumn(recording)
                }
            } else {
                // Если 640 pt недоступны — вертикально, с явными заголовками.
                ScrollView {
                    VStack(alignment: .leading, spacing: DesignTokens.Spacing.m) {
                        sectionTitle("Текст")
                        transcriptColumn(recording)
                        sectionTitle("Саммари")
                        summaryColumn(recording)
                    }
                }
            }
        }
    }

    private func sectionTitle(_ text: String) -> some View {
        Text(text)
            .font(DesignTokens.Typography.panelTitle())
            .foregroundStyle(DesignTokens.Palette.textPrimary)
    }

    @ViewBuilder
    private func transcriptColumn(_ recording: Recording) -> some View {
        if recording.segments.isEmpty {
            Text(emptyTranscriptMessage(for: recording))
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textTertiary)
        } else {
            // Значок и текст — одной колонкой: без VStack они разъехались бы
            // по горизонтали в режиме «Рядом», где родитель — HStack.
            VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
                liveTranscriptBadge(for: recording)
                ScrollView {
                    VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
                        ForEach(recording.segments) { segment in
                            transcriptRow(segment)
                        }
                    }
                }
            }
        }
    }

    private func transcriptRow(_ segment: TranscriptSegment) -> some View {
        HStack(alignment: .top, spacing: DesignTokens.Spacing.xs) {
            Text(segment.timecode)
                .font(DesignTokens.Typography.meta())
                .foregroundStyle(DesignTokens.Palette.textTertiary)
                .frame(width: 44, alignment: .leading)
            VStack(alignment: .leading, spacing: 1) {
                // Метка спикера — только если она реально есть.
                if let speaker = segment.speaker {
                    Text(speaker)
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.textSecondary)
                }
                Text(segment.text)
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(segment.uncertain
                        ? DesignTokens.Palette.textSecondary
                        : DesignTokens.Palette.textPrimary)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    /// Пустой транскрипт объясняется по-разному: во время записи текст ещё
    /// только распознаётся, после неё идёт закрывающая обработка.
    private func emptyTranscriptMessage(for recording: Recording) -> String {
        if recordings.liveRecordingID == recording.id {
            return "Идёт запись. Текст появится, как только распознается первый фрагмент."
        }
        return recording.status == .processing ? "Обрабатываю запись…" : "Транскрипт пока пуст."
    }

    /// Пока идёт запись, текст дописывается — это видно, а не додумывается.
    @ViewBuilder
    private func liveTranscriptBadge(for recording: Recording) -> some View {
        if recordings.liveRecordingID == recording.id {
            HStack(spacing: DesignTokens.Spacing.xxs) {
                Circle()
                    .fill(DesignTokens.Palette.accentRecording)
                    .frame(width: 6, height: 6)
                Text("Идёт запись — текст дописывается")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
            }
        }
    }

    @ViewBuilder
    private func summaryColumn(_ recording: Recording) -> some View {
        switch recordings.summaryState {
        case .generating:
            HStack(spacing: DesignTokens.Spacing.xs) {
                ProgressView().controlSize(.small)
                // Никакого выдуманного процента.
                Text("Готовлю саммари…")
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
            }
        case .unavailable:
            Text("Сначала сделайте запись — саммари появится, когда будет текст.")
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textTertiary)
        case .error(let message):
            Text(message)
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.accentWarning)
                .fixedSize(horizontal: false, vertical: true)
        default:
            if let summary = recording.summary(for: recordings.summaryFormat) {
                summaryContent(summary)
            } else {
                Text("Саммари для этой записи ещё нет.")
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
            }
        }
    }

    private func summaryContent(_ summary: RecordingSummary) -> some View {
        ScrollView {
            VStack(alignment: .leading, spacing: DesignTokens.Spacing.s) {
                if let snapshot = summary.snapshotLabel {
                    Text(snapshot)
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.textTertiary)
                }
                if summary.isStale {
                    HStack(spacing: DesignTokens.Spacing.xs) {
                        Text("Появились новые фрагменты — саммари устарело.")
                            .font(DesignTokens.Typography.caption())
                            .foregroundStyle(DesignTokens.Palette.accentWarning)
                        Button("Обновить") { recordings.requestSummary() }
                            .buttonStyle(.plain)
                            .font(DesignTokens.Typography.caption())
                            .foregroundStyle(DesignTokens.Palette.accentSelection)
                            .disabled(!recordings.canSummarize)
                    }
                }
                if !summary.overview.isEmpty {
                    Text(summary.overview)
                        .font(DesignTokens.Typography.transcript())
                        .foregroundStyle(DesignTokens.Palette.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                ForEach([SummaryItemKind.point, .decision, .action], id: \.rawValue) { kind in
                    let items = summary.items(for: recordings.summaryFormat).filter { $0.kind == kind }
                    if !items.isEmpty {
                        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xxs) {
                            Text(kind.sectionTitle)
                                .font(DesignTokens.Typography.caption())
                                .foregroundStyle(DesignTokens.Palette.textTertiary)
                            ForEach(items) { item in
                                summaryRow(item)
                            }
                        }
                    }
                }
            }
        }
    }

    private func summaryRow(_ item: SummaryItem) -> some View {
        HStack(alignment: .top, spacing: DesignTokens.Spacing.xs) {
            VStack(alignment: .leading, spacing: 2) {
                Text(item.text)
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(DesignTokens.Palette.textPrimary)
                    .fixedSize(horizontal: false, vertical: true)
                if item.isAssumption {
                    // Вывод, который нельзя связать с текстом.
                    Text("Предположение")
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.accentWarning)
                }
            }
            Spacer(minLength: 0)
            if item.kind == .action {
                Button("В фокус") { addToFocus(item) }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.accentSelection)
            }
        }
        .padding(.vertical, 2)
        .overlay(alignment: .bottomLeading) { replacementPrompt(for: item) }
    }

    /// Формулировка для слота: короткая из передачи Samarizator, если она
    /// есть для этой записи и этого пункта (`TaskPanelController.proposedTitle`),
    /// иначе сам пункт сводки.
    private func focusTitle(for item: SummaryItem) -> String {
        taskPanel.proposedTitle(recordingID: recordings.selectedRecording?.id, sourceText: item.text)
            ?? item.text
    }

    /// Дело помнит встречу и исходный пункт — так его находит «Поручения.md».
    private func addTask(from item: SummaryItem) -> TaskPanelController.AddOutcome {
        taskPanel.addTask(
            title: focusTitle(for: item),
            meetingId: recordings.selectedRecording?.id,
            sourceText: item.text
        )
    }

    /// При трёх занятых слотах предлагаем выбрать замену или отменить.
    private func addToFocus(_ item: SummaryItem) {
        if addTask(from: item) == .slotsFull {
            replacementFor = item
        } else {
            replacementFor = nil
        }
    }

    @ViewBuilder
    private func replacementPrompt(for item: SummaryItem) -> some View {
        if replacementFor?.id == item.id {
            VStack(alignment: .leading, spacing: DesignTokens.Spacing.xxs) {
                Text("Все три слота заняты. Что заменить?")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
                ForEach(taskPanel.store.activeTasks) { victim in
                    Button(victim.title) {
                        taskPanel.archive(victim)
                        _ = addTask(from: item)
                        replacementFor = nil
                    }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textPrimary)
                }
                Button("Отменить добавление") { replacementFor = nil }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
            }
            .padding(DesignTokens.Spacing.xs)
            .background(
                RoundedRectangle(cornerRadius: DesignTokens.Radius.row, style: .continuous)
                    .fill(DesignTokens.Palette.surfaceRaised)
            )
            .offset(y: 40)
            .zIndex(1)
        }
    }

    private func errorBlock(_ message: String, recording: Recording) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xxs) {
            Text("Ошибка обработки")
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(DesignTokens.Palette.accentRecording)
            Text(message)
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            // Ошибка саммаризации не удаляет исходную запись и не прячет текст.
            Text("Исходная запись и распознанный текст сохранены.")
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(DesignTokens.Palette.textTertiary)
            Button("Повторить") { recordings.requestSummary() }
                .buttonStyle(.plain)
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.accentSelection)
        }
        .padding(DesignTokens.Spacing.xs)
        .background(
            RoundedRectangle(cornerRadius: DesignTokens.Radius.row, style: .continuous)
                .fill(DesignTokens.Palette.accentRecording.opacity(0.10))
        )
    }
}
