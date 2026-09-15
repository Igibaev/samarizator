import AppKit
import SwiftUI

/// Маска формы капсулы для `NSVisualEffectView`.
///
/// Стекло нельзя обрезать средствами SwiftUI (`clipShape`): обрезка уводит
/// вью в отдельный слой, и размытие того, что ПОЗАДИ окна, при этом
/// теряется — остаётся плоская заливка. Правильный способ придать стеклу
/// форму — его собственное свойство `maskImage`.
enum NotchShapeMask {

    /// Рисует ту же самую `NotchShape` в картинку-маску нужного размера.
    ///
    /// `flipped: true` — чтобы система координат совпала со SwiftUI (Y вниз)
    /// и можно было переиспользовать путь формы один в один, без ручного
    /// зеркалирования, которое разошлось бы с оригиналом при первой же правке.
    static func image(size: NSSize) -> NSImage {
        let image = NSImage(size: size, flipped: true) { rect in
            guard let context = NSGraphicsContext.current?.cgContext else { return false }

            let path = NotchShape(
                topCornerRadius: AppearanceConfig.topCornerRadius,
                bottomCornerRadius: AppearanceConfig.bottomCornerRadius
            ).path(in: rect).cgPath

            context.addPath(path)
            NSColor.black.setFill()
            context.fillPath()
            return true
        }
        // Маска трактуется как шаблон: значение имеет альфа, а не цвет.
        image.isTemplate = true
        return image
    }
}
