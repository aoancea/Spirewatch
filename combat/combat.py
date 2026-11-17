"""Damage rules, the per-step creep snapshot towers aim with, and projectile flight."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, Callable

import numpy as np

from core import data

if TYPE_CHECKING:
    from core.match import Match
    from creeps.creep import Creep

# kind -> (speed, arc height as a fraction of the distance)
PROJECTILE_FLIGHT: dict[str, tuple[float, float]] = {
    "arrow": (34.0, 0.10), "bolt": (52.0, 0.03), "shell": (17.0, 0.34), "frost": (24.0, 0.06), "venom": (22.0, 0.16),
}


def armor_factor(armor: float, k: float) -> float:
    """Share of damage that gets through ``armor`` (above 1 when the armor has been shredded below zero)."""
    if armor >= 0:
        return 1.0 - (k * armor) / (1.0 + k * armor)
    return 2.0 - (1.0 - k) ** (-armor)


class CreepArrays:
    """Positions and stats of every living creep as numpy arrays, rebuilt once per simulation step."""

    def __init__(self) -> None:
        self.creeps: list["Creep"] = []
        self.n = 0
        self.x = self.z = self.rem = self.hp = self.rad = np.zeros(0, np.float32)
        self.fly = self.slowed = self.poisoned = np.zeros(0, bool)

    def rebuild(self, creeps: list["Creep"]) -> None:
        live = [c for c in creeps if c.alive]
        n = len(live)
        self.creeps = live
        self.n = n
        self.x = np.fromiter((c.x for c in live), np.float32, n)
        self.z = np.fromiter((c.z for c in live), np.float32, n)
        self.rem = np.fromiter((c.remaining for c in live), np.float32, n)
        self.hp = np.fromiter((c.hp for c in live), np.float32, n)
        self.rad = np.fromiter((c.radius for c in live), np.float32, n)
        self.fly = np.fromiter((c.flying for c in live), bool, n)
        self.slowed = np.fromiter((c.slow_t > 0 or c.stun_t > 0 for c in live), bool, n)
        self.poisoned = np.fromiter((c.poison_t > 0 for c in live), bool, n)

    def within(self, x: float, z: float, radius: float, ground: bool = True, air: bool = True) -> list["Creep"]:
        """Living creeps whose body overlaps the circle."""
        if self.n == 0:
            return []
        d2 = (self.x - x) ** 2 + (self.z - z) ** 2
        mask = d2 <= (radius + self.rad * 0.5) ** 2
        if not air:
            mask &= ~self.fly
        if not ground:
            mask &= self.fly
        return [self.creeps[i] for i in np.flatnonzero(mask) if self.creeps[i].alive]


class Projectile:
    """A homing missile. Flight is simulated in the ground plane; the arc is purely visual."""

    __slots__ = ("kind", "target", "x", "z", "tx", "tz", "sy", "ty", "speed", "arc", "total", "on_hit", "node", "sx", "sz")

    def __init__(self, kind: str, sx: float, sy: float, sz: float, target: "Creep", on_hit: Callable[[Any, float, float], None]) -> None:
        self.kind = kind
        self.target = target
        self.x, self.z = sx, sz
        self.sx, self.sz, self.sy = sx, sz, sy
        self.tx, self.tz, self.ty = target.x, target.z, target.center_y()
        self.speed, arc = PROJECTILE_FLIGHT.get(kind, PROJECTILE_FLIGHT["arrow"])
        self.total = max(0.5, math.hypot(self.tx - sx, self.tz - sz))
        self.arc = arc * self.total
        self.on_hit = on_hit
        self.node = None

    def update(self, dt: float) -> bool:
        """Advance; returns False once it has landed."""
        t = self.target
        if t is not None:
            if t.alive:
                self.tx, self.tz, self.ty = t.x, t.z, t.center_y()
            else:
                self.target = None
        dx, dz = self.tx - self.x, self.tz - self.z
        d = math.hypot(dx, dz)
        step = self.speed * dt
        if d <= step:
            self.x, self.z = self.tx, self.tz
            self.on_hit(self.target, self.tx, self.tz)
            return False
        self.x += dx / d * step
        self.z += dz / d * step
        return True

    def position(self) -> tuple[float, float, float, float]:
        """(x, y, z, progress 0..1) for drawing."""
        left = math.hypot(self.tx - self.x, self.tz - self.z)
        f = max(0.0, min(1.0, 1.0 - left / self.total))
        y = self.sy + (self.ty - self.sy) * f + math.sin(f * math.pi) * self.arc
        return self.x, y, self.z, f


class Combat:
    """Applies typed damage and keeps the live projectiles."""

    def __init__(self, match: "Match") -> None:
        self.match = match
        c = data.combat()
        self.mult: dict[str, dict[str, float]] = c["multipliers"]
        self.k = float(c.get("armor_k", 0.06))
        self.splash_edge = float(c.get("splash_edge", 0.5))
        self.projectiles: list[Projectile] = []
        self.arr = CreepArrays()

    def type_mult(self, attack_type: str, armor_type: str) -> float:
        return float(self.mult.get(attack_type, {}).get(armor_type, 1.0))

    def damage(self, creep: "Creep", amount: float, attack_type: str, source: Any = None, armor_pierce: float = 0.0,
               kind: str = "hit") -> float:
        """Deal ``amount`` of ``attack_type`` damage to a creep; returns the health removed."""
        if not creep.alive:
            return 0.0
        mult = self.type_mult(attack_type, creep.armor_type)
        if attack_type != "true":
            armor = creep.armor
            if armor > 0 and armor_pierce:
                armor *= 1.0 - armor_pierce
            mult *= armor_factor(armor, self.k)
        return creep.take_damage(amount * mult, source, kind)

    def expected(self, amount: float, attack_type: str, creep: "Creep") -> float:
        return amount * self.type_mult(attack_type, creep.armor_type) * armor_factor(creep.armor, self.k)

    def launch(self, kind: str, sx: float, sy: float, sz: float, target: "Creep", on_hit: Callable[[Any, float, float], None]) -> Projectile:
        p = Projectile(kind, sx, sy, sz, target, on_hit)
        self.projectiles.append(p)
        self.match.fx.projectile_spawned(p)
        return p

    def update(self, dt: float) -> None:
        if not self.projectiles:
            return
        keep = []
        fx = self.match.fx
        for p in self.projectiles:
            if p.update(dt):
                keep.append(p)
            else:
                fx.projectile_landed(p)
        self.projectiles = keep

    def clear(self) -> None:
        for p in self.projectiles:
            self.match.fx.projectile_landed(p, silent=True)
        self.projectiles.clear()
