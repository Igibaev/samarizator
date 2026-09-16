import SwiftUI

/// Правое крыло: глаза → запись → саммари, в этом порядке, на одной высоте
/// с вырезом (design.md §4.1).
///
/// Группа занимает 140 pt: глаза 48 + промежуток 20 + кнопка 32 +
/// промежуток 8 + кнопка 32 — и помещается в крыло шириной 164 pt с полями
/// по 12 pt.
struct WingView: View {
    var eyes: EyesViewModel
    var stateMachine: CompanionStateMachine
    var recordings: RecordingsController
    var isSeamless: Bool
    var notchHeight: CGFloat
    /// Общее «сейчас», тикающее раз в секунду: таймер записи не имеет права
    /// зависеть от случайных перерисовок вью.
    var now: Date
    var onEyesTap: () -> Void
    var onEyesDoubleTap: () -> Void
    var onDragChanged: (CGSize) -> Void
    var onDragEnded: (CGSize) -> Void

    @State private var hoveringEyes = false

    var body: some View {
        HStack(spacing: 0) {
            eyesButton
            recordButton
                .padding(.leading, DesignTokens.Spacing.l)
            summaryButton
                .padding(.leading, DesignTokens.Spacing.xs)
            if let timer = recordingTimerText {
                Text(timer)
                    .font(DesignTokens.Typography.meta())
                    .foregroundStyle(DesignTokens.Palette.accentRecording)
                    .padding(.leading, DesignTokens.Spacing.xs)
                    .fixedSize()
            }
        }
        .padding(.horizontal, CompanionGeometry.Metrics.wingInset)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }

    // MARK: - Глаза

    /// Глаза — одновременно кнопка «открыть фокус» и ручка для перетаскивания.
    ///
    /// Это не `Button`: у кнопки нажатие срабатывало бы в начале любого
    /// перетаскивания. Тап и жест разведены порогом `dragThreshold` —
    /// короткое нажатие открывает фокус, протаскивание двигает корпус.
    private var eyesButton: some View {
        EyesView(
            model: eyes,
            appearance: stateMachine.appearance,
            gazeTargetOffset: stateMachine.gazeTargetOffset
        )
        .frame(width: CharacterConfig.eyesAreaWidth, height: CharacterConfig.eyesAreaHeight)
        .contentShape(Rectangle())
        .onTapGesture(count: 2, perform: onEyesDoubleTap)
        .onTapGesture(perform: onEyesTap)
        .gesture(
            DragGesture(minimumDistance: PlacementController.dragThreshold)
                .onChanged { value in onDragChanged(value.translation) }
                .onEnded { value in onDragEnded(value.translation) }
        )
        .help("Открыть фокус. Перетащите, чтобы переместить; двойной клик вернёт к вырезу")
        .onHover { hovering in
            hoveringEyes = hovering
            // Любопытство при наведении — 280 мс, один раз на вход курсора.
            if hovering { stateMachine.react(.curious) }
        }
    }

    // MARK: - Кнопка записи

    private var recordButton: some View {
        Button {
            recordings.performRecordAction()
        } label: {
            recordIcon
        }
        .buttonStyle(CompanionIconButtonStyle(
            isActive: recordings.captureState.isRecording,
            activeColor: DesignTokens.Palette.accentRecording
        ))
        .help(recordings.recordTooltip)
        .accessibilityLabel(recordings.recordTooltip)
    }

    @ViewBuilder
    private var recordIcon: some View {
        if recordings.captureState.isRecording {
            // При активной записи первая иконка — небольшой квадрат остановки.
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(DesignTokens.Palette.accentRecording)
                .frame(width: 10, height: 10)
        } else {
            // Маленький коралловый круг внутри тонкого светлого кольца.
            ZStack {
                Circle()
                    .strokeBorder(DesignTokens.Palette.textPrimary.opacity(0.55), lineWidth: 1.5)
                    .frame(width: 16, height: 16)
                Circle()
                    .fill(DesignTokens.Palette.accentRecording)
                    .frame(width: 8, height: 8)
            }
        }
    }

    // MARK: - Кнопка саммари

    private var summaryButton: some View {
        Button {
            recordings.requestSummary()
        } label: {
            summaryIcon
        }
        .buttonStyle(CompanionIconButtonStyle(
            isActive: recordings.summaryState == .generating,
            isEnabled: recordings.canSummarize
        ))
        .disabled(!recordings.canSummarize)
        .help(recordings.canSummarize ? recordings.summaryTooltip : "Сначала сделайте запись")
        .accessibilityLabel(recordings.canSummarize ? recordings.summaryTooltip : "Сначала сделайте запись")
    }

    @ViewBuilder
    private var summaryIcon: some View {
        if recordings.summaryState == .generating {
            // Небольшой spinner ВМЕСТО искры — и никаких выдуманных процентов.
            ProgressView()
                .controlSize(.small)
                .scaleEffect(0.7)
        } else {
            // Три короткие горизонтальные линии и маленькая четырёхлучевая искра.
            ZStack(alignment: .topTrailing) {
                VStack(alignment: .leading, spacing: 3) {
                    summaryLine(width: 14)
                    summaryLine(width: 11)
                    summaryLine(width: 8)
                }
                SparkShape()
                    .fill(DesignTokens.Palette.textPrimary)
                    .frame(width: 7, height: 7)
                    .offset(x: 4, y: -3)
            }
            .frame(width: 16, height: 16, alignment: .leading)
        }
    }

    private func summaryLine(width: CGFloat) -> some View {
        Capsule()
            .fill(DesignTokens.Palette.textPrimary)
            .frame(width: width, height: 1.5)
    }

    // MARK: - Таймер записи

    /// Метка времени показывается, только если крыло реально расширилось.
    /// Если места нет — время живёт в панели записи и в tooltip.
    private var recordingTimerText: String? {
        guard case .recording(let startedAt) = recordings.captureState else { return nil }
        return TimecodeFormatter.string(from: now.timeIntervalSince(startedAt))
    }
}

/// Маленькая четырёхлучевая искра.
struct SparkShape: Shape {
    func path(in rect: CGRect) -> Path {
        var path = Path()
        let midX = rect.midX
        let midY = rect.midY
        let waist = rect.width * 0.18
        path.move(to: CGPoint(x: midX, y: rect.minY))
        path.addQuadCurve(to: CGPoint(x: rect.maxX, y: midY), control: CGPoint(x: midX + waist, y: midY - waist))
        path.addQuadCurve(to: CGPoint(x: midX, y: rect.maxY), control: CGPoint(x: midX + waist, y: midY + waist))
        path.addQuadCurve(to: CGPoint(x: rect.minX, y: midY), control: CGPoint(x: midX - waist, y: midY + waist))
        path.addQuadCurve(to: CGPoint(x: midX, y: rect.minY), control: CGPoint(x: midX - waist, y: midY - waist))
        path.closeSubpath()
        return path
    }
}
