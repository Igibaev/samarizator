import Foundation

/// Папка `inbox/` рядом с `tasks.json` — передачи со встреч от Samarizator.
///
/// Мост между Samarizator и компаньоном здесь файловый, а не сетевой и не
/// через XPC/URL-схему: приложение собирается без Xcode-проекта, регистрацию
/// URL-схемы и XPC-сервиса проверить некому, а папка в Application Support
/// уже есть. Python пишет файл через временное имя и `rename` — атомарно,
/// наблюдатель никогда не увидит недописанный JSON. Python пишет, ТОЛЬКО
/// если папка приложения уже существует — то есть компаньон хоть раз
/// запускался; на Mac без компаньона файлов не будет.
///
/// Наблюдение — `DispatchSource` на дескрипторе папки (событие `.write`
/// приходит при появлении/удалении/переименовании записей в ней). Запасной
/// путь на случай, если на живой машине событие не придёт: тик напоминаний
/// раз в 30 секунд перечитывает папку (`TaskPanelController.tickReminders`).
@MainActor
final class HandoffInbox {

    static var directoryURL: URL {
        TaskPersistence.directoryURL.appendingPathComponent("inbox", isDirectory: true)
    }

    /// Вызывается на главном потоке при любом изменении папки.
    var onChange: (() -> Void)?

    /// Источник событий живёт в Sendable-контейнере с замком по той же
    /// причине, что таймеры в `TaskBag`: `deinit` `@MainActor`-класса не
    /// видит изолированные свойства, а снять наблюдение при освобождении
    /// обязательно (HANDOFF.md, «Грабли», п.2).
    private let watch = DirectoryWatch()

    init() {
        try? FileManager.default.createDirectory(
            at: Self.directoryURL,
            withIntermediateDirectories: true
        )
        watch.start(path: Self.directoryURL.path) { [weak self] in
            Task { @MainActor [weak self] in
                self?.onChange?()
            }
        }
    }

    deinit {
        watch.stop()
    }

    /// Все передачи из папки, старшая первой. Заодно убирает файлы старше
    /// `CharacterConfig.handoffRetentionDays`: дольше они не нужны — записи
    /// такой давности на странице «Записи» и так далеко внизу списка.
    ///
    /// Нечитаемый файл (чужой формат, битый JSON) переименовывается в
    /// `.unreadable` — не перечитывать его на каждом событии, но и не
    /// уничтожать молча: пересоздание сводки в Samarizator положит новый.
    func load(now: Date = Date()) -> [MeetingHandoff] {
        let fileManager = FileManager.default
        guard let urls = try? fileManager.contentsOfDirectory(
            at: Self.directoryURL,
            includingPropertiesForKeys: nil
        ) else { return [] }

        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let retention = Double(CharacterConfig.handoffRetentionDays) * 24 * 60 * 60

        var handoffs: [MeetingHandoff] = []
        for url in urls where url.pathExtension == "json" {
            guard let data = try? Data(contentsOf: url) else { continue }
            guard let handoff = try? decoder.decode(MeetingHandoff.self, from: data) else {
                let quarantined = url.deletingPathExtension().appendingPathExtension("unreadable")
                try? fileManager.removeItem(at: quarantined)
                try? fileManager.moveItem(at: url, to: quarantined)
                print("ИИ-компаньон: не удалось прочитать передачу \(url.lastPathComponent), отложена как .unreadable")
                continue
            }
            if now.timeIntervalSince(handoff.createdAt) > retention {
                try? fileManager.removeItem(at: url)
                continue
            }
            handoffs.append(handoff)
        }
        return handoffs.sorted { $0.createdAt < $1.createdAt }
    }
}

/// Держатель `DispatchSource` наблюдения за папкой, не привязанный к актору —
/// см. комментарий у `HandoffInbox.watch`.
final class DirectoryWatch: @unchecked Sendable {

    private let lock = NSLock()
    private var source: DispatchSourceFileSystemObject?

    /// Обработчик вызывается на главной очереди. `O_EVTONLY` — дескриптор
    /// только для событий, без права чтения: так папку нельзя случайно
    /// заблокировать от удаления/перемещения.
    func start(path: String, handler: @escaping @Sendable () -> Void) {
        let descriptor = open(path, O_EVTONLY)
        guard descriptor >= 0 else {
            print("ИИ-компаньон: не удалось открыть папку передач для наблюдения: \(path)")
            return
        }
        let source = DispatchSource.makeFileSystemObjectSource(
            fileDescriptor: descriptor,
            eventMask: .write,
            queue: .main
        )
        source.setEventHandler(handler: handler)
        source.setCancelHandler {
            _ = close(descriptor)
        }
        lock.lock()
        self.source?.cancel()
        self.source = source
        lock.unlock()
        source.resume()
    }

    func stop() {
        lock.lock()
        defer { lock.unlock() }
        source?.cancel()
        source = nil
    }
}
