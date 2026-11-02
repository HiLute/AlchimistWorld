"""Страница «Реагенты»: инвентарь реагентов (FR-3.x)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from alchimist.core.errors import AlchimistError
from alchimist.core.models import RARITY_NAMES_RU
from alchimist.i18n import _, describe
from alchimist.ui_qt.dialogs.ingredient_dialog import IngredientDialog
from alchimist.ui_qt.pages.base import Page
from alchimist.ui_qt.theme import rarity_color, warning_color
from alchimist.ui_qt.widgets.common import SearchBox, hint_label, page_heading
from alchimist.ui_qt.widgets.element_counter import ElementTotals

#: Потолок количества одного реагента в сумке. Ограничение только интерфейсное:
#: в файле инвентаря количество ничем не ограничено.
MAX_REAGENT_QTY = 9999


class AddReagentDialog(QDialog):
    """Поиск с автодополнением (FR-3.3) и создание нового реагента (FR-3.4)."""

    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app
        self.chosen: str | None = None
        self.setWindowTitle(_("Добавить реагент"))
        self.setMinimumWidth(460)

        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Начните вводить название…"))
        self.list = QListWidget()
        self.qty = QComboBox()
        self.qty.setEditable(True)
        for value in range(1, 21):
            self.qty.addItem(str(value), value)

        self.create_button = QPushButton(_("Нет такого — создать реагент"))
        self.create_button.clicked.connect(self._create)
        # Новый реагент — запись в справочнике, её заводит только GM (FR-14.6).
        self.create_button.setVisible(app.can_edit_catalog)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept)
        buttons.rejected.connect(self.reject)

        row = QHBoxLayout()
        row.addWidget(QLabel(_("Сколько")))
        row.addWidget(self.qty)
        row.addStretch(1)
        row.addWidget(self.create_button)

        # Неизученный реагент виден без элементов, а попав в сумку — изучается (FR-14.10).
        self.unknown_hint = hint_label(
            _("«Не изучен» — что в нём, пока неизвестно. Попадёт в сумку — станет изученным.")
        )
        self.unknown_hint.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.search)
        layout.addWidget(self.list, 1)
        layout.addWidget(self.unknown_hint)
        layout.addLayout(row)
        layout.addWidget(buttons)

        self.search.textChanged.connect(self._filter)
        self.list.itemDoubleClicked.connect(lambda _i: self._accept())
        self._filter("")

    def _filter(self, text: str) -> None:
        needle = text.strip().casefold()
        self.list.clear()
        any_unknown = False
        for ingredient in self.app.catalog.ingredients():
            if needle and needle not in ingredient.name.casefold():
                continue
            known = self.app.catalog.knows_ingredient(ingredient.id)
            any_unknown = any_unknown or not known
            elements = ingredient.elements.format_ru() if known else _("не изучен")
            entry = QListWidgetItem(
                f"{ingredient.name}   ·   {RARITY_NAMES_RU[ingredient.rarity]}   ·   {elements}"
            )
            entry.setData(Qt.ItemDataRole.UserRole, ingredient.id)
            self.list.addItem(entry)
        self.unknown_hint.setVisible(any_unknown)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _create(self) -> None:
        dialog = IngredientDialog(self.app, None, self, suggested_name=self.search.text().strip())
        if dialog.exec():
            try:
                ingredient, messages = self.app.add_ingredient(dialog.build())
            except AlchimistError as exc:
                QMessageBox.warning(self, _("Не получилось"), describe(exc.message))
                return
            if messages:
                QMessageBox.information(
                    self, _("Сохранено с замечаниями"), "\n".join(describe(m) for m in messages)
                )
            self.search.setText(ingredient.name)
            self._filter(ingredient.name)

    def _accept(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        self.chosen = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def quantity(self) -> int:
        try:
            return min(MAX_REAGENT_QTY, max(1, int(self.qty.currentText())))
        except ValueError:
            return 1


class ReagentsPage(Page):
    title = "Реагенты"
    icon = "🌿"

    def __init__(self, app, bridge, parent: QWidget | None = None) -> None:
        super().__init__(app, bridge, parent)

        header = QHBoxLayout()
        header.addWidget(page_heading(_("Реагенты в сумке")))
        header.addStretch(1)
        self.search = SearchBox(_("Поиск…"))
        self.search.setMaximumWidth(260)
        self.search.search.connect(lambda _t: self.refresh())
        header.addWidget(self.search)
        add_button = QPushButton(_("Добавить…"))
        add_button.clicked.connect(self._add)
        header.addWidget(add_button)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(6)
        self.tree.setHeaderLabels(
            [_("Реагент"), _("Редкость"), _("Элементы"), _("Штук"), _("В очереди"), _("Заметка")]
        )
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 4):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(5, 180)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemDoubleClicked.connect(lambda _i, _c: self._edit_qty())

        buttons = QHBoxLayout()
        for text, delta in ((_("−1"), -1), (_("+1"), 1)):
            button = QPushButton(text)
            button.clicked.connect(lambda _c=False, d=delta: self._step(d))
            buttons.addWidget(button)
        set_button = QPushButton(_("Указать число…"))
        set_button.clicked.connect(self._edit_qty)
        buttons.addWidget(set_button)
        note_button = QPushButton(_("Заметка…"))
        note_button.clicked.connect(self._edit_note)
        buttons.addWidget(note_button)
        self.card_button = QPushButton(_("Карточка…"))
        self.card_button.setToolTip(_("Поправить реагент в справочнике (только GM)"))
        self.card_button.clicked.connect(self._edit_card)
        buttons.addWidget(self.card_button)
        buttons.addStretch(1)

        self.summary = ElementTotals(wrap=True)  # с переносом: иначе задаёт ширину окна

        box = self.layout_box()
        box.addLayout(header)
        box.addWidget(self.tree, 1)
        box.addLayout(buttons)
        box.addWidget(QLabel(_("Всего единиц элементов в сумке:")))
        box.addWidget(self.summary)
        box.addWidget(hint_label(_("Количество 0 убирает строку из списка.")))

        self.search.focus_shortcut(self)
        bridge.inventory_changed.connect(self.invalidate)
        bridge.catalog_changed.connect(self.invalidate)
        bridge.queue_changed.connect(self.invalidate)

    # ── отрисовка ─────────────────────────────────────────────────────────
    def refresh(self) -> None:
        super().refresh()
        self.card_button.setVisible(self.app.can_edit_catalog)
        needle = self.search.text().strip().casefold()
        current = self._selected_id()
        reserved = self.app.brewing.reserved_quantities()
        self.tree.clear()
        for row in self.app.inventory.reagent_rows(self.app.catalog.ingredient_map()):
            if needle and needle not in row.ingredient.name.casefold():
                continue
            taken = reserved.get(row.ingredient.id, 0)
            item = QTreeWidgetItem(
                [
                    row.ingredient.name,
                    RARITY_NAMES_RU[row.ingredient.rarity],
                    row.ingredient.elements.format_ru(),
                    str(row.qty),
                    str(taken) if taken else "",
                    row.note,
                ]
            )
            item.setForeground(1, rarity_color(row.ingredient.rarity))
            item.setData(0, Qt.ItemDataRole.UserRole, row.ingredient.id)
            for column in (3, 4):
                item.setTextAlignment(
                    column, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                )
            if taken:
                item.setForeground(4, warning_color())
                item.setToolTip(
                    4,
                    _("Отложено под очередь: {taken} из {total}").format(
                        taken=taken, total=row.qty
                    ),
                )
            self.tree.addTopLevelItem(item)
            if row.ingredient.id == current:
                self.tree.setCurrentItem(item)
        self.summary.set_vector(
            self.app.inventory.element_summary(self.app.catalog.ingredient_map())
        )

    def _selected_id(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    # ── действия ──────────────────────────────────────────────────────────
    def _add(self) -> None:
        dialog = AddReagentDialog(self.app, self)
        if dialog.exec() and dialog.chosen:
            self.app.inventory.add_reagent(dialog.chosen, dialog.quantity())

    def _step(self, delta: int) -> None:
        ingredient_id = self._selected_id()
        if ingredient_id:
            self.app.inventory.add_reagent(ingredient_id, delta)

    def _edit_qty(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id:
            return
        current = self.app.inventory.reagent_qty(ingredient_id)
        value, ok = QInputDialog.getInt(
            self,
            _("Сколько штук"),
            self.app.catalog.ingredient(ingredient_id).name,
            current,
            0,
            # Если в сумке уже больше потолка, диалог не должен молча урезать.
            max(MAX_REAGENT_QTY, current),
        )
        if ok:
            self.app.inventory.set_reagent(ingredient_id, value)

    def _edit_note(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id:
            return
        rows = {
            r.ingredient.id: r.note
            for r in self.app.inventory.reagent_rows(self.app.catalog.ingredient_map())
        }
        text, ok = QInputDialog.getText(
            self, _("Заметка"), _("Заметка к позиции"), text=rows.get(ingredient_id, "")
        )
        if ok:
            self.app.inventory.set_reagent_note(ingredient_id, text.strip())

    def _edit_card(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id or not self.app.can_edit_catalog:
            return
        ingredient = self.app.catalog.ingredient(ingredient_id)
        dialog = IngredientDialog(self.app, ingredient, self)
        if dialog.exec():
            try:
                _item, messages = self.app.update_ingredient(dialog.build())
            except AlchimistError as exc:
                QMessageBox.warning(self, _("Не получилось"), describe(exc.message))
                return
            if messages:
                QMessageBox.information(
                    self, _("Сохранено с замечаниями"), "\n".join(describe(m) for m in messages)
                )
