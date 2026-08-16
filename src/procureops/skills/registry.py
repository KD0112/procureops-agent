from __future__ import annotations

from typing import Any


class SkillRegistry:
    """Explicit skill registry; a skill is a bounded workflow, not an untyped prompt."""

    def __init__(self) -> None:
        self._skills: dict[str, Any] = {}

    def register(self, name: str, skill: Any) -> None:
        if not name or name in self._skills:
            raise ValueError("skill name must be unique and non-empty")
        if not callable(getattr(skill, "run", None)):
            raise TypeError("skill must expose an async run method")
        self._skills[name] = skill

    def get(self, name: str) -> Any:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise KeyError(f"skill not registered: {name}") from exc

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._skills))

    async def execute(self, name: str, **kwargs: Any) -> Any:
        result = self.get(name).run(**kwargs)
        if not hasattr(result, "__await__"):
            raise TypeError("skill.run must return an awaitable")
        return await result
