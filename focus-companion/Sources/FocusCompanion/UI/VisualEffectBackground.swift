import AppKit
import SwiftUI

/// Мост к `NSVisualEffectView` — настоящему матовому стеклу macOS.
///
/// Отличие от простой полупрозрачной заливки: стекло РАЗМЫВАЕТ то, что под
/// ним, а не просто просвечивает. Поэтому текст и картинки под капсулой не
/// читаются сквозь неё пятнами, а превращаются в мягкий фон — и граница с
/// физическим вырезом выглядит естественнее.
///
/// `blendingMode = .behindWindow` означает "размывать содержимое ПОЗАДИ окна"
/// (обои, чужие окна). Это работает только если само окно непрозрачно-пустое:
/// у `NotchPanel` выставлены `isOpaque = false` и `backgroundColor = .clear`.
struct VisualEffectBackground: NSViewRepresentable {
    var material: NSVisualEffectView.Material

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = .behindWindow
        // .active, а не .followsWindowActiveState: панель никогда не бывает
        // key-окном (canBecomeKey = false), и при автоматическом режиме стекло
        // осталось бы навсегда в "неактивном" виде.
        view.state = .active
        view.isEmphasized = false
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.material = material
    }
}
