import SwiftUI

/// Архив завершённых и сгоревших задач — отдельный пункт «История»
/// в меню фокуса, а не четвёртая задача и не дополнительный рабочий список.
struct HistoryView: View {
    var taskPanel: TaskPanelController
    var reduceTransparency: Bool
    var onClose: () -> Void

    private var history: [CompanionTask] { taskPanel.store.history }

    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
            HStack {
                Text("История")
                    .font(DesignTokens.Typography.panelTitle())
                    .foregroundStyle(DesignTokens.Palette.textPrimary)
                Spacer()
                Button(action: onClose) {
                    Image(systemName: "xmark")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(DesignTokens.Palette.textSecondary)
                }
                .buttonStyle(.plain)
                .help("Закрыть историю")
            }

            if let pending = pendingRestore {
                replacementPrompt(for: pending)
            }

            if history.isEmpty {
                Text("Здесь появятся выполненные и сгоревшие задачи")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
            } else {
                ScrollView {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(history) { task in
                            row(task)
                        }
                    }
                }
            }
        }
        .padding(DesignTokens.Spacing.m)
        .frame(
            width: CompanionGeometry.Metrics.focusWidth,
            height: CompanionGeometry.Metrics.focusHeight,
            alignment: .top
        )
        .background(
            Rectangle().fill(
                reduceTransparency
                    ? DesignTokens.Palette.surfaceSolid
                    : DesignTokens.Palette.surfaceGlass
            )
        )
    }

    private var pendingRestore: CompanionTask? {
        guard let id = taskPanel.pendingRestoreID else { return nil }
        return history.first { $0.id == id }
    }

    /// При трёх занятых слотах пользователь выбирает, какую задачу заменить.
    /// Четвёртой не появляется, и текущая не вытесняется молча.
    private func replacementPrompt(for task: CompanionTask) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xxs) {
            Text("Чтобы вернуть «\(task.title)», выберите, что заменить:")
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
            ForEach(taskPanel.store.activeTasks) { victim in
                Button {
                    _ = taskPanel.restore(task, replacing: victim)
                } label: {
                    Text(victim.title)
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.textPrimary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 3)
                        .padding(.horizontal, DesignTokens.Spacing.xs)
                        .background(
                            RoundedRectangle(cornerRadius: 6, style: .continuous)
                                .fill(DesignTokens.Palette.surfaceRaised)
                        )
                }
                .buttonStyle(.plain)
            }
            Button("Отменить") { taskPanel.dismissRestorePrompt() }
                .buttonStyle(.plain)
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
        }
        .padding(DesignTokens.Spacing.xs)
        .background(
            RoundedRectangle(cornerRadius: DesignTokens.Radius.row, style: .continuous)
                .fill(DesignTokens.Palette.accentWarning.opacity(0.12))
        )
    }

    private func row(_ task: CompanionTask) -> some View {
        HStack(spacing: DesignTokens.Spacing.xs) {
            Circle()
                .fill(statusColor(task.status))
                .frame(width: 6, height: 6)
            VStack(alignment: .leading, spacing: 1) {
                Text(task.title)
                    .font(DesignTokens.Typography.compactTask())
                    .foregroundStyle(DesignTokens.Palette.textPrimary)
                    .lineLimit(1)
                Text(task.status.displayName)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
            }
            Spacer(minLength: DesignTokens.Spacing.xs)
            // Восстановление требует нового срока и свободного слота.
            Menu("Вернуть") {
                ForEach(CharacterConfig.deadlinePresets, id: \.self) { minutes in
                    Button("На \(minutes) минут") {
                        _ = taskPanel.restore(task, minutes: minutes)
                    }
                }
            }
            .menuStyle(.borderlessButton)
            .fixedSize()
            .font(DesignTokens.Typography.caption())
        }
        .padding(.vertical, DesignTokens.Spacing.xxs)
    }

    private func statusColor(_ status: TaskStatus) -> Color {
        switch status {
        case .completed: return DesignTokens.Palette.accentSuccess
        case .expired: return DesignTokens.Palette.accentRecording
        case .archived: return DesignTokens.Palette.textTertiary
        case .active: return DesignTokens.Palette.accentSelection
        }
    }
}
