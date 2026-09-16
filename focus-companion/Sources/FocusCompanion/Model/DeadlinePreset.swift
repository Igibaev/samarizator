import Foundation

/// Пресеты срока при добавлении задачи (design.md §11.1):
/// 30 / 60 / 90 минут и «Выбрать время». Значение по умолчанию — 60 минут.
enum DeadlinePreset: Identifiable, Hashable {
    case minutes(Int)
    case custom

    static let standard: [DeadlinePreset] = CharacterConfig.deadlinePresets.map { .minutes($0) }

    var id: String {
        switch self {
        case .minutes(let value): return "m\(value)"
        case .custom: return "custom"
        }
    }

    var label: String {
        switch self {
        case .minutes(let value): return "\(value) мин"
        case .custom: return "Выбрать время"
        }
    }

    var duration: TimeInterval? {
        switch self {
        case .minutes(let value): return Double(value) * 60
        case .custom: return nil
        }
    }
}

/// Семейства реакции на истечение срока (design.md §12.1).
/// Один выбранный вариант на событие, не комбинация всех эффектов.
enum DisposalEffect: String, CaseIterable, Identifiable {
    case burn
    case paper
    case sweep

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .burn: return "Сгорание"
        case .paper: return "Бумага"
        case .sweep: return "Уборка"
        }
    }

    /// Эмоция, с которой начинается эффект.
    var emotion: CompanionState {
        switch self {
        case .burn: return .angry
        case .paper: return .discarding
        case .sweep: return .sweeping
        }
    }

    /// Длительность эффекта. Потолок — `disposal.maximum` = 1600 мс.
    var duration: Double {
        let raw: Double
        switch self {
        case .burn: raw = 1.0
        case .paper: raw = CharacterConfig.discardingDuration
        case .sweep: raw = CharacterConfig.sweepingDuration
        }
        return min(raw, DesignTokens.Motion.disposalMaximum)
    }
}

/// Настройка характера (design.md §12.1).
enum CharacterMood: String, CaseIterable, Identifiable {
    case gentle
    case expressive
    case none

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .gentle: return "Мягкий"
        case .expressive: return "Выразительный"
        case .none: return "Без эмоций"
        }
    }

    /// Какой эффект утилизации допустим при этом характере.
    /// «Мягкий» — грустный взгляд и растворение бумаги без огня.
    /// «Без эмоций» — обычное изменение статуса, эффекта нет вовсе.
    func effect(preferred: DisposalEffect) -> DisposalEffect? {
        switch self {
        case .expressive: return preferred
        case .gentle: return preferred == .burn ? .paper : preferred
        case .none: return nil
        }
    }
}
