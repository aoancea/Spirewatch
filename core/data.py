"""Loads the editable JSON game data from the /data folder."""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any

from settings import DATA_DIR


@lru_cache(maxsize=None)
def load(name: str) -> Any:
    """Return the parsed contents of ``data/<name>.json`` (cached). Keys starting with '_' are notes."""
    with (DATA_DIR / f"{name}.json").open("r", encoding="utf-8") as f:
        raw = json.load(f)
    if isinstance(raw, dict):
        raw = {k: v for k, v in raw.items() if not k.startswith("_")}
    return raw


def towers() -> dict[str, Any]:
    return load("towers")


def creeps() -> dict[str, Any]:
    return load("creeps")


def waves() -> dict[str, Any]:
    return load("waves")


def maps() -> dict[str, Any]:
    return load("maps")


def combat() -> dict[str, Any]:
    return load("combat")


def spells() -> dict[str, Any]:
    return load("spells")


def difficulty(name: str) -> dict[str, Any]:
    d = combat()["difficulty"]
    return d.get(name, d["normal"])


def tower_level(type_id: str, level: int) -> dict[str, Any]:
    """Stats of one tower level (1-based)."""
    return towers()[type_id]["levels"][level - 1]


def tower_total_cost(type_id: str, level: int) -> int:
    return int(sum(lv["cost"] for lv in towers()[type_id]["levels"][:level]))


def creep_tags(type_id: str) -> list[str]:
    """Short trait labels shown in the wave preview."""
    c = creeps()[type_id]
    tags = []
    if c.get("boss"):
        tags.append("Boss")
    if c.get("flying"):
        tags.append("Flying")
    if c.get("speed", 0) >= 3.3:
        tags.append("Fast")
    if c.get("armor", 0) >= 3:
        tags.append("Armored")
    if c.get("regen", 0) >= 0.01:
        tags.append("Regenerates")
    if c.get("heal"):
        tags.append("Healer")
    if c.get("split"):
        tags.append("Splits")
    if c.get("armor_type") == "ethereal":
        tags.append("Ethereal")
    if c.get("armor_type") == "warded":
        tags.append("Warded")
    return tags
