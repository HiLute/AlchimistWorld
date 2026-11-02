"""Раскладка «как текст»: элементы идут в строку и переносятся, когда не влезают.

Строки фильтров раньше стояли в одну линию и задавали минимальную ширину всего
окна: на экране 1366×768 окно получалось шире экрана. С переносом строка фильтров
просто становится двумя, а окно можно сузить. Основа — пример FlowLayout из
документации Qt.
"""

from __future__ import annotations

from PySide6.QtCore import QMargins, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import QHBoxLayout, QLayout, QLayoutItem, QWidget


class FlowLayout(QLayout):
    def __init__(
        self, parent: QWidget | None = None, *, spacing: int = 8, line_spacing: int = 8
    ) -> None:
        """`spacing` — между элементами строки, `line_spacing` — между строками."""
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self._line_spacing = line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # ── обязательная часть QLayout ────────────────────────────────────────
    def addItem(self, item: QLayoutItem) -> None:
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        return True

    def heightForWidth(self, width: int) -> int:
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:
        """Всё в одну строку — так строка выглядит, когда места хватает."""
        width = height = 0
        for item in self._visible():
            hint = item.sizeHint()
            width += hint.width() + (self._spacing if width else 0)
            height = max(height, hint.height())
        margins = self.contentsMargins()
        return QSize(
            width + margins.left() + margins.right(), height + margins.top() + margins.bottom()
        )

    def minimumSize(self) -> QSize:
        """По самому широкому элементу: остальные переносятся."""
        size = QSize()
        for item in self._visible():
            size = size.expandedTo(item.minimumSize())
        margins: QMargins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    # ── раскладка ─────────────────────────────────────────────────────────
    def _visible(self) -> list[QLayoutItem]:
        return [i for i in self._items if not (i.widget() and i.widget().isHidden())]

    def _arrange(self, rect: QRect, *, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, line_height = area.x(), area.y(), 0
        for item in self._visible():
            hint = item.sizeHint()
            if x > area.x() and x + hint.width() > area.right() + 1:
                x = area.x()
                y += line_height + self._line_spacing
                line_height = 0
            line_height = max(line_height, hint.height())
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._spacing
        # Элементы строки выравниваются по центру её высоты, как в QHBoxLayout.
        if apply:
            self._center_lines(area)
        return y + line_height - rect.y() + margins.bottom()

    def _center_lines(self, area: QRect) -> None:
        lines: dict[int, list[QLayoutItem]] = {}
        for item in self._visible():
            lines.setdefault(item.geometry().y(), []).append(item)
        for top, items in lines.items():
            height = max(i.geometry().height() for i in items)
            for item in items:
                geometry = item.geometry()
                geometry.moveTop(top + (height - geometry.height()) // 2)
                item.setGeometry(geometry)


def group(*widgets: QWidget, spacing: int = 6) -> QWidget:
    """Несколько виджетов, которые переносятся только вместе: подпись и её список."""
    box = QWidget()
    layout = QHBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(spacing)
    for widget in widgets:
        layout.addWidget(widget)
    return box
