import SwiftUI

/// Корневая вью персонажа.
///
/// Фаза 1: статичная чёрная капсула на весь доступный размер окна, без какого-
/// либо содержимого внутри — глаза, моргание и раскрытие появятся на Фазах 2-3.
struct NotchRootView: View {
    var body: some View {
        NotchShape(
            topCornerRadius: AppearanceConfig.topCornerRadius,
            bottomCornerRadius: AppearanceConfig.bottomCornerRadius
        )
        .fill(AppearanceConfig.capsuleColor)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Окно и так ignoresMouseEvents, но на всякий случай дублируем на уровне
        // вью — эта вью не должна становиться кликабельной ни при каких правках.
        .allowsHitTesting(false)
    }
}

#Preview {
    NotchRootView()
        .frame(width: 220, height: 32)
        .background(Color.gray)
}
