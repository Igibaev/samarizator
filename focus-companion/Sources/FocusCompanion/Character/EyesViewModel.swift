import AppKit
import SwiftUI
import Observation

/// Фоновая жизнь глаз: моргание, слежение за курсором, цикл «думает»,
/// короткий толчок в злости.
///
/// Анимации запускаются событиями интерфейса и не требуют ответа языковой
/// модели. Непрерывного рендера 60 fps нет: между морганиями задача просто
/// спит (design.md §13).
@MainActor
@Observable
final class EyesViewModel {

    /// Доля открытости при моргании: 1 — открыт, ~0.11 — закрыт (18 → 2 pt).
    private(set) var openFraction: CGFloat = 1
    /// Смещение пары за курсором, в pt.
    private(set) var gazeOffset: CGSize = .zero
    /// Короткий толчок в злости, в pt.
    private(set) var jolt: CGFloat = 0
    /// Фаза цикла «думает»: какой глаз сейчас прищурен.
    private(set) var thinkingPhase = 0

    var reduceMotion = false

    private let stateMachine: CompanionStateMachine
    private let taskBag = TaskBag()
    /// Воспроизводимое моргание для скриншотов: FOCUS_BLINK_SEED=<число>.
    private var random: SeededGenerator

    init(stateMachine: CompanionStateMachine) {
        self.stateMachine = stateMachine
        let seedText = ProcessInfo.processInfo.environment[CharacterConfig.blinkSeedEnvironmentKey]
        self.random = SeededGenerator(seed: UInt64(seedText ?? "") ?? UInt64.random(in: 1...UInt64.max))
        scheduleNextBlink()
        startThinkingCycle()
    }

    deinit {
        taskBag.cancelAll()
    }

    // MARK: - Слежение за курсором

    /// Общий сдвиг пары до ±3 pt по x и ±2 pt по y, и только рядом с корпусом.
    func updateGaze(mouse: CGPoint, wing: CGRect) {
        guard stateMachine.appearance.tracksCursor, !reduceMotion else {
            setGaze(.zero)
            return
        }
        let center = CGPoint(x: wing.midX, y: wing.midY)
        let dx = mouse.x - center.x
        let dy = mouse.y - center.y
        let distance = (dx * dx + dy * dy).squareRoot()
        guard distance <= CharacterConfig.gazeNearRadius else {
            setGaze(.zero)
            return
        }
        guard distance > CharacterConfig.gazeDeadZoneRadius else {
            setGaze(.zero)
            return
        }
        let normalizedX = max(-1, min(1, dx / CharacterConfig.gazeNearRadius))
        let normalizedY = max(-1, min(1, dy / CharacterConfig.gazeNearRadius))
        setGaze(CGSize(
            width: normalizedX * CharacterConfig.gazeMaxShiftX,
            // Экранная Y растёт вверх, а SwiftUI-смещение — вниз.
            height: -normalizedY * CharacterConfig.gazeMaxShiftY
        ))
    }

    private func setGaze(_ target: CGSize) {
        guard target != gazeOffset else { return }
        withAnimation(.easeOut(duration: CharacterConfig.gazeSmoothing)) {
            gazeOffset = target
        }
    }

    // MARK: - Моргание

    private func scheduleNextBlink() {
        let delay = Double.random(
            in: CharacterConfig.blinkMinInterval...CharacterConfig.blinkMaxInterval,
            using: &random
        ) / max(0.05, stateMachine.appearance.blinkRateMultiplier)
        taskBag.replace(.blink, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard !Task.isCancelled, let self else { return }
            await self.blinkOnce()
            guard !Task.isCancelled else { return }
            self.scheduleNextBlink()
        })
    }

    /// 18 → 2 → 18 pt за 110 мс.
    func blinkOnce() async {
        guard !reduceMotion else { return }
        let closed = CharacterConfig.blinkClosedHeight / CharacterConfig.eyeHeight
        let half = CharacterConfig.blinkDuration / 2
        withAnimation(.easeIn(duration: half)) { openFraction = closed }
        try? await Task.sleep(nanoseconds: UInt64(half * 1_000_000_000))
        guard !Task.isCancelled else { return }
        withAnimation(.easeOut(duration: half)) { openFraction = 1 }
        try? await Task.sleep(nanoseconds: UInt64(half * 1_000_000_000))
    }

    // MARK: - «Думает»

    /// Поочерёдное сужение глаз, цикл 1.2 с — и ТОЛЬКО при реальной обработке.
    private func startThinkingCycle() {
        taskBag.replace(.thinkingCycle, with: Task { [weak self] in
            while !Task.isCancelled {
                let half = CharacterConfig.thinkingCycle / 2
                try? await Task.sleep(nanoseconds: UInt64(half * 1_000_000_000))
                guard !Task.isCancelled, let self else { return }
                guard self.stateMachine.state == .thinking, !self.reduceMotion else {
                    if self.thinkingPhase != 0 { self.thinkingPhase = 0 }
                    continue
                }
                withAnimation(.easeInOut(duration: half)) {
                    self.thinkingPhase = self.thinkingPhase == 0 ? 1 : 0
                }
            }
        })
    }

    /// Насколько прищурен конкретный глаз в цикле «думает».
    func thinkingSquint(isRight: Bool) -> CGFloat {
        guard stateMachine.state == .thinking else { return 0 }
        let active = isRight ? (thinkingPhase == 1) : (thinkingPhase == 0)
        return active ? 0.22 : 0
    }

    // MARK: - Толчок в злости

    /// Один короткий толчок на 2 pt — часть реакции `.angry` (design.md §12).
    func performJolt() {
        guard !reduceMotion else { return }
        withAnimation(.easeOut(duration: 0.08)) { jolt = -2 }
        taskBag.replace(.stateReset, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: 90_000_000)
            guard !Task.isCancelled, let self else { return }
            withAnimation(.easeIn(duration: 0.12)) { self.jolt = 0 }
        })
    }
}

/// Линейный конгруэнтный генератор: нужен только для воспроизводимого
/// моргания в скриншотах, криптостойкость здесь ни при чём.
struct SeededGenerator: RandomNumberGenerator {
    private var state: UInt64

    init(seed: UInt64) {
        self.state = seed == 0 ? 0x9E3779B97F4A7C15 : seed
    }

    mutating func next() -> UInt64 {
        state = state &* 6364136223846793005 &+ 1442695040888963407
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58476D1CE4E5B9
        z = (z ^ (z >> 27)) &* 0x94D049BB133111EB
        return z ^ (z >> 31)
    }
}
