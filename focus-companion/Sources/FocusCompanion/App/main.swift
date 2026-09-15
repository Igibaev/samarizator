import AppKit

// Точка входа. Без Storyboard/Xcode-проекта NSApplicationMain недоступен —
// поднимаем NSApplication вручную.
let app = NSApplication.shared

// Код верхнего уровня в main.swift НЕ изолирован на главном акторе, а
// AppDelegate изолирован — поэтому создание и запуск оборачиваем в
// MainActor.assumeIsolated. Это не обход проверки: main.swift исполняется на
// главном потоке, так что проверка assumeIsolated заведомо проходит, мы лишь
// сообщаем об этом компилятору, который сам вывести этого не может.
//
// delegate лежит в глобальной переменной намеренно: NSApplication.delegate —
// слабая ссылка, и локальный объект был бы освобождён сразу после присваивания.
let delegate = MainActor.assumeIsolated { AppDelegate() }

MainActor.assumeIsolated {
    app.delegate = delegate
    app.run()
}
