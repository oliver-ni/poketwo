"""
I'm in the process of pulling pokémon data from a SQL database, instead of this mess.
Will use veekun/pokedex.
"""
import random
import unicodedata
from abc import ABC
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import ClassVar, List, Union, overload

from . import constants


def deaccent(text):
    norm = unicodedata.normalize("NFD", text)
    result = "".join(ch for ch in norm if unicodedata.category(ch) != "Mn")
    return unicodedata.normalize("NFC", result)


class _Data:
    # TODO not sure why I made a class for this?

    pokemon = {}
    items = {}
    effects = {}
    moves = {}


# Moves


class MoveEffect:
    id: int
    description: str

    def __init__(self, id: int, description: str):
        self.id = id
        self.description = description


class Move:
    id: int
    slug: str
    name: str
    power: int
    pp: int
    accuracy: int
    priority: int
    target_id: int
    damage_class_id: int
    effect_id: int
    effect_chance: int

    def __init__(
        self,
        id: int,
        slug: str,
        name: str,
        power: int,
        pp: int,
        accuracy: int,
        priority: int,
        type_id: int,
        target_id: int,
        damage_class_id: int,
        effect_id: int,
        effect_chance: int,
    ):
        self.id = id
        self.name = name
        self.power = power
        self.pp = pp
        self.accuracy = accuracy
        self.priority = priority
        self.type_id = type_id
        self.target_id = target_id
        self.damage_class_id = damage_class_id
        self.effect_id = effect_id
        self.effect_chance = effect_chance

    @cached_property
    def type(self):
        return constants.TYPES[self.type_id]

    @cached_property
    def target_text(self):
        return constants.MOVE_TARGETS[self.target_id]

    @cached_property
    def damage_class(self):
        return constants.DAMAGE_CLASSES[self.damage_class_id]

    @cached_property
    def effect(self):
        return _Data.effects[self.effect_id]

    @cached_property
    def description(self):
        return self.effect.description.format(effect_chance=self.effect_chance)

    def __str__(self):
        return self.name


# Items


class Item:
    id: int
    name: str
    description: str
    cost: int
    page: int
    action: str
    inline: bool
    emote: str

    def __init__(
        self,
        id: int,
        name: str,
        description: str,
        cost: int,
        page: int,
        action: str,
        inline: bool,
        emote: str = None,
    ):
        self.id = id
        self.name = name
        self.description = description
        self.cost = cost
        self.page = page
        self.action = action
        self.inline = inline
        self.emote = emote

    def __str__(self):
        return self.name


class MoveMethod(ABC):
    pass


class LevelMethod(MoveMethod):
    level: int

    def __init__(self, level):
        self.level = level

    @cached_property
    def text(self):
        return f"Level {self.level}"


class PokemonMove:
    move_id: int
    method: MoveMethod

    def __init__(self, move_id, method):
        self.move_id = move_id
        self.method = method

    @cached_property
    def move(self):
        return _Data.moves[self.move_id]

    @cached_property
    def text(self):
        return self.method.text


# Evolution


class EvolutionTrigger(ABC):
    pass


@dataclass
class LevelTrigger(EvolutionTrigger):
    level: int
    item_id: int
    move_id: int
    move_type_id: int
    time: str
    relative_stats: int

    @cached_property
    def item(self):
        if self.item_id is None:
            return None
        return _Data.items[self.item_id]

    @cached_property
    def move(self):
        if self.move_id is None:
            return None
        return _Data.moves[self.move_id]

    @cached_property
    def move_type(self):
        if self.move_type_id is None:
            return None
        return constants.TYPES[self.move_type_id]

    @cached_property
    def text(self):
        if self.level is None:
            text = f"when leveled up"
        else:
            text = f"starting from level {self.level}"

        if self.item is not None:
            text += f" while holding a {self.item}"

        if self.move is not None:
            text += f" while knowing {self.move}"

        if self.move_type is not None:
            text += f" while knowing a {self.move_type}-type move"

        if self.relative_stats == 1:
            text += f" when its Attack is higher than its Defense"
        elif self.relative_stats == -1:
            text += f" when its Defense is higher than its Attack"
        elif self.relative_stats == 0:
            text += f" when its Attack is equal to its Defense"

        if self.time is not None:
            text = "somehow"

        return text


class ItemTrigger(EvolutionTrigger):
    def __init__(self, item: int):
        self.item_id = item

    @cached_property
    def item(self):
        return _Data.items[self.item_id]

    @cached_property
    def text(self):
        return f"using a {self.item}"


class TradeTrigger(EvolutionTrigger):
    def __init__(self, item: int = None):
        self.item_id = item

    @cached_property
    def item(self):
        if self.item_id is None:
            return None
        return _Data.items[self.item_id]

    @cached_property
    def text(self):
        if self.item_id is None:
            return "when traded"
        return f"when traded while holding a {self.item}"


class OtherTrigger(EvolutionTrigger):
    @cached_property
    def text(self):
        return "somehow"


class Evolution:
    def __init__(self, target: int, trigger: EvolutionTrigger, evotype: bool):
        self.target_id = target
        self.trigger = trigger
        self.type = evotype

    @classmethod
    def evolve_from(cls, target: int, trigger: EvolutionTrigger):
        return cls(target, trigger, False)

    @classmethod
    def evolve_to(cls, target: int, trigger: EvolutionTrigger):
        return cls(target, trigger, True)

    @cached_property
    def dir(self) -> str:
        return "to" if self.type == True else "from" if self.type == False else "??"

    @cached_property
    def target(self):
        return _Data.pokemon[self.target_id]

    @cached_property
    def text(self):
        if (pevo := getattr(self.target, f"evolution_{self.dir}")) is not None:
            return f"evolves {self.dir} {self.target} {self.trigger.text}, which {pevo.text}"

        return f"evolves {self.dir} {self.target} {self.trigger.text}"


class EvolutionList:
    items: list

    def __init__(self, evolutions: Union[list, Evolution]):
        if type(evolutions) == Evolution:
            evolutions = [evolutions]
        self.items = evolutions

    @cached_property
    def text(self):
        txt = " and ".join(e.text for e in self.items)
        txt = txt.replace(" and ", ", ", txt.count(" and ") - 1)
        return txt


# Stats


class Stats:
    def __init__(self, hp: int, atk: int, defn: int, satk: int, sdef: int, spd: int):
        self.hp = hp
        self.atk = atk
        self.defn = defn
        self.satk = satk
        self.sdef = sdef
        self.spd = spd


# Species


class Species:
    id: int
    name: str
    slug: str
    names: dict
    base_stats: Stats
    evolution_from: EvolutionList
    evolution_to: EvolutionList
    description: str
    mythical: bool
    legendary: bool
    ultra_beast: bool
    dex_number: int
    height: int
    weight: int
    catchable: bool
    is_form: bool
    types: List[str]
    form_item: int
    abundance: int

    moves: List[PokemonMove]

    mega_id: int
    mega_x_id: int
    mega_y_id: int

    def __init__(
        self,
        id: int,
        names: list,
        slug: str,
        base_stats: Stats,
        height: int,
        weight: int,
        dex_number: int,
        catchable: bool,
        types: List[str],
        abundance: int,
        description: str = None,
        mega_id: int = None,
        mega_x_id: int = None,
        mega_y_id: int = None,
        evolution_from: List[Evolution] = None,
        evolution_to: List[Evolution] = None,
        mythical: bool = False,
        legendary: bool = False,
        ultra_beast: bool = False,
        is_form: bool = False,
        form_item: int = None,
        moves: list = None,
    ):
        self.id = id
        self.names = names
        self.slug = slug
        self.name = next(filter(lambda x: x[0] == "🇬🇧", names))[1]
        self.base_stats = base_stats
        self.dex_number = dex_number
        self.catchable = catchable
        self.is_form = is_form
        self.form_item = form_item
        self.abundance = abundance
        self.description = description

        self.height = height
        self.weight = weight

        self.mega_id = mega_id
        self.mega_x_id = mega_x_id
        self.mega_y_id = mega_y_id

        self.types = types
        self.moves = moves or []

        if evolution_from is not None:
            self.evolution_from = EvolutionList(evolution_from)
        else:
            self.evolution_from = None

        if evolution_to is not None:
            self.evolution_to = EvolutionList(evolution_to)
        else:
            self.evolution_to = None

        self.mythical = mythical
        self.legendary = legendary
        self.ultra_beast = ultra_beast

    def __str__(self):
        return self.name

    @cached_property
    def moveset(self):
        return [_Data.moves[x] for x in self.moveset_ids]

    @cached_property
    def mega(self):
        if self.mega_id is None:
            return None

        return _Data.pokemon[self.mega_id]

    @cached_property
    def mega_x(self):
        if self.mega_x_id is None:
            return None

        return _Data.pokemon[self.mega_x_id]

    @cached_property
    def mega_y(self):
        if self.mega_y_id is None:
            return None

        return _Data.pokemon[self.mega_y_id]

    @cached_property
    def image_url(self):
        return f"https://assets.poketwo.net/images/{self.id}.png?v=800"

    @cached_property
    def shiny_image_url(self):
        return f"https://assets.poketwo.net/shiny/{self.id}.png?v=800"

    @cached_property
    def correct_guesses(self):
        extra = []
        if self.is_form:
            extra.extend(_Data.pokemon[self.dex_number].correct_guesses)
        if "nidoran" in self.slug:
            extra.append("nidoran")
        return extra + [deaccent(x.lower()) for _, x in self.names] + [self.slug]

    @cached_property
    def trade_evolution(self):
        if self.evolution_to is None:
            return None

        for e in self.evolution_to.items:
            if isinstance(e.trigger, TradeTrigger):
                return e

        return None

    @cached_property
    def evolution_text(self):
        if self.is_form and self.form_item is not None:
            species = _Data.pokemon[self.dex_number]
            item = _Data.items[self.form_item]
            return f"{self.name} transforms from {species} when given a {item.name}."

        if self.evolution_from is not None and self.evolution_to is not None:
            return (
                f"{self.name} {self.evolution_from.text} and {self.evolution_to.text}."
            )
        elif self.evolution_from is not None:
            return f"{self.name} {self.evolution_from.text}."
        elif self.evolution_to is not None:
            return f"{self.name} {self.evolution_to.text}."
        else:
            return None


def load_data(*, pokemon, items, effects, moves):
    _Data.pokemon = pokemon
    _Data.items = items
    _Data.effects = effects
    _Data.moves = moves


class GameData:
    # TODO not sure why I made a class for this?

    @classmethod
    def all_pokemon(cls):
        return _Data.pokemon.values()

    @classmethod
    def list_alolan(cls):
        if not hasattr(cls, "_alolan"):
            cls._alolan = [
                10091,
                10092,
                10093,
                10100,
                10101,
                10102,
                10103,
                10104,
                10105,
                10106,
                10107,
                10108,
                10109,
                10110,
                10111,
                10112,
                10113,
                10114,
                10115,
            ]
        return cls._alolan

    @classmethod
    def list_mythical(cls):
        if not hasattr(cls, "_mythical"):
            cls._mythical = [v.id for v in _Data.pokemon.values() if v.mythical]
        return cls._mythical

    @classmethod
    def list_legendary(cls):
        if not hasattr(cls, "_legendary"):
            cls._legendary = [v.id for v in _Data.pokemon.values() if v.legendary]
        return cls._legendary

    @classmethod
    def list_ub(cls):
        if not hasattr(cls, "_ultra_beast"):
            cls._ultra_beast = [v.id for v in _Data.pokemon.values() if v.ultra_beast]
        return cls._ultra_beast

    @classmethod
    def list_mega(cls):
        if not hasattr(cls, "_mega"):
            cls._mega = (
                [v.mega_id for v in _Data.pokemon.values() if v.mega_id is not None]
                + [
                    v.mega_x_id
                    for v in _Data.pokemon.values()
                    if v.mega_x_id is not None
                ]
                + [
                    v.mega_y_id
                    for v in _Data.pokemon.values()
                    if v.mega_y_id is not None
                ]
            )
        return cls._mega

    @classmethod
    def list_type(cls, typee: str):
        return [v.id for v in _Data.pokemon.values() if typee.title() in v.types]

    @classmethod
    def all_items(cls):
        return _Data.items.values()

    @classmethod
    def all_species_by_number(cls, number: int) -> Species:
        return [x for x in _Data.pokemon.values() if x.dex_number == number]

    @classmethod
    def all_species_by_name(cls, name: str) -> Species:
        return [
            x
            for x in _Data.pokemon.values()
            if deaccent(name.lower().replace("′", "'")) in x.correct_guesses
        ]

    @classmethod
    def find_all_matches(cls, name: str) -> Species:
        return [
            y.id
            for x in cls.all_species_by_name(name)
            for y in cls.all_species_by_number(x.id)
        ]

    @classmethod
    def species_by_number(cls, number: int) -> Species:
        try:
            return _Data.pokemon[number]
        except KeyError:
            return None

    @classmethod
    def species_by_name(cls, name: str) -> Species:
        try:
            return next(
                filter(
                    lambda x: deaccent(name.lower().replace("′", "'"))
                    in x.correct_guesses,
                    _Data.pokemon.values(),
                )
            )
        except StopIteration:
            return None

    @classmethod
    def item_by_number(cls, number: int) -> Item:
        try:
            return _Data.items[number]
        except KeyError:
            return None

    @classmethod
    def item_by_name(cls, name: str) -> Item:
        try:
            return next(
                filter(
                    lambda x: deaccent(name.lower().replace("′", "'"))
                    == x.name.lower(),
                    _Data.items.values(),
                )
            )
        except StopIteration:
            return None

    @classmethod
    def move_by_number(cls, number: int) -> Move:
        try:
            return _Data.moves[number]
        except KeyError:
            return None

    @classmethod
    def move_by_name(cls, name: str) -> Move:
        try:
            return next(
                filter(
                    lambda x: deaccent(name.lower().replace("′", "'"))
                    == x.name.lower(),
                    _Data.moves.values(),
                )
            )
        except StopIteration:
            return None

    @classmethod
    def random_spawn(cls, rarity="normal"):

        if rarity == "mythical":
            pool = [x for x in cls.all_pokemon() if x.catchable and x.mythical]
        elif rarity == "legendary":
            pool = [x for x in cls.all_pokemon() if x.catchable and x.legendary]
        elif rarity == "ultra_beast":
            pool = [x for x in cls.all_pokemon() if x.catchable and x.ultra_beast]
        else:
            pool = [x for x in cls.all_pokemon() if x.catchable]

        x = random.choices(pool, weights=[x.abundance for x in pool], k=1)[0]

        return x

    @classmethod
    def spawn_weights(cls):
        if not hasattr(cls, "_spawn_weights"):
            cls._spawn_weights = [p.abundance for p in _Data.pokemon.values()]
        return cls._spawn_weights
