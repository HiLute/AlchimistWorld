"""Обмен справочником между игроками (FR-10.2–10.4, 03 §6.8)."""

from __future__ import annotations

import json

import pytest

from alchimist.core.elements import ElementVector as EV
from alchimist.core.errors import AlchimistError, ErrorCode
from alchimist.core.models import BaseType, Ingredient, Rarity, Recipe
from alchimist.services import build_app
from alchimist.services.app import AppService
from alchimist.services.exchange import EXCHANGE_FORMAT, MergeChoice


@pytest.fixture
def other(tmp_path, catalog) -> AppService:
    """Второй игрок со своей установкой."""
    import copy

    service = build_app(tmp_path / "player2")
    service.catalog.replace_all(copy.deepcopy(catalog))
    return service


def test_export_file_shape(app: AppService, tmp_path) -> None:
    path = app.exchange.export(tmp_path / "catalog.json", "Гримли")
    data = json.loads(path.read_text(encoding="utf-8"))

    assert data["format"] == EXCHANGE_FORMAT
    assert data["schema_version"] == 3
    assert data["exported_by"] == "Гримли"
    assert len(data["ingredients"]) == len(app.catalog.catalog.ingredients)
    assert "reagents" not in data and "entries" not in data  # FR-10.4


def test_import_adds_new_records(app: AppService, other: AppService, tmp_path) -> None:
    other.add_ingredient(
        Ingredient(
            id="",
            name="Пепел феникса",
            rarity=Rarity.LEGENDARY,
            elements=EV.from_dict({"fire": 5}),
        )
    )
    path = other.exchange.export(tmp_path / "from-player2.json", "Игрок 2")

    plan = app.exchange.plan(path)
    assert plan.exported_by == "Игрок 2"
    assert [d.name for d in plan.added] == ["Пепел феникса"]
    assert plan.conflicts == []

    app.exchange.apply(plan)
    assert app.catalog.ingredient("pepel-feniksa").rarity is Rarity.LEGENDARY


def test_identical_records_are_silent(app: AppService, other: AppService, tmp_path) -> None:
    path = other.exchange.export(tmp_path / "same.json")
    plan = app.exchange.plan(path)
    assert not plan.has_changes
    assert plan.identical == len(app.catalog.catalog.ingredients) + len(app.catalog.catalog.potions)


def test_conflict_offers_a_choice(app: AppService, other: AppService, tmp_path) -> None:
    other.set_recipe("barmaglot", Recipe(frozenset({BaseType.LIQUID}), EV.from_dict({"magic": 3})))
    path = other.exchange.export(tmp_path / "conflict.json")

    plan = app.exchange.plan(path)
    assert [d.name for d in plan.conflicts] == ["Бармаглот"]
    assert plan.conflicts[0].changed_fields() == ["recipe"]

    # По умолчанию остаётся своё.
    app.exchange.apply(plan)
    assert app.catalog.potion("barmaglot").recipe is None

    # С явным выбором — берём чужое.
    plan = app.exchange.plan(path)
    app.exchange.apply(plan, {"barmaglot": MergeChoice.TAKE_THEIRS})
    assert app.catalog.potion("barmaglot").recipe.elements.to_dict() == {"magic": 3}


def test_choice_works_when_it_comes_as_a_plain_string(
    app: AppService, other: AppService, tmp_path
) -> None:
    """Решение приходит из Qt, а тот разворачивает `StrEnum` в обычную строку.

    Сравнение `is` со строкой всегда ложно, и «взять чужое» молча превращалось
    в «оставить моё»: расхождения не принимались вообще.
    """
    other.set_recipe("barmaglot", Recipe(frozenset({BaseType.LIQUID}), EV.from_dict({"magic": 3})))
    path = other.exchange.export(tmp_path / "conflict.json")

    plan = app.exchange.plan(path)
    app.exchange.apply(plan, {"barmaglot": str(MergeChoice.TAKE_THEIRS.value)})
    assert app.catalog.potion("barmaglot").recipe.elements.to_dict() == {"magic": 3}


def test_unknown_choice_falls_back_to_the_default(
    app: AppService, other: AppService, tmp_path
) -> None:
    other.set_recipe("barmaglot", Recipe(frozenset({BaseType.LIQUID}), EV.from_dict({"magic": 3})))
    path = other.exchange.export(tmp_path / "conflict.json")
    app.exchange.apply(app.exchange.plan(path), {"barmaglot": "чепуха"})
    assert app.catalog.potion("barmaglot").recipe is None


def test_inventory_and_journal_are_not_shared(app: AppService, other: AppService, tmp_path) -> None:
    """FR-10.4."""
    other.inventory.set_reagent("fanana", 7)
    path = other.exchange.export(tmp_path / "no-inventory.json")
    app.exchange.apply(app.exchange.plan(path))
    assert app.inventory.reagent_qty("fanana") == 0


def test_bad_file_is_rejected(app: AppService, tmp_path) -> None:
    path = tmp_path / "not-a-catalog.json"
    path.write_text('{"format": "something-else"}', encoding="utf-8")
    with pytest.raises(AlchimistError) as excinfo:
        app.exchange.plan(path)
    assert excinfo.value.code is ErrorCode.EXCHANGE_BAD_FORMAT


def test_merge_result_is_checked_against_rules(
    app: AppService, other: AppService, tmp_path
) -> None:
    other.set_recipe("barmaglot", Recipe(frozenset({BaseType.LIQUID}), EV.from_dict({"earth": 2})))
    path = other.exchange.export(tmp_path / "collide.json")
    messages = app.exchange.apply(app.exchange.plan(path), {"barmaglot": MergeChoice.TAKE_THEIRS})
    codes = {m.code.value for m in messages}
    assert "recipe_collision" in codes  # столкнулся с Зельем Лазанья
