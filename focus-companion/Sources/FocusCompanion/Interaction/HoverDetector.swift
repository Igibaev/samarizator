import AppKit
import SwiftUI
import Observation

/// Определяет наведение курсора на капсулу и переключает
/// свёрнутое/раскрытое состояние панели.
///
/// Решение автора (см. PHASE-3-PROMPT.md, "Ховер вместо NSTrackingArea"):
/// `NSTrackingArea` требует, чтобы панель видела мышь, а
/// `ignoresMouseEvents = true` — это ровно то, что держит меню-бар под
/// свёрнутой капсулой кликабельным. Поэтому наведение определяется
/// ОПРОСОМ `NSEvent.mouseLocation` по таймеру (~20 Гц), а не монитором
/// событий и не tracking-областью:
/// - опрос дешёвый (чтение состояния, не обработка потока событий);
/// - он одинаково работает и когда панель `ignoresMouseEvents = true`
///   (свёрнуто), и когда `false` (раскрыто) — в отличие от
///   `NSEvent.addGlobalMonitorForEvents` (см. `MouseTracker`), который
///   слепнет, как только панель начинает перехватывать мышь.
///
/// Тем же опросом (через `onMouseLocation`) питается слежение глаз во время
/// раскрытия — см. подключение в `NotchRootView.init`.
@MainActor
@Observable
final class HoverDetector {

    /// Текущее состояние: раскрыта ли панель.
    private(set) var isExpanded = false

    /// Размер свёрнутой капсулы в pt экрана — тот же самый размер и в
    /// локальных координатах содержимого окна (масштабирования между
    /// экранными и view-координатами здесь нет). Считается по текущему
    /// экрану на каждом тике опроса, поэтому переживает смену экрана без
    /// отдельной подписки на уведомления.
    private(set) var collapsedSize: CGSize = .zero

    /// Размер раскрытой капсулы — всегда равен размеру самого окна панели
    /// (решение №2 в PHASE-3-PROMPT.md: окно всегда в размере раскрытого
    /// состояния).
    private(set) var expandedSize: CGSize = .zero

    /// Вызывается на каждом тике опроса с текущей глобальной позицией
    /// курсора — используется `EyesViewModel` для слежения глаз, пока
    /// раскрыто (см. комментарий в шапке файла).
    var onMouseLocation: ((CGPoint) -> Void)?

    /// Вызывается ТОЛЬКО когда `isExpanded` реально меняется. Слушает
    /// `NotchWindowController`, чтобы держать `NSPanel.ignoresMouseEvents`
    /// в синхроне: раскрыто → `false` (панель интерактивна), свёрнуто →
    /// `true` (меню-бар под капсулой остаётся кликабельным).
    var onExpansionChange: ((Bool) -> Void)?

    /// Копит промежуточное "хочет измениться" состояние наведения и момент,
    /// с которого оно держится — простая ручная реализация debounce внутри
    /// одного цикла опроса, без отдельных отменяемых задач на каждый чих.
    private var pendingTargetExpanded: Bool?
    private var pendingSince: Date?

    private let taskBag = TaskBag()

    init() {
        updateSizes()
        startPolling()
    }

    deinit {
        taskBag.cancelAll()
    }

    private func startPolling() {
        taskBag.replace(.hoverPoll, with: Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                self.tick()
                try? await Task.sleep(nanoseconds: Self.nanoseconds(AppearanceConfig.hoverPollInterval))
            }
        })
    }

    private func tick() {
        guard let screen = NSScreen.screenWithMouse ?? NSScreen.main else { return }
        let mouseLocation = NSEvent.mouseLocation

        onMouseLocation?(mouseLocation)
        updateSizes(on: screen)

        let activeRect = isExpanded ? screen.capsulePanelFrame : screen.collapsedCapsuleFrame
        let rawHover = NSMouseInRect(mouseLocation, activeRect, false)
        evaluateDebounce(rawHover: rawHover)
    }

    /// Задержка перед раскрытием (~0.2с, гасит случайный пролёт курсора
    /// мимо) и перед сворачиванием (~0.15с, короче — гасит дрожание курсора
    /// у самой границы, но не должна ощущаться как залипание) — обе в
    /// `AppearanceConfig`.
    private func evaluateDebounce(rawHover: Bool) {
        guard rawHover != isExpanded else {
            pendingTargetExpanded = nil
            pendingSince = nil
            return
        }

        guard pendingTargetExpanded == rawHover, let pendingSince else {
            pendingTargetExpanded = rawHover
            pendingSince = Date()
            return
        }

        let requiredDelay = rawHover ? AppearanceConfig.hoverExpandDelay : AppearanceConfig.hoverCollapseDelay
        guard Date().timeIntervalSince(pendingSince) >= requiredDelay else { return }

        self.pendingTargetExpanded = nil
        self.pendingSince = nil
        withAnimation(.easeInOut(duration: AppearanceConfig.hoverMorphDuration)) {
            isExpanded = rawHover
        }
        onExpansionChange?(rawHover)
    }

    private func updateSizes(on screen: NSScreen? = nil) {
        guard let screen = screen ?? NSScreen.screenWithMouse ?? NSScreen.main else { return }
        collapsedSize = screen.collapsedCapsuleFrame.size
        expandedSize = screen.capsulePanelFrame.size
    }

    private static func nanoseconds(_ seconds: Double) -> UInt64 {
        UInt64(max(0, seconds) * 1_000_000_000)
    }
}
