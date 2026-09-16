import SwiftUI

/// Правая панель: одна оболочка, одна позиция, один механизм ресайза
/// для «Буфера» и «Записей» (design.md §9.1).
///
/// Панель НЕ закрывается при уходе мыши: правило 450 мс относится только
/// к временному focus.
struct DrawerRootView: View {
    var navigation: NavigationController
    var clipboard: ClipboardService
    var recordings: RecordingsController
    var taskPanel: TaskPanelController
    var geometry: CompanionGeometry

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency
    @FocusState private var pageSwitcherFocused: Bool

    var body: some View {
        VStack(spacing: 0) {
            navigationBar
            Divider().overlay(DesignTokens.Palette.strokeHairline)
            content
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        }
        .padding(CompanionGeometry.Metrics.drawerPadding)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(
            RoundedRectangle(cornerRadius: DesignTokens.Radius.drawer, style: .continuous)
                .fill(reduceTransparency
                    ? DesignTokens.Palette.surfaceSolid
                    : DesignTokens.Palette.surfaceGlass)
        )
        .overlay(
            RoundedRectangle(cornerRadius: DesignTokens.Radius.drawer, style: .continuous)
                .strokeBorder(DesignTokens.Palette.strokeHairline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: DesignTokens.Radius.drawer, style: .continuous))
        .offset(x: navigation.edgeResistanceOffset)
        .overlay(alignment: .bottomLeading) { gestureHint }
    }

    // MARK: - Верхняя строка 48 pt

    private var navigationBar: some View {
        HStack(spacing: DesignTokens.Spacing.s) {
            pageSwitcher
            Spacer(minLength: 0)
            Button {
                navigation.drawerPinned.toggle()
            } label: {
                Image(systemName: navigation.drawerPinned ? "pin.fill" : "pin")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(navigation.drawerPinned
                        ? DesignTokens.Palette.accentSelection
                        : DesignTokens.Palette.textSecondary)
            }
            .buttonStyle(CompanionIconButtonStyle(isActive: navigation.drawerPinned))
            .help(navigation.drawerPinned ? "Открепить панель" : "Закрепить панель")

            Button {
                navigation.closeDrawer()
            } label: {
                Image(systemName: "xmark")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
            }
            .buttonStyle(CompanionIconButtonStyle())
            .help("Закрыть панель")
            .keyboardShortcut(.escape, modifiers: [])
        }
        .frame(height: 48)
    }

    /// Видимая альтернатива жесту: переключатель страниц. При фокусе на нём
    /// стрелки влево/вправо меняют страницу.
    private var pageSwitcher: some View {
        HStack(spacing: 2) {
            ForEach(CompanionPage.allCases) { page in
                Button {
                    navigation.show(page: page)
                } label: {
                    Text(page.title)
                        .font(DesignTokens.Typography.compactTask())
                        .foregroundStyle(navigation.page == page
                            ? DesignTokens.Palette.textPrimary
                            : DesignTokens.Palette.textSecondary)
                        .padding(.horizontal, DesignTokens.Spacing.s)
                        .padding(.vertical, 5)
                        .background(
                            Capsule().fill(navigation.page == page
                                ? DesignTokens.Palette.accentSelection.opacity(0.26)
                                : Color.clear)
                        )
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Страница «\(page.title)»")
            }
        }
        .padding(2)
        .background(Capsule().fill(DesignTokens.Palette.surfaceRaised))
        .focusable()
        .focused($pageSwitcherFocused)
        .keyboardFocusRing(pageSwitcherFocused, cornerRadius: 16)
        .onMoveCommand { direction in
            switch direction {
            case .left: navigation.advance(.right)
            case .right: navigation.advance(.left)
            default: break
            }
        }
    }

    // MARK: - Содержимое

    @ViewBuilder
    private var content: some View {
        switch navigation.page {
        case .clipboard:
            ClipboardPageView(clipboard: clipboard)
        case .recordings:
            RecordingsPageView(
                recordings: recordings,
                taskPanel: taskPanel,
                geometry: geometry
            )
        case .focus:
            // Страница «Фокус» живёт под вырезом; в правой панели её не дублируем.
            Text("Фокус открыт под вырезом.")
                .font(DesignTokens.Typography.transcript())
                .foregroundStyle(DesignTokens.Palette.textSecondary)
        }
    }

    @ViewBuilder
    private var gestureHint: some View {
        if navigation.gestureHintVisible {
            HStack(spacing: DesignTokens.Spacing.xs) {
                Text("Два пальца влево — буфер и записи")
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.textSecondary)
                Button("Больше не показывать") { navigation.dismissGestureHint() }
                    .buttonStyle(.plain)
                    .font(DesignTokens.Typography.caption())
                    .foregroundStyle(DesignTokens.Palette.accentSelection)
            }
            .padding(.horizontal, DesignTokens.Spacing.s)
            .padding(.vertical, DesignTokens.Spacing.xs)
            .background(Capsule().fill(DesignTokens.Palette.surfaceRaised))
            .padding(DesignTokens.Spacing.m)
        }
    }
}
