import AppKit
import Observation

/// История буфера обмена.
///
/// Включается ТОЛЬКО явным действием пользователя (design.md §9.2): до этого
/// `NSPasteboard` не опрашивается вообще, а не «опрашивается, но не
/// показывается». Автоматической вставки в чужие приложения нет ни в каком
/// виде — компаньон только копирует по нажатию.
@MainActor
@Observable
final class ClipboardService {

    /// Локальный лимит: 100 элементов или 7 дней, что наступит раньше.
    /// Закреплённые из автоматической очистки исключены.
    private static let maxItems = 100
    private static let maxAge: TimeInterval = 7 * 24 * 60 * 60
    private static let pollInterval: Double = 1.0

    private(set) var items: [ClipboardItem] = []
    private(set) var isEnabled = CompanionSettings.clipboardHistoryEnabled
    /// Пауза истории — отдельно от полного выключения.
    private(set) var isPaused = false
    private(set) var lastCopiedID: UUID?

    private var lastChangeCount: Int = -1
    private let taskBag = TaskBag()

    init() {
        if isEnabled { startPolling() }
    }

    deinit { taskBag.cancelAll() }

    func setEnabled(_ enabled: Bool) {
        isEnabled = enabled
        CompanionSettings.clipboardHistoryEnabled = enabled
        if enabled {
            startPolling()
        } else {
            taskBag.cancel(.clipboardPoll)
            items.removeAll()
        }
    }

    func setPaused(_ paused: Bool) {
        isPaused = paused
    }

    // MARK: - Опрос

    private func startPolling() {
        lastChangeCount = NSPasteboard.general.changeCount
        taskBag.replace(.clipboardPoll, with: Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(Self.pollInterval * 1_000_000_000))
                guard !Task.isCancelled, let self else { return }
                self.capture()
            }
        })
    }

    private func capture() {
        guard isEnabled, !isPaused else { return }
        let pasteboard = NSPasteboard.general
        guard pasteboard.changeCount != lastChangeCount else { return }
        lastChangeCount = pasteboard.changeCount

        if let string = pasteboard.string(forType: .string),
           !string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            let isLink = string.hasPrefix("http://") || string.hasPrefix("https://")
            append(ClipboardItem(
                type: isLink ? .link : .text,
                contentReference: string
            ))
            return
        }
        if pasteboard.data(forType: .tiff) != nil || pasteboard.data(forType: .png) != nil {
            // Сам бинарник в состояние интерфейса не кладём: только пометка,
            // что в буфере было изображение. Ссылку на локальный ресурс
            // добавит тот, кто научится сохранять его на диск.
            append(ClipboardItem(type: .image, contentReference: "Изображение из буфера"))
        }
    }

    private func append(_ item: ClipboardItem) {
        // Тот же самый текст подряд не дублируем.
        if let first = items.first, first.contentReference == item.contentReference { return }
        items.insert(item, at: 0)
        prune()
    }

    private func prune() {
        let cutoff = Date().addingTimeInterval(-Self.maxAge)
        items.removeAll { !$0.pinned && $0.capturedAt < cutoff }
        let unpinnedCount = items.filter { !$0.pinned }.count
        guard unpinnedCount > Self.maxItems else { return }
        var remaining = unpinnedCount - Self.maxItems
        for index in items.indices.reversed() where remaining > 0 {
            guard !items[index].pinned else { continue }
            items.remove(at: index)
            remaining -= 1
        }
    }

    // MARK: - Действия пользователя

    func togglePin(_ id: UUID) {
        guard let index = items.firstIndex(where: { $0.id == id }) else { return }
        items[index].pinned.toggle()
    }

    func delete(_ id: UUID) {
        items.removeAll { $0.id == id }
    }

    /// Копирует элемент обратно в буфер. Ни в какое чужое приложение
    /// содержимое не вставляется.
    func copy(_ item: ClipboardItem) {
        let pasteboard = NSPasteboard.general
        pasteboard.clearContents()
        pasteboard.setString(item.contentReference, forType: .string)
        lastChangeCount = pasteboard.changeCount
        lastCopiedID = item.id
        taskBag.replace(.copyConfirmation, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            guard !Task.isCancelled, let self else { return }
            self.lastCopiedID = nil
        })
    }

    func filtered(query: String) -> [ClipboardItem] {
        let matching = items.filter { $0.matches(query: query) }
        return matching.sorted { lhs, rhs in
            if lhs.pinned != rhs.pinned { return lhs.pinned }
            return lhs.capturedAt > rhs.capturedAt
        }
    }

    /// Демонстрационное наполнение — только по явному включению демо-режима.
    func loadDemoItems(_ demo: [ClipboardItem]) {
        items = demo
    }
}
