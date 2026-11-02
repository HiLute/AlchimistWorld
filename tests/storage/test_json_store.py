from __future__ import annotations

import json

import pytest

from alchimist.core.elements import ElementVector as EV
from alchimist.core.errors import ErrorCode, StorageError, WarningCode
from alchimist.core.models import (
    BaseType,
    Catalog,
    Ingredient,
    IngredientCategory,
    Inventory,
    JournalEntry,
    JournalEntryType,
    Outcome,
    PotionStack,
    Rarity,
    ReagentStack,
)
from alchimist.storage.atomic import backup_path
from alchimist.storage.json_store import (
    JsonCatalogRepository,
    JsonInventoryRepository,
    JsonJournalRepository,
    restore_from_backup,
)
from alchimist.storage.migrations import load_json
from alchimist.storage.paths import Paths
from alchimist.storage.settings_store import Settings, SettingsStore


@pytest.fixture
def paths(tmp_path) -> Paths:
    return Paths.at(tmp_path).ensure()


def test_catalog_roundtrip(paths: Paths, catalog: Catalog) -> None:
    repo = JsonCatalogRepository(paths)
    repo.save(catalog)
    loaded = repo.load()
    assert {i.id for i in loaded.ingredients} == {i.id for i in catalog.ingredients}
    assert {p.id for p in loaded.potions} == {p.id for p in catalog.potions}
    original = catalog.potion_by_id("zele-lecheniya-slaboe")
    restored = loaded.potion_by_id("zele-lecheniya-slaboe")
    assert restored.recipe == original.recipe
    assert restored.family == "Зелье Лечения"
    assert loaded.potion_by_id("barmaglot").recipe is None


def test_files_are_human_readable(paths: Paths, catalog: Catalog) -> None:
    """NFR-3: UTF-8, отступ 2, кириллица без \\u-экранирования."""
    JsonCatalogRepository(paths).save(catalog)
    text = paths.ingredients_file.read_text(encoding="utf-8")
    assert "Щёлкорех" in text
    assert "\\u" not in text
    assert '\n  "schema_version": 3,' in text
    assert text.endswith("\n")


def test_atomic_write_leaves_backup(paths: Paths, catalog: Catalog) -> None:
    repo = JsonCatalogRepository(paths)
    repo.save(catalog)
    first = paths.ingredients_file.read_text(encoding="utf-8")
    repo.save_ingredients(catalog.ingredients[:1])

    assert backup_path(paths.ingredients_file).read_text(encoding="utf-8") == first
    assert not paths.ingredients_file.with_suffix(".json.tmp").exists()


def test_corrupt_file_reports_path_and_line(paths: Paths, catalog: Catalog) -> None:
    """NFR-8: ошибка называет файл и строку, приложение не падает."""
    repo_before = JsonCatalogRepository(paths)
    repo_before.save(catalog)
    repo_before.save(catalog)  # вторая запись оставляет .bak
    paths.ingredients_file.write_text('{\n  "schema_version": 1,\n  oops\n}', encoding="utf-8")
    # .bak остался от предыдущей записи — данные восстанавливаются из него.
    repo = JsonCatalogRepository(paths)
    loaded = repo.load()
    assert loaded.ingredients
    assert [m.code for m in repo.notices] == [WarningCode.STORAGE_RECOVERED_FROM_BAK]


def test_corrupt_file_without_backup_raises(paths: Paths) -> None:
    paths.ingredients_file.write_text("{ not json", encoding="utf-8")
    with pytest.raises(StorageError) as excinfo:
        JsonCatalogRepository(paths).load()
    assert excinfo.value.code is ErrorCode.STORAGE_CORRUPT
    assert excinfo.value.params["path"] == str(paths.ingredients_file)
    assert excinfo.value.params["line"] == 1


def test_restore_from_backup(paths: Paths, catalog: Catalog) -> None:
    repo = JsonCatalogRepository(paths)
    repo.save(catalog)
    repo.save_ingredients([])
    assert repo.load().ingredients == []

    restore_from_backup(paths.ingredients_file)
    assert repo.load().ingredients


def test_unknown_schema_version_is_reported(paths: Paths) -> None:
    """NFR-4: файл из будущего не читается молча."""
    paths.ingredients_file.write_text(
        json.dumps({"schema_version": 99, "ingredients": []}), encoding="utf-8"
    )
    with pytest.raises(StorageError) as excinfo:
        load_json(paths.ingredients_file, "ingredients")
    assert excinfo.value.code is ErrorCode.STORAGE_UNKNOWN_SCHEMA


def test_migration_chain_runs_and_keeps_original(tmp_path, monkeypatch) -> None:
    from alchimist.storage import migrations

    monkeypatch.setitem(migrations.CURRENT_VERSIONS, "ingredients", 2)
    monkeypatch.setitem(
        migrations.MIGRATIONS["ingredients"],
        1,
        lambda data: {**data, "ingredients": [{**i, "hidden": False} for i in data["ingredients"]]},
    )
    path = tmp_path / "ingredients.json"
    path.write_text(
        json.dumps({"schema_version": 1, "ingredients": [{"id": "a", "name": "А"}]}),
        encoding="utf-8",
    )

    data = load_json(path, "ingredients")
    assert data["schema_version"] == 2
    assert data["ingredients"][0]["hidden"] is False
    assert (tmp_path / "ingredients.json.v1.bak").exists()


def test_inventory_roundtrip(paths: Paths) -> None:
    repo = JsonInventoryRepository(paths)
    inventory = Inventory(
        reagents=(ReagentStack("mogilnaya-loza", 3),),
        potions=(PotionStack("zele-lecheniya-slaboe", 2, "у Гримли в сумке"),),
    )
    repo.save(inventory)
    loaded = repo.load()
    assert loaded == inventory
    assert loaded.potions[0].note == "у Гримли в сумке"


def test_journal_roundtrip(paths: Paths) -> None:
    from datetime import datetime

    from alchimist.core.models import BrewResult, ResultKind

    repo = JsonJournalRepository(paths)
    entry = JournalEntry(
        id="0f5c",
        ts=datetime.fromisoformat("2026-09-11T21:40:00+04:00"),
        type=JournalEntryType.BREW,
        base=BaseType.LIQUID,
        reagents=(ReagentStack("podlunnukh", 1), ReagentStack("svechnaya-roza", 1)),
        elements=EV.from_dict({"dark": 1, "light": 1}),
        outcome=Outcome.SUCCESS,
        result=BrewResult(ResultKind.KNOWN, "zele-lecheniya-slaboe"),
        yield_qty=1,
    )
    repo.save([entry])
    loaded = repo.load()
    assert loaded == [entry]

    raw = json.loads(paths.journal_file.read_text(encoding="utf-8"))
    assert raw["entries"][0]["result"] == {
        "kind": "known",
        "potion_id": "zele-lecheniya-slaboe",
    }
    assert raw["entries"][0]["elements"] == {"light": 1, "dark": 1}


def test_missing_files_give_empty_data(paths: Paths) -> None:
    assert JsonCatalogRepository(paths).load().ingredients == []
    assert JsonInventoryRepository(paths).load() == Inventory()
    assert JsonJournalRepository(paths).load() == []


def test_settings_roundtrip(paths: Paths) -> None:
    from alchimist.core.models import Theme

    store = SettingsStore(paths)
    assert store.load() == Settings()

    store.save(Settings(language="ru", theme=Theme.LIGHT, active_profile="grimli"))
    loaded = store.load()
    assert loaded.theme is Theme.LIGHT
    assert loaded.active_profile == "grimli"


def test_legacy_kits_and_name_are_read_but_not_written(paths: Paths) -> None:
    """Наборы и имя переехали к персонажу (FR-14.5): старые читаются для переноса."""
    from alchimist.core.models import Kit

    paths.settings_file.write_text(
        'kits = ["herbalist", "poisoner"]\ncharacter_name = "Гримли"\n', encoding="utf-8"
    )
    store = SettingsStore(paths)
    loaded = store.load()
    assert loaded.legacy_kits == (Kit.HERBALIST, Kit.POISONER)
    assert loaded.legacy_character_name == "Гримли"

    store.save(loaded)
    text = paths.settings_file.read_text(encoding="utf-8")
    assert "kits" not in text and "character_name" not in text


def test_broken_settings_report_path(paths: Paths) -> None:
    paths.settings_file.write_text("kits = [", encoding="utf-8")
    with pytest.raises(StorageError) as excinfo:
        SettingsStore(paths).load()
    assert excinfo.value.params["path"] == str(paths.settings_file)


def test_data_dir_env_override(tmp_path, monkeypatch) -> None:
    """03 §6.1: ALCHIMIST_DATA_DIR переопределяет путь."""
    from alchimist.storage import paths as paths_module

    monkeypatch.setenv(paths_module.DATA_DIR_ENV, str(tmp_path / "portable"))
    assert paths_module.data_dir() == tmp_path / "portable"
    assert paths_module.config_dir() == tmp_path / "portable"


def test_unknown_element_in_file_is_an_error(paths: Paths) -> None:
    paths.ingredients_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "ingredients": [{"id": "x", "name": "X", "elements": {"plasma": 1}}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(StorageError):
        JsonCatalogRepository(paths).load()


def test_ingredient_fields_survive(paths: Paths) -> None:
    repo = JsonCatalogRepository(paths)
    ingredient = Ingredient(
        id="x",
        name="Проверка",
        rarity=Rarity.EPIC,
        category=IngredientCategory.CREATURE,
        elements=EV.from_dict({"magic": 4}),
        habitats=["Океан", "Арктика"],
        description="Текст",
        hidden=True,
    )
    repo.save(Catalog([ingredient], []))
    loaded = repo.load().ingredient_by_id("x")
    assert loaded == ingredient


def test_theme_defaults_to_dark_and_is_remembered(paths: Paths) -> None:
    """FR-9.5: при первом запуске тема тёмная, дальше — та, что выбрали."""
    from alchimist.core.models import Theme

    store = SettingsStore(paths)
    assert store.load().theme is Theme.DARK  # файла ещё нет

    store.save(store.load().with_(theme=Theme.LIGHT))
    assert 'theme = "light"' in paths.settings_file.read_text(encoding="utf-8")
    assert SettingsStore(paths).load().theme is Theme.LIGHT

    store.save(store.load().with_(theme=Theme.DARK))
    assert SettingsStore(paths).load().theme is Theme.DARK


def test_unknown_theme_falls_back_to_dark(paths: Paths) -> None:
    from alchimist.core.models import Theme

    paths.settings_file.write_text('theme = "неоновая"\n', encoding="utf-8")
    assert SettingsStore(paths).load().theme is Theme.DARK


def test_settings_file_without_theme_still_loads(paths: Paths) -> None:
    """Файл, записанный прошлой версией, темы не содержит."""
    from alchimist.core.models import Theme

    paths.settings_file.write_text(
        'schema_version = 1\nlanguage = "ru"\nkits = ["alchemist"]\n', encoding="utf-8"
    )
    settings = SettingsStore(paths).load()
    assert settings.theme is Theme.DARK
    assert settings.language == "ru"


def test_journal_migrates_from_v1(paths: Paths) -> None:
    """NFR-4: журнал, записанный до правила П-8, читается и обновляется."""
    from alchimist.core.models import JournalEntryType
    from alchimist.storage.migrations import CURRENT_VERSIONS

    paths.journal_file.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "entries": [
                    {
                        "id": "old",
                        "ts": "2026-09-11T21:40:00+04:00",
                        "type": "brew",
                        "base": "liquid",
                        "reagents": [{"ingredient_id": "shcholkorekh", "qty": 2}],
                        "elements": {"fire": 2},
                        "outcome": "success",
                        "result": {"kind": "known", "potion_id": "alkhimicheskiy-ogon"},
                        "yield": 1,
                        "note": "",
                        "undone": False,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    repo = JsonJournalRepository(paths)
    entries = repo.load()
    assert len(entries) == 1
    entry = entries[0]
    assert entry.type is JournalEntryType.BREW
    # Тогда варили на одну порцию и ровно по рецепту, а сложность не считали.
    assert entry.portions == 1
    assert entry.excess.is_empty
    assert entry.difficulty is None
    assert (paths.journal_file.with_suffix(".json.v1.bak")).exists()

    # После перезаписи в файле стоит честная версия, и миграция больше не нужна.
    repo.save(entries)
    raw = json.loads(paths.journal_file.read_text(encoding="utf-8"))
    assert raw["schema_version"] == CURRENT_VERSIONS["journal"]
    assert raw["entries"][0]["portions"] == 1


def test_files_declare_their_real_schema_version(paths: Paths, catalog: Catalog) -> None:
    """Версия в файле должна отражать схему, а не быть прибитой единицей."""
    from alchimist.storage.migrations import CURRENT_VERSIONS

    JsonCatalogRepository(paths).save(catalog)
    JsonInventoryRepository(paths).save(Inventory())
    JsonJournalRepository(paths).save([])

    for path, kind in (
        (paths.ingredients_file, "ingredients"),
        (paths.potions_file, "potions"),
        (paths.inventory_file, "inventory"),
        (paths.journal_file, "journal"),
    ):
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["schema_version"] == CURRENT_VERSIONS[kind], kind


def test_character_keeps_learned_reagents() -> None:
    """FR-14.9: изученные реагенты пишутся; без ключа (до 2.5) читаются как None."""
    from alchimist.core.models import Character
    from alchimist.storage.migrations import CURRENT_VERSIONS, migrate
    from alchimist.storage.serde import character_from_dict, character_to_dict

    ayn = Character("ayn", "Айн", known_ingredients={"koren-kavy"})
    raw = character_to_dict(ayn)
    assert raw["schema_version"] == CURRENT_VERSIONS["character"] == 2
    assert raw["known_ingredients"] == ["koren-kavy"]
    assert character_from_dict(raw, "ayn").known_ingredients == frozenset({"koren-kavy"})

    old = {"schema_version": 1, "name": "Айн", "kits": ["alchemist"], "known_recipes": []}
    assert character_from_dict(migrate("character", old), "ayn").known_ingredients is None


def test_secondary_category_round_trip() -> None:
    """П-3.3: вторая категория пишется только у основы, у остальных ключа нет."""
    from alchimist.core.models import Ingredient, IngredientCategory
    from alchimist.storage.serde import ingredient_from_dict, ingredient_to_dict

    sap = Ingredient(
        id="sok",
        name="Сок",
        category=IngredientCategory.BASE,
        secondary_category=IngredientCategory.PLANT,
    )
    raw = ingredient_to_dict(sap)
    assert raw["secondary_category"] == "plant"
    assert ingredient_from_dict(raw).secondary_category is IngredientCategory.PLANT
    assert "secondary_category" not in ingredient_to_dict(Ingredient(id="x", name="Х"))
