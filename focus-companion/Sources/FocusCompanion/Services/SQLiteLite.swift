import Foundation
import SQLite3

/// Минимальная обёртка над C-API SQLite — ровно столько, сколько нужно для
/// чтения базы Samarizator.
///
/// База открывается ТОЛЬКО на чтение и в режиме `immutable`-совместимого
/// доступа через `SQLITE_OPEN_READONLY`: компаньон не имеет права влиять на
/// данные приложения, которое может работать с этой же базой прямо сейчас.
final class SQLiteDatabase {

    enum Failure: Error, LocalizedError {
        case cannotOpen(String)
        case cannotPrepare(String)

        var errorDescription: String? {
            switch self {
            case .cannotOpen(let message): return "Не удалось открыть базу: \(message)"
            case .cannotPrepare(let message): return "Не удалось выполнить запрос: \(message)"
            }
        }
    }

    private var handle: OpaquePointer?

    init(path: String) throws {
        var db: OpaquePointer?
        let flags = SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX
        let status = sqlite3_open_v2(path, &db, flags, nil)
        guard status == SQLITE_OK, let db else {
            let message = db.map { String(cString: sqlite3_errmsg($0)) } ?? "код \(status)"
            if let db { sqlite3_close_v2(db) }
            throw Failure.cannotOpen(message)
        }
        handle = db
    }

    deinit {
        if let handle {
            sqlite3_close_v2(handle)
        }
    }

    /// Значение одной колонки: только те типы, которые реально встречаются
    /// в схеме Samarizator.
    enum Value {
        case text(String)
        case number(Double)
        case integer(Int64)
        case null

        var stringValue: String? {
            if case .text(let value) = self { return value }
            return nil
        }

        var doubleValue: Double? {
            switch self {
            case .number(let value): return value
            case .integer(let value): return Double(value)
            default: return nil
            }
        }

        var intValue: Int? {
            switch self {
            case .integer(let value): return Int(value)
            case .number(let value): return Int(value)
            default: return nil
            }
        }
    }

    /// Выполняет запрос и отдаёт строки словарями «колонка → значение».
    func query(_ sql: String, parameters: [String] = []) throws -> [[String: Value]] {
        var statement: OpaquePointer?
        // SQLITE_TRANSIENT: SQLite обязан скопировать строку, иначе она
        // освободится раньше, чем запрос выполнится.
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        guard sqlite3_prepare_v2(handle, sql, -1, &statement, nil) == SQLITE_OK, let statement else {
            let message = handle.map { String(cString: sqlite3_errmsg($0)) } ?? "неизвестная ошибка"
            throw Failure.cannotPrepare(message)
        }
        defer { sqlite3_finalize(statement) }

        for (index, parameter) in parameters.enumerated() {
            sqlite3_bind_text(statement, Int32(index + 1), parameter, -1, transient)
        }

        var rows: [[String: Value]] = []
        while sqlite3_step(statement) == SQLITE_ROW {
            var row: [String: Value] = [:]
            let columnCount = sqlite3_column_count(statement)
            for column in 0..<columnCount {
                guard let namePointer = sqlite3_column_name(statement, column) else { continue }
                let name = String(cString: namePointer)
                switch sqlite3_column_type(statement, column) {
                case SQLITE_TEXT:
                    if let pointer = sqlite3_column_text(statement, column) {
                        row[name] = .text(String(cString: pointer))
                    } else {
                        row[name] = .null
                    }
                case SQLITE_INTEGER:
                    row[name] = .integer(sqlite3_column_int64(statement, column))
                case SQLITE_FLOAT:
                    row[name] = .number(sqlite3_column_double(statement, column))
                default:
                    row[name] = .null
                }
            }
            rows.append(row)
        }
        return rows
    }
}
