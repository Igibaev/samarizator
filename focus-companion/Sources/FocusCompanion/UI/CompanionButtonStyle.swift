import SwiftUI

/// Кнопка 32 × 32 pt с иконкой 14–16 pt (design.md §4.1, §6.2).
///
/// Оформлены все состояния, которых требует задание: наведение, нажатие,
/// активное состояние, отключённая кнопка и клавиатурный фокус.
struct CompanionIconButtonStyle: ButtonStyle {
    var isActive: Bool = false
    var activeColor: Color = DesignTokens.Palette.accentSelection
    var isEnabled: Bool = true

    func makeBody(configuration: Configuration) -> some View {
        // Наведение живёт во вложенном View, а не в самом стиле: `@State`
        // внутри `ButtonStyle` не имеет надёжного хранилища — `makeBody`
        // возвращает View, но сам стилем View не является.
        IconButtonBody(
            configuration: configuration,
            isActive: isActive,
            activeColor: activeColor,
            isEnabled: isEnabled
        )
    }
}

private struct IconButtonBody: View {
    let configuration: ButtonStyleConfiguration
    let isActive: Bool
    let activeColor: Color
    let isEnabled: Bool

    @State private var isHovering = false

    var body: some View {
        configuration.label
            .frame(width: 32, height: 32)
            .background(Circle().fill(background))
            .opacity(isEnabled ? 1 : 0.38)
            .scaleEffect(configuration.isPressed ? 0.94 : 1)
            .animation(.easeOut(duration: DesignTokens.Motion.feedbackPress), value: configuration.isPressed)
            .onHover { hovering in
                isHovering = isEnabled && hovering
            }
            .contentShape(Circle())
    }

    private var background: Color {
        if isActive { return activeColor.opacity(0.22) }
        if configuration.isPressed { return Color.white.opacity(0.14) }
        if isHovering { return DesignTokens.Palette.surfaceHover }
        return .clear
    }
}

/// Тонкий синий контур клавиатурного фокуса — не «просто посветлее серым».
struct KeyboardFocusRing: ViewModifier {
    var isFocused: Bool
    var cornerRadius: CGFloat = DesignTokens.Radius.row

    func body(content: Content) -> some View {
        content.overlay(
            RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                .strokeBorder(DesignTokens.Palette.accentSelection, lineWidth: isFocused ? 1.5 : 0)
                .opacity(isFocused ? 1 : 0)
        )
    }
}

extension View {
    func keyboardFocusRing(_ isFocused: Bool, cornerRadius: CGFloat = DesignTokens.Radius.row) -> some View {
        modifier(KeyboardFocusRing(isFocused: isFocused, cornerRadius: cornerRadius))
    }

    /// Дымчатое стекло: один слой размытия и одна тонкая внутренняя линия.
    /// При Reduce Transparency — непрозрачная `surface.solid`.
    func companionGlass(cornerRadius: CGFloat, reduceTransparency: Bool) -> some View {
        self
            .background(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .fill(reduceTransparency ? DesignTokens.Palette.surfaceSolid : DesignTokens.Palette.surfaceGlass)
            )
            .overlay(
                RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
                    .strokeBorder(DesignTokens.Palette.strokeHairline, lineWidth: 1)
            )
            .shadow(
                color: DesignTokens.Glass.shadowColor,
                radius: DesignTokens.Glass.shadowRadius / 2,
                x: 0,
                y: DesignTokens.Glass.shadowY / 2
            )
    }
}
