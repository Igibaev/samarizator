import AppKit
import Observation

/// Перетаскивание компаньона по экрану.
///
/// Тащат за область глаз: кнопки записи и саммари при этом остаются
/// кликабельными, а случайный клик по глазам по-прежнему раскрывает фокус —
/// жест начинается только после `dragThreshold` пунктов смещения.
@MainActor
@Observable
final class PlacementController {

    /// Сколько нужно протащить, чтобы это считалось перетаскиванием, а не кликом.
    static let dragThreshold: CGFloat = 4
    /// Радиус прилипания к краям рабочей области.
    static let edgeSnapDistance: CGFloat = 24
    /// Насколько близко к вырезу надо поднести капсулу, чтобы она пристыковалась.
    static let dockSnapDistance: CGFloat = 56

    private(set) var anchor: CompanionAnchor = .notch
    private(set) var isDragging = false

    /// Экран, для которого сейчас действует `anchor`.
    private(set) var screenKey: String?

    var onChange: (() -> Void)?

    /// Положение капсулы в момент начала жеста, в координатах экрана.
    private var dragStartOrigin: CGPoint?

    // MARK: - Загрузка и сохранение

    func load(for screen: NSScreen) {
        let key = CompanionPlacementStore.key(for: screen)
        guard key != screenKey else { return }
        screenKey = key
        anchor = CompanionPlacementStore.anchor(for: screen)
        onChange?()
    }

    /// Вернуть компаньона к вырезу — пункт меню и двойной клик по глазам.
    func dock(on screen: NSScreen) {
        anchor = .notch
        CompanionPlacementStore.save(.notch, for: screen)
        onChange?()
    }

    // MARK: - Жест

    func beginDrag(geometry: CompanionGeometry) {
        guard !isDragging else { return }
        isDragging = true
        dragStartOrigin = geometry.wingRect.origin
    }

    /// `translation` приходит из SwiftUI, где ось Y направлена ВНИЗ,
    /// а координаты экрана в AppKit растут ВВЕРХ — отсюда минус.
    func updateDrag(translation: CGSize, geometry: CompanionGeometry) {
        guard isDragging, let start = dragStartOrigin else { return }
        let moved = CGPoint(x: start.x + translation.width, y: start.y - translation.height)
        let fractions = geometry.fractions(forCapsuleOrigin: moved)
        anchor = .free(xFraction: fractions.x, yFraction: fractions.y)
        onChange?()
    }

    /// Завершение жеста: прилипание и, если капсулу поднесли к вырезу,
    /// возврат к бесшовной форме.
    func endDrag(geometry: CompanionGeometry, screen: NSScreen) {
        guard isDragging else { return }
        isDragging = false
        dragStartOrigin = nil

        if shouldDock(geometry: geometry) {
            dock(on: screen)
            return
        }
        anchor = snapped(geometry: geometry)
        CompanionPlacementStore.save(anchor, for: screen)
        onChange?()
    }

    func cancelDrag() {
        isDragging = false
        dragStartOrigin = nil
    }

    // MARK: - Прилипание

    /// Капсула считается поднесённой к вырезу, если она стоит на его высоте
    /// и её левый край рядом с правой границей выреза — то есть ровно там,
    /// где она и станет бесшовным крылом.
    private func shouldDock(geometry: CompanionGeometry) -> Bool {
        guard let notch = geometry.notchRect, geometry.canAttachSeamlessWing else { return false }
        let capsule = geometry.wingRect
        let dx = abs(capsule.minX - notch.maxX)
        let dy = abs(capsule.midY - notch.midY)
        return dx <= Self.dockSnapDistance && dy <= Self.dockSnapDistance
    }

    /// Прилипание к краям рабочей области: капсулу легко поставить чуть криво,
    /// а ровный край читается заметно спокойнее.
    private func snapped(geometry: CompanionGeometry) -> CompanionAnchor {
        let capsule = geometry.wingRect
        let area = geometry.visibleFrame
        var origin = capsule.origin

        if abs(capsule.minX - area.minX) <= Self.edgeSnapDistance {
            origin.x = area.minX
        } else if abs(area.maxX - capsule.maxX) <= Self.edgeSnapDistance {
            origin.x = area.maxX - capsule.width
        }

        if abs(area.maxY - capsule.maxY) <= Self.edgeSnapDistance {
            origin.y = area.maxY - capsule.height
        } else if abs(capsule.minY - area.minY) <= Self.edgeSnapDistance {
            origin.y = area.minY
        }

        let fractions = geometry.fractions(forCapsuleOrigin: origin)
        return .free(xFraction: fractions.x, yFraction: fractions.y)
    }
}
