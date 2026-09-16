import Foundation

/// На каком материале была сделана сводка.
///
/// В базе Samarizator такого поля нет, а знать это нужно: сводка, сделанная
/// во время записи, — это СНИМОК, и после новых фрагментов она устаревает.
/// Хранится у компаньона и переживает перезапуск: иначе после закрытия
/// приложения устаревшая сводка молча выглядела бы актуальной.
struct SummarySnapshot: Codable, Equatable {
    /// Сколько реплик было распознано в момент саммаризации.
    let segmentCount: Int
    /// Время снимка, «12:34». Показывается только для снимков во время записи.
    let at: String
    /// Сводка сделана во время записи, а не по законченному материалу.
    let duringRecording: Bool

    /// Сводка устарела, если с тех пор распознали больше.
    func isStale(currentSegmentCount: Int) -> Bool {
        currentSegmentCount > segmentCount
    }
}

/// Снимки по записям. Ключ — идентификатор записи в базе Samarizator.
enum SummarySnapshotStore {
    private static let key = "companion.summarySnapshots"

    static func all() -> [String: SummarySnapshot] {
        guard let data = UserDefaults.standard.data(forKey: key),
              let decoded = try? JSONDecoder().decode([String: SummarySnapshot].self, from: data) else {
            return [:]
        }
        return decoded
    }

    static func snapshot(for recordingID: String) -> SummarySnapshot? {
        all()[recordingID]
    }

    static func save(_ snapshot: SummarySnapshot, for recordingID: String) {
        var current = all()
        current[recordingID] = snapshot
        guard let data = try? JSONEncoder().encode(current) else { return }
        UserDefaults.standard.set(data, forKey: key)
    }
}
