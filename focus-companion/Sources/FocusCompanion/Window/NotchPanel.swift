import AppKit

/// Окно-панель для капсулы над вырезом.
///
/// Полностью прозрачная, без рамки. В свёрнутом состоянии не реагирует на
/// клики мыши вообще и никогда не может забрать фокус (Фаза 1). В раскрытом
/// состоянии интерактивна (Фаза 3) и может ненадолго стать key-окном на
/// время ввода текста в поле новой задачи (Фаза 4а) — см. `allowsKeyWhenExpanded`.
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

        // Фаза 4а: полю ввода новой задачи нужен фокус (key window), иначе
        // клавиатурный ввод в него физически не попадёт — `.nonactivatingPanel`
        // сознательно подавляет обычное AppKit-поведение "клик = активация
        // окна", на котором чекбоксы и так прекрасно работают без key-статуса.
        //
        // `becomesKeyOnlyIfNeeded` — штатное AppKit-свойство `NSPanel` именно
        // под этот случай: панель становится key ТОЛЬКО когда конкретное вью
        // внутри реально запросило firstResponder (текстовое поле при клике),
        // а не от любого клика по панели. Это снимает "курицу и яйцо" — не
        // нужно вручную угадывать момент клика до его обработки AppKit, чтобы
        // заранее выставить `canBecomeKey`.
        becomesKeyOnlyIfNeeded = true
    }

    /// Разрешение панели стать key-окном. Обновляется контроллером синхронно
    /// со свёрнутым/раскрытым состоянием (`NotchWindowController`).
    ///
    /// Свёрнутая панель ГАРАНТИРОВАННО не может перехватить фокус — это
    /// критерий Фазы 1, повторное нарушение которого недопустимо (см.
    /// HANDOFF.md). Раскрытая панель тоже НЕ становится key просто оттого,
    /// что раскрыта: `becomesKeyOnlyIfNeeded` выше сужает это ещё дальше —
    /// фактически до момента клика в текстовое поле ввода задачи.
    var allowsKeyWhenExpanded = false

    override var canBecomeKey: Bool { allowsKeyWhenExpanded }
    override var canBecomeMain: Bool { false }
}
