"""Generator registry with alias index."""

from __future__ import annotations

from .generators import buildings, containers, furniture, mechanical, misc, robotics, vehicles, wearables
from .generators.base import Generator


class GeneratorRegistry:
    def __init__(self, generators: list[Generator] | None = None) -> None:
        self._by_id: dict[str, Generator] = {}
        self._aliases: dict[str, tuple[Generator, dict]] = {}
        for g in generators if generators is not None else default_generators():
            self.register(g)

    def register(self, g: Generator) -> None:
        if g.id in self._by_id:
            raise ValueError(f"duplicate generator {g.id}")
        self._by_id[g.id] = g
        for alias, preset in g.aliases.items():
            key = alias.lower()
            if key in self._aliases and self._aliases[key][0] is not g:
                raise ValueError(f"alias {alias!r} claimed by {self._aliases[key][0].id} and {g.id}")
            self._aliases[key] = (g, preset)

    def get(self, generator_id: str) -> Generator | None:
        return self._by_id.get(generator_id)

    def aliases(self) -> dict[str, tuple[Generator, dict]]:
        return self._aliases

    def all(self) -> list[Generator]:
        return list(self._by_id.values())

    def catalog(self) -> list[dict]:
        return [g.info() for g in self._by_id.values()]

    def supported_types(self) -> list[str]:
        return sorted({g.object_type for g in self._by_id.values()})


def default_generators() -> list[Generator]:
    out: list[Generator] = []
    for module in (vehicles, buildings, containers, furniture, wearables, robotics, mechanical, misc):
        out.extend(module.GENERATORS)
    return out
