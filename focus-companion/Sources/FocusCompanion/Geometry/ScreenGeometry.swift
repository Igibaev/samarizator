import AppKit

/// Метрики выреза (notch) экрана.
///
/// `auxiliaryTopLeftArea` / `auxiliaryTopRightArea` — приватные-по-факту, но
/// публичные API AppKit (macOS 12.3+), описывающие безопасные для контента
/// зоны меню-бара слева и справа от выреза. Их наличие/отсутствие и есть
/// официальный способ понять, физически ли есть вырез на этом экране.
extension NSScreen {

    /// Экран, над которым сейчас находится курсор мыши. `nil`, если почему-то
    /// не удалось определить (теоретически не должно случаться — курсор всегда
    /// над каким-то экраном).
    static var screenWithMouse: NSScreen? {
        let mouseLocation = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(mouseLocation, $0.frame, false) }
    }

    /// Есть ли у экрана физический вырез камеры.
    var hasNotch: Bool {
        auxiliaryTopLeftArea?.width != nil && auxiliaryTopRightArea?.width != nil
    }

    /// Размер выреза в координатах экрана, если он есть.
    var notchSize: NSSize? {
        guard let leftPad = auxiliaryTopLeftArea?.width,
              let rightPad = auxiliaryTopRightArea?.width else { return nil }
        // Высота выреза = верхний safe area inset (глубина выреза от верхнего края экрана).
        // Ширина = вся ширина экрана минус "уши" меню-бара слева и справа от выреза,
        // где меню-бар остаётся обычной высоты.
        return NSSize(width: frame.width - leftPad - rightPad, height: safeAreaInsets.top)
    }

    /// Прямоугольник выреза в координатах экрана (низ = Y растёт вверх, как принято в AppKit).
    var notchFrame: NSRect? {
        guard let notchSize else { return nil }
        return NSRect(
            x: frame.midX - notchSize.width / 2,
            y: frame.maxY - notchSize.height,
            width: notchSize.width,
            height: notchSize.height
        )
    }

    /// Высота системного меню-бара (не совпадает с высотой выреза на "ушастых" экранах).
    var menubarHeight: CGFloat {
        frame.maxY - visibleFrame.maxY
    }

    /// Фрейм окна-капсулы с запасным вариантом.
    ///
    /// На экранах без физического выреза (MacBook Air без Face ID, внешний
    /// монитор) `notchFrame` равен `nil` — в этом случае рисуем виртуальную
    /// капсулу по центру верхнего края экрана, шириной из конфига и высотой
    /// в размер меню-бара, чтобы приложение выглядело одинаково на всех трёх
    /// конфигурациях (с вырезом / без выреза / внешний монитор).
    var notchFrameWithFallback: NSRect {
        if let notchFrame {
            return notchFrame
        }

        let fallbackWidth = AppearanceConfig.virtualCapsuleWidth
        let fallbackHeight = menubarHeight
        return NSRect(
            x: frame.midX - fallbackWidth / 2,
            y: frame.maxY - fallbackHeight,
            width: fallbackWidth,
            height: fallbackHeight
        )
    }

    /// Фрейм самой панели-капсулы — шире выреза на `topCornerRadius` с каждой
    /// стороны.
    ///
    /// Зачем: верхние углы `NotchShape` — вогнутые "загибы", которые съедают по
    /// `topCornerRadius` слева и справа от прямоугольника. Если дать панели
    /// ровно `notchFrame`, чёрное тело капсулы окажется УЖЕ выреза, и по краям
    /// выреза проступят два светлых клина — ровно тот шов, которого мы избегаем.
    /// Расширяем фрейм так, чтобы боковые рёбра тела совпали с краями выреза,
    /// а загибы легли поверх меню-бара (кликам это не мешает — панель
    /// `ignoresMouseEvents`).
    var capsulePanelFrame: NSRect {
        notchFrameWithFallback.insetBy(dx: -AppearanceConfig.topCornerRadius, dy: 0)
    }
}
