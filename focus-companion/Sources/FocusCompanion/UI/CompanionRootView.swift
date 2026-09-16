import SwiftUI

/// Содержимое верхнего окна: бесшовное правое крыло и то, что растёт под
/// вырезом — компактная полка задач или раскрытая панель фокуса.
///
/// Окно одно на обе поверхности: так стык крыла и полки не может разъехаться
/// между двумя независимо позиционируемыми окнами. Каждая поверхность
/// ставится в абсолютные координаты, вычисленные `CompanionGeometry`.
struct CompanionRootView: View {
    var stateMachine: CompanionStateMachine
    var hoverDetector: HoverDetector
    var taskPanel: TaskPanelController
    var navigation: NavigationController
    var recordings: RecordingsController
    var eyes: EyesViewModel

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @Environment(\.accessibilityReduceTransparency) private var reduceTransparency

    private var geometry: CompanionGeometry? { hoverDetector.geometry }

    var body: some View {
        GeometryReader { proxy in
            if let geometry {
                let window = geometry.topWindowRect
                ZStack(alignment: .topLeading) {
                    surfaces(geometry: geometry, window: window)
                }
                .frame(width: proxy.size.width, height: proxy.size.height, alignment: .topLeading)
            }
        }
        .ignoresSafeArea()
        .onChange(of: reduceMotion) { _, value in applyReduceMotion(value) }
        .onAppear { applyReduceMotion(reduceMotion) }
    }

    private func applyReduceMotion(_ value: Bool) {
        stateMachine.reduceMotion = value
        taskPanel.reduceMotion = value
        eyes.reduceMotion = value
    }

    @ViewBuilder
    private func surfaces(geometry: CompanionGeometry, window: CGRect) -> some View {
        // 1. Крыло: неподвижно, не растягивается вместе с focus.
        let wing = geometry.local(geometry.wingRect, in: window)
        wingSurface(geometry: geometry)
            .frame(width: wing.width, height: wing.height)
            .offset(x: wing.minX, y: wing.minY)

        // 2. Под вырезом — либо полка, либо раскрытая панель.
        if navigation.isFocusOpen {
            let focus = geometry.local(geometry.focusRect, in: window)
            focusSurface
                .frame(width: focus.width, height: focus.height)
                .offset(x: focus.minX, y: focus.minY)
                .transition(.opacity)
        } else if let shelfRect = geometry.shelfRect {
            let shelf = geometry.local(shelfRect, in: window)
            shelfSurface
                .frame(width: shelf.width, height: shelf.height)
                .offset(x: shelf.minX, y: shelf.minY)
                .transition(.opacity)
        }
    }

    // MARK: - Крыло

    private func wingSurface(geometry: CompanionGeometry) -> some View {
        let shape = WingShape(
            topCornerRadius: 10,
            bottomCornerRadius: max(0, geometry.wingRect.height - 10),
            roundsLeftSide: !geometry.wingIsSeamless
        )
        return ZStack {
            shape.fill(CharacterConfig.isDebug ? Color.red : DesignTokens.Palette.companionBase)
            WingView(
                eyes: eyes,
                stateMachine: stateMachine,
                recordings: recordings,
                isSeamless: geometry.wingIsSeamless,
                notchHeight: geometry.wingRect.height,
                now: taskPanel.now,
                onEyesTap: handleEyesTap
            )
        }
        .contentShape(shape)
    }

    /// Клик по глазам при закрытой панели открывает и закрепляет focus;
    /// при открытой правой панели — закрывает её и открывает focus.
    private func handleEyesTap() {
        if navigation.isDrawerOpen {
            navigation.show(page: .focus)
            return
        }
        navigation.toggleFocusPinned()
    }

    // MARK: - Полка и панель фокуса

    private var shelfSurface: some View {
        ShelfShape(cornerRadius: DesignTokens.Radius.shelf)
            .fill(DesignTokens.Palette.companionBase)
            .overlay {
                TaskShelfView(
                    taskPanel: taskPanel,
                    disposal: taskPanel.runningDisposal,
                    reduceMotion: reduceMotion
                )
            }
            .clipShape(ShelfShape(cornerRadius: DesignTokens.Radius.shelf))
    }

    private var focusSurface: some View {
        let shape = ShelfShape(cornerRadius: DesignTokens.Radius.focus)
        return shape
            .fill(reduceTransparency
                ? DesignTokens.Palette.surfaceSolid
                : DesignTokens.Palette.surfaceGlass)
            .overlay {
                FocusPanelView(
                    taskPanel: taskPanel,
                    navigation: navigation,
                    reduceMotion: reduceMotion,
                    reduceTransparency: reduceTransparency,
                    onHoldChange: { hoverDetector.holdsOpen = $0 }
                )
            }
            .overlay {
                shape.stroke(DesignTokens.Palette.strokeHairline, lineWidth: 1)
            }
            .clipShape(shape)
            .shadow(
                color: DesignTokens.Glass.shadowColor,
                radius: DesignTokens.Glass.shadowRadius / 2,
                x: 0,
                y: DesignTokens.Glass.shadowY / 2
            )
    }
}
