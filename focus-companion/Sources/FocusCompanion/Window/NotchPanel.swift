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

        // Стартовое значение — свёрнутое состояние. С Фазы 3 это больше не
        // константа на всю жизнь панели: `NotchWindowController` переключает
        // флаг при раскрытии/сворачивании по наведению (см. `HoverDetector`),
        // но начинать нужно именно с true — иначе на долю секунды между
        // созданием панели и первым тиком опроса наведения меню-бар под
        // капсулой оказался бы некликабельным.
        ignoresMouseEvents = true

        isMovableByWindowBackground = false
    }

    // Окно по-прежнему не должно становиться key/main — раскрытая панель
    // Фазы 3 интерактивна по клику (в Фазе 4 — чекбоксы задач), но текстового
    // ввода или чего-либо, требующего первого respondera, там нет. Если это
    // изменится, пересматривать нужно осознанно, а не как побочный эффект.
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}
