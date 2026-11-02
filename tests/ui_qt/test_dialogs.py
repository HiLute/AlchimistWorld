"""Диалоги: варка, карточки, обмен."""

from __future__ import annotations

import pytest

from alchimist.core.elements import ElementVector as EV
from alchimist.core.models import (
    BaseType,
    Ingredient,
    IngredientCategory,
    Outcome,
    Rarity,
    ResultKind,
)
from alchimist.ui_qt.dialogs.brew_dialog import BrewDialog
from alchimist.ui_qt.dialogs.ingredient_dialog import IngredientDialog
from alchimist.ui_qt.dialogs.merge_dialog import MergeDialog
from alchimist.ui_qt.dialogs.potion_dialog import PotionDialog


# ── диалог варки (FR-7.1, FR-7.2) ────────────────────────────────────────────
def test_brew_dialog_prefills_and_brews(ui_app, qapp) -> None:
    ui_app.inventory.set_reagent("podlunnukh", 1)
    ui_app.inventory.set_reagent("svechnaya-roza", 1)
    dialog = BrewDialog(
        ui_app,
        BaseType.LIQUID,
        {"podlunnukh": 1, "svechnaya-roza": 1},
        expected=ui_app.catalog.potion("zele-lecheniya-slaboe"),
    )
    assert dialog.elements.vector().to_dict() == {"light": 1, "dark": 1}
    assert "Зелье Лечения (Слабое)" in dialog.hint._label.text()
    assert dialog.potion.currentData() == "zele-lecheniya-slaboe"
    assert dialog.reagent_list.count() == 2

    dialog._confirm()
    assert dialog.outcome is not None
    assert ui_app.inventory.potion_qty("zele-lecheniya-slaboe") == 1
    assert ui_app.inventory.reagent_qty("podlunnukh") == 0


def test_brew_dialog_failure_path(ui_app, qapp) -> None:
    ui_app.inventory.set_reagent("shcholkorekh", 2)
    dialog = BrewDialog(ui_app, BaseType.LIQUID, {"shcholkorekh": 2})
    dialog.failure.setChecked(True)
    assert not dialog.potion.isEnabled()
    request = dialog.request()
    assert request.outcome is Outcome.FAILURE
    assert request.result_kind is ResultKind.NONE
    assert request.potion_id is None


def test_brew_dialog_warns_about_unknown_combination(ui_app, qapp) -> None:
    ui_app.inventory.set_reagent("shcholkorekh", 1)
    ui_app.inventory.set_reagent("fanana", 1)
    dialog = BrewDialog(ui_app, BaseType.LIQUID, {"shcholkorekh": 1, "fanana": 1})
    assert "Неизвестная комбинация" in dialog.hint._label.text()


def test_brew_dialog_remembers_previous_attempt(ui_app, qapp) -> None:
    ui_app.inventory.set_reagent("sopli-trollya", 4)
    first = BrewDialog(ui_app, BaseType.EXPLOSIVE, {"sopli-trollya": 2})
    first.failure.setChecked(True)  # «Ничего не вышло» — это провал
    assert first.result_kind.currentText() == "Ничего не вышло"
    first._confirm()

    second = BrewDialog(ui_app, BaseType.EXPLOSIVE, {"sopli-trollya": 2})
    assert "уже пробовали" in second.hint._label.text()
    assert "провал" in second.hint._label.text()


# ── карточка реагента (FR-1.3, FR-1.4) ───────────────────────────────────────
def test_ingredient_dialog_live_warning(ui_app, qapp) -> None:
    dialog = IngredientDialog(ui_app, None)
    dialog.name.setText("Пробный корень")
    dialog.rarity.setCurrentIndex(2)  # редкий → нужно 3 единицы
    dialog.elements.set_vector(EV.from_dict({"earth": 1}))
    assert "должно быть 3" in dialog.messages._label.text()

    dialog.elements.set_vector(EV.from_dict({"earth": 3}))
    assert dialog.messages._label.text() == ""

    built = dialog.build()
    assert built.name == "Пробный корень"
    assert built.rarity is Rarity.RARE
    assert built.elements.to_dict() == {"earth": 3}


def test_ingredient_dialog_has_no_herb_flag(ui_app, qapp) -> None:
    """Травник работает по категории «Растение» (П-6.3): отдельной галочки нет."""
    dialog = IngredientDialog(ui_app, None)
    assert not hasattr(dialog, "is_herb")
    assert dialog.build().category is IngredientCategory.PLANT  # новый реагент — растение
    categories = [dialog.category.itemData(i) for i in range(dialog.category.count())]
    assert "herb" not in categories and IngredientCategory.BASE in categories


def test_ingredient_dialog_loads_existing(ui_app, qapp) -> None:
    ingredient = ui_app.catalog.ingredient("semya-nochnogo-plameni")
    dialog = IngredientDialog(ui_app, ingredient)
    assert dialog.build() == ingredient


# ── карточка зелья и рецепт (FR-2.4, FR-2.5) ─────────────────────────────────
def test_potion_dialog_recipe_editor(ui_app, qapp) -> None:
    dialog = PotionDialog(ui_app, ui_app.catalog.potion("zele-lecheniya-slaboe"))
    editor = dialog.recipe_editor
    assert editor.known.isChecked()
    assert editor.bases[BaseType.LIQUID].isChecked()
    assert editor.bases[BaseType.VISCOUS].isChecked()
    assert not editor.bases[BaseType.EXPLOSIVE].isChecked()
    assert editor.elements.vector().to_dict() == {"light": 1, "dark": 1}

    editor.known.setChecked(False)
    assert dialog.build().recipe is None


def test_potion_dialog_shows_collision(ui_app, qapp) -> None:
    from alchimist.core.models import Potion

    potion, _messages = ui_app.add_potion(Potion(id="", name="Ещё лазанья"))
    dialog = PotionDialog(ui_app, potion)
    dialog.recipe_editor.known.setChecked(True)
    dialog.recipe_editor.bases[BaseType.LIQUID].setChecked(True)
    dialog.recipe_editor.elements.set_vector(EV.from_dict({"earth": 2}))
    assert "Зелье Лазанья" in dialog.messages._label.text()


# ── обмен справочником (FR-10.3) ─────────────────────────────────────────────
@pytest.fixture
def plan(ui_app, tmp_path, catalog):
    import copy

    from alchimist.core.models import Recipe
    from alchimist.services import build_app

    other = build_app(tmp_path / "other")
    other.catalog.replace_all(copy.deepcopy(catalog))
    other.add_ingredient(
        Ingredient(
            id="", name="Пепел феникса", rarity=Rarity.LEGENDARY, elements=EV.from_dict({"fire": 5})
        )
    )
    other.set_recipe("barmaglot", Recipe(frozenset({BaseType.LIQUID}), EV.from_dict({"magic": 3})))
    path = other.exchange.export(tmp_path / "catalog.json", "Игрок 2")
    return ui_app.exchange.plan(path)


def test_merge_dialog_lists_changes(plan, qapp) -> None:
    dialog = MergeDialog(plan)
    assert dialog.tree.topLevelItem(0).childCount() == 1  # новое
    assert dialog.tree.topLevelItem(1).childCount() == 1  # расхождение
    assert dialog.choices() == {"barmaglot": "mine"}

    dialog._set_all("theirs")
    assert dialog.choices() == {"barmaglot": "theirs"}
