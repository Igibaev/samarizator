import AppKit

/// Мост между AppKit и чистой `CompanionGeometry`.
///
/// `auxiliaryTopLeftArea` / `auxiliaryTopRightArea` — публичные API AppKit
/// (macOS 12.3+), описывающие безопасные зоны меню-бара слева и справа от
/// выреза. Их наличие и есть официальный способ понять, есть ли вырез.
extension NSScreen {

    /// Экран, над которым сейчас курсор.
    static var screenWithMouse: NSScreen? {
        let mouseLocation = NSEvent.mouseLocation
        return NSScreen.screens.first { NSMouseInRect(mouseLocation, $0.frame, false) }
    }

    var hasNotch: Bool {
        auxiliaryTopLeftArea?.width != nil && auxiliaryTopRightArea?.width != nil
    }

    var notchSize: NSSize? {
        guard let leftPad = auxiliaryTopLeftArea?.width,
              let rightPad = auxiliaryTopRightArea?.width else { return nil }
        return NSSize(width: frame.width - leftPad - rightPad, height: safeAreaInsets.top)
    }

    /// Прямоугольник выреза в координатах экрана, если он есть.
    var notchFrame: NSRect? {
        guard let notchSize else { return nil }
        return NSRect(
            x: frame.midX - notchSize.width / 2,
            y: frame.maxY - notchSize.height,
            width: notchSize.width,
            height: notchSize.height
        )
    }

    var menubarHeight: CGFloat {
        let measured = frame.maxY - visibleFrame.maxY
        // На экране без меню-бара (внешний монитор в некоторых конфигурациях)
        // разность равна нулю — тогда капсуле нужна хоть какая-то высота.
        return measured > 0 ? measured : 24
    }

    /// Полная геометрия компаньона для этого экрана.
    func companionGeometry(
        activeTaskCount: Int,
        isFocusOpen: Bool,
        isRecording: Bool
    ) -> CompanionGeometry {
        CompanionGeometry(
            screenFrame: frame,
            visibleFrame: visibleFrame,
            notchRect: notchFrame,
            menuBarHeight: menubarHeight,
            activeTaskCount: activeTaskCount,
            isFocusOpen: isFocusOpen,
            isRecording: isRecording
        )
    }

    var geometryDescription: String {
        let notch = notchSize.map { "\($0.width)x\($0.height)" } ?? "НЕТ"
        let geometry = companionGeometry(activeTaskCount: 3, isFocusOpen: false, isRecording: false)
        return "экран \(localizedName)\n"
            + "  frame=\(frame)\n"
            + "  visibleFrame=\(visibleFrame)\n"
            + "  вырез: \(notch), safeAreaInsets.top=\(safeAreaInsets.top), menubarHeight=\(menubarHeight)\n"
            + "  крыло=\(geometry.wingRect) бесшовное=\(geometry.wingIsSeamless)\n"
            + "  полка (3 задачи)=\(String(describing: geometry.shelfRect))\n"
            + "  focus=\(geometry.focusRect)\n"
            + "  окно верхней панели=\(geometry.topWindowRect)\n"
            + "  правая панель=\(geometry.drawerRect) (ширина \(geometry.drawerWidth))"
    }
}
