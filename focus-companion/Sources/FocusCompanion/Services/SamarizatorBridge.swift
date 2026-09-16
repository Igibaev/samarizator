import Foundation

/// Чтение записей, транскриптов и саммари из базы Samarizator.
///
/// Это НАСТОЯЩАЯ интеграция с существующим сервисом проекта, но только на
/// чтение: `~/Library/Application Support/Samarizator/meetings.sqlite3`.
/// Схема — из `src/samarizator/store.py`:
/// `meetings(id,title,source,created,status,duration,summary,error)`,
/// `segments(id,meeting,start,end,speaker,text,uncertain)`.
///
/// Чего этот мост НЕ умеет и не притворяется, что умеет: начинать запись,
/// запускать транскрибацию и саммаризацию. У Samarizator нет внешнего API
/// или IPC — только GUI. Поэтому кнопки записи и саммари в таком режиме
/// показывают честную недоступность и предлагают открыть само приложение.
struct SamarizatorBridge {

    enum Availability: Equatable {
        case available(databasePath: String)
        /// Приложение не найдено или база ещё не создана.
        case databaseMissing(expectedPath: String)
        case unreadable(reason: String)

        var isAvailable: Bool {
            if case .available = self { return true }
            return false
        }

        var explanation: String {
            switch self {
            case .available(let path):
                return "База Samarizator найдена: \(path)"
            case .databaseMissing(let path):
                return "База Samarizator не найдена по пути \(path). "
                    + "Запустите Samarizator хотя бы раз, чтобы она появилась."
            case .unreadable(let reason):
                return "База Samarizator найдена, но прочитать её не удалось: \(reason)"
            }
        }
    }

    /// Тот же путь, что вычисляет `samarizator.config.data_dir()`,
    /// включая переопределение через `SAMARIZATOR_HOME`.
    static var databaseURL: URL {
        let environmentHome = ProcessInfo.processInfo.environment["SAMARIZATOR_HOME"]
        let base: URL
        if let environmentHome, !environmentHome.isEmpty {
            base = URL(fileURLWithPath: environmentHome, isDirectory: true)
        } else {
            base = FileManager.default.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Application Support/Samarizator", isDirectory: true)
        }
        return base.appendingPathComponent("meetings.sqlite3")
    }

    static func availability() -> Availability {
        let url = databaseURL
        guard FileManager.default.fileExists(atPath: url.path) else {
            return .databaseMissing(expectedPath: url.path)
        }
        do {
            let database = try SQLiteDatabase(path: url.path)
            _ = try database.query("SELECT id FROM meetings LIMIT 1")
            return .available(databasePath: url.path)
        } catch {
            return .unreadable(reason: error.localizedDescription)
        }
    }

    // MARK: - Чтение

    /// Список записей без транскриптов — для колонки слева.
    static func loadRecordings(limit: Int = 100) throws -> [Recording] {
        let database = try SQLiteDatabase(path: databaseURL.path)
        let rows = try database.query(
            "SELECT id,title,source,created,status,duration,summary,error "
                + "FROM meetings ORDER BY created DESC LIMIT \(max(1, limit))"
        )
        return rows.compactMap { row in
            guard let id = row["id"]?.stringValue else { return nil }
            let rawStatus = row["status"]?.stringValue ?? "ready"
            let summaryJSON = row["summary"]?.stringValue
            var summaries: [SummaryFormat: RecordingSummary] = [:]
            if let summaryJSON, !summaryJSON.isEmpty {
                summaries = SummaryParser.parse(json: summaryJSON)
            }
            return Recording(
                id: id,
                title: row["title"]?.stringValue ?? "Без названия",
                createdAt: parseDate(row["created"]?.stringValue),
                duration: row["duration"]?.doubleValue,
                source: row["source"]?.stringValue ?? "",
                status: RecordingStatus.fromSamarizator(rawStatus),
                segments: [],
                summaries: summaries,
                // Пустая строка в колонке `error` — это «ошибки нет», а не
                // ошибка с пустым текстом.
                processingError: nonEmpty(row["error"]?.stringValue)
            )
        }
    }

    /// Транскрипт конкретной записи — грузится только для выбранной.
    static func loadSegments(recordingID: String, limit: Int = 2000) throws -> [TranscriptSegment] {
        let database = try SQLiteDatabase(path: databaseURL.path)
        let rows = try database.query(
            "SELECT id,start,end,speaker,text,uncertain FROM segments "
                + "WHERE meeting=? ORDER BY start,id LIMIT \(max(1, limit))",
            parameters: [recordingID]
        )
        return rows.compactMap { row in
            guard let id = row["id"]?.intValue else { return nil }
            let speaker = row["speaker"]?.stringValue
            return TranscriptSegment(
                id: id,
                start: row["start"]?.doubleValue ?? 0,
                end: row["end"]?.doubleValue ?? 0,
                text: row["text"]?.stringValue ?? "",
                // Метку спикера показываем, только если она реально есть.
                speaker: (speaker?.isEmpty ?? true) ? nil : speaker,
                uncertain: (row["uncertain"]?.intValue ?? 0) != 0
            )
        }
    }

    private static func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }

    /// `meetings.created` пишется как ISO-8601.
    private static func parseDate(_ raw: String?) -> Date {
        guard let raw else { return Date() }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = iso.date(from: raw) { return date }
        iso.formatOptions = [.withInternetDateTime]
        if let date = iso.date(from: raw) { return date }
        let fallback = DateFormatter()
        fallback.locale = Locale(identifier: "en_US_POSIX")
        fallback.dateFormat = "yyyy-MM-dd HH:mm:ss"
        return fallback.date(from: raw) ?? Date()
    }
}

/// Разбор JSON саммари Samarizator.
///
/// Формат (`src/samarizator/summary.py`): объект с `overview`, `items`
/// (у каждого `kind` из point/decision/action/risk/question, `text`,
/// `evidence`), а также `brief` и `detailed` — два готовых представления.
/// Форматы «Кратко» и «Подробно» берутся оттуда, «Действия» — это фильтр по
/// `kind == action`, а не отдельная генерация; выдавать за отдельный
/// результат его нельзя.
enum SummaryParser {

    static func parse(json: String) -> [SummaryFormat: RecordingSummary] {
        guard let data = json.data(using: .utf8),
              let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else {
            return [:]
        }
        var result: [SummaryFormat: RecordingSummary] = [:]
        let brief = (root["brief"] as? [String: Any]) ?? root
        result[.brief] = summary(from: brief)
        if let detailed = root["detailed"] as? [String: Any] {
            result[.detailed] = summary(from: detailed)
        } else {
            result[.detailed] = result[.brief]
        }
        // «Действия» — представление того же результата, отдельной генерации нет.
        if let base = result[.detailed] {
            result[.actions] = RecordingSummary(
                overview: base.overview,
                items: base.items.filter { $0.kind == .action },
                snapshotLabel: base.snapshotLabel,
                isStale: base.isStale
            )
        }
        return result
    }

    private static func summary(from object: [String: Any]) -> RecordingSummary {
        let overview = object["overview"] as? String ?? ""
        let rawItems = object["items"] as? [[String: Any]] ?? []
        let items = rawItems.enumerated().map { index, item -> SummaryItem in
            let kind = SummaryItemKind(rawValue: item["kind"] as? String ?? "point") ?? .point
            let evidence = (item["evidence"] as? [Any])?.map { String(describing: $0) } ?? []
            return SummaryItem(
                id: "\(index)",
                kind: kind,
                text: item["text"] as? String ?? "",
                evidence: evidence
            )
        }
        return RecordingSummary(overview: overview, items: items, snapshotLabel: nil, isStale: false)
    }
}
