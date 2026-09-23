import SwiftUI

/// Короткое сообщение под полкой задач в КОМПАКТНОМ виде.
///
/// Нужно для вещей, которые обязаны работать при закрытой панели:
/// - подпись напоминания на 4 секунды (design.md §11.3 — панели не открываются сами);
/// - «Задача сгорела» и кнопка «Вернуть» на 8 секунд (§11.2);
/// - реплика «принёс дела со встречи» с кнопкой «Открыть» (передача от
///   Samarizator, `TaskPanelController.handoffNotice`).
///
/// Если показать их только в раскрытом focus, пользователь их не увидит:
/// большую часть времени панель закрыта.
struct CompactNoticeView: View {
    var taskPanel: TaskPanelController
    var reduceMotion: Bool

    var body: some View {
        HStack(spacing: DesignTokens.Spacing.xs) {
            if taskPanel.recentlyExpired != nil {
                DisposedPaperGlyph(reduceMotion: reduceMotion)
            }
            Text(text)
                .font(DesignTokens.Typography.compactTask())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: DesignTokens.Spacing.xs)
            if let expired = taskPanel.recentlyExpired {
                Button("Вернуть") { _ = taskPanel.restore(expired) }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.accentSelection)
                    .padding(.horizontal, DesignTokens.Spacing.xs)
                    .padding(.vertical, 3)
                    .background(
                        Capsule().fill(DesignTokens.Palette.accentSelection.opacity(0.18))
                    )
            }
            if taskPanel.recentlyExpired == nil, taskPanel.handoffNotice != nil {
                Button("Открыть") { taskPanel.openHandoff() }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.accentSelection)
                    .padding(.horizontal, DesignTokens.Spacing.xs)
                    .padding(.vertical, 3)
                    .background(
                        Capsule().fill(DesignTokens.Palette.accentSelection.opacity(0.18))
                    )
                    .help("Страница «Записи» с этой встречей")
            }
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 8, weight: .semibold))
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
                    .frame(width: 16, height: 16)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help("Скрыть сообщение")
        }
        .padding(.horizontal, DesignTokens.Spacing.s)
        .frame(width: CompanionGeometry.Metrics.shelfWidth,
               height: CompanionGeometry.Metrics.noticeHeight)
        .accessibilityElement(children: .combine)
        .accessibilityLabel(text)
    }

    private var text: String {
        if let expired = taskPanel.recentlyExpired {
            return "Задача «\(expired.title)» сгорела"
        }
        if let caption = taskPanel.reminderCaption { return caption }
        if let message = taskPanel.catchUpMessage { return message }
        if let message = taskPanel.slotsFullMessage { return message }
        if let handoff = taskPanel.handoffNotice { return handoff.line.text }
        if let line = taskPanel.companionLine { return line }
        return ""
    }

    private func dismiss() {
        if taskPanel.recentlyExpired != nil {
            taskPanel.dismissExpiredNotice()
        } else if taskPanel.catchUpMessage != nil {
            taskPanel.dismissCatchUpMessage()
        } else if taskPanel.handoffNotice != nil {
            taskPanel.dismissHandoffNotice()
        } else if taskPanel.companionLine != nil {
            taskPanel.dismissCompanionLine()
        }
    }
}

/// Сгоревший листок бумаги рядом с сообщением — временный графический
/// предмет, который появляется только на время сообщения и исчезает вместе
/// с ним. Постоянных рук и тела у персонажа нет.
private struct DisposedPaperGlyph: View {
    var reduceMotion: Bool

    var body: some View {
        ZStack(alignment: .topTrailing) {
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(DesignTokens.Palette.textSecondary.opacity(0.65))
                .frame(width: 11, height: 14)
                .rotationEffect(.degrees(-8))
            if !reduceMotion {
                SparkShape()
                    .fill(DesignTokens.Palette.accentWarning)
                    .frame(width: 7, height: 7)
                    .offset(x: 3, y: -2)
            }
        }
        .frame(width: 16, height: 16)
        .allowsHitTesting(false)
    }
}
