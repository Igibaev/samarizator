import Foundation

/// Канонические данные макета (design.md §15).
///
/// Это ДЕМОНСТРАЦИОННЫЕ данные, а не результат обработки. Интерфейс обязан
/// подписывать их как демонстрационные везде, где они показаны, — выдавать
/// их за работающую интеграцию нельзя.
enum DemoFixtures {

    /// Три задачи с фиксированным остатком: 18 мин / 45 мин / 1 ч 20 мин.
    /// Первая имеет исходный интервал 90 минут, поэтому 18 минут — это 20%,
    /// и янтарное состояние `dueSoon` включается по правилу, а не вручную.
    static func tasks(now: Date = Date()) -> [CompanionTask] {
        [
            CompanionTask(
                title: "Отправить предложение",
                startedAt: now.addingTimeInterval(-72 * 60),
                expiresAt: now.addingTimeInterval(18 * 60),
                order: 0
            ),
            CompanionTask(
                title: "Закончить дизайн",
                startedAt: now.addingTimeInterval(-15 * 60),
                expiresAt: now.addingTimeInterval(45 * 60),
                order: 1
            ),
            CompanionTask(
                title: "Разобрать заметки",
                startedAt: now.addingTimeInterval(-10 * 60),
                expiresAt: now.addingTimeInterval(80 * 60),
                order: 2
            ),
        ]
    }

    /// История для проверки восстановления.
    static func history(now: Date = Date()) -> [CompanionTask] {
        [
            CompanionTask(
                title: "Ответить на письмо",
                startedAt: now.addingTimeInterval(-180 * 60),
                expiresAt: now.addingTimeInterval(-120 * 60),
                status: .expired,
                order: 0,
                expiredAt: now.addingTimeInterval(-120 * 60)
            ),
            CompanionTask(
                title: "Собрать прототип",
                startedAt: now.addingTimeInterval(-240 * 60),
                expiresAt: now.addingTimeInterval(-200 * 60),
                status: .completed,
                order: 1,
                completedAt: now.addingTimeInterval(-210 * 60)
            ),
        ]
    }

    /// Записи: «Встреча по проекту», «Идеи для компаньона», «Голосовая заметка».
    /// Выбрана первая: «Сегодня, 14:30 · 24 мин».
    static var recordings: [Recording] {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        let at1430 = calendar.date(byAdding: .minute, value: 14 * 60 + 30, to: today) ?? Date()

        return [
            Recording(
                id: "demo-meeting",
                title: "Встреча по проекту",
                createdAt: at1430,
                duration: 24 * 60,
                source: "Микрофон и системный звук",
                status: .ready,
                segments: demoSegments,
                summaries: demoSummaries,
                processingError: nil,
                isDemo: true
            ),
            Recording(
                id: "demo-ideas",
                title: "Идеи для компаньона",
                createdAt: at1430.addingTimeInterval(-26 * 60 * 60),
                duration: 11 * 60,
                source: "Микрофон",
                status: .processing,
                segments: [],
                summaries: [:],
                processingError: nil,
                isDemo: true
            ),
            Recording(
                id: "demo-voice",
                title: "Голосовая заметка",
                createdAt: at1430.addingTimeInterval(-50 * 60 * 60),
                duration: 2 * 60 + 40,
                source: "Микрофон",
                status: .failed,
                segments: [],
                summaries: [:],
                processingError: "Модель вернула ответ, который не удалось разобрать. "
                    + "Исходная запись и текст сохранены.",
                isDemo: true
            ),
        ]
    }

    private static var demoSegments: [TranscriptSegment] {
        [
            TranscriptSegment(
                id: 1,
                start: 0,
                end: 14,
                text: "Давайте зафиксируем композицию компаньона: глаза и кнопки в продолжении выреза.",
                speaker: nil,
                uncertain: false
            ),
            TranscriptSegment(
                id: 2,
                start: 14,
                end: 38,
                text: "Задачи висят по центру под вырезом, максимум три. Четвёртой не появляется.",
                speaker: nil,
                uncertain: false
            ),
            TranscriptSegment(
                id: 3,
                start: 38,
                end: 72,
                text: "Записи и буфер открываются в одной правой панели шириной в половину экрана.",
                speaker: nil,
                uncertain: false
            ),
            TranscriptSegment(
                id: 4,
                start: 72,
                end: 96,
                text: "Следующий шаг — подготовить прототип и проверить жесты на живом трекпаде.",
                speaker: nil,
                uncertain: true
            ),
        ]
    }

    private static var demoSummaries: [SummaryFormat: RecordingSummary] {
        let items = [
            SummaryItem(
                id: "1",
                kind: .point,
                text: "Согласовали дизайн компаньона и три задачи в фокусе.",
                evidence: ["1", "2"]
            ),
            SummaryItem(
                id: "2",
                kind: .decision,
                text: "Записи и буфер открываются справа.",
                evidence: ["3"]
            ),
            SummaryItem(
                id: "3",
                kind: .action,
                text: "Подготовить прототип.",
                evidence: ["4"]
            ),
        ]
        let brief = RecordingSummary(
            overview: "Демонстрационные данные. Это не результат обработки записи.",
            items: items,
            snapshotLabel: nil,
            isStale: false
        )
        return [
            .brief: brief,
            .detailed: brief,
            .actions: RecordingSummary(
                overview: brief.overview,
                items: items.filter { $0.kind == .action },
                snapshotLabel: nil,
                isStale: false
            ),
        ]
    }

    /// Буфер: ссылка «Документация проекта» и текст «Идея для следующей встречи».
    static var clipboardItems: [ClipboardItem] {
        [
            ClipboardItem(
                type: .link,
                contentReference: "https://example.com/docs/companion",
                capturedAt: Date().addingTimeInterval(-6 * 60),
                pinned: true,
                sourceLabel: nil,
                linkTitle: "Документация проекта"
            ),
            ClipboardItem(
                type: .text,
                contentReference: "Идея для следующей встречи",
                capturedAt: Date().addingTimeInterval(-24 * 60)
            ),
        ]
    }
}

/// Воспроизводимые состояния для проверки без ожидания реальных сроков
/// (design.md §16, пункт задания 9).
///
/// Открываются из меню в строке состояния и переменной окружения
/// `FOCUS_FIXTURE=<имя>`.
enum CompanionFixture: String, CaseIterable, Identifiable {
    case empty
    case threeTasks
    case oneFreeSlot
    case dueSoon
    case aboutToExpire
    case expiredWithUndo
    case recordingInProgress
    case summaryGenerating
    case processingError

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .empty: return "Без задач"
        case .threeTasks: return "Три задачи"
        case .oneFreeSlot: return "Свободный слот"
        case .dueSoon: return "Срок близко"
        case .aboutToExpire: return "Срок через 3 секунды"
        case .expiredWithUndo: return "Сгоревшая задача"
        case .recordingInProgress: return "Идёт запись"
        case .summaryGenerating: return "Генерация саммари"
        case .processingError: return "Ошибка обработки"
        }
    }

    /// Задачи для этой фикстуры.
    func tasks(now: Date = Date()) -> [CompanionTask] {
        switch self {
        case .empty:
            return []
        case .threeTasks, .recordingInProgress, .summaryGenerating, .processingError:
            return DemoFixtures.tasks(now: now) + DemoFixtures.history(now: now)
        case .oneFreeSlot:
            return Array(DemoFixtures.tasks(now: now).prefix(2)) + DemoFixtures.history(now: now)
        case .dueSoon:
            var tasks = DemoFixtures.tasks(now: now)
            tasks[1].startedAt = now.addingTimeInterval(-55 * 60)
            tasks[1].expiresAt = now.addingTimeInterval(4 * 60)
            return tasks
        case .aboutToExpire:
            var tasks = DemoFixtures.tasks(now: now)
            tasks[0].startedAt = now.addingTimeInterval(-90 * 60)
            tasks[0].expiresAt = now.addingTimeInterval(3)
            return tasks
        case .expiredWithUndo:
            var tasks = DemoFixtures.tasks(now: now)
            tasks[0].status = .expired
            tasks[0].expiredAt = now
            return tasks + DemoFixtures.history(now: now)
        }
    }
}
