import Foundation

/// Одно событие headless-интерфейса Samarizator.
///
/// Протокол — NDJSON: одна JSON-строка на событие. Разбираем построчно и
/// отдаём по мере поступления, чтобы состояние в интерфейсе менялось по
/// факту, а не по таймеру.
struct CompanionEvent: Equatable {
    let name: String
    let fields: [String: String]

    var mid: String? { fields["mid"] }
    var message: String? { fields["message"] }
    var stage: String? { fields["stage"] }

    func flag(_ key: String) -> Bool { fields[key] == "true" }
    func text(_ key: String) -> String? { fields[key] }

    /// Разбор одной строки. `nil` — строка не является событием протокола
    /// (например, посторонний вывод зависимостей), и её надо просто
    /// пропустить, а не превращать в ошибку.
    static func parse(line: String) -> CompanionEvent? {
        guard let data = line.data(using: .utf8),
              let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let name = object["event"] as? String else {
            return nil
        }
        var fields: [String: String] = [:]
        for (key, value) in object where key != "event" {
            switch value {
            case let text as String: fields[key] = text
            case let flag as Bool: fields[key] = flag ? "true" : "false"
            case let number as NSNumber: fields[key] = number.stringValue
            default: fields[key] = String(describing: value)
            }
        }
        return CompanionEvent(name: name, fields: fields)
    }
}

/// Запуск headless-команды Samarizator отдельным процессом.
///
/// Владение процессом и есть владение операцией: чтобы остановить запись,
/// процессу посылается `SIGINT` — тот же сигнал, который обрабатывает
/// `samarizator.companion`. Ни pid-файлов, ни отдельной команды остановки
/// для этого не нужно.
final class CompanionProcess {

    private let process = Process()
    private let output = Pipe()
    private let errors = Pipe()
    private var buffer = Data()
    private let lock = NSLock()

    /// Вызывается на ГЛАВНОМ потоке: читает канал фоновый поток, но наружу
    /// события отдаются уже там, где живёт интерфейс. Замыкания намеренно не
    /// помечены `@Sendable` — они захватывают `@MainActor`-контроллер,
    /// который Sendable не является; переход на актор делает
    /// `MainActor.assumeIsolated` на стороне получателя.
    private let onEvent: (CompanionEvent) -> Void
    private let onFinish: (Int32) -> Void

    init(
        toolchain: SamarizatorToolchain,
        arguments: [String],
        onEvent: @escaping (CompanionEvent) -> Void,
        onFinish: @escaping (Int32) -> Void
    ) {
        self.onEvent = onEvent
        self.onFinish = onFinish

        let command = toolchain.command(arguments)
        process.executableURL = command.executable
        process.arguments = command.arguments
        process.currentDirectoryURL = toolchain.root
        process.standardOutput = output
        // Диагностику зависимостей не мешаем с протоколом, но и не теряем:
        // при ошибке она пригодится в логе.
        process.standardError = errors

        var environment = ProcessInfo.processInfo.environment
        environment["PYTHONUNBUFFERED"] = "1"
        process.environment = environment
    }

    var isRunning: Bool { process.isRunning }

    func start() throws {
        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            self?.consume(handle.availableData)
        }
        process.terminationHandler = { [weak self] process in
            self?.finish(code: process.terminationStatus)
        }
        try process.run()
    }

    /// Остановка записи. Именно `SIGINT`, а не `terminate()`: интерфейс
    /// Samarizator на него закрывает файл, дописывает базу и запускает
    /// закрывающее распознавание. Грубое завершение потеряло бы запись.
    func requestStop() {
        guard process.isRunning else { return }
        kill(process.processIdentifier, SIGINT)
    }

    /// Аварийное завершение — только когда штатная остановка не сработала.
    func terminate() {
        guard process.isRunning else { return }
        process.terminate()
    }

    private func consume(_ data: Data) {
        guard !data.isEmpty else { return }
        lock.lock()
        buffer.append(data)
        let lines = Self.drainLines(from: &buffer)
        lock.unlock()

        for line in lines {
            guard let event = CompanionEvent.parse(line: line) else { continue }
            let handler = onEvent
            DispatchQueue.main.async { handler(event) }
        }
    }

    /// Достаёт из буфера все ЗАВЕРШЁННЫЕ строки, оставляя хвост до следующей порции.
    /// Без этого событие, разорванное по границе чтения, потерялось бы.
    static func drainLines(from buffer: inout Data) -> [String] {
        var lines: [String] = []
        let newline = UInt8(ascii: "\n")
        while let index = buffer.firstIndex(of: newline) {
            let lineData = buffer[buffer.startIndex..<index]
            buffer.removeSubrange(buffer.startIndex...index)
            if let line = String(data: Data(lineData), encoding: .utf8),
               !line.trimmingCharacters(in: .whitespaces).isEmpty {
                lines.append(line)
            }
        }
        return lines
    }

    private func finish(code: Int32) {
        output.fileHandleForReading.readabilityHandler = nil
        // Дочитываем хвост: последняя строка может прийти вместе с завершением.
        let tail = output.fileHandleForReading.availableData
        if !tail.isEmpty { consume(tail) }
        let handler = onFinish
        DispatchQueue.main.async { handler(code) }
    }
}
