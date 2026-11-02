"""Особые основы в сценариях (П-4.4) и переход «трав» в «растения» (П-3.3)."""

from __future__ import annotations

import json

import pytest

from alchimist.core.elements import ElementVector as EV
from alchimist.core.errors import AlchimistError, ErrorCode, StorageError
from alchimist.core.models import (
    BaseType,
    Ingredient,
    IngredientCategory,
    Outcome,
    Potion,
    Rarity,
    Recipe,
    ResultKind,
)
from alchimist.data import BUILTIN_CATALOG
from alchimist.i18n import describe
from alchimist.services import build_app
from alchimist.services.app import AppService
from alchimist.services.brewing import BrewRequest

BLOOD = "krov-vampira"
CURE = "lekarstvo"
REAGENTS = {"svechnaya-roza": 1, "mylnaya-trava": 1, "sopli-trollya": 1}


@pytest.fixture
def vampire(app: AppService) -> AppService:
    """Справочник с кровью вампира и лекарством, которое варится только на ней."""
    app.add_ingredient(
        Ingredient(
            id="",
            name="Кровь вампира",
            rarity=Rarity.RARE,
            category=IngredientCategory.BASE,
            elements=EV.from_dict({"dark": 1, "magic": 1}),
        )
    )
    app.add_potion(
        Potion(
            id="",
            name="Лекарство",
            rarity=Rarity.UNCOMMON,
            recipe=Recipe(
                frozenset(), EV.from_dict({"light": 2, "water": 1}), required_base_id=BLOOD
            ),
        )
    )
    for ingredient_id, qty in REAGENTS.items():
        app.inventory.set_reagent(ingredient_id, qty)
    app.inventory.set_reagent(BLOOD, 1)
    return app


def brew_cure(app: AppService, **changes) -> object:
    request = BrewRequest(
        base=BLOOD,
        reagents=dict(REAGENTS),
        outcome=Outcome.SUCCESS,
        result_kind=ResultKind.KNOWN,
        potion_id=CURE,
    )
    return app.brewing.brew(request.with_(**changes))


def code_of(call) -> ErrorCode:
    with pytest.raises(AlchimistError) as excinfo:
        call()
    return excinfo.value.code


# ── варка и отмена ───────────────────────────────────────────────────────────
def test_brew_on_special_base_spends_it_and_records_it(vampire: AppService) -> None:
    # Из тех же реагентов варятся и обычные зелья; лекарство — только на крови.
    assert CURE in [r.potion.id for r in vampire.brewing.can_brew()]
    outcome = brew_cure(vampire)

    entry = outcome.entry
    assert entry.base is None and entry.base_ingredient_id == BLOOD
    assert entry.elements == EV.from_dict({"light": 2, "water": 1})  # без крови
    assert BLOOD not in {r.ingredient_id for r in entry.reagents}
    assert vampire.inventory.reagent_qty(BLOOD) == 0
    assert vampire.inventory.potion_qty(CURE) == 1
    assert entry.difficulty is not None  # рецепт известен — сложность посчитана

    vampire.brewing.undo_brew(entry.id)
    assert vampire.inventory.reagent_qty(BLOOD) == 1
    assert vampire.inventory.reagent_qty("svechnaya-roza") == 1


def test_journal_survives_restart_with_special_base(vampire: AppService, tmp_path) -> None:
    brew_cure(vampire)
    fresh = build_app(tmp_path)
    assert fresh.journal.entries()[0].base_ingredient_id == BLOOD


def test_no_base_in_the_bag(vampire: AppService) -> None:
    vampire.inventory.set_reagent(BLOOD, 0)
    assert code_of(lambda: brew_cure(vampire)) is ErrorCode.NO_SPECIAL_BASE
    # Одна штука не может быть и основой, и реагентом.
    vampire.inventory.set_reagent(BLOOD, 1)
    with_blood = {**REAGENTS, BLOOD: 1}
    assert code_of(lambda: brew_cure(vampire, reagents=with_blood)) is ErrorCode.NO_SPECIAL_BASE


def test_special_base_error_text(vampire: AppService) -> None:
    vampire.inventory.set_reagent(BLOOD, 0)
    with pytest.raises(AlchimistError) as excinfo:
        brew_cure(vampire)
    assert describe(excinfo.value.message) == "В сумке нет основы «Кровь вампира»"


def test_save_as_recipe_keeps_the_special_base(vampire: AppService) -> None:
    """GM сварил новое на крови вампира — рецепт будет требовать именно её."""
    vampire.add_potion(Potion(id="", name="Новинка", rarity=Rarity.UNCOMMON))
    outcome = brew_cure(vampire, result_kind=ResultKind.NEW, potion_id="novinka")
    potion, _messages = vampire.save_as_recipe(outcome.entry.id)
    assert potion.recipe.required_base_id == BLOOD
    assert potion.recipe.bases == frozenset()
    assert potion.recipe.elements == EV.from_dict({"light": 2, "water": 1})


# ── лаборатория ──────────────────────────────────────────────────────────────
def test_lab_hint_depends_on_the_base(vampire: AppService) -> None:
    on_blood = vampire.brewing.hint(BLOOD, dict(REAGENTS))
    assert [p.id for p in on_blood.matches] == [CURE]
    on_liquid = vampire.brewing.hint(BaseType.LIQUID, dict(REAGENTS))
    assert CURE not in [p.id for p in on_liquid.matches]
    # Строка из Qt тоже годится: «liquid» — это жидкая основа, а не особая.
    assert CURE not in [p.id for p in vampire.brewing.hint("liquid", dict(REAGENTS)).matches]


def test_tried_history_is_per_special_base(vampire: AppService) -> None:
    brew_cure(vampire, outcome=Outcome.FAILURE, result_kind=ResultKind.NONE, potion_id=None)
    vampire.inventory.set_reagent(BLOOD, 1)
    for ingredient_id, qty in REAGENTS.items():
        vampire.inventory.set_reagent(ingredient_id, qty)
    assert vampire.brewing.hint(BLOOD, dict(REAGENTS)).was_tried
    assert not vampire.brewing.hint(BaseType.LIQUID, dict(REAGENTS)).was_tried


def test_player_discovers_recipe_on_special_base(vampire: AppService) -> None:
    vampire.create_character("Айн", seed_catalog=False)  # новичок: знает только обычные
    vampire.inventory.set_reagent(BLOOD, 1)
    for ingredient_id, qty in REAGENTS.items():
        vampire.inventory.set_reagent(ingredient_id, qty)
    assert CURE not in [r.potion.id for r in vampire.brewing.can_brew()]
    found = vampire.catalog.unlearned_exact(BLOOD, EV.from_dict({"light": 2, "water": 1}))
    assert [p.id for p in found] == [CURE]


# ── очередь ──────────────────────────────────────────────────────────────────
def test_queue_reserves_the_special_base(vampire: AppService, tmp_path) -> None:
    option = next(r for r in vampire.brewing.can_brew() if r.potion.id == CURE).best
    entry = vampire.queue.add(CURE, option.base, option.combination.as_map())
    assert entry.base_ingredient_id == BLOOD
    have = vampire.inventory.reagent_quantities()
    assert vampire.queue.reserved(have)[BLOOD] == 1
    assert vampire.brewing.available_quantities().get(BLOOD, 0) == 0
    assert build_app(tmp_path).queue.queue.entries[0].base_key == BLOOD


# ── справочник ───────────────────────────────────────────────────────────────
def test_texts_and_lists(vampire: AppService) -> None:
    potion = vampire.catalog.potion(CURE)
    assert vampire.catalog.bases_text(potion.recipe) == "Кровь вампира"
    assert [i.id for i in vampire.catalog.special_bases()] == [BLOOD]
    assert [p.id for p in vampire.catalog.recipes_requiring(BLOOD)] == [CURE]


def test_collision_text_on_special_base(vampire: AppService) -> None:
    twin = Potion(
        id="",
        name="Двойник",
        rarity=Rarity.UNCOMMON,
        recipe=vampire.catalog.potion(CURE).recipe,
    )
    _potion, messages = vampire.add_potion(twin)
    assert [describe(m) for m in messages] == [
        "«Двойник»: такой рецепт на основе «Кровь вампира» уже есть у «Лекарство» (П-5.6)"
    ]  # и никакого «не выбрана ни одна основа»: основа есть, особая


def test_required_base_cannot_be_deleted(vampire: AppService) -> None:
    vampire.inventory.set_reagent(BLOOD, 0)
    assert not vampire.can_delete_ingredient(BLOOD)
    assert code_of(lambda: vampire.delete_ingredient(BLOOD)) is ErrorCode.INGREDIENT_IN_USE


def test_base_type_values_are_never_ingredient_ids(app: AppService) -> None:
    for name in ("Liquid", "viscous", "EXPLOSIVE"):
        ingredient, _messages = app.add_ingredient(
            Ingredient(id="", name=name, category=IngredientCategory.BASE)
        )
        assert ingredient.id not in {"liquid", "viscous", "explosive"}


# ── форматы и миграции ───────────────────────────────────────────────────────
def test_old_herbs_become_plants(tmp_path) -> None:
    catalog = tmp_path / "catalog"
    catalog.mkdir()
    (catalog / "ingredients.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ingredients": [
                    {"id": "a", "name": "Трава", "category": "herb", "is_herb": True},
                    {"id": "b", "name": "Корень", "category": "plant", "is_herb": True},
                    {"id": "c", "name": "Коготь", "category": "creature", "is_herb": False},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    app = build_app(tmp_path)
    categories = {i.id: i.category for i in app.catalog.ingredients()}
    assert categories == {
        "a": IngredientCategory.PLANT,
        "b": IngredientCategory.PLANT,
        "c": IngredientCategory.CREATURE,
    }


def test_old_exchange_file_is_migrated(app: AppService, tmp_path) -> None:
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "format": "alchimist-catalog",
                "schema_version": 1,
                "ingredients": [
                    {"id": "nova", "name": "Новая трава", "category": "herb", "is_herb": True}
                ],
                "potions": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    app.exchange.apply(app.exchange.plan(path))
    assert app.catalog.ingredient("nova").category is IngredientCategory.PLANT


def test_too_new_exchange_file_is_refused(app: AppService, tmp_path) -> None:
    path = tmp_path / "future.json"
    path.write_text(
        json.dumps({"format": "alchimist-catalog", "schema_version": 99, "ingredients": []}),
        encoding="utf-8",
    )
    with pytest.raises(StorageError) as excinfo:
        app.exchange.plan(path)
    assert excinfo.value.code is ErrorCode.STORAGE_UNKNOWN_SCHEMA


def test_builtin_catalog_is_current() -> None:
    data = json.loads(BUILTIN_CATALOG.read_text(encoding="utf-8"))
    assert data["schema_version"] == 3
    assert all("is_herb" not in i for i in data["ingredients"])
    assert {i["category"] for i in data["ingredients"]} <= {"plant", "essence", "creature"}


def test_old_journal_and_queue_still_load(tmp_path) -> None:
    profile = tmp_path / "profiles" / "default"
    profile.mkdir(parents=True)
    (profile / "journal.json").write_text(
        json.dumps(
            {
                "schema_version": 3,
                "entries": [
                    {
                        "id": "e1",
                        "ts": "2026-10-01T10:00:00+04:00",
                        "type": "brew",
                        "base": "liquid",
                        "reagents": [{"ingredient_id": "fanana", "qty": 1}],
                        "elements": {"air": 1},
                        "outcome": "failure",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (profile / "queue.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "id": "q1",
                        "potion_id": "x",
                        "base": "viscous",
                        "reagents": [{"ingredient_id": "fanana", "qty": 1}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = build_app(tmp_path)
    entry = app.journal.entries()[0]
    assert entry.base is BaseType.LIQUID and entry.base_ingredient_id is None
    assert app.queue.queue.entries[0].base_key is BaseType.VISCOUS


# ── особая основа по одной на порцию ─────────────────────────────────────────
def _stock_for_portions(app: AppService, portions: int, blood: int) -> None:
    for ingredient_id, qty in REAGENTS.items():
        app.inventory.set_reagent(ingredient_id, qty * portions)
    app.inventory.set_reagent(BLOOD, blood)


def _cure_options(app: AppService):
    app.brewing.invalidate()
    row = next((r for r in app.brewing.can_brew(max_portions=3) if r.potion.id == CURE), None)
    return [] if row is None else list(row.options)


def test_can_brew_offers_as_many_portions_as_bases(vampire: AppService) -> None:
    """Реагентов на три порции, а крови две — больше двух порций не предложат."""
    _stock_for_portions(vampire, 3, blood=2)
    assert max(o.portions for o in _cure_options(vampire)) == 2

    vampire.inventory.set_reagent(BLOOD, 3)
    options = _cure_options(vampire)
    assert max(o.portions for o in options) == 3
    three = next(o for o in options if o.portions == 3)
    assert three.base_need == 3 and three.repeats == 1


def test_repeats_count_the_bases(vampire: AppService) -> None:
    """Реагентов на три варки, а крови на одну — «×1», а не «×3»."""
    _stock_for_portions(vampire, 3, blood=1)
    options = _cure_options(vampire)
    single = next(o for o in options if o.portions == 1)
    assert single.repeats == 1
    vampire.brewing.invalidate()
    row = next(r for r in vampire.brewing.can_brew(max_portions=3) if r.potion.id == CURE)
    assert row.max_repeats == 1


def test_brewing_several_portions_spends_a_base_each(vampire: AppService) -> None:
    _stock_for_portions(vampire, 3, blood=3)
    outcome = brew_cure(vampire, reagents={k: v * 3 for k, v in REAGENTS.items()}, portions=3)
    assert outcome.entry.portions == 3 and outcome.entry.base_qty == 3
    assert vampire.inventory.reagent_qty(BLOOD) == 0
    assert vampire.inventory.potion_qty(CURE) == 3

    vampire.brewing.undo_brew(outcome.entry.id)
    assert vampire.inventory.reagent_qty(BLOOD) == 3  # вернулись все три


def test_failed_brew_spends_bases_for_planned_portions(vampire: AppService) -> None:
    _stock_for_portions(vampire, 2, blood=2)
    outcome = brew_cure(
        vampire,
        reagents={k: v * 2 for k, v in REAGENTS.items()},
        portions=2,
        outcome=Outcome.FAILURE,
        result_kind=ResultKind.NONE,
        potion_id=None,
    )
    assert outcome.entry.base_qty == 2
    assert vampire.inventory.reagent_qty(BLOOD) == 0


def test_not_enough_bases_for_portions(vampire: AppService) -> None:
    _stock_for_portions(vampire, 2, blood=1)
    reagents = {k: v * 2 for k, v in REAGENTS.items()}
    code = code_of(lambda: brew_cure(vampire, reagents=reagents, portions=2))
    assert code is ErrorCode.NO_SPECIAL_BASE
    assert vampire.inventory.reagent_qty(BLOOD) == 1  # ничего не списано


def test_lab_hint_caps_portions_by_bases(vampire: AppService) -> None:
    _stock_for_portions(vampire, 3, blood=2)
    reagents = {k: v * 3 for k, v in REAGENTS.items()}
    assert vampire.brewing.special_base_limit(BLOOD, reagents) == 2
    assert vampire.brewing.special_base_limit(BaseType.LIQUID, reagents) is None
    best = next(c for c in vampire.brewing.hint(BLOOD, reagents).candidates if c.potion.id == CURE)
    assert best.portions == 2
    assert best.excess == EV.from_dict({"light": 2, "water": 1})  # третья порция — лишнее


def test_queue_reserves_a_base_per_portion(vampire: AppService) -> None:
    _stock_for_portions(vampire, 2, blood=3)
    entry = vampire.queue.add(CURE, BLOOD, {k: v * 2 for k, v in REAGENTS.items()}, portions=2)
    assert entry.needs()[BLOOD] == 2
    assert vampire.brewing.available_quantities().get(BLOOD) == 1


def test_old_journal_entry_returns_one_base(vampire: AppService, tmp_path) -> None:
    """Запись из 2.5 (основа одна на варку) при отмене возвращает одну основу."""
    _stock_for_portions(vampire, 2, blood=2)
    outcome = brew_cure(vampire, reagents={k: v * 2 for k, v in REAGENTS.items()}, portions=2)
    path = vampire.paths.journal_file
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 4
    for raw in data["entries"]:
        raw.pop("base_qty", None)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    reopened = build_app(tmp_path)
    assert reopened.journal.entry(outcome.entry.id).base_qty == 1
    reopened.brewing.undo_brew(outcome.entry.id)
    assert reopened.inventory.reagent_qty(BLOOD) == 1
