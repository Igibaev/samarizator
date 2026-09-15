import AppKit

/// Окно-панель для капсулы над вырезом.
///
/// Полностью прозрачная, без рамки, никогда не забирает фокус и (на Фазе 1)
/// не реагирует на клики мыши вообще.
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

        // .screenSaver, а не .statusBar: капсула должна оставаться видимой даже
        // поверх full-screen приложений (этому же служит .fullScreenAuxiliary
        // ниже в collectionBehavior). .statusBar лежит ниже full-screen слоя и
        // на полноэкранном приложении исчезнет — а вырез экрана физически
        // никуда не девается, значит и капсула должна оставаться на месте.
        level = .screenSaver

        collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary]

        // Фаза 1: у панели нет интерактивного содержимого, поэтому она не должна
        // перехватывать ни один клик — меню-бар под и вокруг капсулы обязан
        // остаться полностью кликабельным.
        ignoresMouseEvents = true

        isMovableByWindowBackground = false
    }

    // Окно не должно ни при каких условиях забирать фокус у активного приложения.
    // На Фазе 3, когда появится ввод (взаимодействие при раскрытии), это будет
    // пересмотрено осознанно, а не как побочный эффект.
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
