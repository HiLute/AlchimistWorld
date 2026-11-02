"""Подбор комбинаций, повторы и «почти готово» (03 §5)."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace

from alchimist.core.elements import ElementVector
from alchimist.core.models import (
    ALL_BASES,
    BASE_NAMES_RU,
    BASE_ORDER,
    BaseKey,
    BaseType,
    Ingredient,
    Kit,
    Potion,
    Rarity,
)
from alchimist.core.rules import (
    Difficulty,
    brew_difficulty,
    kit_allows_base,
    kit_allows_ingredient,
    kit_allows_potion,
)

#: Вес редкости в стоимости варианта (03 §5.2): редкое тратится неохотно.
RARITY_WEIGHT: dict[Rarity, int] = {
    Rarity.COMMON: 1,
    Rarity.UNCOMMON: 3,
    Rarity.RARE: 9,
    Rarity.EPIC: 27,
    Rarity.LEGENDARY: 81,
}


@dataclass(frozen=True, slots=True)
class StockItem:
    """Строка инвентаря глазами подбора: реагент и сколько его есть."""

    ingredient: Ingredient
    qty: int

    @property
    def elements(self) -> ElementVector:
        return self.ingredient.elements


@dataclass(frozen=True, slots=True)
class ClassPick:
    """Сколько единиц взято из класса взаимозаменяемых реагентов (03 §5.1 п.2)."""

    elements: ElementVector
    count: int
    #: Конкретные реагенты класса, отсортированные по «бери сначала эти».
    members: tuple[StockItem, ...]
    #: Общее количество Q = Σ qty. Считается один раз при сборке класса.
    stock: int = 0

    def taking(self, count: int) -> ClassPick:
        return ClassPick(self.elements, count, self.members, self.stock)


@dataclass(frozen=True, slots=True)
class Pick:
    """Конкретный реагент в варианте: «Щёлкорех ×2»."""

    ingredient: Ingredient
    count: int


@dataclass(frozen=True, slots=True)
class Combination:
    """Вариант набора реагентов под целевой вектор.

    С П-8.1 сумма больше не обязана совпадать с рецептом ровно: лишнее
    допускается и попадает в `excess`, а `portions` говорит, на сколько порций
    набранного хватило (П-8.2).
    """

    picks: tuple[Pick, ...]
    classes: tuple[ClassPick, ...] = ()
    kit: Kit | None = None
    portions: int = 1
    excess: ElementVector = field(default_factory=ElementVector)

    @property
    def excess_units(self) -> int:
        return self.excess.total

    @property
    def is_exact(self) -> bool:
        """Точное совпадение — как до появления правила о лишних эссенциях."""
        return self.excess.is_empty

    @property
    def elements(self) -> ElementVector:
        total = ElementVector()
        for pick in self.picks:
            total = total + pick.ingredient.elements * pick.count
        return total

    @property
    def reagent_count(self) -> int:
        """Сколько штук реагентов тратится."""
        return sum(p.count for p in self.picks)

    @property
    def cost(self) -> int:
        """Стоимость варианта (03 §5.2)."""
        return sum(RARITY_WEIGHT[p.ingredient.rarity] * p.count for p in self.picks)

    @property
    def repeats(self) -> int:
        """Сколько раз вариант можно повторить при текущих запасах (03 §5.3)."""
        counts = [cp.stock // cp.count for cp in self.classes if cp.count]
        return min(counts) if counts else 0

    def leftover(self) -> int:
        """Сколько штук реагентов останется после одной варки — для сортировки."""
        return sum(cp.stock - cp.count for cp in self.classes)

    def as_map(self) -> dict[str, int]:
        return {p.ingredient.id: p.count for p in self.picks}

    def sort_key(self) -> tuple[int, int, int, int, str]:
        """Меньше лишнего → дешевле → меньше реагентов → больше останется."""
        return (
            self.excess_units,
            self.cost,
            self.reagent_count,
            -self.leftover(),
            "|".join(sorted(p.ingredient.name for p in self.picks)),
        )


@dataclass(frozen=True, slots=True)
class BrewOption:
    """Зелье, которое можно сварить, и чем именно (FR-5.1, FR-5.2).

    Основы хранятся набором: если один и тот же набор реагентов годится для
    нескольких основ, это один вариант, а не три почти одинаковых строки.
    Какую именно основу брать, игрок выбирает в диалоге варки.
    """

    potion: Potion
    bases: frozenset[BaseType]
    combination: Combination
    kit: Kit
    #: Особая основа, которую требует рецепт (П-4.4); тогда `bases` пустой.
    base_ingredient: Ingredient | None = None
    #: Сколько этой основы в сумке всего: она тратится по одной на порцию.
    base_stock: int = 0

    @property
    def base(self) -> BaseKey:
        """Основа по умолчанию — особая, если её требует рецепт, иначе первая по порядку."""
        if self.base_ingredient is not None:
            return self.base_ingredient.id
        return self.sorted_bases()[0]

    def sorted_bases(self) -> list[BaseType]:
        return [b for b in BASE_ORDER if b in self.bases]

    def format_bases_ru(self) -> str:
        if self.base_ingredient is not None:
            return self.base_ingredient.name
        if self.bases == ALL_BASES:
            return "Любая"
        return " или ".join(BASE_NAMES_RU[b].lower() for b in self.sorted_bases()).capitalize()

    @property
    def repeats(self) -> int:
        """Сколько раз хватит запасов — с особой основой ещё и сколько хватит основ."""
        repeats = self.combination.repeats
        if self.base_ingredient is not None:
            per_brew = self.base_need
            repeats = min(repeats, self.base_stock // per_brew if per_brew else repeats)
        return repeats

    @property
    def base_need(self) -> int:
        """Сколько штук особой основы уходит на одну такую варку: основой и реагентом."""
        if self.base_ingredient is None:
            return 0
        as_reagent = self.combination.as_map().get(self.base_ingredient.id, 0)
        return self.combination.portions + as_reagent

    @property
    def portions(self) -> int:
        return self.combination.portions

    @property
    def difficulty(self) -> Difficulty:
        """Сложность проверки для этой варки (П-8)."""
        return brew_difficulty(
            self.potion.rarity, self.combination.portions, self.combination.excess_units
        )


@dataclass(frozen=True, slots=True)
class BrewablePotion:
    """Строка экрана «Могу сварить»: зелье и его варианты, лучший первым."""

    potion: Potion
    options: tuple[BrewOption, ...]

    @property
    def best(self) -> BrewOption:
        return self.options[0]

    @property
    def easiest(self) -> BrewOption:
        """Вариант с самой низкой сложностью проверки."""
        return min(self.options, key=lambda o: (o.difficulty.total, o.combination.sort_key()))

    @property
    def max_repeats(self) -> int:
        """Жадная оценка общего числа варок (03 §5.3)."""
        return greedy_max_repeats(self.options)


@dataclass(frozen=True, slots=True)
class Filler:
    """Чем закрыть недостачу (FR-6.2): не больше двух реагентов из справочника."""

    picks: tuple[Pick, ...]

    @property
    def cost(self) -> int:
        return sum(RARITY_WEIGHT[p.ingredient.rarity] * p.count for p in self.picks)

    @property
    def count(self) -> int:
        return sum(p.count for p in self.picks)


@dataclass(frozen=True, slots=True)
class AlmostReady:
    """Строка экрана «Почти готово» (FR-6.1–6.3)."""

    potion: Potion
    base: BaseKey
    kit: Kit
    have: Combination
    missing: ElementVector
    fillers: tuple[Filler, ...] = ()
    #: Особая основа рецепта (П-4.4) и есть ли её нехватка: тогда в строке «нужна основа».
    base_ingredient: Ingredient | None = None
    missing_base: bool = False

    @property
    def missing_units(self) -> int:
        """Недостача в единицах; нехватающая особая основа считается за одну."""
        return self.missing.total + (1 if self.missing_base else 0)


# ── §5.1 Точные комбинации ────────────────────────────────────────────────────
@dataclass(frozen=True, slots=True)
class ClassSet:
    """Классы инвентаря с предрасчётом для отсева (NFR-5).

    `masks[i]` — битовая маска осей, по которым класс ненулевой. Класс может
    участвовать в комбинации, только если все его оси есть у цели.
    """

    classes: tuple[ClassPick, ...] = ()
    masks: tuple[int, ...] = ()

    def overlapping(self, target: ElementVector) -> list[ClassPick]:
        """Классы, у которых есть хоть один элемент из цели (П-8.1).

        Реагент, не пересекающийся с рецептом, покрыть ничего не может и только
        поднимает сложность, поэтому в осмысленный вариант он не входит никогда.

        Порядок — не тот, что при точном переборе: сначала те, что не дают отхода
        и закрывают больше. Перебор берёт классы в этом порядке, поэтому хорошие
        варианты находятся первыми, и обрыв по лимиту режет только худшие.
        """
        target_mask = 0
        for axis, value in enumerate(target.counts):
            if value:
                target_mask |= 1 << axis
        chosen = [
            cls for cls, mask in zip(self.classes, self.masks, strict=True) if mask & target_mask
        ]
        counts = target.counts

        def rank(cls: ClassPick) -> tuple[int, int, int, tuple[int, ...]]:
            useful = sum(min(a, b) for a, b in zip(cls.elements.counts, counts, strict=True))
            return (cls.elements.total - useful, -useful, -cls.stock, cls.elements.counts)

        chosen.sort(key=rank)
        return chosen[:MAX_COVER_CLASSES]

    def fitting(self, target: ElementVector) -> list[ClassPick]:
        counts = target.counts
        target_mask = 0
        for axis, value in enumerate(counts):
            if value:
                target_mask |= 1 << axis
        return [
            cls
            for cls, mask in zip(self.classes, self.masks, strict=True)
            if not mask & ~target_mask
            and all(a <= b for a, b in zip(cls.elements.counts, counts, strict=True))
        ]


def build_classes(stock: Sequence[StockItem], *, group: bool = True) -> ClassSet:
    """Классы взаимозаменяемых реагентов, отсортированные для перебора (03 §5.1 п.2).

    Группировка от цели не зависит, поэтому для одного инвентаря её достаточно
    собрать один раз и переиспользовать для всех рецептов (NFR-5).

    `group=False` оставляет каждый реагент сам по себе: так подсказки «чем закрыть»
    перечисляют все взаимозаменяемые варианты, а не одного представителя класса.
    """
    groups: dict[object, list[StockItem]] = {}
    for item in stock:
        if item.qty <= 0 or item.elements.is_empty:
            continue
        key = item.elements if group else (item.elements, item.ingredient.id)
        groups.setdefault(key, []).append(item)
    classes: list[ClassPick] = []
    for members in groups.values():
        # Внутри класса сначала берутся самые многочисленные (03 §5.1 п.4),
        # при равенстве — самые дешёвые, потом по названию для стабильности.
        members.sort(key=lambda m: (-m.qty, RARITY_WEIGHT[m.ingredient.rarity], m.ingredient.name))
        classes.append(
            ClassPick(members[0].elements, 0, tuple(members), sum(m.qty for m in members))
        )
    # Порядок классов фиксирован: так перебор детерминирован.
    classes.sort(key=lambda c: (-c.elements.total, c.elements.counts))
    masks = []
    for cls in classes:
        mask = 0
        for axis, value in enumerate(cls.elements.counts):
            if value:
                mask |= 1 << axis
        masks.append(mask)
    return ClassSet(tuple(classes), tuple(masks))


def _fitting(classes: ClassSet | Sequence[ClassPick], target: ElementVector) -> list[ClassPick]:
    """Отсев: реагент с «лишним» элементом не подойдёт ни в какой комбинации (03 §5.1 п.1)."""
    if isinstance(classes, ClassSet):
        return classes.fitting(target)
    counts = target.counts
    return [
        c for c in classes if all(a <= b for a, b in zip(c.elements.counts, counts, strict=True))
    ]


def _overlapping(classes: ClassSet | Sequence[ClassPick], target: ElementVector) -> list[ClassPick]:
    """Отсев для перебора с излишком: нужен хоть один общий элемент с целью."""
    if isinstance(classes, ClassSet):
        return classes.overlapping(target)
    return [
        c
        for c in classes
        if any(a and b for a, b in zip(c.elements.counts, target.counts, strict=True))
    ]


def _expand(class_counts: Sequence[tuple[ClassPick, int]]) -> tuple[Pick, ...]:
    """Комбинация классов → конкретные реагенты (03 §5.1 п.4)."""
    picks: list[Pick] = []
    for class_pick, count in class_counts:
        left = count
        for member in class_pick.members:
            if left <= 0:
                break
            take = min(left, member.qty)
            picks.append(Pick(member.ingredient, take))
            left -= take
    return tuple(picks)


def _search(
    classes: Sequence[ClassPick],
    target: ElementVector,
    max_reagents: int | None,
) -> list[Combination]:
    """Перебор в глубину по классам (03 §5.1 п.3), на «сырых» кортежах ради скорости."""
    n = len(classes)
    # Для каждого класса: ненулевые оси, суммарный вес и запас.
    axes = [tuple((i, v) for i, v in enumerate(c.elements.counts) if v) for c in classes]
    totals = [c.elements.total for c in classes]
    stocks = [c.stock for c in classes]
    # Сколько единиц максимум могут дать классы с i-го и дальше: отсекает пустые ветки.
    capacity = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        capacity[i] = capacity[i + 1] + totals[i] * stocks[i]

    results: list[Combination] = []
    chosen: list[int] = [0] * n

    def rec(i: int, remainder: list[int], left: int, used: int) -> None:
        if left == 0:
            picked = [(classes[k], chosen[k]) for k in range(i) if chosen[k]]
            results.append(
                Combination(
                    picks=_expand(picked),
                    classes=tuple(cp.taking(c) for cp, c in picked),
                )
            )
            return
        if i >= n or left > capacity[i]:
            return
        vector = axes[i]
        take_max = stocks[i]
        for axis, need in vector:
            take_max = min(take_max, remainder[axis] // need)
            if not take_max:
                break
        if max_reagents is not None:
            take_max = min(take_max, max_reagents - used)
        unit = totals[i]
        for count in range(take_max, -1, -1):
            chosen[i] = count
            if count:
                for axis, need in vector:
                    remainder[axis] -= need * count
                rec(i + 1, remainder, left - unit * count, used + count)
                for axis, need in vector:
                    remainder[axis] += need * count
            else:
                rec(i + 1, remainder, left, used)
        chosen[i] = 0

    rec(0, list(target.counts), target.total, 0)
    return results


#: Предохранители перебора с излишком (NFR-5). Точный перебор полон всегда, а вот
#: с излишком вариантов тем больше, чем шире инвентарь: на 500 реагентах их
#: миллионы. Показать мы всё равно можем единицы, поэтому поиск останавливается,
#: набрав достаточно, и в любом случае не уходит дальше отведённого числа узлов.
COVER_NODE_LIMIT = 1_500
COVER_RESULT_LIMIT = 32
#: Сколько классов вообще рассматривать. Классы отсортированы по полезности для
#: цели, так что за этой границей остаются взаимозаменяемые и заведомо худшие —
#: варианты из них игрок всё равно не увидел бы.
MAX_COVER_CLASSES = 24


def _search_cover(
    classes: Sequence[ClassPick],
    target: ElementVector,
    budget: int,
    max_reagents: int | None,
    limit: int = COVER_RESULT_LIMIT,
) -> list[Combination]:
    """Наборы, чья сумма **покрывает** цель, с излишком не больше `budget` (П-8.1).

    От точного перебора отличается условием приёма: годится любой набор, где по
    каждому элементу набрано не меньше нужного. Как только набор покрыл цель,
    ветка обрывается — добавлять сверху уже нечего, это лишь поднимет сложность.
    """
    n = len(classes)
    axes = [tuple((i, v) for i, v in enumerate(c.elements.counts) if v) for c in classes]
    totals = [c.elements.total for c in classes]
    stocks = [c.stock for c in classes]
    #: Больше этого числа единиц в варианте быть не может: всё сверх цели — излишек.
    cap = target.total + budget

    capacity = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        capacity[i] = capacity[i + 1] + totals[i] * stocks[i]

    results: list[Combination] = []
    chosen: list[int] = [0] * n
    nodes = 0

    def rec(i: int, remainder: list[int], deficit: int, spent: int, used: int) -> None:
        nonlocal nodes
        if deficit == 0:
            picked = [(classes[k], chosen[k]) for k in range(i) if chosen[k]]
            results.append(
                Combination(
                    picks=_expand(picked),
                    classes=tuple(cp.taking(c) for cp, c in picked),
                    excess=ElementVector(-value for value in remainder),
                )
            )
            return
        nodes += 1
        if nodes > COVER_NODE_LIMIT or len(results) >= limit or i >= n:
            return
        # Нижняя оценка итога: потрачено плюс то, что ещё обязательно надо набрать.
        if spent + deficit > cap or deficit > capacity[i]:
            return

        vector = axes[i]
        unit = totals[i]
        take_max = min(stocks[i], (cap - spent) // unit)
        if max_reagents is not None:
            take_max = min(take_max, max_reagents - used)
        # Сверх того, что закрывает нехватку, брать класс бессмысленно: покрытие
        # уже не растёт, а вариант становится надмножеством другого, но с большей
        # сложностью. Без этого перебор выдаёт кучу заведомо худших наборов.
        useful = 0
        for axis, need in vector:
            short = remainder[axis]
            if short > 0:
                useful = max(useful, -(-short // need))
        take_max = min(take_max, useful)
        for count in range(take_max, -1, -1):
            chosen[i] = count
            if count:
                for axis, need in vector:
                    remainder[axis] -= need * count
                rec(
                    i + 1,
                    remainder,
                    sum(value for value in remainder if value > 0),
                    spent + unit * count,
                    used + count,
                )
                for axis, need in vector:
                    remainder[axis] += need * count
            else:
                rec(i + 1, remainder, deficit, spent, used)
        chosen[i] = 0

    rec(0, list(target.counts), target.total, 0, 0)
    return results


def find_combinations(
    target: ElementVector,
    stock: Sequence[StockItem],
    *,
    limit: int | None = None,
    max_reagents: int | None = None,
    max_excess: int = 0,
    group_classes: bool = True,
    classes: ClassSet | Sequence[ClassPick] | None = None,
) -> list[Combination]:
    """Наборы реагентов под цель (П-5.3, П-8.1).

    `max_excess=0` — прежнее точное совпадение: сумма равна цели ровно. Больше
    нуля разрешает лишние эссенции, каждая из которых поднимает сложность (П-8.1);
    столько их максимум и допускается.

    `stock` уже отфильтрован по набору инструментов. `limit` ограничивает число
    возвращаемых вариантов (после сортировки), `max_reagents` — число штук в варианте.
    `classes` позволяет передать заранее собранные классы (см. `build_classes`).
    """
    if target.is_empty or target.is_negative:
        return []
    if classes is None:
        classes = build_classes(stock, group=group_classes)

    if max_excess <= 0:
        # Точный перебор уже. Реагент с «лишним» элементом отсеивается сразу.
        usable = _fitting(classes, target)
        results = _search(usable, target, max_reagents) if usable else []
    else:
        usable = _overlapping(classes, target)
        # Искать заметно больше, чем покажем: сортировка потом выберет лучшее.
        internal = COVER_RESULT_LIMIT if limit is None else max(4 * limit, 32)
        results = (
            _search_cover(usable, target, max_excess, max_reagents, internal) if usable else []
        )

    results.sort(key=lambda c: c.sort_key())
    return results[:limit] if limit is not None else results


def greedy_max_repeats(options: Iterable[BrewOption]) -> int:
    """Сколько всего варок можно сделать, если каждый раз брать самый дешёвый вариант.

    Не всегда точный максимум (точный — задача целочисленного программирования),
    но для подсказки «×3» этого достаточно (03 §5.3).
    """
    options = sorted(options, key=lambda o: o.combination.sort_key())
    if not options:
        return 0
    # Запас реагента одинаков во всех вариантах: он лежит в членах классов.
    remaining: dict[str, int] = {}
    for option in options:
        for class_pick in option.combination.classes:
            for member in class_pick.members:
                remaining[member.ingredient.id] = member.qty
    # Особая основа: в классах её запас урезан на занятые под основу штуки,
    # поэтому берётся полный запас, а основа считается в каждую варку (П-4.4).
    for option in options:
        if option.base_ingredient is not None:
            remaining[option.base_ingredient.id] = option.base_stock

    total = 0
    while True:
        for option in options:
            need = option.combination.as_map()
            if option.base_ingredient is not None:
                need[option.base_ingredient.id] = option.base_need
            if all(remaining.get(iid, 0) >= qty for iid, qty in need.items()):
                for iid, qty in need.items():
                    remaining[iid] -= qty
                total += 1
                break
        else:
            return total


# ── Подбор по каталогу ────────────────────────────────────────────────────────
def portions_and_excess(
    elements: ElementVector, recipe_elements: ElementVector
) -> tuple[int, ElementVector]:
    """На сколько порций хватает суммы и что остаётся лишним (П-8.1, П-8.2)."""
    if recipe_elements.is_empty:
        return 0, elements
    portions = recipe_elements.max_repeats_in(elements)
    if portions <= 0:
        return 0, elements
    return portions, elements - recipe_elements * portions


def read_as_portions(combo: Combination, recipe_elements: ElementVector) -> Combination:
    """Перечитывает вариант на максимуме порций.

    Одна и та же горсть реагентов читается двояко: как одна порция с кучей
    лишнего или как несколько порций с малым остатком. Второе всегда не сложнее:
    порция стоит +2, а лишние эссенции того же объёма — не меньше +2, ведь
    единиц в рецепте минимум две (П-5.2). Поэтому берём максимум порций.
    """
    portions, excess = portions_and_excess(combo.elements, recipe_elements)
    if portions <= 0:
        return combo
    return replace(combo, portions=portions, excess=excess)


def as_portions(combo: Combination, recipe_elements: ElementVector, portions: int) -> Combination:
    """Вариант ровно на `portions` порций: всё сверх — лишние эссенции (П-8.1)."""
    return replace(combo, portions=portions, excess=combo.elements - recipe_elements * portions)


def _stock_for_kit(stock: Sequence[StockItem], kit: Kit) -> list[StockItem]:
    return [s for s in stock if kit_allows_ingredient(kit, s.ingredient)]


def _without(stock: Sequence[StockItem], ingredient_id: str, count: int = 1) -> list[StockItem]:
    """Запас минус `count` штук: столько уходит на особую основу (по одной на порцию)."""
    left: list[StockItem] = []
    for item in stock:
        if item.ingredient.id == ingredient_id:
            if item.qty > count:
                left.append(StockItem(item.ingredient, item.qty - count))
            continue
        left.append(item)
    return left


def _available(stock: Sequence[StockItem]) -> ElementVector:
    """Сколько всего единиц каждого элемента доступно — для верхней оценки порций."""
    total = ElementVector()
    for item in stock:
        total = total + item.elements * item.qty
    return total


def brewable(
    potions: Iterable[Potion],
    stock: Sequence[StockItem],
    kits: Iterable[Kit],
    *,
    max_excess: int = 0,
    max_portions: int = 1,
    options_per_potion: int = 8,
) -> list[BrewablePotion]:
    """Что можно сварить из инвентаря с учётом наборов (§8 правил, FR-5.1).

    Подбор идёт **для каждого набора отдельно**, результаты объединяются:
    смешивать права разных наборов в одной варке нельзя (03 §4.3).

    `max_excess` разрешает лишние эссенции (П-8.1), `max_portions` — варить
    несколько порций сразу (П-8.2). Нули и единица дают прежнее поведение:
    только точные совпадения в одну порцию.
    """
    kits = list(kits) or [Kit.ALCHEMIST]
    # Классы от рецепта не зависят, поэтому собираются один раз на набор (NFR-5).
    per_kit_stock = {kit: _stock_for_kit(stock, kit) for kit in kits}
    per_kit_classes = {kit: build_classes(items) for kit, items in per_kit_stock.items()}
    per_kit_available = {kit: _available(items) for kit, items in per_kit_stock.items()}
    in_stock = {item.ingredient.id: item for item in stock}
    result: list[BrewablePotion] = []

    for potion in potions:
        recipe = potion.recipe
        if recipe is None or potion.hidden:
            continue
        # Рецепт на особой основе варится только на ней (П-4.4): нет её в сумке —
        # нечего и подбирать, зелье уйдёт в «Почти готово».
        special = recipe.required_base_id
        base_item = in_stock.get(special) if special else None
        if special and base_item is None:
            continue
        # Один и тот же набор реагентов могут разрешать несколько наборов
        # инструментов, и основы у них разные (П-6.3). Вариант тут один, а основы
        # у него — объединение: иначе порядок наборов в настройках молча отнимал
        # бы у игрока взрывную основу, которую алхимик вполне разрешает.
        merged: dict[tuple[tuple[str, int], ...], tuple[Combination, set[BaseType], Kit]] = {}
        for kit in kits:
            if not kit_allows_potion(kit, potion):
                continue
            if special:
                # Основа уходит по одной на порцию: реагентами эти штуки уже не будут.
                # Запас под одну порцию — для оценки, достижим ли рецепт вообще.
                bases: list[BaseType] = []
                kit_stock = _without(per_kit_stock[kit], special)
                classes = build_classes(kit_stock)
                available = _available(kit_stock)
            else:
                bases = [b for b in recipe.sorted_bases() if kit_allows_base(kit, b)]
                if not bases:
                    continue
                classes = per_kit_classes[kit]
                available = per_kit_available[kit]

            # Если элементов не хватает даже при полном опустошении сумки, перебирать
            # нечего: ни один набор цель не покроет. Проверка стоит семь сравнений и
            # снимает основную часть работы — большинство рецептов просто недостижимы.
            reachable = recipe.elements.max_repeats_in(available)
            if reachable <= 0:
                continue

            combos: dict[tuple[tuple[str, int], ...], Combination] = {}
            top = min(max_portions, reachable)
            if special:
                top = min(top, base_item.qty)  # type: ignore[union-attr]
            for portions in range(1, top + 1):
                portion_classes = classes
                if special and portions > 1:
                    # На каждую порцию — своя штука основы: запас под реагенты меньше.
                    portion_classes = build_classes(_without(per_kit_stock[kit], special, portions))
                found = find_combinations(
                    recipe.elements * portions,
                    (),
                    limit=options_per_potion,
                    max_excess=max_excess,
                    classes=portion_classes,
                )
                for combo in found:
                    # С особой основой порций ровно столько, на сколько отложено
                    # основ; лишнее идёт в лишние эссенции, а не в новую порцию.
                    normalized = (
                        as_portions(combo, recipe.elements, portions)
                        if special
                        else read_as_portions(combo, recipe.elements)
                    )
                    key = tuple(sorted(normalized.as_map().items()))
                    known = combos.get(key)
                    if known is None or normalized.portions > known.portions:
                        combos[key] = normalized

            for combo in combos.values():
                signature = tuple(sorted(combo.as_map().items()))
                known = merged.get(signature)
                if known is None:
                    merged[signature] = (combo, set(bases), kit)
                    continue
                known_combo, known_bases, known_kit = known
                # «Повторов» больше у того набора, которому видно больше запасов:
                # у травника класс собран только из трав, и оценка занижена.
                better = combo.repeats > known_combo.repeats
                merged[signature] = (
                    combo if better else known_combo,
                    known_bases | set(bases),
                    kit if len(bases) > len(known_bases) else known_kit,
                )

        base_ingredient = base_item.ingredient if base_item is not None else None
        base_stock = base_item.qty if base_item is not None else 0
        options = [
            BrewOption(potion, frozenset(bases), combo, kit, base_ingredient, base_stock)
            for combo, bases, kit in merged.values()
        ]
        if options:
            # Сначала одна порция, потом больше; внутри — по сложности проверки.
            options.sort(
                key=lambda o: (
                    o.combination.portions,
                    o.difficulty.total,
                    o.combination.sort_key(),
                )
            )
            result.append(BrewablePotion(potion, tuple(options[:options_per_potion])))

    result.sort(key=lambda b: (b.potion.rarity, b.potion.name))
    return result


# ── §5.4 «Почти готово» ───────────────────────────────────────────────────────
def best_subset(
    target: ElementVector,
    stock: Sequence[StockItem],
    *,
    classes: ClassSet | Sequence[ClassPick] | None = None,
) -> Combination:
    """Подмножество инвентаря с максимальной суммой, помещающейся в target.

    Среди равных по сумме выбирается более дешёвое (03 §5.4 п.1).
    """
    if classes is None:
        classes = build_classes(stock)
    usable = _fitting(classes, target)
    if not usable:
        return Combination(picks=())

    n = len(usable)
    axes = [tuple((i, v) for i, v in enumerate(c.elements.counts) if v) for c in usable]
    totals = [c.elements.total for c in usable]
    stocks = [c.stock for c in usable]
    chosen: list[int] = [0] * n
    best: tuple[int, int, Combination] | None = None

    def rec(i: int, remainder: list[int], used_total: int) -> None:
        nonlocal best
        if i >= n:
            picked = [(usable[k], chosen[k]) for k in range(n) if chosen[k]]
            combo = Combination(
                picks=_expand(picked),
                classes=tuple(cp.taking(c) for cp, c in picked),
            )
            candidate = (-used_total, combo.cost, combo)
            if best is None or candidate[:2] < best[:2]:
                best = candidate
            return
        vector = axes[i]
        take_max = stocks[i]
        for axis, need in vector:
            take_max = min(take_max, remainder[axis] // need)
            if not take_max:
                break
        for count in range(take_max, -1, -1):
            chosen[i] = count
            if count:
                for axis, need in vector:
                    remainder[axis] -= need * count
                rec(i + 1, remainder, used_total + totals[i] * count)
                for axis, need in vector:
                    remainder[axis] += need * count
            else:
                rec(i + 1, remainder, used_total)
        chosen[i] = 0

    rec(0, list(target.counts), 0)
    return best[2] if best else Combination(picks=())


def find_fillers(
    missing: ElementVector,
    catalog_ingredients: Iterable[Ingredient],
    *,
    max_reagents: int = 2,
    limit: int = 3,
) -> list[Filler]:
    """Реагенты из справочника, закрывающие недостачу ровно (FR-6.2, 03 §5.4 п.3).

    Количество считается бесконечным: это подсказка «что искать», а не варка.
    """
    if missing.is_empty:
        return []
    infinite = [
        StockItem(ingredient, missing.total)
        for ingredient in catalog_ingredients
        if not ingredient.hidden and not ingredient.elements.is_empty
    ]
    combos = find_combinations(missing, infinite, max_reagents=max_reagents, group_classes=False)
    fillers = [Filler(combo.picks) for combo in combos]
    fillers.sort(key=lambda f: (f.count, f.cost, "|".join(p.ingredient.name for p in f.picks)))
    return fillers[:limit]


def almost_ready(
    potions: Iterable[Potion],
    stock: Sequence[StockItem],
    kits: Iterable[Kit],
    catalog_ingredients: Iterable[Ingredient],
    *,
    brewable_ids: Iterable[str] = (),
    max_missing: int | None = None,
) -> list[AlmostReady]:
    """Известные рецепты, которые сейчас не сварить, и чего им не хватает (FR-6.1–6.3)."""
    kits = list(kits) or [Kit.ALCHEMIST]
    catalog_ingredients = list(catalog_ingredients)
    by_id = {i.id: i for i in catalog_ingredients}
    per_kit_stock = {kit: _stock_for_kit(stock, kit) for kit in kits}
    per_kit_classes = {kit: build_classes(items) for kit, items in per_kit_stock.items()}
    in_stock = {item.ingredient.id for item in stock}
    skip = set(brewable_ids)
    rows: list[AlmostReady] = []

    for potion in potions:
        recipe = potion.recipe
        if recipe is None or potion.hidden or potion.id in skip:
            continue
        special = recipe.required_base_id
        has_base = bool(special) and special in in_stock
        candidates: list[AlmostReady] = []
        for kit in kits:
            if not kit_allows_potion(kit, potion):
                continue
            if special:
                # Особая основа (П-4.4): если она есть, одна штука занята под основу одной порции.
                base: BaseKey = special
                classes = (
                    build_classes(_without(per_kit_stock[kit], special))
                    if has_base
                    else per_kit_classes[kit]
                )
            else:
                bases = [b for b in recipe.sorted_bases() if kit_allows_base(kit, b)]
                if not bases:
                    continue
                base = bases[0]
                classes = per_kit_classes[kit]
            have = best_subset(recipe.elements, (), classes=classes)
            missing = (recipe.elements - have.elements).clamped()
            missing_base = bool(special) and not has_base
            if missing.is_empty and not missing_base:
                continue  # это зелье уже варится, оно на другом экране
            candidates.append(
                AlmostReady(
                    potion,
                    base,
                    kit,
                    have,
                    missing,
                    base_ingredient=by_id.get(special) if special else None,
                    missing_base=missing_base,
                )
            )
        if not candidates:
            continue
        best = min(candidates, key=lambda a: (a.missing_units, a.have.cost))
        if max_missing is not None and best.missing_units > max_missing:
            continue
        fillers = find_fillers(best.missing, catalog_ingredients) if best.missing else []
        rows.append(replace(best, fillers=tuple(fillers)))

    rows.sort(key=lambda a: (a.missing_units, a.potion.rarity, a.potion.name))
    return rows


def stock_from(
    ingredients: Mapping[str, Ingredient],
    quantities: Mapping[str, int],
) -> list[StockItem]:
    """Инвентарь → вход подбора. Реагенты не из справочника пропускаются."""
    items: list[StockItem] = []
    for ingredient_id, qty in quantities.items():
        ingredient = ingredients.get(ingredient_id)
        if ingredient is None or qty <= 0 or ingredient.hidden:
            continue
        items.append(StockItem(ingredient, qty))
    items.sort(key=lambda s: s.ingredient.name)
    return items
