import SwiftUI

/// Корневая вью персонажа.
///
/// Фаза 1 отрисовывала только статичную капсулу. Фаза 2 добавляет поверх неё
/// пару живых глаз (`EyesView`): слежение за курсором, моргание, саккады,
/// едва заметное дыхание. Сама капсула по-прежнему статична геометрически —
/// дыхание масштабирует только глаза, не форму (см. решение №2 в
/// PHASE-2-PROMPT.md): она жёстко привязана к физическому вырезу, и любая
/// пульсация формы немедленно сломала бы стык с краем экрана.
struct NotchRootView: View {
    // @State, а не let: ViewModel должен пережить перерисовки этой вью
    // (не пересоздаваться на каждый re-render), а таймеры моргания/саккад/
    // дыхания и глобальный монитор мыши внутри него живут, пока жива вью.
    @State private var eyesModel = EyesViewModel()

    /// Подложка капсулы: матовое стекло плюс затемняющая тонировка.
    /// В debug-режиме — плоский красный, иначе капсулу не разглядеть.
    @ViewBuilder
    private var capsuleBackground: some View {
        if AppearanceConfig.isDebug {
            AppearanceConfig.capsuleColor
        } else {
            ZStack {
                VisualEffectBackground(material: AppearanceConfig.capsuleMaterial)
                AppearanceConfig.capsuleColor
                    .opacity(AppearanceConfig.capsuleTintOpacity)
            }
        }
    }

    var body: some View {
        ZStack {
            capsuleBackground

            EyesView(model: eyesModel)
                // Debug-режим: персонаж увеличен и сдвинут ниже, чтобы
                // моргание и саккады было видно в деталях — сама капсула в
                // этом же режиме тоже вытянута вниз.
                .scaleEffect(AppearanceConfig.isDebug ? CharacterConfig.debugScale : 1)
                .offset(
                    y: CharacterConfig.eyesYOffset
                        + (AppearanceConfig.isDebug ? CharacterConfig.debugYOffset : 0)
                )
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        // Персонаж обязан жить ВНУТРИ формы капсулы. Без обрезки глаза,
        // вылезшие за её край, срезаются прямоугольной границей окна — это
        // выглядит как обрубок, а не как лицо.
        .clipShape(
            NotchShape(
                topCornerRadius: AppearanceConfig.topCornerRadius,
                bottomCornerRadius: AppearanceConfig.bottomCornerRadius
            )
        )
        // Окно и так ignoresMouseEvents, но на всякий случай дублируем на уровне
        // вью — эта вью не должна становиться кликабельной ни при каких правках.
        .allowsHitTesting(false)
    }
}

// #Preview здесь намеренно нет: макрос Preview реализован плагином Xcode
// (PreviewsMacros), которого нет при сборке через `swift build` из терминала —
// любой #Preview в исходниках валит нашу сборку. Смотреть результат — запуском,
// желательно с FOCUS_DEBUG=1 (см. README).
