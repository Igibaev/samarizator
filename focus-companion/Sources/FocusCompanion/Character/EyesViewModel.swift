import AppKit
import SwiftUI
import Observation

/// Состояние "живости" персонажа: слежение зрачков за курсором, моргание,
/// саккады и дыхание.
///
/// Анимация всех переходов сделана через `withAnimation` вокруг присвоений
/// `@Observable`-свойств (SwiftUI сам подхватывает изменение при следующей
/// перерисовке `EyesView`), а не через ручной интегратор пружины на таймере —
/// так вся физика (`interactiveSpring`, `easeOut`...) остаётся стандартной
/// логикой SwiftUI, а не самодельным кодом, который здесь физически нельзя
/// ни разу собрать и посмотреть глазами.
@MainActor
@Observable
final class EyesViewModel {

    /// Текущее смещение зрачков, нормированное -1...1 по обеим осям
    /// (1 = зрачок у самого края склеры). Общее для обоих глаз — расхождение
    /// глаз (конвергенция при близком курсоре) в Фазе 2 не делаем.
    private(set) var pupilOffset: CGPoint = .zero

    /// Текущий вертикальный масштаб глаза: 1 — открыт полностью,
    /// `CharacterConfig.blinkClosedScaleY` — закрыт.
    private(set) var eyeScaleY: CGFloat = 1

    /// Масштаб всей пары глаз от дыхания (1 ± амплитуда). Капсулу не трогает —
    /// её форма жёстко привязана к физическому вырезу (см. PHASE-2-PROMPT.md).
    private(set) var breathScale: CGFloat = 1

    private let mouseTracker = MouseTracker()

    /// Точка, куда зрачок "должен" смотреть в состоянии покоя — то есть
    /// нормированное смещение, посчитанное из реальной позиции курсора.
    /// Саккады временно уводят `pupilOffset` от неё и возвращают обратно,
    /// а не наоборот.
    private var restOffset: CGPoint = .zero

    private var blinkTask: Task<Void, Never>?
    private var saccadeLoopTask: Task<Void, Never>?
    private var breathTask: Task<Void, Never>?

    init() {
        mouseTracker.onMove = { [weak self] location in
            self?.handleMouseMove(to: location)
        }
        startBreathing()
        scheduleNextBlink()
        startSaccadeLoop()
    }

    deinit {
        blinkTask?.cancel()
        saccadeLoopTask?.cancel()
        breathTask?.cancel()
    }

    // MARK: - Слежение за курсором

    private func handleMouseMove(to location: CGPoint) {
        let target = Self.normalizedOffset(mouseLocation: location)
        restOffset = target
        withAnimation(
            .interactiveSpring(
                response: CharacterConfig.trackingSpringResponse,
                dampingFraction: CharacterConfig.trackingSpringDamping
            )
        ) {
            pupilOffset = target
        }
    }

    /// Переводит глобальную позицию курсора в нормированное -1...1 смещение
    /// относительно центра персонажа на экране, где он сейчас находится.
    private static func normalizedOffset(mouseLocation: CGPoint) -> CGPoint {
        guard let screen = NSScreen.screenWithMouse ?? NSScreen.main else { return .zero }
        let notch = screen.notchFrameWithFallback
        let center = CGPoint(x: notch.midX, y: notch.midY)

        let dx = mouseLocation.x - center.x
        let dy = mouseLocation.y - center.y
        let distance = (dx * dx + dy * dy).squareRoot()

        guard distance > CharacterConfig.trackingDeadZoneRadius else { return .zero }

        let range = CharacterConfig.trackingRange
        let nx = clamp(dx / range, to: -1...1)
        // В AppKit Y растёт вверх; в нашей нормировке "вниз" (курсор ниже
        // персонажа) должно быть положительным смещением зрачка вниз —
        // поэтому знак инвертируем.
        let ny = clamp(-dy / range, to: -1...1)
        return CGPoint(x: nx, y: ny)
    }

    // MARK: - Моргание

    private func scheduleNextBlink() {
        blinkTask?.cancel()
        let delay = Double.random(in: CharacterConfig.blinkMinInterval...CharacterConfig.blinkMaxInterval)
        blinkTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.nanoseconds(delay))
            guard !Task.isCancelled, let self else { return }
            await self.performBlinkCycle()
            self.scheduleNextBlink()
        }
    }

    private func performBlinkCycle() async {
        await singleBlink()
        guard !Task.isCancelled else { return }
        if Double.random(in: 0..<1) < CharacterConfig.doubleBlinkProbability {
            try? await Task.sleep(nanoseconds: Self.nanoseconds(CharacterConfig.doubleBlinkPause))
            guard !Task.isCancelled else { return }
            await singleBlink()
        }
    }

    private func singleBlink() async {
        let half = CharacterConfig.blinkDuration / 2
        withAnimation(.easeIn(duration: half)) {
            eyeScaleY = CharacterConfig.blinkClosedScaleY
        }
        try? await Task.sleep(nanoseconds: Self.nanoseconds(half))
        guard !Task.isCancelled else { return }
        withAnimation(.easeOut(duration: half)) {
            eyeScaleY = 1
        }
        try? await Task.sleep(nanoseconds: Self.nanoseconds(half))
    }

    // MARK: - Саккады

    /// Постоянно (раз в полсекунды) проверяет, сколько курсор простоял без
    /// движения, и включает саккаду при превышении порога. Опрос, а не
    /// подписка на "нет событий N секунд" — таймеров бездействия в AppKit нет,
    /// а перезапускать один Task-таймер при каждом chirp'е движения мыши
    /// сложнее и не даёт выигрыша: саккада — редкое и не батарее-критичное
    /// событие.
    private func startSaccadeLoop() {
        saccadeLoopTask?.cancel()
        saccadeLoopTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 500_000_000)
                guard !Task.isCancelled, let self else { return }
                let idleFor = Date().timeIntervalSince(self.mouseTracker.lastMovementDate)
                if idleFor >= CharacterConfig.saccadeIdleThreshold {
                    await self.performSaccade()
                    guard !Task.isCancelled else { return }

                    // Пауза до следующей саккады. Без неё цикл, пока курсор
                    // неподвижен, запускал бы саккаду каждые ~0.9 сек без
                    // остановки: глаза дёргались бы непрерывно, и персонаж
                    // читался бы как тревожный, а не как живой.
                    let pause = Double.random(
                        in: CharacterConfig.saccadeMinPause...CharacterConfig.saccadeMaxPause
                    )
                    try? await Task.sleep(nanoseconds: Self.nanoseconds(pause))
                }
            }
        }
    }

    private func performSaccade() async {
        let angle = Double.random(in: 0..<(2 * Double.pi))
        let amplitude = CharacterConfig.saccadeAmplitude
        let jump = CGPoint(
            x: Self.clamp(restOffset.x + CGFloat(cos(angle)) * amplitude, to: -1...1),
            y: Self.clamp(restOffset.y + CGFloat(sin(angle)) * amplitude, to: -1...1)
        )

        // Рывок — быстрый и резкий (короткий easeOut), возврат — заметно
        // медленнее и мягче. Несимметрично специально: настоящая саккада —
        // это почти мгновенный скачок глаза с плавным "остыванием" после,
        // а не одинаково плавное движение туда-обратно (то читалось бы как
        // "плавающий", заторможенный взгляд).
        withAnimation(.easeOut(duration: CharacterConfig.saccadeJumpDuration)) {
            pupilOffset = jump
        }
        try? await Task.sleep(nanoseconds: Self.nanoseconds(CharacterConfig.saccadeJumpDuration))
        guard !Task.isCancelled else { return }

        let hold = Double.random(
            in: CharacterConfig.saccadeMinHold...CharacterConfig.saccadeMaxHold
        )
        try? await Task.sleep(nanoseconds: Self.nanoseconds(hold))
        guard !Task.isCancelled else { return }

        withAnimation(.easeInOut(duration: CharacterConfig.saccadeReturnDuration)) {
            pupilOffset = restOffset
        }
        try? await Task.sleep(nanoseconds: Self.nanoseconds(CharacterConfig.saccadeReturnDuration))
    }

    // MARK: - Дыхание

    /// Бесконечный вдох-выдох через явный цикл `Task`, а не
    /// `.repeatForever(autoreverses:)`: так весь ритм управляется теми же
    /// понятными сегментами `withAnimation` + `Task.sleep`, что моргание и
    /// саккады, и одинаково надёжно подхватывается @Observable-свойством
    /// вне тела `View`.
    private func startBreathing() {
        breathTask?.cancel()
        breathTask = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let half = CharacterConfig.breathPeriod / 2
                withAnimation(.easeInOut(duration: half)) {
                    self.breathScale = 1 + CharacterConfig.breathAmplitude
                }
                try? await Task.sleep(nanoseconds: Self.nanoseconds(half))
                guard !Task.isCancelled else { return }
                withAnimation(.easeInOut(duration: half)) {
                    self.breathScale = 1
                }
                try? await Task.sleep(nanoseconds: Self.nanoseconds(half))
            }
        }
    }

    // MARK: - Мелкие утилиты

    private static func nanoseconds(_ seconds: Double) -> UInt64 {
        UInt64(max(0, seconds) * 1_000_000_000)
    }

    private static func clamp(_ value: CGFloat, to range: ClosedRange<CGFloat>) -> CGFloat {
        min(max(value, range.lowerBound), range.upperBound)
    }
}
