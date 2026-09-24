import Foundation

/// Где лежит установленный Samarizator и чем его запускать.
///
/// Компаньон вызывает headless-интерфейс `python -m samarizator.companion`
/// из виртуального окружения самого приложения (`.venv`, которое создаёт
/// `start.sh`). Собственного Python в бандл не кладём: у приложения есть
/// закреплённые версии зависимостей и модель Whisper, и вторая копия
/// окружения разошлась бы с первой.
struct SamarizatorToolchain: Equatable {

    /// Корень репозитория Samarizator.
    let root: URL
    /// Интерпретатор из `.venv` этого репозитория.
    let python: URL

    /// Ключ, которым путь запоминается после выбора вручную.
    private static let defaultsKey = "companion.samarizatorRoot"
    private static let environmentKey = "FOCUS_SAMARIZATOR_ROOT"

    enum Failure: Error, Equatable {
        /// Папку найти не удалось совсем.
        case notFound
        /// Папка есть, но это не Samarizator.
        case notSamarizator(path: String)
        /// Папка та, но окружение не создано — приложение ни разу не ставили.
        case environmentMissing(path: String)

        var explanation: String {
            switch self {
            case .notFound:
                return "Папка с Samarizator не найдена. Укажите её вручную — это та папка, "
                    + "в которой лежит start.sh."
            case .notSamarizator(let path):
                return "В папке \(path) нет Samarizator: не найден pyproject.toml с этим именем."
            case .environmentMissing(let path):
                return "Samarizator найден в \(path), но окружение не создано. "
                    + "Запустите ./start.sh в этой папке — он поставит зависимости и модель."
            }
        }
    }

    // MARK: - Поиск

    /// Порядок поиска: переменная окружения → сохранённый выбор пользователя →
    /// подъём вверх от самого приложения.
    static func resolve() -> Result<SamarizatorToolchain, Failure> {
        if let raw = ProcessInfo.processInfo.environment[environmentKey], !raw.isEmpty {
            return validate(URL(fileURLWithPath: raw, isDirectory: true))
        }
        // Сохранённый выбор, если он всё ещё годится; устаревший (папку
        // переместили) не должен мешать автопоиску ниже.
        if let stored = UserDefaults.standard.string(forKey: defaultsKey), !stored.isEmpty,
           case .success(let toolchain) = validate(URL(fileURLWithPath: stored, isDirectory: true)) {
            return .success(toolchain)
        }
        // `build-app.sh` кладёт .app внутрь focus-companion/, то есть на один
        // уровень ниже корня репозитория. Поднимаемся, пока не найдём его —
        // от бандла, от самого бинарника (запуск из терминала) и от текущей
        // папки терминала. Первый найденный корень запоминаем.
        var starts = [Bundle.main.bundleURL]
        if let executable = Bundle.main.executableURL { starts.append(executable) }
        starts.append(URL(fileURLWithPath: FileManager.default.currentDirectoryPath, isDirectory: true)
            .appendingPathComponent("placeholder"))
        var lastFailure: Failure = .notFound
        for start in starts {
            var candidate = start.resolvingSymlinksInPath()
            for _ in 0..<7 {
                candidate = candidate.deletingLastPathComponent()
                switch validate(candidate) {
                case .success(let toolchain):
                    UserDefaults.standard.set(candidate.path, forKey: defaultsKey)
                    return .success(toolchain)
                case .failure(.environmentMissing(let path)):
                    // Папка та, но окружения нет — это полезнее, чем «не найдено».
                    lastFailure = .environmentMissing(path: path)
                case .failure:
                    continue
                }
            }
        }
        return .failure(lastFailure)
    }

    /// Строка для лога запуска: где искали и что нашли.
    static func diagnostics() -> String {
        switch resolve() {
        case .success(let toolchain):
            return "найден: \(toolchain.root.path)"
        case .failure(let failure):
            return "НЕ найден — \(failure.explanation) Бандл: \(Bundle.main.bundleURL.path), "
                + "папка терминала: \(FileManager.default.currentDirectoryPath)"
        }
    }

    /// Проверяет папку и запоминает её, если она подходит.
    @discardableResult
    static func select(_ root: URL) -> Result<SamarizatorToolchain, Failure> {
        let result = validate(root)
        if case .success = result {
            UserDefaults.standard.set(root.path, forKey: defaultsKey)
        }
        return result
    }

    static func forget() {
        UserDefaults.standard.removeObject(forKey: defaultsKey)
    }

    static func validate(_ root: URL) -> Result<SamarizatorToolchain, Failure> {
        let manifest = root.appendingPathComponent("pyproject.toml")
        guard let text = try? String(contentsOf: manifest, encoding: .utf8),
              text.contains("name = \"samarizator\"") else {
            return .failure(.notSamarizator(path: root.path))
        }
        let python = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: python.path) else {
            return .failure(.environmentMissing(path: root.path))
        }
        return .success(SamarizatorToolchain(root: root, python: python))
    }

    // MARK: - Запуск

    /// Аргументы для запуска headless-команды.
    func command(_ arguments: [String]) -> (executable: URL, arguments: [String]) {
        (executable: python, arguments: ["-m", "samarizator.companion"] + arguments)
    }
}
