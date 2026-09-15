import SwiftUI

/// Содержимое раскрытой панели ниже "головы" персонажа — Фаза 4а.
///
/// Список активных задач с чекбоксом выполнения, поле ввода новой задачи,
/// заботливое сообщение при заполненных трёх слотах и короткая сводка по
/// выполненным (без самого списка — не копится, см. `TaskStore`).
struct ExpandedPanelView: View {
    var hoverDetector: HoverDetector
    var taskPanel: TaskPanelController

    @State private var newTaskText: String = ""

    /// Отдаёт фокус текстовому полю. Держим отдельно от `newTaskText`:
    /// сворачивание панели должно сбрасывать именно фокус (и тем самым —
    /// ключевой статус окна, см. `NotchPanel.allowsKeyWhenExpanded`), а не
    /// обязательно очищать введённый текст.
    @FocusState private var isInputFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            taskList
            inputRow
            completedSummary
        }
        .padding(.horizontal, 14)
        .padding(.top, 8)
        .padding(.bottom, 10)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        // Своя интерактивность (Фаза 4а) остаётся в рамках того, что уже
        // разрешил родитель: `NotchRootView.capsuleGroup` включает
        // hit-testing на весь этот поддерево ТОЛЬКО когда панель реально
        // раскрыта — здесь отдельно это не дублируем и не ослабляем.
        //
        // Сворачивание панели обязано забрать фокус у поля ввода — иначе
        // текстовое поле осталось бы "focused" в SwiftUI-смысле даже когда
        // панель уже не key-окно и клавиатурный ввод в неё не попадает.
        .onChange(of: hoverDetector.isExpanded) { _, expanded in
            if !expanded {
                isInputFocused = false
            }
        }
    }

    // MARK: - Список задач

    private var taskList: some View {
        VStack(alignment: .leading, spacing: 4) {
            if taskPanel.store.activeTasks.isEmpty {
                Text("Задач нет — можно просто посидеть")
                    .font(.system(size: 10))
                    .foregroundStyle(.white.opacity(0.45))
            } else {
                ForEach(taskPanel.store.activeTasks) { task in
                    taskRow(task)
                }
            }
        }
    }

    private func taskRow(_ task: CompanionTask) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 6) {
            Button {
                taskPanel.complete(task)
            } label: {
                Image(systemName: "circle")
                    .font(.system(size: 11))
                    .foregroundStyle(.white.opacity(0.65))
            }
            .buttonStyle(.plain)

            Text(task.text)
                .font(.system(size: 11))
                .foregroundStyle(.white.opacity(0.9))
                .lineLimit(1)
                .truncationMode(.tail)

            Spacer(minLength: 0)
        }
    }

    // MARK: - Ввод новой задачи

    private var inputRow: some View {
        VStack(alignment: .leading, spacing: 3) {
            TextField("Новая задача…", text: $newTaskText)
                .textFieldStyle(.plain)
                .font(.system(size: 11))
                .foregroundStyle(.white)
                .focused($isInputFocused)
                .onSubmit(commitNewTask)
                .padding(.vertical, 4)
                .padding(.horizontal, 6)
                .background(
                    RoundedRectangle(cornerRadius: 6)
                        .fill(Color.white.opacity(0.08))
                )

            if let message = taskPanel.slotsFullMessage {
                // Заботливый тон, не предупреждение об ошибке — см.
                // HANDOFF.md, раздел «Тон персонажа». Сознательно не
                // упоминает никакой кнопки отпускания задачи: механики
                // отпускания ещё нет (Фаза 4б).
                Text(message)
                    .font(.system(size: 9))
                    .foregroundStyle(.white.opacity(0.6))
            }
        }
    }

    private func commitNewTask() {
        let outcome = taskPanel.addTask(text: newTaskText)
        // Текст очищаем только при реальном добавлении: если слоты заняты,
        // пользователь не должен перепечатывать то же самое после того, как
        // освободит место.
        if outcome == .added {
            newTaskText = ""
        }
    }

    // MARK: - Выполненные задачи

    /// Только счётчик, без самого списка — иначе панель со временем
    /// превращается в свалку (см. PHASE-4A-PROMPT.md: "не должны копиться").
    /// `TaskStore` вдобавок физически не хранит больше последних нескольких.
    @ViewBuilder
    private var completedSummary: some View {
        if !taskPanel.store.completedTasks.isEmpty {
            Text("Выполнено: \(taskPanel.store.completedTasks.count)")
                .font(.system(size: 9))
                .foregroundStyle(.white.opacity(0.4))
        }
    }
}

// #Preview здесь намеренно нет: макрос Preview реализован плагином Xcode
// (PreviewsMacros), которого нет при сборке через `swift build` из
// терминала — любой #Preview в исходниках валит сборку (см. HANDOFF.md).
