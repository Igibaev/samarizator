import SwiftUI

/// Полный набор визуальных параметров одного `CompanionState`.
///
/// ВСЕ числа тут — сознательно вынесены из логики в один конфиг-подобный
/// файл (по требованию PHASE-3-PROMPT.md), чтобы автор крутил внешний вид
/// эмоций, глядя на результат на живой машине, не трогая
/// `CompanionStateMachine`/`EyesViewModel`/`EyesView`. Множители — относительно
/// базовых величин в `CharacterConfig`: 1.0 означает "как в Фазе 2 / idle".
///
/// Значения в `table` ниже — ориентиры из таблицы PHASE-3-PROMPT.md, не
/// финальная подгонка: сборка не проверялась (Linux-контейнер, нет
/// Swift-тулчейна), автор поправит цифры на месте.
struct StateAppearance {

    // MARK: - Форма глаз

    var eyeWidthMultiplier: CGFloat = 1
    var eyeHeightMultiplier: CGFloat = 1
    var eyeTiltDegrees: Double = 0
    var eyeShape: EyeShape = .slit

    /// Доп. смещение взгляда по X (-1...1), поверх обычного слежения за
    /// курсором. Нужно для "взгляд уходит вбок" у `.thinking` — персонаж
    /// как будто задумался, даже когда курсор прямо перед ним.
    var gazeBiasX: CGFloat = 0

    // MARK: - Подсветка состояния

    var highlightColor: Color = .clear
    /// 0...1. У `.idle` — 0 (подсветки не видно вовсе).
    var highlightIntensity: Double = 0

    // MARK: - Характер движения (множители к CharacterConfig)

    var breathRateMultiplier: Double = 1
    var breathAmplitudeMultiplier: Double = 1

    /// "Подпрыгивание" `.celebrating` переиспользует уже существующий цикл
    /// дыхания в `EyesViewModel.startBreathing` — второй таймер под это
    /// специально не заводили (см. PHASE-3-PROMPT.md: "без второго набора
    /// таймеров"). 0 — не подпрыгивает вовсе.
    var bobAmplitudeMultiplier: Double = 0

    var blinkRateMultiplier: Double = 1
    var saccadeFrequencyMultiplier: Double = 1
    var saccadeAmplitudeMultiplier: Double = 1
    /// >1 — саккада медленнее (длиннее и рывок, и возврат). Нужен для
    /// "медленных саккад" у `.thinking`.
    var saccadeSpeedMultiplier: Double = 1
}

extension StateAppearance {

    /// Таблица `CompanionState → StateAppearance`. `CompanionStateMachine`
    /// достаёт из неё целевые параметры при каждом `setState`.
    static let table: [CompanionState: StateAppearance] = [

        .idle: StateAppearance(),

        .listening: StateAppearance(
            // "шире, раскрытые" — увеличенные, не прищуренные глаза.
            eyeWidthMultiplier: 1.15,
            eyeHeightMultiplier: 1.3,
            highlightColor: .cyan,
            highlightIntensity: 0.5,
            breathRateMultiplier: 1.3
        ),

        .thinking: StateAppearance(
            // Прищур + взгляд вбок + медленные, но частые саккады
            // ("задумчиво блуждающий взгляд").
            eyeHeightMultiplier: 0.6,
            gazeBiasX: 0.5,
            highlightColor: .purple,
            highlightIntensity: 0.3,
            saccadeFrequencyMultiplier: 1.6,
            saccadeSpeedMultiplier: 1.8
        ),

        .reminding: StateAppearance(
            // "мягкая пульсация подсветки" — не отдельная анимация: см.
            // NotchRootView.highlightGlow, интенсивность там модулируется
            // EyesViewModel.breathPulse, который и так уже дышит по циклу.
            highlightColor: .yellow,
            highlightIntensity: 0.45
        ),

        // Самоироничное, ИГРОВОЕ раздражение — персонаж скучает и "вздыхает",
        // НЕ укоряет пользователя. Никакого сведённого "злого" прищура —
        // только скучающий прищур и лёгкий наклон. См. «Тон персонажа» в
        // PHASE-3-PROMPT.md — требование прямое, не смягчать.
        .annoyed: StateAppearance(
            eyeHeightMultiplier: 0.55,
            eyeTiltDegrees: -4,
            highlightColor: .orange,
            // Заметно слабее .celebrating — позитив громче негатива,
            // прямое требование автора (см. ниже и PHASE-3-PROMPT.md).
            highlightIntensity: 0.25,
            // Медленнее и глубже — "подчёркнутые вздохи".
            breathRateMultiplier: 0.6,
            breathAmplitudeMultiplier: 2.4,
            // Скучающее, ленивое, редкое моргание.
            blinkRateMultiplier: 0.7,
            saccadeFrequencyMultiplier: 1.4,
            saccadeAmplitudeMultiplier: 1.3
        ),

        // По амплитуде и яркости ЗАМЕТНО СИЛЬНЕЕ, чем .annoyed — прямое
        // требование автора: позитив должен быть громче негатива.
        .celebrating: StateAppearance(
            eyeShape: .crescent,
            highlightColor: .yellow,
            highlightIntensity: 0.9,
            breathRateMultiplier: 2.2,
            bobAmplitudeMultiplier: 6,
            blinkRateMultiplier: 1.6
        ),

        // Фаза 4 достроит логику "сжигания" задачи; здесь только внешний
        // вид по ориентирам из PHASE-3-PROMPT.md ("сузились", "тёплая
        // подсветка снизу" — позиционирование подсветки снизу оставлено
        // на Фазу 4, здесь только цвет/сила).
        .burning: StateAppearance(
            eyeHeightMultiplier: 0.7,
            highlightColor: .orange,
            highlightIntensity: 0.6,
            breathRateMultiplier: 1.4
        ),
    ]
}
