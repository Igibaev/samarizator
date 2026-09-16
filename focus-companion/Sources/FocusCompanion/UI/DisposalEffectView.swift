import SwiftUI

/// Три эффекта утилизации задачи (design.md §12.1).
///
/// На событие играется РОВНО ОДИН эффект, не комбинация, и не дольше 1.6 с.
/// Эффект — украшение поверх уже сохранённого статуса `expired`: если он не
/// доиграет, задача всё равно останется в истории.
struct DisposalModifier: ViewModifier {
    var isActive: Bool
    var effect: DisposalEffect
    var reduceMotion: Bool

    @State private var progress: Double = 0

    func body(content: Content) -> some View {
        content
            .modifier(effectGeometry)
            .overlay(alignment: .trailing) { decoration }
            .onChange(of: isActive) { _, active in
                guard active else {
                    progress = 0
                    return
                }
                // Reduce Motion: ни частиц, ни летящей бумаги, ни щётки —
                // только исчезновение строки.
                guard !reduceMotion else {
                    progress = 1
                    return
                }
                progress = 0
                withAnimation(.easeInOut(duration: effect.duration)) { progress = 1 }
            }
    }

    private var effectGeometry: some ViewModifier {
        DisposalGeometry(effect: effect, progress: isActive ? progress : 0, reduceMotion: reduceMotion)
    }

    @ViewBuilder
    private var decoration: some View {
        if isActive && !reduceMotion {
            switch effect {
            case .burn:
                EmberParticles(progress: progress)
            case .paper:
                WasteBasketGlyph(progress: progress)
            case .sweep:
                BroomGlyph(progress: progress)
            }
        }
    }
}

/// Геометрия самой строки во время эффекта.
private struct DisposalGeometry: ViewModifier {
    var effect: DisposalEffect
    var progress: Double
    var reduceMotion: Bool

    func body(content: Content) -> some View {
        switch effect {
        case .burn:
            content
                .opacity(1 - progress)
                // Тлеющий край: строка выгорает слева направо.
                .overlay(alignment: .leading) {
                    Rectangle()
                        .fill(
                            LinearGradient(
                                colors: [
                                    DesignTokens.Palette.accentWarning.opacity(0.9),
                                    DesignTokens.Palette.accentRecording.opacity(0.0),
                                ],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .frame(width: 22)
                        .offset(x: progress * CompanionGeometry.Metrics.shelfWidth)
                        .opacity(progress < 1 ? 1 : 0)
                        .allowsHitTesting(false)
                }
        case .paper:
            // Строка сминается и уходит короткой дугой в корзину.
            content
                .scaleEffect(x: 1 - 0.55 * progress, y: 1 - 0.35 * progress, anchor: .center)
                .rotationEffect(.degrees(progress * 14))
                .offset(x: progress * 70, y: progress * 26)
                .opacity(1 - progress)
        case .sweep:
            // Остатки сдвигаются за край.
            content
                .offset(x: progress * CompanionGeometry.Metrics.shelfWidth * 0.75)
                .opacity(1 - progress)
        }
    }
}

/// 6–10 маленьких частиц. Постоянного тела и рук у персонажа нет — предметы
/// появляются только в области задачи и исчезают после действия.
private struct EmberParticles: View {
    var progress: Double
    private let count = 8

    var body: some View {
        ZStack {
            ForEach(0..<count, id: \.self) { index in
                let phase = Double(index) / Double(count)
                Circle()
                    .fill(index.isMultiple(of: 2)
                        ? DesignTokens.Palette.accentWarning
                        : DesignTokens.Palette.accentRecording)
                    .frame(width: 2, height: 2)
                    .offset(
                        x: CGFloat(-10 + phase * 20),
                        y: CGFloat(-progress * (10 + phase * 8))
                    )
                    .opacity(progress < 0.15 ? 0 : (1 - progress))
            }
        }
        .frame(width: 28, height: 20)
        .allowsHitTesting(false)
    }
}

private struct WasteBasketGlyph: View {
    var progress: Double

    var body: some View {
        Image(systemName: "trash")
            .font(.system(size: 11, weight: .regular))
            .foregroundStyle(DesignTokens.Palette.textSecondary)
            .opacity(progress > 0.05 && progress < 0.95 ? 1 : 0)
            .padding(.trailing, DesignTokens.Spacing.xxs)
            .allowsHitTesting(false)
    }
}

private struct BroomGlyph: View {
    var progress: Double

    var body: some View {
        Image(systemName: "wind")
            .font(.system(size: 11, weight: .regular))
            .foregroundStyle(DesignTokens.Palette.textSecondary)
            .rotationEffect(.degrees(-12 + progress * 24))
            .opacity(progress < 0.95 ? 1 : 0)
            .padding(.trailing, DesignTokens.Spacing.xxs)
            .allowsHitTesting(false)
    }
}
