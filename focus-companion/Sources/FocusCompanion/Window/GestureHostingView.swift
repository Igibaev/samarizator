import AppKit
import SwiftUI

/// `NSHostingView`, который пропускает горизонтальные свайпы двумя пальцами
/// в распознаватель и отдаёт всё остальное обычной прокрутке SwiftUI.
///
/// Распознавание работает ТОЛЬКО внутри самого компаньона: глобальные жесты
/// Spaces и Mission Control не перехватываются — этот класс видит события
/// лишь тогда, когда курсор над его собственным окном.
final class GestureHostingView<Content: View>: NSHostingView<Content> {

    let swipeRecognizer = SwipeRecognizer()

    /// Жест распознаётся, только если он начался внутри разрешённой зоны.
    /// Возвращает `true` для точки в координатах ЭКРАНА.
    var isGestureAllowed: ((CGPoint) -> Bool)?

    required init(rootView: Content) {
        super.init(rootView: rootView)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) не используется: вью создаётся только кодом")
    }

    override func scrollWheel(with event: NSEvent) {
        let allowed = isGestureAllowed?(NSEvent.mouseLocation) ?? true
        guard allowed else {
            super.scrollWheel(with: event)
            return
        }
        // Вертикальная прокрутка списков сохраняется: распознаватель
        // поглощает событие, только когда сам зафиксировал горизонтальную ось.
        if swipeRecognizer.handle(event) { return }
        super.scrollWheel(with: event)
    }
}
