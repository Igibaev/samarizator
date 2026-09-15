import AppKit

// Точка входа. Без Storyboard/Xcode-проекта NSApplicationMain недоступен —
// поднимаем NSApplication вручную.
let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.run()
