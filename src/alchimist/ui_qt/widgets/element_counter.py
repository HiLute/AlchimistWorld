"""Счётчики на каждый из семи элементов (FR-2.4, экран «Лаборатория»)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QGridLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from alchimist.core.elements import ELEMENT_NAMES_RU, ELEMENT_ORDER, Element, ElementVector
from alchimist.ui_qt.theme import bold, element_color, muted_color, on_theme_changed
from alchimist.ui_qt.widgets.element_badge import colored_dot
from alchimist.ui_qt.widgets.flow import FlowLayout, group

#: Предел по умолчанию. Поле ввода молча обрезает значение до максимума, поэтому
#: «на глаз» его ставить нельзя: счётчик без явно заданного предела не должен
#: ничего подрезать. Там, где предел осмыслен (5 единиц в реагенте по П-3.2,
#: 6 в рецепте по П-5.2), вызывающий передаёт его сам.
DEFAULT_MAXIMUM = 9_999_999


class ElementCounters(QWidget):
    """Семь счётчиков в фиксированном порядке.

    `read_only=True` — только показ (сумма в лаборатории и в окне варки): тогда
    вместо полей ввода подписи, оформленные как поле. Поле ввода без стрелок в
    Qt 6.12 под таблицей стилей сжимает текст до пикселя, и число пропадало.
    """

    changed = Signal(object)  # ElementVector

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        read_only: bool = False,
        columns: int = 2,
        maximum: int = DEFAULT_MAXIMUM,
    ) -> None:
        super().__init__(parent)
        self._read_only = read_only
        self._boxes: dict[Element, QSpinBox | QLabel] = {}
        self._values: dict[Element, int] = dict.fromkeys(ELEMENT_ORDER, 0)
        self._dots: dict[Element, QLabel] = {}

        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(4)

        for i, element in enumerate(ELEMENT_ORDER):
            row, column = divmod(i, columns)
            dot = QLabel()
            dot.setPixmap(colored_dot(element_color(element)))
            self._dots[element] = dot
            name = QLabel(ELEMENT_NAMES_RU[element])
            box: QSpinBox | QLabel
            if read_only:
                box = QLabel("0")
                box.setObjectName("valueField")
            else:
                box = QSpinBox()
                box.setRange(0, maximum)
                box.valueChanged.connect(self._emit)
            box.setAccessibleName(ELEMENT_NAMES_RU[element])
            self._boxes[element] = box

            layout.addWidget(dot, row, column * 3)
            layout.addWidget(name, row, column * 3 + 1)
            layout.addWidget(box, row, column * 3 + 2)
        layout.setColumnStretch(columns * 3, 1)
        on_theme_changed(self._retheme)

    def _retheme(self) -> None:
        """Цвет кружка зашит в картинку, поэтому её надо перерисовать."""
        for element, dot in self._dots.items():
            dot.setPixmap(colored_dot(element_color(element)))

    def _emit(self) -> None:
        if not self._read_only:
            self.changed.emit(self.vector())

    def vector(self) -> ElementVector:
        values = (
            self._values if self._read_only else {e: b.value() for e, b in self._boxes.items()}  # type: ignore[union-attr]
        )
        return ElementVector.from_dict({str(e.value): v for e, v in values.items() if v})

    def set_vector(self, vector: ElementVector) -> None:
        for element, box in self._boxes.items():
            if isinstance(box, QLabel):
                self._values[element] = vector[element]
                box.setText(str(vector[element]))
            else:
                box.blockSignals(True)
                box.setValue(vector[element])
                box.blockSignals(False)
        self._emit()

    def clear(self) -> None:
        self.set_vector(ElementVector())


class ElementTotals(QWidget):
    """Итог по семи элементам: только показ, без ввода (FR-3.5).

    Отдельный виджет, а не счётчики в режиме «только чтение»: сумма по сумке
    ничем не ограничена, а поле ввода всегда имеет верхний предел и молча
    обрезает до него. Заодно исчезает рамка фокуса на числе, которое всё равно
    нельзя править.
    """

    def __init__(
        self, parent: QWidget | None = None, *, columns: int = 7, wrap: bool = False
    ) -> None:
        """`wrap=True` — элементы переносятся на следующую строку, когда не влезают.

        В одну линию семь итогов задавали ширину всего окна (issue #2).
        """
        super().__init__(parent)
        self._dots: dict[Element, QLabel] = {}
        self._values: dict[Element, QLabel] = {}
        self._vector = ElementVector()

        flow = FlowLayout(self, spacing=18, line_spacing=4) if wrap else None
        layout = QGridLayout() if flow is not None else QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(4)

        for i, element in enumerate(ELEMENT_ORDER):
            row, column = divmod(i, columns)
            dot = QLabel()
            dot.setPixmap(colored_dot(element_color(element)))
            self._dots[element] = dot

            name = QLabel(ELEMENT_NAMES_RU[element])
            value = QLabel("0")
            value.setFont(bold(value.font()))
            value.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setAccessibleName(ELEMENT_NAMES_RU[element])
            self._values[element] = value

            if flow is not None:
                flow.addWidget(group(dot, name, value))
                continue
            layout.addWidget(dot, row, column * 3)
            layout.addWidget(name, row, column * 3 + 1)
            layout.addWidget(value, row, column * 3 + 2)
        if flow is None:
            layout.setColumnStretch(columns * 3, 1)

        on_theme_changed(self._retheme)
        self.set_vector(self._vector)

    def _retheme(self) -> None:
        for element, dot in self._dots.items():
            dot.setPixmap(colored_dot(element_color(element)))
        self.set_vector(self._vector)

    def vector(self) -> ElementVector:
        return self._vector

    def set_vector(self, vector: ElementVector) -> None:
        self._vector = vector
        for element, label in self._values.items():
            count = vector[element]
            label.setText(str(count))
            # Нули приглушены: так видно, чего в сумке нет вовсе.
            label.setStyleSheet(f"color: {muted_color().name()}" if count == 0 else "")
        self.setToolTip(vector.format_ru())

    def clear(self) -> None:
        self.set_vector(ElementVector())
