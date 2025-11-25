from __future__ import annotations

import json
import random
from pathlib import Path
from typing import List, Set


class JsonNamePool:
    """
    Simple pool of usernames loaded from a JSON file.

    JSON file format: ["kimblue373", "pumpkin_s0up", ...]
    """

    def __init__(self, json_path: str):
        self.json_path = Path(json_path)
        if not self.json_path.exists():
            raise FileNotFoundError(f"Usernames file not found: {self.json_path}")
        self._names: List[str] = []
        self._used: Set[str] = set()
        self._load()

    def _load(self) -> None:
        with self.json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(f"Expected a list of usernames in {self.json_path}")
        self._names = [str(n) for n in data if n]

    def next(self) -> str:
        available = [n for n in self._names if n not in self._used]
        if not available:
            raise RuntimeError(
                f"No more unique usernames available in {self.json_path}. "
                "Please expand usernames.json or reduce agent count."
            )
        name = random.choice(available)
        self._used.add(name)
        return name

    def next_many(self, count: int) -> List[str]:
        return [self.next() for _ in range(count)]

