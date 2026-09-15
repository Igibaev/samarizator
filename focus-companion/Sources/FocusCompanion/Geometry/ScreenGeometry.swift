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

    /// Фрейм капсулы в СВЁРНУТОМ состоянии — шире и глубже физического выреза.
    ///
    /// Ширина: базовое расширение на `topCornerRadius` с каждой стороны —
    /// верхние углы `NotchShape` вогнутые "загибы", которые съедают по
    /// `topCornerRadius` слева и справа от прямоугольника; без этого запаса
    /// чёрное тело капсулы оказалось бы УЖЕ выреза, и по краям проступили бы
    /// два светлых клина — ровно тот шов, которого мы избегаем. Поверх этого
    /// ширина берётся из `collapsedWidth`
    /// сделать персонажа шире выреза (см. AppearanceConfig).
    ///
    /// Глубина: капсула дополнительно свисает вниз на `capsuleExtraDepth`
    /// (и ещё на `debugExtraHeight` в debug-режиме). Верхний край при этом
    /// НЕ трогаем — он обязан остаться прижат к краю экрана, иначе загибы
    /// перестанут стыковаться с физическим вырезом без шва.
    ///
    /// Центрирование по X не ломается: `insetBy` симметричен, а
    /// `notchFrameWithFallback` и так уже центрирован по `frame.midX`.
    ///
    /// До Фазы 3 это был фрейм самой панели (`NSPanel`). С Фазы 3 панель
    /// ВСЕГДА в размере раскрытого состояния (см. `capsulePanelFrame` ниже),
    /// а это — размер и позиция капсулы ВНУТРИ окна, когда она не раскрыта
    /// (используется `HoverDetector` для проверки наведения и `NotchRootView`
    /// для размера отрисовки).
    var collapsedCapsuleFrame: NSRect {
        let notch = notchFrameWithFallback

        // Высота — ровно в вырез: капсула сидит в той же полосе, что и он.
        let height = notch.height
            + AppearanceConfig.capsuleExtraDepth
            + AppearanceConfig.debugExtraHeight

        // Тело начинается от ЛЕВОГО края выреза и тянется вправо, поглощая
        // сам вырез. Если начинать от правого края, у стыка видно скругление
        // капсулы — шов между вырезом и персонажем.
        //
        // Загибы формы (topCornerRadius) торчат за пределы тела с обеих
        // сторон, поэтому фрейм окна шире тела на их удвоенную ширину.
        let flare = AppearanceConfig.topCornerRadius
        let bodyWidth = notch.width + AppearanceConfig.collapsedExtensionRight

        return NSRect(
            x: notch.minX - flare,
            y: frame.maxY - height,
            width: bodyWidth + flare * 2,
            height: height
        )
    }

    /// Верхний край, как и у `collapsedCapsuleFrame`, совпадает с верхним
    /// краем экрана (иначе верх капсулы в любом из состояний "оторвётся" от
    /// выреза); по горизонтали окно центрировано на экране.
    var capsulePanelFrame: NSRect {
        let width = AppearanceConfig.expandedWidth
        let height = AppearanceConfig.expandedHeight

        // Окно центрируется по ВЫРЕЗУ: раскрытие должно расти симметрично
        // вокруг него, как будто вырез — центр персонажа.
        var x = notchFrameWithFallback.midX - width / 2
        x = min(max(x, frame.minX), frame.maxX - width)

        return NSRect(x: x, y: frame.maxY - height, width: width, height: height)
    }

    /// Горизонтальное смещение центра СВЁРНУТОЙ капсулы относительно центра
    /// окна. Нужно потому, что окно центрировано по вырезу, а свёрнутая
    /// капсула уходит вправо.
    var collapsedOffsetXInPanel: CGFloat {
        collapsedCapsuleFrame.midX - capsulePanelFrame.midX
    }

    /// Горизонтальное положение глаз относительно центра окна.
    ///
    /// Глаза сидят по центру той части капсулы, что торчит СПРАВА от выреза —
    /// то есть там же, где они видны в свёрнутом состоянии. Значение общее
    /// для обоих состояний: окно центрировано по вырезу и не переезжает, а
    /// значит и глаза при раскрытии не должны смещаться по горизонтали.
    var eyesOffsetXInPanel: CGFloat {
        let notch = notchFrameWithFallback
        let eyesCenterX = notch.maxX + AppearanceConfig.collapsedExtensionRight / 2
        return eyesCenterX - capsulePanelFrame.midX
    }

    /// Метрики экрана одной строкой — для отладочного вывода при запуске.
    var geometryDescription: String {
        let notch = notchSize.map { "\($0.width)x\($0.height)" } ?? "НЕТ"
        return "экран \(localizedName)\n"
            + "  frame=\(frame)\n"
            + "  visibleFrame=\(visibleFrame)\n"
            + "  вырез: \(notch), safeAreaInsets.top=\(safeAreaInsets.top), menubarHeight=\(menubarHeight)\n"
            + "  auxTopLeft=\(String(describing: auxiliaryTopLeftArea))\n"
            + "  auxTopRight=\(String(describing: auxiliaryTopRightArea))\n"
            + "  фрейм капсулы (свёрнуто)=\(collapsedCapsuleFrame)\n"
            + "  фрейм панели (=капсула раскрыто)=\(capsulePanelFrame)"
    }
}
