import SwiftUI

/// Буфер обмена (design.md §9.2).
///
/// История включается явным действием. До включения показывается кнопка
/// «Включить историю буфера» — и никаких «уже собранных» данных за ней нет.
struct ClipboardPageView: View {
    var clipboard: ClipboardService

    @State private var query = ""
    @State private var selectedID: UUID?
    @FocusState private var searchFocused: Bool

    private var items: [ClipboardItem] { clipboard.filtered(query: query) }

    var body: some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.s) {
            header
            if !clipboard.isEnabled {
                optIn
            } else if items.isEmpty {
                emptyState
            } else {
                list
            }
        }
        .padding(.top, DesignTokens.Spacing.s)
    }

    private var header: some View {
        HStack(spacing: DesignTokens.Spacing.s) {
            Text("Буфер")
                .font(DesignTokens.Typography.panelTitle())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
            if clipboard.isEnabled {
                TextField("Поиск в буфере", text: $query)
                    .textFieldStyle(.plain)
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(DesignTokens.Palette.textPrimary)
                    .focused($searchFocused)
                    .padding(.vertical, 5)
                    .padding(.horizontal, DesignTokens.Spacing.xs)
                    .background(
                        RoundedRectangle(cornerRadius: 8, style: .continuous)
                            .fill(DesignTokens.Palette.surfaceRaised)
                    )
                    .keyboardFocusRing(searchFocused, cornerRadius: 8)
                Spacer(minLength: 0)
                Menu {
                    Toggle("Пауза истории буфера", isOn: Binding(
                        get: { clipboard.isPaused },
                        set: { clipboard.setPaused($0) }
                    ))
                    Divider()
                    Button("Выключить историю буфера") { clipboard.setEnabled(false) }
                } label: {
                    Image(systemName: "ellipsis")
                        .font(.system(size: 12, weight: .medium))
                        .foregroundStyle(DesignTokens.Palette.textSecondary)
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
            }
        }
    }

    private var optIn: some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
            Text("История буфера выключена")
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textPrimary)
            Text("Пока она выключена, компаньон не читает буфер обмена вообще. "
                + "Надёжно распознать в скопированном пароли и секреты он не умеет — "
                + "это стоит учитывать, включая историю.")
                .font(DesignTokens.Typography.caption())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            Button("Включить историю буфера") { clipboard.setEnabled(true) }
                .buttonStyle(.plain)
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.accentSelection)
        }
    }

    private var emptyState: some View {
        Text("Здесь появится скопированное")
            .font(DesignTokens.Typography.transcript())
            .foregroundStyle(DesignTokens.Palette.textTertiary)
    }

    private var list: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 0) {
                ForEach(items) { item in
                    row(item)
                    if item.id != items.last?.id {
                        Divider().overlay(DesignTokens.Palette.strokeHairline)
                    }
                }
            }
        }
    }

    private func row(_ item: ClipboardItem) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xxs) {
            Button {
                selectedID = selectedID == item.id ? nil : item.id
            } label: {
                HStack(alignment: .top, spacing: DesignTokens.Spacing.xs) {
                    Image(systemName: item.type.symbolName)
                        .font(.system(size: 12, weight: .regular))
                        .foregroundStyle(DesignTokens.Palette.textSecondary)
                        .frame(width: 18)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.primaryLine)
                            .font(DesignTokens.Typography.transcript())
                            .foregroundStyle(DesignTokens.Palette.textPrimary)
                            // Текстовое превью — максимум три строки.
                            .lineLimit(3)
                            .multilineTextAlignment(.leading)
                        if let subtitle = subtitle(for: item) {
                            Text(subtitle)
                                .font(DesignTokens.Typography.meta())
                                .foregroundStyle(DesignTokens.Palette.textTertiary)
                        }
                    }
                    Spacer(minLength: 0)
                    if item.pinned {
                        Image(systemName: "pin.fill")
                            .font(.system(size: 9))
                            .foregroundStyle(DesignTokens.Palette.accentSelection)
                    }
                }
                .padding(.vertical, DesignTokens.Spacing.xs)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if selectedID == item.id {
                detail(item)
            }
        }
        .background(
            RoundedRectangle(cornerRadius: DesignTokens.Radius.row, style: .continuous)
                .fill(selectedID == item.id ? DesignTokens.Palette.surfaceRaised : Color.clear)
        )
        .contextMenu {
            Button(item.pinned ? "Открепить" : "Закрепить") { clipboard.togglePin(item.id) }
            Button("Удалить") { clipboard.delete(item.id) }
        }
    }

    /// Источник и время показываются, только если эти сведения реально есть.
    private func subtitle(for item: ClipboardItem) -> String? {
        var parts: [String] = []
        if let domain = item.domain { parts.append(domain) }
        if let source = item.sourceLabel { parts.append(source) }
        parts.append(TimecodeFormatter.clockLabel(for: item.capturedAt))
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private func detail(_ item: ClipboardItem) -> some View {
        VStack(alignment: .leading, spacing: DesignTokens.Spacing.xs) {
            Text(item.contentReference)
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: DesignTokens.Spacing.s) {
                Button("Копировать") { clipboard.copy(item) }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.transcript())
                    .foregroundStyle(DesignTokens.Palette.accentSelection)
                if clipboard.lastCopiedID == item.id {
                    Text("Скопировано")
                        .font(DesignTokens.Typography.caption())
                        .foregroundStyle(DesignTokens.Palette.accentSuccess)
                }
                Spacer(minLength: 0)
                // Содержимое буфера не становится задачей или запросом к модели
                // само: для этого есть явное действие.
                Text("Обработать — в будущей интеграции")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textTertiary)
            }
        }
        .padding(.horizontal, DesignTokens.Spacing.xs)
        .padding(.bottom, DesignTokens.Spacing.xs)
    }
}
