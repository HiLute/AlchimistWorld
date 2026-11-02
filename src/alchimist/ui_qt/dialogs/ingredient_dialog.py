"""Форма реагента (FR-1.2, FR-1.3, FR-1.4)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from alchimist.core.elements import ElementVector
from alchimist.core.models import (
    CATEGORY_NAMES_RU,
    RARITY_NAMES_RU,
    SECONDARY_CATEGORIES,
    Ingredient,
    IngredientCategory,
    Rarity,
    coerce_enum,
)
from alchimist.i18n import _
from alchimist.services.app import AppService
from alchimist.ui_qt.widgets.common import MessageStrip, hint_label
from alchimist.ui_qt.widgets.element_counter import ElementCounters


class IngredientDialog(QDialog):
    """Добавление и правка реагента. Проверка П-3.2 показывается живьём."""

    def __init__(
        self,
        app: AppService,
        ingredient: Ingredient | None = None,
        parent: QWidget | None = None,
        *,
        suggested_name: str = "",
    ) -> None:
        super().__init__(parent)
        self.app = app
        self.original = ingredient
        # Без id — новая запись, в том числе копия существующей («Дублировать…»).
        self.setWindowTitle(_("Реагент") if ingredient and ingredient.id else _("Новый реагент"))
        self.setMinimumWidth(520)

        self.name = QLineEdit(ingredient.name if ingredient else suggested_name)
        self.rarity = QComboBox()
        for rarity in Rarity:
            self.rarity.addItem(f"{RARITY_NAMES_RU[rarity]} ({int(rarity)})", rarity)
        self.category = QComboBox()
        for category in IngredientCategory:
            self.category.addItem(CATEGORY_NAMES_RU[category], category)
        # Особая основа может быть ещё растением или «с существ» (П-3.3).
        self.secondary = QComboBox()
        self.secondary.addItem(_("нет"), None)
        for category in SECONDARY_CATEGORIES:
            self.secondary.addItem(CATEGORY_NAMES_RU[category], category)
        self.secondary.setToolTip(
            _("Основа-растение подходит набору травника и как реагент (П-6.3)")
        )
        self.elements = ElementCounters(columns=2, maximum=9)
        self.habitats = QLineEdit()
        self.habitats.setPlaceholderText(_("Лес, Луг, Болота — через запятую"))
        self.description = QPlainTextEdit()
        self.description.setPlaceholderText(_("Описание (на расчёты не влияет)"))
        self.hidden = QCheckBox(_("Скрыт: не показывать в списках и подборе"))
        self.messages = MessageStrip()

        if ingredient:
            self.rarity.setCurrentIndex(list(Rarity).index(ingredient.rarity))
            self.category.setCurrentIndex(list(IngredientCategory).index(ingredient.category))
            self.secondary.setCurrentIndex(
                max(0, self.secondary.findData(ingredient.secondary_category))
            )
            self.elements.set_vector(ingredient.elements)
            self.habitats.setText(", ".join(ingredient.habitats))
            self.description.setPlainText(ingredient.description)
            self.hidden.setChecked(ingredient.hidden)
        else:
            self.category.setCurrentIndex(list(IngredientCategory).index(IngredientCategory.PLANT))

        form = self._form = QFormLayout()
        form.addRow(_("Название"), self.name)
        form.addRow(_("Редкость"), self.rarity)
        form.addRow(_("Категория"), self.category)
        form.addRow(_("Вторая категория"), self.secondary)
        form.addRow(
            "",
            hint_label(
                _("Набору травника доступны только растения, в том числе основа-растение (П-6.3).")
            ),
        )
        form.addRow(_("Элементы"), self.elements)
        form.addRow(_("Места обитания"), self.habitats)
        form.addRow(_("Описание"), self.description)
        form.addRow("", self.hidden)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(
            hint_label(
                _(
                    "П-3.2: единиц элементов столько же, сколько уровень редкости; "
                    "у особой основы — на одну меньше (П-4.4)"
                )
            )
        )
        layout.addWidget(self.messages)
        layout.addWidget(buttons)

        self.category.currentIndexChanged.connect(self._category_changed)
        self.secondary.currentIndexChanged.connect(self._revalidate)
        self.rarity.currentIndexChanged.connect(self._revalidate)
        self.elements.changed.connect(lambda _v: self._revalidate())
        self.name.textChanged.connect(self._revalidate)
        self._category_changed()  # доступность второй категории и проверка П-3.2

    def _category_changed(self) -> None:
        """У особой основы своё правило редкости (П-4.4) — пересчитать предупреждения.

        Вторая категория бывает только у основы: у остальных этой строки не видно.
        """
        # Qt отдаёт StrEnum обратно обычной строкой — приводим, иначе `is` не сработает.
        category = coerce_enum(IngredientCategory, self.category.currentData())
        is_base = category is IngredientCategory.BASE
        self._form.setRowVisible(self.secondary, is_base)
        if not is_base:
            self.secondary.setCurrentIndex(0)
        self._revalidate()

    def _revalidate(self) -> None:
        self.messages.show_messages(self.app.catalog.validate_ingredient(self.build()))
        self._fit_height()

    def _fit_height(self) -> None:
        """Выросло содержимое (строка второй категории, предупреждение) — подрастить окно.

        Сам Qt лишь поднимает минимальную высоту, а тайловые оконные менеджеры
        (Hyprland) применяют её только при следующем изменении размера: до тех пор
        кнопки «Сохранить» и «Отмена» уходят за нижний край окна.
        """
        layout = self.layout()
        if layout is None or not self.isVisible():
            return
        layout.activate()
        needed = self.sizeHint().height()
        if self.height() < needed:
            self.resize(self.width(), needed)

    def build(self) -> Ingredient:
        habitats = [h.strip() for h in self.habitats.text().split(",") if h.strip()]
        return Ingredient(
            id=self.original.id if self.original else "",
            name=self.name.text().strip(),
            rarity=self.rarity.currentData(),
            category=self.category.currentData(),
            secondary_category=self.secondary.currentData(),
            elements=self.elements.vector() or ElementVector(),
            habitats=habitats,
            description=self.description.toPlainText().strip(),
            hidden=self.hidden.isChecked(),
        )
