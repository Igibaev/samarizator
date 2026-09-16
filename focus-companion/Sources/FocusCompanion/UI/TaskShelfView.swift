import SwiftUI

/// Компактная полка задач под notch: три строки по 24 pt, без скролла.
///
/// Задачи остаются видимыми, когда большая панель закрыта (design.md §6.3).
struct TaskShelfView: View {
    var taskPanel: TaskPanelController
    var disposal: (taskID: UUID, effect: DisposalEffect)?
    var reduceMotion: Bool

    var body: some View {
        VStack(spacing: 0) {
            ForEach(taskPanel.store.activeTasks) { task in
                row(task)
                    .frame(height: CompanionGeometry.Metrics.shelfRowHeight)
            }
        }
        .padding(.vertical, CompanionGeometry.Metrics.shelfVerticalPadding / 2)
        .frame(width: CompanionGeometry.Metrics.shelfWidth)
    }

    private func row(_ task: CompanionTask) -> some View {
        HStack(spacing: DesignTokens.Spacing.xs) {
            CompletionRing(isDone: false) {
                taskPanel.complete(task)
            }
            Text(task.title)
                .font(DesignTokens.Typography.compactTask())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
                .help(task.title)
            Spacer(minLength: DesignTokens.Spacing.xs)
            Text(task.remainingLabel(now: taskPanel.now))
                .font(DesignTokens.Typography.meta())
                .foregroundStyle(deadlineColor(task))
                .fixedSize()
        }
        .padding(.horizontal, DesignTokens.Spacing.s)
        .overlay(alignment: .bottom) { deadlineLine(task) }
        .modifier(DisposalModifier(
            isActive: disposal?.taskID == task.id,
            effect: disposal?.effect ?? .burn,
            reduceMotion: reduceMotion
        ))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(task.title), осталось \(task.remainingLabel(now: taskPanel.now))")
    }

    /// Линия оставшегося ВРЕМЕНИ — отдельная тонкая полоса 1 pt, намеренно
    /// не похожая на прогресс выполненной работы.
    private func deadlineLine(_ task: CompanionTask) -> some View {
        GeometryReader { proxy in
            ZStack(alignment: .leading) {
                Rectangle()
                    .fill(Color.white.opacity(0.06))
                Rectangle()
                    .fill(deadlineColor(task))
                    .frame(width: proxy.size.width * task.remainingFraction(now: taskPanel.now))
            }
        }
        .frame(height: 1)
        .padding(.horizontal, DesignTokens.Spacing.s)
    }

    private func deadlineColor(_ task: CompanionTask) -> Color {
        if task.remainingSeconds(now: taskPanel.now) <= 0 { return DesignTokens.Palette.accentRecording }
        if task.isDueSoon(now: taskPanel.now) { return DesignTokens.Palette.accentWarning }
        return DesignTokens.Palette.textSecondary
    }
}

/// Кольцо выполнения: это кнопка «выполнить», а не индикатор процента работы.
struct CompletionRing: View {
    var isDone: Bool
    var action: () -> Void

    @State private var isHovering = false

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .strokeBorder(
                        isHovering ? DesignTokens.Palette.accentSuccess : DesignTokens.Palette.textSecondary,
                        lineWidth: 1.5
                    )
                    .frame(width: 12, height: 12)
                if isDone {
                    Image(systemName: "checkmark")
                        .font(.system(size: 7, weight: .bold))
                        .foregroundStyle(DesignTokens.Palette.accentSuccess)
                }
            }
            // Hit-area расширена до 24 × 24 pt (design.md §13).
            .frame(width: 24, height: 24)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { isHovering = $0 }
        .help("Отметить выполненной")
        .accessibilityLabel("Отметить выполненной")
    }
}
