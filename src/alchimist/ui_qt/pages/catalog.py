"""Страница «Справочник»: две вкладки — реагенты и зелья (FR-1.x, FR-2.x)."""

from __future__ import annotations

from collections.abc import Iterable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from alchimist.core.elements import ELEMENT_NAMES_RU, ELEMENT_ORDER, Element
from alchimist.core.errors import AlchimistError
from alchimist.core.ids import normalize_name
from alchimist.core.models import (
    CATEGORY_NAMES_RU,
    KIND_NAMES_RU,
    RARITY_NAMES_RU,
    TAG_NAMES_RU,
    IngredientCategory,
    PotionKind,
    Rarity,
    is_starter_ingredient,
)
from alchimist.i18n import _, describe
from alchimist.ui_qt.dialogs.ingredient_dialog import IngredientDialog
from alchimist.ui_qt.dialogs.potion_dialog import PotionDialog
from alchimist.ui_qt.markdown import to_html
from alchimist.ui_qt.pages.base import Page
from alchimist.ui_qt.theme import muted_color, rarity_color, warning_color
from alchimist.ui_qt.widgets.common import FilterBar, SearchBox, page_heading
from alchimist.ui_qt.widgets.flow import FlowLayout

ANY = "__any__"


def copy_name(name: str, taken: Iterable[str]) -> str:
    """Имя копии: «X (копия)», а если занято — «X (копия 2)», «X (копия 3)»…"""
    used = {normalize_name(n) for n in taken}
    candidate = _("{name} (копия)").format(name=name)
    number = 2
    while normalize_name(candidate) in used:
        candidate = _("{name} (копия {n})").format(name=name, n=number)
        number += 1
    return candidate


def unknown_hint() -> str:
    """Галочка «Показывать неизученные» одна на обе вкладки: это одна настройка персонажа."""
    return _("Показывать реагенты и зелья, которые персонаж ещё не изучил")


class IngredientsTab(QWidget):
    """Список реагентов с фильтрами и карточкой справа (FR-1.1, FR-1.2)."""

    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app

        self.search = SearchBox(_("Поиск по названию…"))
        self.search.search.connect(lambda _t: self.refresh())

        self.filters = FilterBar()
        self.filters.add_combo(
            "rarity",
            _("Редкость"),
            [(_("любая"), ANY), *[(RARITY_NAMES_RU[r], r) for r in Rarity]],
            enum_type=Rarity,
        )
        self.filters.add_combo(
            "category",
            _("Категория"),
            [(_("любая"), ANY), *[(CATEGORY_NAMES_RU[c], c) for c in IngredientCategory]],
            enum_type=IngredientCategory,
        )
        self.element_filter = self.filters.add_combo(
            "element", _("Элемент"), [(_("любой"), ANY)], enum_type=Element
        )
        self.habitat_filter = self.filters.add_combo("habitat", _("Место"), [(_("любое"), ANY)])
        self.hidden_check = self.filters.add_check("hidden", _("Показывать скрытые"))
        self.filters.add_stretch()
        self.filters.changed.connect(self.refresh)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([_("Название"), _("Редкость"), _("Категория"), _("Элементы")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(lambda _c, _p: self._show_card())
        self.tree.itemDoubleClicked.connect(lambda _i, _c: self.edit())

        self.card = QTextBrowser()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # Справочник правит только GM (FR-14.6): у игрока этих кнопок нет.
        buttons = FlowLayout()  # с переносом: иначе строка задаёт ширину окна
        self.gm_buttons: list[QPushButton] = []
        for text, handler in (
            (_("Добавить…"), self.add),
            (_("Дублировать…"), self.duplicate),
            (_("Изменить…"), self.edit),
            (_("Скрыть / показать"), self.toggle_hidden),
            (_("Удалить"), self.delete),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _c=False, h=handler: h())
            buttons.addWidget(button)
            self.gm_buttons.append(button)
        # Игрок реагенты не правит, а изучает и забывает (FR-14.9).
        self.learn_button = QPushButton(_("Изучить реагент"))
        self.learn_button.setToolTip(_("Узнали в игре, что в нём, — элементы станут видны"))
        self.learn_button.clicked.connect(self.learn)
        self.forget_button = QPushButton(_("Забыть реагент"))
        self.forget_button.clicked.connect(self.forget)
        self.unknown_check = QCheckBox(_("Показывать неизученные"))
        self.unknown_check.setToolTip(unknown_hint())
        self.unknown_check.toggled.connect(self._toggle_unknown)
        for widget in (self.learn_button, self.forget_button, self.unknown_check):
            buttons.addWidget(widget)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(self.filters)
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)

    # ── отрисовка ─────────────────────────────────────────────────────────
    def refresh(self) -> None:
        if self.element_filter.count() == 1:
            for element in ELEMENT_ORDER:
                self.element_filter.addItem(ELEMENT_NAMES_RU[element], element)
        habitats = self.app.catalog.habitats()
        if self.habitat_filter.count() - 1 != len(habitats):
            current = self.habitat_filter.currentData()
            self.habitat_filter.blockSignals(True)
            self.habitat_filter.clear()
            self.habitat_filter.addItem(_("любое"), ANY)
            for habitat in habitats:
                self.habitat_filter.addItem(habitat, habitat)
            index = self.habitat_filter.findData(current)
            self.habitat_filter.setCurrentIndex(max(0, index))
            self.habitat_filter.blockSignals(False)

        needle = self.search.text().strip().casefold()
        rarity = self.filters.value("rarity")
        category = self.filters.value("category")
        element = self.filters.value("element")
        habitat = self.filters.value("habitat")
        gm = self.app.can_edit_catalog
        active = self.app.characters.active
        player = active is not None and not active.is_gm
        for button in self.gm_buttons:
            button.setVisible(gm)
        for widget in (self.learn_button, self.forget_button, self.unknown_check):
            widget.setVisible(player)
        # Скрытое GM убрал из игры: игроку его не показываем вовсе.
        self.hidden_check.setVisible(gm)
        show_hidden = gm and self.filters.value("hidden")
        show_unknown = not player or active.show_unknown
        self.unknown_check.blockSignals(True)
        self.unknown_check.setChecked(show_unknown)
        self.unknown_check.blockSignals(False)
        warnings = self.app.catalog.all_warnings() if gm else {}

        current = self._selected_id()
        self.tree.clear()
        for ingredient in self.app.catalog.ingredients(include_hidden=show_hidden):
            known = self.app.catalog.knows_ingredient(ingredient.id)
            if not show_unknown and not known:
                continue
            if needle and needle not in ingredient.name.casefold():
                continue
            if rarity != ANY and ingredient.rarity != rarity:
                continue
            if category != ANY and ingredient.category != category:
                continue
            if element != ANY and not ingredient.elements[element]:
                continue
            if habitat != ANY and habitat not in ingredient.habitats:
                continue
            item = QTreeWidgetItem(
                [
                    ingredient.name + (" ⚠" if ingredient.id in warnings else ""),
                    RARITY_NAMES_RU[ingredient.rarity],
                    CATEGORY_NAMES_RU[ingredient.category],
                    ingredient.elements.format_ru() if known else _("не изучен"),
                ]
            )
            item.setForeground(1, rarity_color(ingredient.rarity))
            if not known:
                item.setForeground(3, muted_color())
            if ingredient.hidden:
                item.setForeground(0, muted_color())
            if ingredient.id in warnings:
                item.setToolTip(0, "\n".join(describe(m) for m in warnings[ingredient.id]))
            item.setData(0, Qt.ItemDataRole.UserRole, ingredient.id)
            self.tree.addTopLevelItem(item)
            if ingredient.id == current:
                self.tree.setCurrentItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._show_card()

    def _selected_id(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _update_actions(self) -> None:
        """«Изучить» — у неизученного; «Забыть» — у изученного сверх стартовых и не из сумки."""
        ingredient_id = self._selected_id()
        if not ingredient_id:
            self.learn_button.setEnabled(False)
            self.forget_button.setEnabled(False)
            return
        ingredient = self.app.catalog.raw_ingredient(ingredient_id)
        known = self.app.catalog.knows_ingredient(ingredient_id)
        self.learn_button.setEnabled(not known)
        can_forget = False
        if not known:
            reason = ""
        elif is_starter_ingredient(ingredient):
            reason = _("Обычные реагенты и эссенции знают все — забыть их нельзя")
        elif self.app.inventory.reagent_qty(ingredient_id) > 0:
            reason = _("Лежит в сумке — забыть его нельзя")
        else:
            reason = _("Забыть, что в нём: элементы снова станут скрыты")
            can_forget = True
        self.forget_button.setEnabled(can_forget)
        self.forget_button.setToolTip(reason)

    def _show_card(self) -> None:
        ingredient_id = self._selected_id()
        self._update_actions()
        if not ingredient_id:
            self.card.clear()
            return
        ingredient = self.app.catalog.ingredient(ingredient_id)
        known = self.app.catalog.knows_ingredient(ingredient_id)
        warnings = (
            self.app.catalog.validate_ingredient(ingredient) if self.app.can_edit_catalog else []
        )
        if known:
            elements = ingredient.elements.format_ru()
        else:
            elements = f"<i>{_('не изучен')}</i> — " + _(
                "станет изученным, как только попадёт в сумку, или кнопкой «Изучить реагент»"
            )
        parts = [
            f"<h3>{ingredient.name}</h3>",
            f"<p><i>{RARITY_NAMES_RU[ingredient.rarity]} · "
            f"{CATEGORY_NAMES_RU[ingredient.category]}" + "</i></p>",
            f"<p><b>{_('Элементы')}:</b> {elements}</p>",
        ]
        if ingredient.habitats:
            parts.append(f"<p><b>{_('Места')}:</b> {', '.join(ingredient.habitats)}</p>")
        if warnings:
            parts.append(
                f'<p style="color:{warning_color().name()}">'
                + "<br>".join(describe(m) for m in warnings)
                + "</p>"
            )
        if ingredient.description:
            parts.append(f"<p>{ingredient.description}</p>")

        if ingredient.is_special_base:
            # Особая основа (П-4.4): основой работает только там, где её требует рецепт.
            needed_by = self.app.catalog.recipes_requiring(ingredient.id)
            if needed_by:
                names = ", ".join(p.name for p in needed_by)
                parts.append(f"<p><b>{_('Основа для рецептов')}:</b> {names}</p>")
            parts.append(
                f"<p><i>{
                    _(
                        'Особая основа: основой служит только в рецептах, которые требуют '
                        'именно её, и тогда её элементы в сумму не идут. В остальных '
                        'варках — обычный реагент.'
                    )
                }</i></p>"
            )

        recipes = self.app.brewing.recipes_using(ingredient)
        if recipes:
            names = ", ".join(p.name for p in recipes[:12])
            more = _(" и ещё {n}").format(n=len(recipes) - 12) if len(recipes) > 12 else ""
            parts.append(f"<p><b>{_('Может участвовать в рецептах')}:</b> {names}{more}</p>")
        self.card.setHtml("".join(parts))

    # ── действия ──────────────────────────────────────────────────────────
    def add(self) -> None:
        dialog = IngredientDialog(self.app, None, self)
        if dialog.exec():
            self._save(dialog.build(), new=True)

    def duplicate(self) -> None:
        """Issue #1: новый реагент по образцу выбранного — быстро завести похожий."""
        ingredient_id = self._selected_id()
        if not ingredient_id or not self.app.can_edit_catalog:
            return
        source = self.app.catalog.raw_ingredient(ingredient_id)
        names = (i.name for i in self.app.catalog.catalog.ingredients)
        template = source.copy(id="", name=copy_name(source.name, names), updated_at=None)
        dialog = IngredientDialog(self.app, template, self)
        if dialog.exec():
            self._save(dialog.build(), new=True)

    def edit(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id or not self.app.can_edit_catalog:
            return
        dialog = IngredientDialog(self.app, self.app.catalog.raw_ingredient(ingredient_id), self)
        if dialog.exec():
            self._save(dialog.build(), new=False)

    def _save(self, ingredient, *, new: bool) -> None:
        try:
            _item, messages = (
                self.app.add_ingredient(ingredient)
                if new
                else self.app.update_ingredient(ingredient)
            )
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))
            return
        if messages:
            QMessageBox.information(
                self, _("Сохранено с замечаниями"), "\n".join(describe(m) for m in messages)
            )

    def toggle_hidden(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id:
            return
        ingredient = self.app.catalog.ingredient(ingredient_id)
        self.app.set_ingredient_hidden(ingredient_id, not ingredient.hidden)

    def learn(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id:
            return
        try:
            self.app.learn_ingredient(ingredient_id)
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))

    def forget(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id:
            return
        ingredient = self.app.catalog.ingredient(ingredient_id)
        answer = QMessageBox.question(
            self,
            _("Забыть реагент"),
            _(
                "Забыть, что в реагенте «{name}»? Он снова станет изученным, как только "
                "попадёт в сумку, или кнопкой «Изучить реагент»."
            ).format(name=ingredient.name),
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self.app.forget_ingredient(ingredient_id)
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))

    def _toggle_unknown(self, show: bool) -> None:
        if self.app.characters.active is not None:
            self.app.characters.set_show_unknown(show)

    def delete(self) -> None:
        ingredient_id = self._selected_id()
        if not ingredient_id:
            return
        ingredient = self.app.catalog.ingredient(ingredient_id)
        if not self.app.can_delete_ingredient(ingredient_id):
            answer = QMessageBox.question(
                self,
                _("Удалить нельзя"),
                _(
                    "«{name}» есть в инвентаре или журнале, поэтому удалить его нельзя.\n"
                    "Скрыть вместо удаления?"
                ).format(name=ingredient.name),
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.app.set_ingredient_hidden(ingredient_id, True)
            return
        answer = QMessageBox.question(
            self, _("Удалить"), _("Удалить «{name}» из справочника?").format(name=ingredient.name)
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app.delete_ingredient(ingredient_id)


class PotionsTab(QWidget):
    """Список зелий с фильтрами и карточкой (FR-2.1, FR-2.2, FR-2.5, FR-2.6)."""

    def __init__(self, app, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.app = app

        self.search = SearchBox(_("Поиск по названию…"))
        self.search.search.connect(lambda _t: self.refresh())

        self.filters = FilterBar()
        self.filters.add_combo(
            "rarity",
            _("Редкость"),
            [(_("любая"), ANY), *[(RARITY_NAMES_RU[r], r) for r in Rarity]],
            enum_type=Rarity,
        )
        self.filters.add_combo(
            "kind",
            _("Вид"),
            [(_("любой"), ANY), *[(KIND_NAMES_RU[k], k) for k in PotionKind]],
            enum_type=PotionKind,
        )
        self.filters.add_combo(
            "recipe",
            _("Рецепт"),
            [(_("любой"), ANY), (_("известен"), True), (_("неизвестен"), False)],
        )
        self.family_filter = self.filters.add_combo("family", _("Семейство"), [(_("любое"), ANY)])
        self.filters.add_check("market", _("Чёрный рынок"))
        self.hidden_check = self.filters.add_check("hidden", _("Показывать скрытые"))
        self.filters.add_stretch()
        self.filters.changed.connect(self.refresh)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([_("Название"), _("Редкость"), _("Вид"), _("Рецепт")])
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            self.tree.header().setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.tree.setColumnWidth(3, 220)
        self.tree.setAlternatingRowColors(True)
        self.tree.currentItemChanged.connect(lambda _c, _p: self._show_card())
        self.tree.itemDoubleClicked.connect(lambda _i, _c: self.edit())

        self.card = QTextBrowser()

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.tree)
        splitter.addWidget(self.card)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # Справочник правит только GM (FR-14.6): у игрока этих кнопок нет.
        buttons = FlowLayout()  # с переносом: иначе строка задаёт ширину окна
        self.gm_buttons: list[QPushButton] = []
        for text, handler in (
            (_("Добавить…"), self.add),
            (_("Дублировать…"), self.duplicate),
            (_("Изменить…"), self.edit),
            (_("Скрыть / показать"), self.toggle_hidden),
            (_("Удалить"), self.delete),
        ):
            button = QPushButton(text)
            button.clicked.connect(lambda _c=False, h=handler: h())
            buttons.addWidget(button)
            self.gm_buttons.append(button)
        # Игрок рецепты не правит, а изучает и забывает (FR-14.3, FR-14.4).
        self.learn_button = QPushButton(_("Изучить рецепт"))
        self.learn_button.setToolTip(_("Рецепт узнали в игре — теперь он работает в подборе"))
        self.learn_button.clicked.connect(self.learn)
        self.forget_button = QPushButton(_("Забыть рецепт"))
        self.forget_button.clicked.connect(self.forget)
        self.unknown_check = QCheckBox(_("Показывать неизученные"))
        self.unknown_check.setToolTip(unknown_hint())
        self.unknown_check.toggled.connect(self._toggle_unknown)
        for widget in (self.learn_button, self.forget_button, self.unknown_check):
            buttons.addWidget(widget)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.search)
        layout.addWidget(self.filters)
        layout.addWidget(splitter, 1)
        layout.addLayout(buttons)

    def refresh(self) -> None:
        families = self.app.catalog.families()
        if self.family_filter.count() - 1 != len(families):
            current = self.family_filter.currentData()
            self.family_filter.blockSignals(True)
            self.family_filter.clear()
            self.family_filter.addItem(_("любое"), ANY)
            for family in families:
                self.family_filter.addItem(family, family)
            index = self.family_filter.findData(current)
            self.family_filter.setCurrentIndex(max(0, index))
            self.family_filter.blockSignals(False)

        needle = self.search.text().strip().casefold()
        rarity = self.filters.value("rarity")
        kind = self.filters.value("kind")
        known = self.filters.value("recipe")
        family = self.filters.value("family")
        market = self.filters.value("market")
        gm = self.app.can_edit_catalog
        active = self.app.characters.active
        player = active is not None and not active.is_gm
        for button in self.gm_buttons:
            button.setVisible(gm)
        for widget in (self.learn_button, self.forget_button, self.unknown_check):
            widget.setVisible(player)
        self.hidden_check.setVisible(gm)
        show_hidden = gm and self.filters.value("hidden")
        show_unknown = not player or active.show_unknown
        self.unknown_check.blockSignals(True)
        self.unknown_check.setChecked(show_unknown)
        self.unknown_check.blockSignals(False)
        warnings = self.app.catalog.all_warnings() if gm else {}

        current = self._selected_id()
        self.tree.clear()
        for potion in self.app.catalog.potions(include_hidden=show_hidden):
            if needle and needle not in potion.name.casefold():
                continue
            if rarity != ANY and potion.rarity != rarity:
                continue
            if kind != ANY and potion.kind != kind:
                continue
            if known != ANY and potion.is_known != known:
                continue
            if not show_unknown and not potion.is_known:
                continue
            if family != ANY and potion.family != family:
                continue
            if market and not potion.is_black_market:
                continue
            recipe_text = self._recipe_text(potion)
            item = QTreeWidgetItem(
                [
                    potion.name + (" ⚠" if potion.id in warnings else ""),
                    RARITY_NAMES_RU[potion.rarity],
                    KIND_NAMES_RU[potion.kind],
                    recipe_text,
                ]
            )
            item.setForeground(1, rarity_color(potion.rarity))
            if not potion.recipe:
                item.setForeground(3, muted_color())
            if potion.hidden:
                item.setForeground(0, muted_color())
            if potion.id in warnings:
                item.setToolTip(0, "\n".join(describe(m) for m in warnings[potion.id]))
            item.setData(0, Qt.ItemDataRole.UserRole, potion.id)
            self.tree.addTopLevelItem(item)
            if potion.id == current:
                self.tree.setCurrentItem(item)
        if self.tree.currentItem() is None and self.tree.topLevelItemCount():
            self.tree.setCurrentItem(self.tree.topLevelItem(0))
        self._show_card()

    def _selected_id(self) -> str | None:
        item = self.tree.currentItem()
        return item.data(0, Qt.ItemDataRole.UserRole) if item else None

    def _recipe_text(self, potion) -> str:
        """У игрока «не изучен» — рецепт есть, но он его не знает; «неизвестен» — нет ни у кого."""
        if potion.recipe:
            bases = self.app.catalog.bases_text(potion.recipe)
            return f"{bases} · {potion.recipe.elements.format_ru()}"
        if self.app.catalog.has_recipe(potion.id):
            return _("не изучен")
        return _("неизвестен")

    def _update_actions(self) -> None:
        potion_id = self._selected_id()
        learned = bool(potion_id) and self.app.catalog.potion(potion_id).recipe is not None
        learnable = bool(potion_id) and self.app.catalog.has_recipe(potion_id)
        self.learn_button.setEnabled(learnable and not learned)
        self.forget_button.setEnabled(learned)

    def _show_card(self) -> None:
        potion_id = self._selected_id()
        self._update_actions()
        if not potion_id:
            self.card.clear()
            return
        potion = self.app.catalog.potion(potion_id)
        warnings = self.app.catalog.potion_warnings(potion) if self.app.can_edit_catalog else []
        tags = " · ".join(TAG_NAMES_RU.get(t, t) for t in sorted(potion.tags))
        parts = [
            f"<h3>{potion.name}</h3>",
            f"<p><i>{RARITY_NAMES_RU[potion.rarity]} · {KIND_NAMES_RU[potion.kind]}"
            + (f" · {potion.family}" if potion.family else "")
            + (f" · {tags}" if tags else "")
            + "</i></p>",
        ]
        if potion.recipe:
            parts.append(
                f"<p><b>{_('Рецепт')}:</b> {self.app.catalog.bases_text(potion.recipe)} · "
                f"{potion.recipe.elements.format_ru()}</p>"
            )
        elif self.app.catalog.has_recipe(potion.id):
            parts.append(
                f"<p><b>{_('Рецепт')}:</b> <i>{_('не изучен')}</i> — "
                + _("изучите его кнопкой «Изучить рецепт», когда узнаете в игре")
                + "</p>"
            )
        else:
            parts.append(f"<p><b>{_('Рецепт')}:</b> <i>{_('неизвестен')}</i></p>")
        if potion.recipe_note:
            parts.append(f"<p><i>{potion.recipe_note}</i></p>")
        if warnings:
            parts.append(
                f'<p style="color:{warning_color().name()}">'
                + "<br>".join(describe(m) for m in warnings)
                + "</p>"
            )
        parts.append(to_html(potion.description_md))
        self.card.setHtml("".join(parts))

    def add(self) -> None:
        dialog = PotionDialog(self.app, None, self)
        if dialog.exec():
            self._save(dialog.build(), new=True)

    def duplicate(self) -> None:
        """Issue #1: новое зелье по образцу выбранного — вместе с рецептом и тегами."""
        potion_id = self._selected_id()
        if not potion_id or not self.app.can_edit_catalog:
            return
        source = self.app.catalog.raw_potion(potion_id)
        names = (p.name for p in self.app.catalog.catalog.potions)
        template = source.copy(id="", name=copy_name(source.name, names), updated_at=None)
        dialog = PotionDialog(self.app, template, self)
        if dialog.exec():
            self._save(dialog.build(), new=True)

    def edit(self) -> None:
        potion_id = self._selected_id()
        if not potion_id or not self.app.can_edit_catalog:
            return
        dialog = PotionDialog(self.app, self.app.catalog.potion(potion_id), self)
        if dialog.exec():
            self._save(dialog.build(), new=False)

    def _save(self, potion, *, new: bool) -> None:
        try:
            _item, messages = self.app.add_potion(potion) if new else self.app.update_potion(potion)
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))
            return
        if messages:
            QMessageBox.warning(
                self, _("Сохранено с предупреждениями"), "\n".join(describe(m) for m in messages)
            )

    def toggle_hidden(self) -> None:
        potion_id = self._selected_id()
        if not potion_id:
            return
        potion = self.app.catalog.potion(potion_id)
        self.app.set_potion_hidden(potion_id, not potion.hidden)

    def delete(self) -> None:
        potion_id = self._selected_id()
        if not potion_id:
            return
        potion = self.app.catalog.potion(potion_id)
        if not self.app.can_delete_potion(potion_id):
            answer = QMessageBox.question(
                self,
                _("Удалить нельзя"),
                _(
                    "«{name}» есть в инвентаре или журнале, поэтому удалить его нельзя.\n"
                    "Скрыть вместо удаления?"
                ).format(name=potion.name),
            )
            if answer == QMessageBox.StandardButton.Yes:
                self.app.set_potion_hidden(potion_id, True)
            return
        answer = QMessageBox.question(
            self, _("Удалить"), _("Удалить «{name}» из справочника?").format(name=potion.name)
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app.delete_potion(potion_id)

    def learn(self) -> None:
        potion_id = self._selected_id()
        if not potion_id:
            return
        try:
            self.app.learn_recipe(potion_id)
        except AlchimistError as exc:
            QMessageBox.warning(self, _("Не получилось"), describe(exc.message))

    def forget(self) -> None:
        potion_id = self._selected_id()
        if not potion_id:
            return
        potion = self.app.catalog.potion(potion_id)
        answer = QMessageBox.question(
            self,
            _("Забыть рецепт"),
            _("Забыть рецепт «{name}»? Изучить его снова можно в любой момент.").format(
                name=potion.name
            ),
        )
        if answer == QMessageBox.StandardButton.Yes:
            self.app.forget_recipe(potion_id)

    def _toggle_unknown(self, show: bool) -> None:
        if self.app.characters.active is not None:
            self.app.characters.set_show_unknown(show)


class CatalogPage(Page):
    title = "Справочник"
    icon = "📖"

    def __init__(self, app, bridge, parent: QWidget | None = None) -> None:
        super().__init__(app, bridge, parent)
        self.ingredients = IngredientsTab(app)
        self.potions = PotionsTab(app)

        tabs = QTabWidget()
        tabs.addTab(self.ingredients, _("Реагенты"))
        tabs.addTab(self.potions, _("Зелья"))
        self.tabs = tabs

        box = self.layout_box()
        box.addWidget(page_heading(_("Справочник партии")))
        box.addWidget(tabs, 1)

        bridge.catalog_changed.connect(self.invalidate)
        bridge.inventory_changed.connect(self.invalidate)
        bridge.character_changed.connect(self.invalidate)

    def refresh(self) -> None:
        super().refresh()
        self.ingredients.refresh()
        self.potions.refresh()
