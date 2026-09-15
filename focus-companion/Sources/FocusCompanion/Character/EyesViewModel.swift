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
///
/// Фаза 3: класс получает `CompanionStateMachine` и на каждом шаге своих же
/// циклов читает её `appearance` — множители к интервалам/амплитудам. Так
/// машина состояний ВЛИЯЕТ на уже существующие таймеры, не заводя вторых
/// (см. PHASE-3-PROMPT.md, раздел про `CompanionStateMachine`).
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

    /// Нормированная (0...1) фаза дыхания: 0 на выдохе, 1 на пике вдоха.
    /// Используется как готовый источник плавной пульсации для подсветки
    /// состояния (`.reminding`) — второй таймер под "мягкую пульсацию" не
    /// заводим, переиспользуем уже идущий цикл дыхания.
    private(set) var breathPulse: Double = 0

    /// Доп. вертикальный сдвиг пары глаз от "подпрыгивания" `.celebrating`
    /// (`StateAppearance.bobAmplitudeMultiplier`). Синхронно с дыханием, тем
    /// же циклом — см. `startBreathing`.
    private(set) var bobOffsetY: CGFloat = 0

    private let mouseTracker = MouseTracker()

    /// Машина состояний — источник множителей к циклам ниже. Ссылка общая с
    /// `NotchRootView`/`EyesView` (та же инстанция), не собственная копия.
    private let stateMachine: CompanionStateMachine

    /// Точка, куда зрачок "должен" смотреть в состоянии покоя — то есть
    /// нормированное смещение, посчитанное из реальной позиции курсора.
    /// Саккады временно уводят `pupilOffset` от неё и возвращают обратно,
    /// а не наоборот.
    private var restOffset: CGPoint = .zero

    /// Таймеры моргания, саккад и дыхания. Лежат в `TaskBag`, а не в
    /// обычных свойствах, потому что их надо отменять в `deinit` — а тот у
    /// @MainActor-класса не имеет доступа к изолированным свойствам.
    private let taskBag = TaskBag()

    init(stateMachine: CompanionStateMachine) {
        self.stateMachine = stateMachine
        mouseTracker.onMove = { [weak self] location in
            self?.handleMouseMove(to: location)
        }
        startBreathing()
        scheduleNextBlink()
        startSaccadeLoop()
    }

    deinit {
        taskBag.cancelAll()
    }

    // MARK: - Слежение за курсором

    /// Публичная точка входа для слежения, питаемая ЛИБО глобальным
    /// монитором (`MouseTracker`, свёрнутое состояние панели), ЛИБО 20 Гц
    /// опросом `HoverDetector` (раскрытое состояние, где монитор слепнет —
    /// см. PHASE-3-PROMPT.md, решение №1). Вызывающая сторона сама решает,
    /// какой источник сейчас актуален — здесь оба ведут к одной и той же
    /// логике.
    func updateGaze(from location: CGPoint) {
        handleMouseMove(to: location)
    }

    private func handleMouseMove(to location: CGPoint) {
        let target = normalizedOffset(mouseLocation: location)
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
    /// Добавляет `StateAppearance.gazeBiasX` текущего состояния поверх
    /// обычного слежения — источник "взгляда вбок" у `.thinking`, заметного
    /// даже когда курсор прямо перед персонажем (внутри мёртвой зоны).
    private func normalizedOffset(mouseLocation: CGPoint) -> CGPoint {
        let bias = stateMachine.appearance.gazeBiasX
        guard let screen = NSScreen.screenWithMouse ?? NSScreen.main else {
            return CGPoint(x: bias, y: 0)
        }
        let notch = screen.notchFrameWithFallback
        let center = CGPoint(x: notch.midX, y: notch.midY)

        let dx = mouseLocation.x - center.x
        let dy = mouseLocation.y - center.y
        let distance = (dx * dx + dy * dy).squareRoot()

        guard distance > CharacterConfig.trackingDeadZoneRadius else {
            return CGPoint(x: Self.clamp(bias, to: -1...1), y: 0)
        }

        let range = CharacterConfig.trackingRange
        let nx = Self.clamp(dx / range + bias, to: -1...1)
        // В AppKit Y растёт вверх; в нашей нормировке "вниз" (курсор ниже
        // персонажа) должно быть положительным смещением зрачка вниз —
        // поэтому знак инвертируем.
        let ny = Self.clamp(-dy / range, to: -1...1)
        return CGPoint(x: nx, y: ny)
    }

    // MARK: - Моргание

    private func scheduleNextBlink() {
        let rateMultiplier = max(stateMachine.appearance.blinkRateMultiplier, 0.05)
        let delay = Double.random(in: CharacterConfig.blinkMinInterval...CharacterConfig.blinkMaxInterval) / rateMultiplier
        taskBag.replace(.blink, with: Task { [weak self] in
            try? await Task.sleep(nanoseconds: Self.nanoseconds(delay))
            guard !Task.isCancelled, let self else { return }
            await self.performBlinkCycle()
            self.scheduleNextBlink()
        })
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
        taskBag.replace(.saccade, with: Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 500_000_000)
                guard !Task.isCancelled, let self else { return }
                let freqMultiplier = max(self.stateMachine.appearance.saccadeFrequencyMultiplier, 0.05)
                let idleFor = Date().timeIntervalSince(self.mouseTracker.lastMovementDate)
                if idleFor >= CharacterConfig.saccadeIdleThreshold / freqMultiplier {
                    await self.performSaccade()
                    guard !Task.isCancelled else { return }

                    // Пауза до следующей саккады. Без неё цикл, пока курсор
                    // неподвижен, запускал бы саккаду каждые ~0.9 сек без
                    // остановки: глаза дёргались бы непрерывно, и персонаж
                    // читался бы как тревожный, а не как живой.
                    let pause = Double.random(
                        in: CharacterConfig.saccadeMinPause...CharacterConfig.saccadeMaxPause
                    ) / freqMultiplier
                    try? await Task.sleep(nanoseconds: Self.nanoseconds(pause))
                }
            }
        })
    }

    private func performSaccade() async {
        let appearance = stateMachine.appearance
        let angle = Double.random(in: 0..<(2 * Double.pi))
        let amplitude = CharacterConfig.saccadeAmplitude * appearance.saccadeAmplitudeMultiplier
        let speedMultiplier = max(appearance.saccadeSpeedMultiplier, 0.05)
        let jump = CGPoint(
            x: Self.clamp(restOffset.x + CGFloat(cos(angle)) * amplitude, to: -1...1),
            y: Self.clamp(restOffset.y + CGFloat(sin(angle)) * amplitude, to: -1...1)
        )

        // Рывок — быстрый и резкий (короткий easeOut), возврат — заметно
        // медленнее и мягче. Несимметрично специально: настоящая саккада —
        // это почти мгновенный скачок глаза с плавным "остыванием" после,
        // а не одинаково плавное движение туда-обратно (то читалось бы как
        // "плавающий", заторможенный взгляд). `saccadeSpeedMultiplier` > 1
        // растягивает обе фазы — источник "медленных саккад" у `.thinking`.
        let jumpDuration = CharacterConfig.saccadeJumpDuration * speedMultiplier
        withAnimation(.easeOut(duration: jumpDuration)) {
            pupilOffset = jump
        }
        try? await Task.sleep(nanoseconds: Self.nanoseconds(jumpDuration))
        guard !Task.isCancelled else { return }

        let hold = Double.random(
            in: CharacterConfig.saccadeMinHold...CharacterConfig.saccadeMaxHold
        )
        try? await Task.sleep(nanoseconds: Self.nanoseconds(hold))
        guard !Task.isCancelled else { return }

        let returnDuration = CharacterConfig.saccadeReturnDuration * speedMultiplier
        withAnimation(.easeInOut(duration: returnDuration)) {
            pupilOffset = restOffset
        }
        try? await Task.sleep(nanoseconds: Self.nanoseconds(returnDuration))
    }

    // MARK: - Дыхание

    /// Бесконечный вдох-выдох через явный цикл `Task`, а не
    /// `.repeatForever(autoreverses:)`: так весь ритм управляется теми же
    /// понятными сегментами `withAnimation` + `Task.sleep`, что моргание и
    /// саккады, и одинаково надёжно подхватывается @Observable-свойством
    /// вне тела `View`.
    ///
    /// Фаза 3: тот же цикл заодно двигает `breathPulse` (для пульсации
    /// подсветки `.reminding`) и `bobOffsetY` (для "подпрыгивания"
    /// `.celebrating`) — специально не заводим под них отдельные таймеры.
    private func startBreathing() {
        taskBag.replace(.breath, with: Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let appearance = self.stateMachine.appearance
                let rateMultiplier = max(appearance.breathRateMultiplier, 0.05)
                let half = (CharacterConfig.breathPeriod / 2) / rateMultiplier
                let amplitude = CharacterConfig.breathAmplitude * appearance.breathAmplitudeMultiplier
                let bobPeak = -CharacterConfig.bobAmplitude * appearance.bobAmplitudeMultiplier

                withAnimation(.easeInOut(duration: half)) {
                    self.breathScale = 1 + amplitude
                    self.breathPulse = 1
                    self.bobOffsetY = bobPeak
                }
                try? await Task.sleep(nanoseconds: Self.nanoseconds(half))
                guard !Task.isCancelled else { return }
                withAnimation(.easeInOut(duration: half)) {
                    self.breathScale = 1
                    self.breathPulse = 0
                    self.bobOffsetY = 0
                }
                try? await Task.sleep(nanoseconds: Self.nanoseconds(half))
            }
        })
    }

    // MARK: - Мелкие утилиты

    private static func nanoseconds(_ seconds: Double) -> UInt64 {
        UInt64(max(0, seconds) * 1_000_000_000)
    }

    private static func clamp(_ value: CGFloat, to range: ClosedRange<CGFloat>) -> CGFloat {
        min(max(value, range.lowerBound), range.upperBound)
    }
}
