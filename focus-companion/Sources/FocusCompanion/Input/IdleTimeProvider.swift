import CoreGraphics

/// Даёт "давность" последнего пользовательского ввода (мышь, клавиатура,
/// трекпад) во всей системе — БЕЗ разрешений Accessibility.
///
/// Нужен ровно для одной вещи (Фаза 4в, PHASE-4C-PROMPT.md): понять, что
/// человек отошёл или залип, и не лезть с напоминанием, пока он активно
/// печатает и кликает. Прямое требование автора — просить у пользователя
/// доступ к клавиатуре ради напоминалки недопустимо, а `CGEventSource`
/// (в отличие от глобального монитора клавиатуры, `NSEvent.addGlobalMonitorForEvents`
/// с `.keyDown`) такого разрешения не требует: это тот же системный
/// источник, которым пользуются экранные заставки и энергосбережение.
enum IdleTimeProvider {

    /// `kCGAnyInputEventType` (0xFFFFFFFF) — системная константа "любой тип
    /// события" из `CGEventTypes.h`, исторически используемая именно для
    /// измерения простоя (тот же механизм, что у заставок/энергосбережения).
    ///
    /// НЕ ПРОВЕРЕНО КОМПИЛЯЦИЕЙ (Linux-контейнер, нет macOS SDK) — уверенность
    /// в этом месте средняя. `CGEventType` импортируется в Swift как
    /// non-frozen C-перечисление (структура с `RawRepresentable`, а не
    /// закрытый `enum`), и для таких типов `init(rawValue:)` НЕ является
    /// проваливающимся (тот же паттерн, что у `CGBlendMode`, `CGImageAlphaInfo`)
    /// — поэтому здесь нет `!`/`?`. Но сама константа `kCGAnyInputEventType`
    /// в Swift по имени не экспортируется (публикуется только конечный
    /// список конкретных типов событий), поэтому она собрана руками из
    /// сырого значения, задокументированного в заголовке. Если на живой
    /// машине это не даст ожидаемого поведения (например,
    /// `secondsSinceLastEventType` с этим значением всегда возвращает 0
    /// или явно неверное число) — запасной вариант, который явно допускает
    /// сам PHASE-4C-PROMPT.md: взять МИНИМУМ `secondsSinceLastEventType` по
    /// нескольким конкретным типам (`.mouseMoved`, `.leftMouseDown`,
    /// `.rightMouseDown`, `.keyDown`, `.flagsChanged`, `.scrollWheel`) —
    /// см. закомментированную альтернативу ниже.
    private static let anyInputEventType = CGEventType(rawValue: 0xFFFFFFFF)

    /// Секунд с последнего ЛЮБОГО пользовательского ввода в системе (не
    /// только внутри этого приложения — `CGEventSource` видит весь сеанс).
    ///
    /// `.combinedSessionState` — источник событий с учётом объединённого
    /// состояния сеанса (в т.ч. удалённых событий вроде Screen Sharing), а
    /// не только локальных аппаратных — тот же выбор, которым в системе
    /// определяется простой для заставки/энергосбережения.
    static func secondsSinceLastEvent() -> Double {
        CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: anyInputEventType)

        // Запасной вариант (если строка выше на живой машине поведёт себя
        // не так, как ожидается) — минимум по конкретным типам событий:
        //
        // let types: [CGEventType] = [
        //     .mouseMoved, .leftMouseDown, .rightMouseDown,
        //     .keyDown, .flagsChanged, .scrollWheel,
        // ]
        // return types
        //     .map { CGEventSource.secondsSinceLastEventType(.combinedSessionState, eventType: $0) }
        //     .min() ?? .infinity
    }
}
