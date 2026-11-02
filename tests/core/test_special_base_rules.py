"""Особые основы (П-4.4) и травник по категории «Растение» (П-6.3)."""

from __future__ import annotations

import pytest

from alchimist.core.elements import ElementVector as EV
from alchimist.core.errors import WarningCode
from alchimist.core.matcher import StockItem, almost_ready, brewable
from alchimist.core.models import (
    BaseType,
    Ingredient,
    IngredientCategory,
    Kit,
    Potion,
    Rarity,
    Recipe,
)
from alchimist.core.rules import (
    build_recipe_index,
    check_ingredient,
    check_recipe,
    kit_allows_base,
    kit_allows_ingredient,
)

BLOOD = "krov-vampira"


def blood(rarity: Rarity = Rarity.RARE, elements: dict[str, int] | None = None) -> Ingredient:
    return Ingredient(
        id=BLOOD,
        name="Кровь вампира",
        rarity=rarity,
        category=IngredientCategory.BASE,
        elements=EV.from_dict(elements if elements is not None else {"dark": 1, "magic": 1}),
    )


def cure(elements: dict[str, int] | None = None) -> Potion:
    """Лекарство от вампиризма: варится только на крови вампира."""
    elements = elements or {"light": 2, "water": 1}
    return Potion(
        id="lekarstvo",
        name="Лекарство от вампиризма",
        rarity=Rarity(sum(elements.values()) - 1),
        recipe=Recipe(frozenset(), EV.from_dict(elements), required_base_id=BLOOD),
    )


def stock(ingredients: dict[str, Ingredient], **qty: int) -> list[StockItem]:
    catalog = {**ingredients, BLOOD: blood()}
    return [StockItem(catalog[key.replace("_", "-")], n) for key, n in qty.items()]


# ── редкость (П-3.2, П-4.4) ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    ("rarity", "units"),
    [(Rarity.UNCOMMON, 1), (Rarity.RARE, 2), (Rarity.EPIC, 3), (Rarity.LEGENDARY, 4)],
)
def test_special_base_has_one_unit_less_than_rarity(rarity: Rarity, units: int) -> None:
    base = blood(rarity, {"dark": units})
    assert base.expected_units() == units
    assert check_ingredient(base) == []


def test_special_base_rarity_mismatch_and_emptiness_warn() -> None:
    wrong = check_ingredient(blood(Rarity.RARE, {"dark": 3}))
    assert [m.code for m in wrong] == [WarningCode.RARITY_UNITS_MISMATCH]
    assert wrong[0].params["expected"] == 2
    # Обычной особой основы не бывает: без элементов — предупреждение.
    empty = check_ingredient(blood(Rarity.COMMON, {}))
    assert [m.code for m in empty] == [WarningCode.INGREDIENT_NO_ELEMENTS]


def test_ordinary_reagent_rule_is_unchanged(ingredients: dict[str, Ingredient]) -> None:
    assert check_ingredient(ingredients["semya-nochnogo-plameni"]) == []  # редкое, 3 единицы


# ── рецепт на особой основе ──────────────────────────────────────────────────
def test_recipe_on_special_base_has_no_ordinary_bases() -> None:
    recipe = Recipe(frozenset({BaseType.LIQUID}), EV.from_dict({"fire": 1}), BLOOD)
    assert recipe.bases == frozenset()  # только на ней
    assert recipe.base_keys() == [BLOOD]
    assert recipe.index_keys() == [(BLOOD, EV.from_dict({"fire": 1}))]
    assert recipe.format_bases_ru({BLOOD: "Кровь вампира"}) == "Кровь вампира"


def test_brewable_only_with_the_base_in_the_bag(ingredients: dict[str, Ingredient]) -> None:
    potions = [cure()]
    reagents = stock(ingredients, svechnaya_roza=1, mylnaya_trava=1, sopli_trollya=1)
    assert brewable(potions, reagents, [Kit.ALCHEMIST]) == []

    rows = brewable(potions, [*reagents, StockItem(blood(), 1)], [Kit.ALCHEMIST])
    option = rows[0].best
    assert option.base == BLOOD
    assert option.base_ingredient.name == "Кровь вампира"
    assert option.format_bases_ru() == "Кровь вампира"
    # Основа в котёл не идёт: её элементы в сумму не входят.
    assert BLOOD not in option.combination.as_map()
    assert option.combination.elements == EV.from_dict({"light": 2, "water": 1})


def test_one_piece_cannot_be_both_base_and_reagent() -> None:
    """Рецепт хочет Тьму и Магию — ровно то, что в самой крови. Нужно две штуки."""
    potions = [cure({"dark": 1, "magic": 1})]
    assert brewable(potions, [StockItem(blood(), 1)], [Kit.ALCHEMIST]) == []

    rows = brewable(potions, [StockItem(blood(), 2)], [Kit.ALCHEMIST])
    assert rows[0].best.combination.as_map() == {BLOOD: 1}


def test_special_base_works_as_plain_reagent_elsewhere() -> None:
    ordinary = Potion(
        "temnoe",
        "Тёмное зелье",
        Rarity.COMMON,
        recipe=Recipe(frozenset({BaseType.LIQUID}), EV.from_dict({"dark": 1, "magic": 1})),
    )
    rows = brewable([ordinary], [StockItem(blood(), 1)], [Kit.ALCHEMIST])
    assert rows[0].best.combination.as_map() == {BLOOD: 1}
    assert rows[0].best.base is BaseType.LIQUID


# ── наборы ───────────────────────────────────────────────────────────────────
def test_herbalist_takes_plants_only(ingredients: dict[str, Ingredient]) -> None:
    assert kit_allows_ingredient(Kit.HERBALIST, ingredients["shcholkorekh"])  # растение
    assert not kit_allows_ingredient(Kit.HERBALIST, ingredients["tusklaya-essentsiya-ognya"])
    assert not kit_allows_ingredient(Kit.HERBALIST, ingredients["krov-drakocherepakhi"])
    assert not kit_allows_ingredient(Kit.HERBALIST, blood())


def test_herbalist_may_use_special_base_as_base(ingredients: dict[str, Ingredient]) -> None:
    assert kit_allows_base(Kit.HERBALIST, BLOOD)
    assert not kit_allows_base(Kit.HERBALIST, BaseType.EXPLOSIVE)
    plants = stock(ingredients, svechnaya_roza=1, mylnaya_trava=1, sopli_trollya=1)
    rows = brewable([cure()], [*plants, StockItem(blood(), 1)], [Kit.HERBALIST])
    assert rows and rows[0].best.kit is Kit.HERBALIST


# ── «Почти готово» ───────────────────────────────────────────────────────────
def test_almost_ready_reports_missing_base(ingredients: dict[str, Ingredient]) -> None:
    reagents = stock(ingredients, svechnaya_roza=1, mylnaya_trava=1, sopli_trollya=1)
    catalog = [*ingredients.values(), blood()]
    rows = almost_ready([cure()], reagents, [Kit.ALCHEMIST], catalog)
    assert len(rows) == 1
    row = rows[0]
    assert row.missing_base and row.missing.is_empty
    assert row.missing_units == 1
    assert row.base_ingredient.name == "Кровь вампира"
    assert almost_ready([cure()], reagents, [Kit.ALCHEMIST], catalog, max_missing=0) == []


def test_almost_ready_with_base_present_counts_only_elements(
    ingredients: dict[str, Ingredient],
) -> None:
    reagents = [*stock(ingredients, svechnaya_roza=1, sopli_trollya=1), StockItem(blood(), 1)]
    rows = almost_ready([cure()], reagents, [Kit.ALCHEMIST], [*ingredients.values(), blood()])
    assert not rows[0].missing_base
    assert rows[0].missing == EV.from_dict({"light": 1})


# ── коллизии (П-5.6) ─────────────────────────────────────────────────────────
def test_collisions_are_per_special_base() -> None:
    first = cure()
    twin = Potion("twin", "Двойник", Rarity.UNCOMMON, recipe=first.recipe)
    liquid = Potion(
        "liquid-one",
        "На жидкой",
        Rarity.UNCOMMON,
        recipe=Recipe(frozenset({BaseType.LIQUID}), first.recipe.elements),
    )
    index = build_recipe_index([first, liquid])
    names = {"lekarstvo": first.name, "liquid-one": liquid.name}
    clash = check_recipe(twin, twin.recipe, index, names, {BLOOD: "Кровь вампира"})
    collisions = [m for m in clash if m.code is WarningCode.RECIPE_COLLISION]
    assert len(collisions) == 1
    assert collisions[0].params["others"] == ["Лекарство от вампиризма"]  # не «На жидкой»
    assert collisions[0].params["base_name"] == "Кровь вампира"


# ── вторая категория у особой основы (П-3.3) ─────────────────────────────────
def _sap(secondary=IngredientCategory.PLANT, category=IngredientCategory.BASE) -> Ingredient:
    return Ingredient(
        id="sok",
        name="Сок Стального Дерева",
        rarity=Rarity.UNCOMMON,
        category=category,
        elements=EV.from_dict({"earth": 1}),
        secondary_category=secondary,
    )


def test_secondary_category_only_for_bases() -> None:
    sap = _sap()
    assert sap.categories == (IngredientCategory.BASE, IngredientCategory.PLANT)
    assert sap.in_category(IngredientCategory.PLANT) and sap.is_special_base
    assert sap.category_names_ru() == "Основа · Растение"
    # Не основе вторая категория не положена, а основе — только растение или существа.
    assert _sap(category=IngredientCategory.PLANT).secondary_category is None
    assert _sap(secondary=IngredientCategory.ESSENCE).secondary_category is None
    assert _sap(secondary=IngredientCategory.BASE).secondary_category is None
    assert _sap(secondary="creature").secondary_category is IngredientCategory.CREATURE
    assert _sap(secondary="other").secondary_category is None
    assert _sap(secondary=None).categories == (IngredientCategory.BASE,)


def test_secondary_category_keeps_the_base_rarity_rule() -> None:
    """Правило П-3.2 — как у основы: необычной основе-растению положен один элемент."""
    assert _sap().expected_units() == 1
    assert check_ingredient(_sap()) == []
    two = _sap()
    two.elements = EV.from_dict({"earth": 2})
    assert [m.code for m in check_ingredient(two)] == [WarningCode.RARITY_UNITS_MISMATCH]


def test_herbalist_takes_a_plant_base_as_a_reagent() -> None:
    assert kit_allows_ingredient(Kit.HERBALIST, _sap())
    assert not kit_allows_ingredient(Kit.HERBALIST, _sap(secondary=IngredientCategory.CREATURE))
    assert not kit_allows_ingredient(Kit.HERBALIST, _sap(secondary=None))
