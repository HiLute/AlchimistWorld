"""Форма зелья и ввод рецепта (FR-2.2, FR-2.3, FR-2.4, FR-2.5)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from alchimist.core.elements import ElementVector
from alchimist.core.models import (
    BASE_NAMES_RU,
    BASE_ORDER,
    KIND_NAMES_RU,
    RARITY_NAMES_RU,
    TAG_BLACK_MARKET,
    Ingredient,
    Potion,
    PotionKind,
    Rarity,
    Recipe,
)
from alchimist.i18n import _
from alchimist.services.app import AppService
from alchimist.ui_qt.widgets.common import MessageStrip, hint_label
from alchimist.ui_qt.widgets.element_counter import ElementCounters


class RecipeEditor(QGroupBox):
    """Чекбоксы основ или особая основа + счётчики на каждый из 7 элементов (FR-2.4)."""

    def __init__(
        self, parent: QWidget | None = None, *, specials: list[Ingredient] | None = None
    ) -> None:
        super().__init__(_("Рецепт"), parent)
        self.known = QCheckBox(_("Рецепт известен"))
        self.bases = {base: QCheckBox(BASE_NAMES_RU[base]) for base in BASE_ORDER}
        # Особая основа (П-4.4): рецепт варится только на ней, обычные основы не нужны.
        self.special = QComboBox()
        self.special.addItem(_("нет — обычные основы"), None)
        for ingredient in specials or []:
            self.special.addItem(ingredient.name, ingredient.id)
        self.special.setToolTip(
            _("Рецепт варится только на этой особой основе; её элементы в сумму не идут")
        )
        self.special.currentIndexChanged.connect(lambda _i: self._toggle(self.known.isChecked()))
        self.elements = ElementCounters(columns=2, maximum=9)
        self.note = QLineEdit()
        self.note.setPlaceholderText(_("Примечание к рецепту, если он не сводится к элементам"))

        bases_row = QHBoxLayout()
        for check in self.bases.values():
            bases_row.addWidget(check)
        bases_row.addStretch(1)

        form = QFormLayout(self)
        form.addRow("", self.known)
        form.addRow(_("Основа"), bases_row)
        form.addRow(_("Особая основа"), self.special)
        form.addRow(_("Элементы"), self.elements)
        form.addRow(_("Примечание"), self.note)

        self.known.toggled.connect(self._toggle)
        self._toggle(False)

    def _toggle(self, on: bool) -> None:
        special = self.special.currentData() is not None
        for check in self.bases.values():
            check.setEnabled(on and not special)
        self.special.setEnabled(on)
        self.elements.setEnabled(on)

    def set_recipe(self, recipe: Recipe | None, note: str | None) -> None:
        self.known.setChecked(recipe is not None)
        for base, check in self.bases.items():
            check.setChecked(bool(recipe) and base in recipe.bases)
        required = recipe.required_base_id if recipe else None
        index = self.special.findData(required) if required else 0
        self.special.setCurrentIndex(max(0, index))
        self.elements.set_vector(recipe.elements if recipe else ElementVector())
        self.note.setText(note or "")
        self._toggle(recipe is not None)

    def recipe(self) -> Recipe | None:
        if not self.known.isChecked():
            return None
        special = self.special.currentData()
        if special:
            return Recipe(
                bases=frozenset(), elements=self.elements.vector(), required_base_id=special
            )
        bases = frozenset(b for b, c in self.bases.items() if c.isChecked())
        return Recipe(bases=bases, elements=self.elements.vector())

    def recipe_note(self) -> str | None:
        return self.note.text().strip() or None


class PotionDialog(QDialog):
    """Карточка зелья: поля и рецепт. Конфликты показываются сразу (FR-2.5)."""

    def __init__(
        self,
        app: AppService,
        potion: Potion | None = None,
        parent: QWidget | None = None,
        *,
        suggested_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.original = potion
        # Без id — новая запись, в том числе копия существующей («Дублировать…»).
        self.setWindowTitle(_("Зелье") if potion and potion.id else _("Новое зелье"))
        self.setMinimumWidth(560)

        self.name = QLineEdit(potion.name if potion else suggested_name)
        self.family = QComboBox()
        self.family.setEditable(True)
        self.family.addItem("")
        for family in app.catalog.families():
            self.family.addItem(family)
        self.rarity = QComboBox()
        for rarity in Rarity:
            self.rarity.addItem(f"{RARITY_NAMES_RU[rarity]} ({int(rarity)})", rarity)
        self.kind = QComboBox()
        for kind in PotionKind:
            self.kind.addItem(KIND_NAMES_RU[kind], kind)
        self.black_market = QCheckBox(_("Чёрный рынок"))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText(_("Описание эффекта (Markdown)"))
        self.hidden = QCheckBox(_("Скрыто"))
        self.recipe_editor = RecipeEditor(specials=app.catalog.special_bases(include_hidden=True))
        self.messages = MessageStrip()

        if potion:
            self.family.setCurrentText(potion.family or "")
            self.rarity.setCurrentIndex(list(Rarity).index(potion.rarity))
            self.kind.setCurrentIndex(list(PotionKind).index(potion.kind))
            self.black_market.setChecked(potion.is_black_market)
            self.description.setPlainText(potion.description_md)
            self.hidden.setChecked(potion.hidden)
            self.recipe_editor.set_recipe(potion.recipe, potion.recipe_note)

        form = QFormLayout()
        form.addRow(_("Название"), self.name)
        form.addRow(_("Семейство"), self.family)
        form.addRow(_("Редкость"), self.rarity)
        form.addRow(_("Вид"), self.kind)
        form.addRow("", self.black_market)
        form.addRow(_("Описание"), self.description)
        form.addRow("", self.hidden)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(self.recipe_editor)
        layout.addWidget(
            hint_label(_("П-5.2: единиц в рецепте должно быть на одну больше, чем редкость"))
        )
        layout.addWidget(self.messages)
        layout.addWidget(buttons)

        self.rarity.currentIndexChanged.connect(self._revalidate)
        self.recipe_editor.elements.changed.connect(lambda _v: self._revalidate())
        self.recipe_editor.known.toggled.connect(lambda _v: self._revalidate())
        for check in self.recipe_editor.bases.values():
            check.toggled.connect(lambda _v: self._revalidate())
        self.recipe_editor.special.currentIndexChanged.connect(lambda _i: self._revalidate())
        self._revalidate()

    def _revalidate(self) -> None:
        potion = self.build()
        self.messages.show_messages(self.app.catalog.validate_recipe(potion, potion.recipe))

    def build(self) -> Potion:
        tags = set(self.original.tags) if self.original else set()
        tags.discard(TAG_BLACK_MARKET)
        if self.black_market.isChecked():
            tags.add(TAG_BLACK_MARKET)
        return Potion(
            id=self.original.id if self.original else "",
            name=self.name.text().strip(),
            rarity=self.rarity.currentData(),
            kind=self.kind.currentData(),
            family=self.family.currentText().strip() or None,
            tags=tags,
            description_md=self.description.toPlainText().strip(),
            recipe=self.recipe_editor.recipe(),
            recipe_note=self.recipe_editor.recipe_note(),
            hidden=self.hidden.isChecked(),
        )
