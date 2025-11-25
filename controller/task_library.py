from __future__ import annotations

import random
from typing import Dict, List

__all__ = [
    "BASE_USERNAME_STEMS",
    "UsernameGenerator",
    "TASK_LIBRARY",
    "load_task_library",
]

# Username stems that feel pleasant in-game. Shared across controllers so that
# both the GUI runner and the multi-process launcher can mint unique names.
BASE_USERNAME_STEMS: List[str] = [
    "AliceBot",
    "BobBot",
    "CharlieBot",
    "DaisyBot",
    "EveBot",
    "FrankBot",
    "GraceBot",
    "NovaBot",
    "PixelBot",
    "MinerBot",
]


class UsernameGenerator:
    """
    Generates random, never-reused usernames based on a configurable base list.
    """

    def __init__(self, base_names: List[str] | None = None):
        self.base_names = list(base_names or BASE_USERNAME_STEMS)
        self._used: set[str] = set()

    def mark_used(self, names: List[str]) -> None:
        for n in names:
            self._used.add(n)

    def generate_one(self) -> str:
        for _ in range(100_000):
            base = random.choice(self.base_names)
            suffix = random.randint(0, 99_999)
            candidate = f"{base}_{suffix}"
            if candidate not in self._used:
                self._used.add(candidate)
                return candidate
        raise RuntimeError("Unable to generate a new unique username.")

    def generate_many(self, count: int) -> List[str]:
        return [self.generate_one() for _ in range(count)]


TASK_LIBRARY: List[Dict[str, str]] = [
    {
        "id": "gather_wood_and_store",
        "description": (
            "Gather basic wood resources near your starting area.\n"
            "- Move to a nearby tree.\n"
            "- Chop down enough trees to collect some wood logs.\n"
            "- Craft some logs into planks and sticks.\n"
            "- If there is no chest near the main base / spawn, craft a chest and place it.\n"
            "- Store surplus logs and planks into that chest.\n"
            "Avoid destroying obviously player-built structures."
        ),
    },
    {
        "id": "mine_stone_and_coal",
        "description": (
            "Set up early-game mining infrastructure.\n"
            "- Find exposed stone or dig a simple staircase mine.\n"
            "- Collect cobblestone and some coal if you see it.\n"
            "- Craft a furnace if there is not already one at the base.\n"
            "- Place the furnace in a reasonable base area near the main chest."
        ),
    },
    {
        "id": "smelt_iron_and_craft_tools",
        "description": (
            "Upgrade the tech tree by getting iron tools.\n"
            "- Explore caves or dig deeper to find iron ore.\n"
            "- Mine some iron ore.\n"
            "- Smelt the iron ore into ingots using a furnace and fuel.\n"
            "- Craft at least one iron pickaxe or sword if possible."
        ),
    },
    {
        "id": "set_up_wheat_farm",
        "description": (
            "Establish a small, sustainable wheat farm near the base.\n"
            "- Acquire seeds by breaking grass if needed.\n"
            "- Find or create farmland near a water source.\n"
            "- Till the soil with a hoe and plant wheat seeds.\n"
            "- Place fences and torches around the farm for basic protection."
        ),
    },
    {
        "id": "farm_to_table_cooking",
        "description": (
            "Create a 'farm-to-table' food pipeline.\n"
            "- Hunt nearby animals (cows, pigs, chickens, sheep) for food.\n"
            "- Collect raw meat and useful drops (leather, feathers, etc.).\n"
            "- Use a furnace or smoker to cook the meat.\n"
            "- Store cooked food in a base chest or share with nearby players."
        ),
    },
    {
        "id": "build_starter_house",
        "description": (
            "Build a small, safe starter house near the main base.\n"
            "- Pick a flat area close to spawn / the main chest.\n"
            "- Build a compact wooden house with walls and a roof.\n"
            "- Add a door, basic lighting, and at least a chest + crafting table."
        ),
    },
    {
        "id": "build_watchtower_with_ladder",
        "description": (
            "Create a simple watchtower that players can climb.\n"
            "- Near the base, erect a vertical pillar or small tower.\n"
            "- Attach a ladder or staircase so it is easy to climb.\n"
            "- Build a small lit platform at the top."
        ),
    },
    {
        "id": "organize_storage",
        "description": (
            "Improve storage organization around the base.\n"
            "- Open nearby chests and inspect contents.\n"
            "- Create or repurpose a few chests to group items by type.\n"
            "- Move items to form neat stacks and category-based storage."
        ),
    },
    {
        "id": "explore_and_light_caves",
        "description": (
            "Safely explore nearby caves and light them up.\n"
            "- Craft or obtain a good number of torches.\n"
            "- Locate nearby caves or dark areas that could spawn hostile mobs.\n"
            "- Carefully explore, placing torches along the way."
        ),
    },
    {
        "id": "fishing_and_feeding",
        "description": (
            "Set up fishing as an alternate food source.\n"
            "- If you do not have a fishing rod, craft one from sticks and string.\n"
            "- Fish in nearby water.\n"
            "- Cook the fish if possible and store it or share it."
        ),
    },
]


def load_task_library() -> List[Dict[str, str]]:
    """
    Return a list of dictionaries (id + description) for downstream consumers
    that still expect the previous structure.
    """
    return [task.copy() for task in TASK_LIBRARY]

