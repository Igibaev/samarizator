import AppKit

/// Окно верхней панели: крыло + полка/focus.
///
/// `.nonactivatingPanel` и `becomesKeyOnlyIfNeeded` нужны, чтобы компаньон не
/// забирал клавиатурный фокус у активного приложения: поле ввода получает
/// его только после явного клика.
final class NotchPanel: NSPanel {
    init(contentRect: NSRect) {
        super.init(
            contentRect: contentRect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        level = .screenSaver
        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]
        ignoresMouseEvents = true
        isMovableByWindowBackground = false
        becomesKeyOnlyIfNeeded = true
    }

    /// Панель имеет право стать key-окном только когда раскрыта и в ней
    /// действительно есть поле ввода.
    var allowsKeyWhenExpanded = false

    override var canBecomeKey: Bool { allowsKeyWhenExpanded }
    override var canBecomeMain: Bool { false }
}

/// Правая панель. Отдельное окно: у неё своя геометрия, свой уровень
/// взаимодействия и своё правило закрытия — уход курсора её не закрывает.
final class DrawerPanel: NSPanel {
    /// Вызывается при клике мимо панели, когда она не закреплена.
    var onOutsideClick: (() -> Void)?

    private var outsideClickMonitor: Any?

    init(contentRect: NSRect) {
        super.init(
            contentRect: contentRect,
            styleMask: [.borderless, .nonactivatingPanel],
            backing: .buffered,
            defer: false
        )
        backgroundColor = .clear
        isOpaque = false
        hasShadow = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        isMovableByWindowBackground = false
        becomesKeyOnlyIfNeeded = true
        hidesOnDeactivate = false
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }

    /// Незакреплённая панель закрывается по внешнему клику, сохраняя
    /// страницу, выделение и позицию чтения.
    func startWatchingOutsideClicks() {
        stopWatchingOutsideClicks()
        outsideClickMonitor = NSEvent.addGlobalMonitorForEvents(
            matching: [.leftMouseDown, .rightMouseDown]
        ) { [weak self] _ in
            guard let self else { return }
            let location = NSEvent.mouseLocation
            guard !self.frame.contains(location) else { return }
            self.onOutsideClick?()
        }
    }

    func stopWatchingOutsideClicks() {
        if let outsideClickMonitor {
            NSEvent.removeMonitor(outsideClickMonitor)
        }
        outsideClickMonitor = nil
    }

    // Снятия монитора в `deinit` здесь СПЕЦИАЛЬНО нет: `NSPanel` изолирован
    // на главном акторе, а `deinit` выполняется вне его — обращаться из него
    // к изолированным свойствам нельзя. Монитор снимает
    // `stopWatchingOutsideClicks()`, который зовётся при каждом закрытии и
    // при закреплении панели.
}
