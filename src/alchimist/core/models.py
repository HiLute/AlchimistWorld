"""Сущности предметной области (03 §4.2)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import IntEnum, StrEnum

from alchimist.core.elements import ElementVector


class Rarity(IntEnum):
    """Пять уровней редкости (П-2.1)."""

    COMMON = 1
    UNCOMMON = 2
    RARE = 3
    EPIC = 4
    LEGENDARY = 5


RARITY_NAMES_RU: dict[Rarity, str] = {
    Rarity.COMMON: "Обычный",
    Rarity.UNCOMMON: "Необычный",
    Rarity.RARE: "Редкий",
    Rarity.EPIC: "Эпический",
    Rarity.LEGENDARY: "Легендарный",
}


class BaseType(StrEnum):
    """Три типа основы (П-4.1)."""

    LIQUID = "liquid"
    VISCOUS = "viscous"
    EXPLOSIVE = "explosive"


BASE_ORDER: tuple[BaseType, ...] = (BaseType.LIQUID, BaseType.VISCOUS, BaseType.EXPLOSIVE)
ALL_BASES: frozenset[BaseType] = frozenset(BASE_ORDER)

#: Основа варки: обычный тип или id особой основы (П-4.4). id особой основы никогда не
#: совпадает со значением `BaseType` — такие id не выдаются (`CatalogService`).
BaseKey = BaseType | str


def is_special_base_key(base: BaseKey | None) -> bool:
    """Особая основа — это строка-id, а не один из трёх типов."""
    return base is not None and coerce_enum(BaseType, base) is None


def as_base_key(base: object) -> BaseKey | None:
    """Qt и JSON отдают строки: тип основы — в `BaseType`, остальное — id как есть."""
    if base is None or base == "":
        return None
    return coerce_enum(BaseType, base) or str(base)


BASE_NAMES_RU: dict[BaseType, str] = {
    BaseType.LIQUID: "Жидкая",
    BaseType.VISCOUS: "Вязкая",
    BaseType.EXPLOSIVE: "Взрывная",
}

#: Предложный падеж: «на жидкой основе».
BASE_NAMES_RU_LOC: dict[BaseType, str] = {
    BaseType.LIQUID: "жидкой",
    BaseType.VISCOUS: "вязкой",
    BaseType.EXPLOSIVE: "взрывной",
}


class IngredientCategory(StrEnum):
    """Категории реагентов (П-3.3).

    Отдельной «Травы» больше нет: травы — это растения, и набор травника работает
    ровно с ними (П-6.3). Старые файлы с `herb` переводятся в `plant` миграцией.
    """

    PLANT = "plant"
    ESSENCE = "essence"
    CREATURE = "creature"
    #: Особая основа: основа в рецептах, которые требуют именно её, иначе реагент (П-4.4).
    BASE = "base"
    OTHER = "other"


#: Какой второй категорией может быть особая основа: Сок Стального Дерева — основа
#: и растение, кровь дракочерепахи — основа и «с существ» (П-3.3).
SECONDARY_CATEGORIES: tuple[IngredientCategory, ...] = (
    IngredientCategory.PLANT,
    IngredientCategory.CREATURE,
)

CATEGORY_NAMES_RU: dict[IngredientCategory, str] = {
    IngredientCategory.PLANT: "Растение",
    IngredientCategory.ESSENCE: "Эссенция",
    IngredientCategory.CREATURE: "С существ",
    IngredientCategory.BASE: "Основа",
    IngredientCategory.OTHER: "Прочее",
}


class PotionKind(StrEnum):
    """Вид продукта (П-6.1)."""

    POTION = "potion"
    POISON = "poison"
    OIL = "oil"
    BOMB = "bomb"


KIND_NAMES_RU: dict[PotionKind, str] = {
    PotionKind.POTION: "Зелье",
    PotionKind.POISON: "Яд",
    PotionKind.OIL: "Масло",
    PotionKind.BOMB: "Бомба",
}


class Theme(StrEnum):
    """Оформление интерфейса. По умолчанию тёмное."""

    DARK = "dark"
    LIGHT = "light"


THEME_NAMES_RU: dict[Theme, str] = {
    Theme.DARK: "Тёмная",
    Theme.LIGHT: "Светлая",
}


class Kit(StrEnum):
    """Наборы инструментов (П-6.3)."""

    ALCHEMIST = "alchemist"
    HERBALIST = "herbalist"
    POISONER = "poisoner"


KIT_NAMES_RU: dict[Kit, str] = {
    Kit.ALCHEMIST: "Инструменты алхимика",
    Kit.HERBALIST: "Набор травника",
    Kit.POISONER: "Инструменты отравителя",
}

#: Справочная пометка, на расчёты не влияет (П-6.2).
TAG_BLACK_MARKET = "black_market"

TAG_NAMES_RU: dict[str, str] = {TAG_BLACK_MARKET: "Чёрный рынок"}


def coerce_enum(kind, value, default=None):
    """Приводит значение к типу перечисления.

    Нужно, потому что модели собирают не только внутренние слои: JSON отдаёт строки,
    а Qt возвращает из виджетов обычные `str` и `int` вместо самих enum'ов. Проверять
    это в каждом вызывающем месте бессмысленно — проще привести один раз здесь.
    """
    if value is None:
        return default
    if isinstance(value, kind):
        return value
    try:
        return kind(value)
    except (ValueError, TypeError):
        return default


@dataclass(slots=True)
class Ingredient:
    """Реагент: набор единиц элементов плюс справочные атрибуты (П-3.x)."""

    id: str
    name: str
    rarity: Rarity = Rarity.COMMON
    category: IngredientCategory = IngredientCategory.OTHER
    elements: ElementVector = field(default_factory=ElementVector)
    habitats: list[str] = field(default_factory=list)
    description: str = ""
    hidden: bool = False
    updated_at: datetime | None = None
    #: Вторая категория — только у особой основы и только из `SECONDARY_CATEGORIES`.
    secondary_category: IngredientCategory | None = None

    def __post_init__(self) -> None:
        self.rarity = coerce_enum(Rarity, self.rarity, Rarity.COMMON)
        self.category = coerce_enum(IngredientCategory, self.category, IngredientCategory.OTHER)
        secondary = coerce_enum(IngredientCategory, self.secondary_category)
        if self.category is not IngredientCategory.BASE or secondary not in SECONDARY_CATEGORIES:
            secondary = None
        self.secondary_category = secondary

    @property
    def categories(self) -> tuple[IngredientCategory, ...]:
        """Все категории реагента: своя и, у особой основы, вторая."""
        if self.secondary_category is None:
            return (self.category,)
        return (self.category, self.secondary_category)

    def in_category(self, category: IngredientCategory) -> bool:
        return category in self.categories

    def category_names_ru(self) -> str:
        """«Растение» или «Основа · Растение»."""
        return " · ".join(CATEGORY_NAMES_RU[c] for c in self.categories)

    @property
    def is_plant(self) -> bool:
        """Подходит набору травника (П-6.3): растение, в том числе основа-растение."""
        return self.in_category(IngredientCategory.PLANT)

    @property
    def is_special_base(self) -> bool:
        """Особая основа (П-4.4)."""
        return self.category is IngredientCategory.BASE

    def expected_units(self) -> int:
        """П-3.2: единиц столько, сколько уровень редкости; у особой основы на одну меньше."""
        return int(self.rarity) - (1 if self.is_special_base else 0)

    def copy(self, **changes: object) -> Ingredient:
        return replace(self, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class Recipe:
    """Пара «допустимые основы + мультимножество элементов» (П-5.1).

    Рецепт на особой основе (П-4.4) варится только на ней: тогда `required_base_id`
    задан, а `bases` пустой. Элементы самой основы в `elements` не входят.
    """

    bases: frozenset[BaseType]
    elements: ElementVector
    #: Особая основа, которую требует рецепт (id реагента категории «Основа»).
    required_base_id: str | None = None

    def __post_init__(self) -> None:
        bases = frozenset(
            base for b in self.bases if (base := coerce_enum(BaseType, b)) is not None
        )
        if self.required_base_id:
            bases = frozenset()  # на особой основе — только на ней
        object.__setattr__(self, "bases", bases)

    @property
    def is_any_base(self) -> bool:
        return self.bases == ALL_BASES

    @property
    def needs_special_base(self) -> bool:
        return bool(self.required_base_id)

    def sorted_bases(self) -> list[BaseType]:
        return [b for b in BASE_ORDER if b in self.bases]

    def base_keys(self) -> list[BaseKey]:
        """Все основы, на которых варится рецепт: типы по порядку или одна особая."""
        if self.required_base_id:
            return [self.required_base_id]
        return list(self.sorted_bases())

    def format_bases_ru(self, names: Mapping[str, str] | None = None) -> str:
        """«Жидкая или вязкая», «Любая» или имя особой основы (из `names`, если есть)."""
        if self.required_base_id:
            return (names or {}).get(self.required_base_id, self.required_base_id)
        if self.is_any_base:
            return "Любая"
        return " или ".join(BASE_NAMES_RU[b].lower() for b in self.sorted_bases()).capitalize()

    def index_keys(self) -> list[tuple[BaseKey, ElementVector]]:
        """Ключи индекса рецептов: рецепт с N основами занимает N ключей (03 §5.5)."""
        return [(base, self.elements) for base in self.base_keys()]


@dataclass(slots=True)
class Potion:
    """Зелье, яд, масло или бомба. `recipe = None` — рецепт неизвестен (П-5.7)."""

    id: str
    name: str
    rarity: Rarity = Rarity.COMMON
    kind: PotionKind = PotionKind.POTION
    family: str | None = None
    tags: set[str] = field(default_factory=set)
    description_md: str = ""
    recipe: Recipe | None = None
    #: «3+ случайных реагента с провалом» и прочее, что не легло в рецепт (П-5.9).
    recipe_note: str | None = None
    hidden: bool = False
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        self.rarity = coerce_enum(Rarity, self.rarity, Rarity.COMMON)
        self.kind = coerce_enum(PotionKind, self.kind, PotionKind.POTION)

    @property
    def is_known(self) -> bool:
        """Участвует ли зелье в подборе (П-5.7)."""
        return self.recipe is not None

    @property
    def is_black_market(self) -> bool:
        return TAG_BLACK_MARKET in self.tags

    def copy(self, **changes: object) -> Potion:
        return replace(self, **changes)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class ReagentStack:
    """Строка инвентаря реагентов."""

    ingredient_id: str
    qty: int
    note: str = ""


@dataclass(frozen=True, slots=True)
class PotionStack:
    """Строка инвентаря зелий."""

    potion_id: str
    qty: int
    note: str = ""


@dataclass(frozen=True, slots=True)
class Inventory:
    """Что лежит в сумке у персонажа (03 §6.5)."""

    reagents: tuple[ReagentStack, ...] = ()
    potions: tuple[PotionStack, ...] = ()

    def reagent_qty(self, ingredient_id: str) -> int:
        return next((s.qty for s in self.reagents if s.ingredient_id == ingredient_id), 0)

    def potion_qty(self, potion_id: str) -> int:
        return next((s.qty for s in self.potions if s.potion_id == potion_id), 0)

    def reagent_map(self) -> dict[str, int]:
        return {s.ingredient_id: s.qty for s in self.reagents if s.qty > 0}


@dataclass(frozen=True, slots=True)
class QueueEntry:
    """Запланированная варка (FR-12.x).

    Запись фиксирует **конкретный** набор: те реагенты, ту основу, столько
    порций. Иначе резерв «плавал» бы при каждом пересчёте, и ответить на вопрос
    «почему теперь не хватает» было бы нельзя.
    """

    id: str
    potion_id: str
    base: BaseType = BaseType.LIQUID
    reagents: tuple[ReagentStack, ...] = ()
    portions: int = 1
    note: str = ""
    #: Особая основа, на которой варится запись (П-4.4); тогда `base` не важен.
    base_ingredient_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "base", coerce_enum(BaseType, self.base, BaseType.LIQUID))

    @property
    def base_key(self) -> BaseKey:
        return self.base_ingredient_id or self.base

    def reagent_map(self) -> dict[str, int]:
        """Реагенты котла — без особой основы: её элементы в сумму не идут."""
        return {s.ingredient_id: s.qty for s in self.reagents if s.qty > 0}

    def needs(self) -> dict[str, int]:
        """Всё, что запись занимает в сумке: реагенты и особая основа — по одной на порцию."""
        needed = self.reagent_map()
        if self.base_ingredient_id:
            base = self.base_ingredient_id
            needed[base] = needed.get(base, 0) + max(1, self.portions)
        return needed


@dataclass(frozen=True, slots=True)
class BrewQueue:
    """Очередь варок: что игрок собирается сварить (FR-12.1).

    Пока очередь учитывается, её реагенты считаются занятыми, и подбор по всему
    приложению идёт от остатка. Порядок важен: каждая следующая запись считается
    от того, что осталось после предыдущих.
    """

    entries: tuple[QueueEntry, ...] = ()
    #: Выключатель: очередь можно отложить, не разбирая её.
    active: bool = True

    def __bool__(self) -> bool:
        return bool(self.entries)

    @property
    def is_reserving(self) -> bool:
        return self.active and bool(self.entries)

    def entry(self, entry_id: str) -> QueueEntry | None:
        return next((e for e in self.entries if e.id == entry_id), None)

    def feasible(self, have: Mapping[str, int]) -> list[bool]:
        """Хватает ли реагентов на каждую запись, если идти по порядку.

        Запись, на которую не хватило, ничего не резервирует: сварить её всё
        равно нельзя, и занимать под неё чужие реагенты было бы неправдой.
        """
        left = dict(have)
        result: list[bool] = []
        for entry in self.entries:
            needed = entry.needs()
            enough = all(left.get(i, 0) >= qty for i, qty in needed.items())
            result.append(enough)
            if enough:
                for ingredient_id, qty in needed.items():
                    left[ingredient_id] -= qty
        return result

    def reserved(self, have: Mapping[str, int]) -> dict[str, int]:
        """Сколько каждого реагента занято очередью."""
        if not self.is_reserving:
            return {}
        taken: dict[str, int] = {}
        for entry, enough in zip(self.entries, self.feasible(have), strict=True):
            if not enough:
                continue
            for ingredient_id, qty in entry.needs().items():
                taken[ingredient_id] = taken.get(ingredient_id, 0) + qty
        return taken

    def available(self, have: Mapping[str, int]) -> dict[str, int]:
        """Что остаётся свободным после резерва очереди."""
        reserved = self.reserved(have)
        if not reserved:
            return dict(have)
        left = {}
        for ingredient_id, qty in have.items():
            rest = qty - reserved.get(ingredient_id, 0)
            if rest > 0:
                left[ingredient_id] = rest
        return left


class JournalEntryType(StrEnum):
    BREW = "brew"
    USE = "use"
    ADJUST = "adjust"
    #: Разбор реагентов на эссенции (П-9).
    DISTILL = "distill"


class Outcome(StrEnum):
    """Исход варки указывается вручную (П-7.2)."""

    SUCCESS = "success"
    FAILURE = "failure"


class ResultKind(StrEnum):
    """Что вышло из варки (03 §6.6)."""

    KNOWN = "known"
    NEW = "new"
    NOTHING = "nothing"
    JABBERWOCK = "jabberwock"
    NONE = "none"


@dataclass(frozen=True, slots=True)
class BrewResult:
    kind: ResultKind = ResultKind.NONE
    potion_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", coerce_enum(ResultKind, self.kind, ResultKind.NONE))


@dataclass(frozen=True, slots=True)
class JournalEntry:
    """Запись журнала (П-7.4, 03 §6.6)."""

    id: str
    ts: datetime
    type: JournalEntryType
    base: BaseType | None = None
    reagents: tuple[ReagentStack, ...] = ()
    elements: ElementVector = field(default_factory=ElementVector)
    outcome: Outcome | None = None
    result: BrewResult | None = None
    yield_qty: int = 1
    #: Сколько порций варилось за раз (П-8.2).
    portions: int = 1
    #: Лишние эссенции сверх рецепта (П-8.1).
    excess: ElementVector = field(default_factory=ElementVector)
    #: Сложность проверки алхимии, посчитанная на момент варки (П-8).
    difficulty: int | None = None
    #: Что появилось в сумке: эссенции после дистилляции (П-9).
    produced: tuple[ReagentStack, ...] = ()
    potion_id: str | None = None
    qty: int = 0
    note: str = ""
    undone: bool = False
    #: Особая основа варки (П-4.4): списана из сумки, в `elements` не входит.
    base_ingredient_id: str | None = None
    #: Сколько штук особой основы списано: по одной на порцию. У записей до 2.6,
    #: когда основа уходила одна на варку, здесь 1 — столько и вернёт отмена.
    base_qty: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "type", coerce_enum(JournalEntryType, self.type, JournalEntryType.BREW)
        )
        object.__setattr__(self, "base", coerce_enum(BaseType, self.base))
        object.__setattr__(self, "outcome", coerce_enum(Outcome, self.outcome))

    @property
    def base_key(self) -> BaseKey | None:
        return self.base_ingredient_id or self.base

    @property
    def combination_key(self) -> tuple[BaseKey, ElementVector] | None:
        """Ключ для подсказки «уже пробовали» (П-7.6)."""
        base = self.base_key
        if self.type is not JournalEntryType.BREW or base is None:
            return None
        return (base, self.elements)


@dataclass(slots=True)
class Catalog:
    """Справочник партии: реагенты и зелья (общие для всех игроков)."""

    ingredients: list[Ingredient] = field(default_factory=list)
    potions: list[Potion] = field(default_factory=list)

    def ingredient_by_id(self, ingredient_id: str) -> Ingredient | None:
        return next((i for i in self.ingredients if i.id == ingredient_id), None)

    def potion_by_id(self, potion_id: str) -> Potion | None:
        return next((p for p in self.potions if p.id == potion_id), None)


# ── Персонажи ─────────────────────────────────────────────────────────────────
class Role(StrEnum):
    """Кто сидит за персонажем: игрок или мастер."""

    PLAYER = "player"
    GM = "gm"


#: Имя, которое при создании персонажа добавляет встроенный GM-аккаунт.
#: Регистр не важен: «GM», «gm» и «Gm» — одно и то же.
GM_NAME = "GM"

#: Каталог профиля GM. Других персонажей с таким `id` не бывает.
GM_ID = "gm"


def is_gm_name(name: str) -> bool:
    return name.strip().casefold() == GM_NAME.casefold()


@dataclass(frozen=True, slots=True)
class Character:
    """Персонаж: свои наборы, инвентарь, журнал, изученные рецепты и реагенты.

    `id` — имя папки профиля, при переименовании не меняется. GM знает все рецепты
    и реагенты и единственный может менять справочник; у игрока работают только
    рецепты из `known_recipes`, остальные он изучает сам, кнопкой.

    Реагенты обычной редкости и эссенции игрок знает всегда (`is_starter_ingredient`),
    остальные — из `known_ingredients`: изучил кнопкой или получил в сумку (FR-14.9).
    `None` там бывает только у персонажа из версии до 2.5, пока сервис при загрузке
    не впишет ему все реагенты справочника.
    """

    id: str
    name: str
    role: Role = Role.PLAYER
    kits: tuple[Kit, ...] = (Kit.ALCHEMIST,)
    known_recipes: frozenset[str] = frozenset()
    #: Показывать ли в справочнике неизученные зелья и реагенты.
    show_unknown: bool = True
    known_ingredients: frozenset[str] | None = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "role", coerce_enum(Role, self.role, Role.PLAYER))
        kits = tuple(k for k in (coerce_enum(Kit, k) for k in self.kits) if k is not None)
        object.__setattr__(self, "kits", kits or (Kit.ALCHEMIST,))
        object.__setattr__(self, "known_recipes", frozenset(self.known_recipes))
        if self.known_ingredients is not None:
            object.__setattr__(self, "known_ingredients", frozenset(self.known_ingredients))

    @property
    def is_gm(self) -> bool:
        return self.role is Role.GM

    def knows(self, potion_id: str) -> bool:
        return self.is_gm or potion_id in self.known_recipes

    def knows_ingredient(self, ingredient: Ingredient) -> bool:
        """FR-14.9: стартовые реагенты известны всем, остальные — изученные."""
        return (
            self.is_gm
            or is_starter_ingredient(ingredient)
            or self.known_ingredients is None
            or ingredient.id in self.known_ingredients
        )

    def with_(self, **changes: object) -> Character:
        return replace(self, **changes)  # type: ignore[arg-type]


def starter_recipes(potions: Iterable[Potion]) -> frozenset[str]:
    """Что новый персонаж знает с самого начала: рецепты всех обычных зелий."""
    return frozenset(p.id for p in potions if p.rarity is Rarity.COMMON and p.recipe is not None)


def is_starter_ingredient(ingredient: Ingredient) -> bool:
    """Реагент, который знают все и забыть нельзя (FR-14.2).

    Это реагенты обычной редкости и эссенции: у эссенции элемент и сила видны
    из самого названия, а дистилляция и так их делает.
    """
    return ingredient.rarity is Rarity.COMMON or ingredient.category is IngredientCategory.ESSENCE
