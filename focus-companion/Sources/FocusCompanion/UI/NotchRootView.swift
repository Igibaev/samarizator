import SwiftUI

/// Корневая вью персонажа.
///
/// Фаза 1 отрисовывала только статичную капсулу. Фаза 2 добавила живые
/// глаза. Фаза 3 добавляет раскрытие по ховеру и машину состояний эмоций —
/// окно `NSPanel` теперь ВСЕГДА в размере раскрытой панели (решение автора
/// №2 в PHASE-3-PROMPT.md), а морфится только то, что рисуется внутри:
/// размер и радиусы капсулы (`NotchShape.animatableData`) и положение/
/// масштаб глаз. `matchedGeometryEffect` сознательно не используется — глаза
/// существуют в одном экземпляре и никуда не "переезжают" между иерархиями,
/// им достаточно анимировать значения (см. решение №3 в PHASE-3-PROMPT.md).
struct NotchRootView: View {
    var stateMachine: CompanionStateMachine
    var hoverDetector: HoverDetector

    // @State, а не let: ViewModel должен пережить перерисовки этой вью
    // (не пересоздаваться на каждый re-render), а таймеры моргания/саккад/
    // дыхания внутри него живут, пока жива вью. `stateMachine`/`hoverDetector`
    // сюда не попадают — они персистентны на уровне NotchWindowController и
    // переживают даже пересоздание САМОЙ этой вью (переезд между экранами).
    @State private var eyesModel: EyesViewModel

    init(stateMachine: CompanionStateMachine, hoverDetector: HoverDetector) {
        self.stateMachine = stateMachine
        self.hoverDetector = hoverDetector

        let eyes = EyesViewModel(stateMachine: stateMachine)
        _eyesModel = State(initialValue: eyes)
    }

    var body: some View {
        ZStack(alignment: .top) {
            capsuleGroup
        }
        // Окно всегда в размере раскрытой панели — эта вью заполняет его
        // целиком, а `capsuleGroup` внутри занимает только текущий (collapsed
        // или expanded) размер, прижатый к верху и центрированный по X.
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        // Здесь намеренно НЕТ заливки/фона на весь фрейм: пустые поля вокруг
        // capsuleGroup — это буквально ничего не нарисованное, а значит и не
        // hit-testable по умолчанию. `capsuleGroup` ниже всё равно явно
        // ограничивает попадания своей формой и включает hit-testing только
        // когда панель раскрыта — это защита от рассинхронизации на случай
        // правок, а не единственная линия обороны.
        .onAppear(perform: connectGazeToHoverPolling)
    }

    /// Подключает слежение глаз к опросу курсора, которым `HoverDetector` и
    /// так определяет наведение.
    ///
    /// Зачем это нужно: глобальный монитор мыши (`MouseTracker` внутри
    /// `EyesViewModel`) слепнет, как только панель раскрывается и начинает
    /// перехватывать клики — курсор в этот момент как раз над собственным
    /// окном приложения. Без этой связки взгляд замирал бы ровно тогда, когда
    /// пользователь смотрит на раскрытого персонажа.
    ///
    /// Делается в `onAppear`, а не в `init`: структура-вью может быть создана
    /// повторно, и тогда замыкание указывало бы на выброшенную копию модели,
    /// а не на ту, что реально живёт в `@State`.
    ///
    /// Обе ссылки слабые. `detector` — потому что замыкание хранится внутри
    /// самого детектора, и сильная ссылка замкнула бы цикл.
    private func connectGazeToHoverPolling() {
        let eyes = eyesModel
        let detector = hoverDetector
        detector.onMouseLocation = { [weak eyes, weak detector] location in
            guard let eyes, let detector, detector.isExpanded else { return }
            eyes.updateGaze(from: location)
        }
    }

    /// Капсула (фон + подсветка + голова персонажа + заготовка контента
    /// раскрытой панели), сама по себе — без внешнего выравнивания.
    private var capsuleGroup: some View {
        let shape = NotchShape(
            topCornerRadius: hoverDetector.isExpanded
                ? AppearanceConfig.expandedTopCornerRadius
                : AppearanceConfig.topCornerRadius,
            bottomCornerRadius: hoverDetector.isExpanded
                ? AppearanceConfig.expandedBottomCornerRadius
                : AppearanceConfig.bottomCornerRadius
        )
        let size = hoverDetector.isExpanded ? hoverDetector.expandedSize : hoverDetector.collapsedSize

        return ZStack(alignment: .top) {
            shape.fill(AppearanceConfig.capsuleColor)

            highlightGlow

            // "Головная" зона — фиксированной высоты, равной высоте
            // СВЁРНУТОЙ капсулы, всегда прижата к верху. Она нужна, чтобы
            // положение глаз (принятое в Фазе 2, трогать нельзя) не
            // зависело от того, насколько сейчас разрослась капсула вниз —
            // без этой развязки глаза при раскрытии "уезжали" бы к центру
            // увеличенного фрейма вслед за стандартным центрированием ZStack.
            ZStack {
                EyesView(model: eyesModel, appearance: stateMachine.appearance)
                    // Debug-режим: персонаж увеличен и сдвинут ниже, чтобы
                    // моргание и саккады было видно в деталях — сама капсула
                    // в этом же режиме тоже вытянута вниз.
                    .scaleEffect(AppearanceConfig.isDebug ? CharacterConfig.debugScale : 1)
                    .offset(
                        y: CharacterConfig.eyesYOffset
                            + (AppearanceConfig.isDebug ? CharacterConfig.debugYOffset : 0)
                    )
            }
            .frame(width: size.width, height: hoverDetector.collapsedSize.height)

            ExpandedPanelView(stateMachine: stateMachine)
                .padding(.top, hoverDetector.collapsedSize.height)
                .opacity(hoverDetector.isExpanded ? 1 : 0)
        }
        .frame(width: size.width, height: size.height)
        .clipShape(shape)
        // Хит-тест ограничен точной формой капсулы, а не прямоугольником
        // фрейма — иначе прозрачные "уши" вокруг вогнутых верхних углов
        // тоже ловили бы клики. Включаем интерактивность только когда
        // панель реально раскрыта: `NotchPanel.ignoresMouseEvents` и так уже
        // это гарантирует на уровне AppKit, но дублируем на уровне SwiftUI —
        // окно в Фазе 3 заметно больше самой капсулы, и ошибка здесь
        // заблокировала бы существенный кусок экрана (см. PHASE-3-PROMPT.md).
        .contentShape(shape)
        .allowsHitTesting(hoverDetector.isExpanded)
    }

    /// Мягкая цветная подсветка состояния позади глаз. Цвет и базовую силу
    /// задаёт `StateAppearance.highlightColor/highlightIntensity`, а
    /// собственно "мягкая пульсация" (нужна для `.reminding`) — это не
    /// отдельная анимация, а модуляция уже идущим циклом дыхания
    /// (`EyesViewModel.breathPulse`) — второй таймер под неё не заводили.
    private var highlightGlow: some View {
        Circle()
            .fill(stateMachine.appearance.highlightColor)
            .frame(width: CharacterConfig.highlightGlowSize, height: CharacterConfig.highlightGlowSize)
            .blur(radius: CharacterConfig.highlightBlurRadius)
            .opacity(stateMachine.appearance.highlightIntensity * (0.6 + 0.4 * eyesModel.breathPulse))
            .offset(y: CharacterConfig.eyesYOffset)
            .allowsHitTesting(false)
    }
}

// #Preview здесь намеренно нет: макрос Preview реализован плагином Xcode
// (PreviewsMacros), которого нет при сборке через `swift build` из терминала —
// любой #Preview в исходниках валит нашу сборку. Смотреть результат — запуском,
// желательно с FOCUS_DEBUG=1 (см. README).
