"""Tower logic: construction, targeting, the five attack kinds, upgrades and auras."""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Any

import numpy as np

from core import data
from core.events import events
from core.util import yaw_to
from settings import BUILD_TIME, FULL_REFUND_SECONDS, MAX_LEVEL, SELL_REFUND, UPGRADE_TIME
from towers.models import MUZZLE, REACH

if TYPE_CHECKING:
    from core.match import Match
    from creeps.creep import Creep

MODES = ("first", "last", "strong", "weak", "near")
MODE_NAMES = {"first": "First", "last": "Last", "strong": "Strongest", "weak": "Weakest", "near": "Closest"}
MODE_HELP = {"first": "the creep closest to the Heartfire", "last": "the creep furthest from the Heartfire",
             "strong": "the creep with the most health", "weak": "the creep with the least health",
             "near": "the creep closest to this tower"}


class Tower:
    """One tower standing on a grid cell."""

    def __init__(self, match: "Match", type_id: str, cell: int, instant: bool = False) -> None:
        self.match = match
        self.type_id = type_id
        self.defn: dict[str, Any] = data.towers()[type_id]
        self.kind: str = self.defn["kind"]
        self.attack_type: str = self.defn.get("attack_type", "pierce")
        targets = self.defn.get("targets", [])
        self.hits_ground = "ground" in targets
        self.hits_air = "air" in targets
        self.level = 1
        self.stats: dict[str, Any] = self.defn["levels"][0]
        self.cell = cell
        self.x, self.z = match.grid.center(cell)
        self.y = match.level.cell_y[cell]
        self.invested = int(self.stats["cost"])
        self.mode: str = self.defn.get("default_mode", "first")
        self.state = "ready" if instant else "building"
        self.busy_total = BUILD_TIME
        self.busy_t = 0.0 if instant else BUILD_TIME
        self.built_at = match.time
        self.cooldown = 0.0
        self.acq_t = random.uniform(0.0, 0.12)
        self.aim_yaw = 180.0
        self.firing = 0.0
        self.kills = 0
        self.damage_dealt = 0.0
        self.selected = False
        # buffs from banners / Overcharge
        self.buff_damage = 0.0
        self.buff_rate = 0.0
        self.buff_range = 0.0
        self.overcharge_t = 0.0
        # beam state
        self.beam_target: "Creep | None" = None
        self.ramp = 1.0
        self.view = None

    # ================================================================== stats
    @property
    def name(self) -> str:
        return self.stats.get("name", self.defn["name"])

    @property
    def ready(self) -> bool:
        return self.state == "ready"

    @property
    def range(self) -> float:
        r = float(self.stats.get("range", 0.0))
        return r if self.kind == "aura" else r * (1.0 + self.buff_range)

    @property
    def damage(self) -> float:
        return float(self.stats.get("damage", 0.0)) * (1.0 + self.buff_damage)

    @property
    def interval(self) -> float:
        rate = (1.0 + self.buff_rate) * (2.0 if self.overcharge_t > 0 else 1.0)
        return float(self.stats.get("cooldown", 1.0)) / rate

    @property
    def can_upgrade(self) -> bool:
        return self.level < MAX_LEVEL and self.state == "ready"

    def upgrade_cost(self) -> int:
        return int(self.defn["levels"][self.level]["cost"]) if self.level < MAX_LEVEL else 0

    def next_stats(self) -> dict[str, Any] | None:
        return self.defn["levels"][self.level] if self.level < MAX_LEVEL else None

    def sell_value(self) -> int:
        fresh = self.level == 1 and self.match.time - self.built_at <= FULL_REFUND_SECONDS
        return int(round(self.invested * (1.0 if fresh else SELL_REFUND)))

    def dps(self, stats: dict[str, Any] | None = None) -> float:
        """Rough sustained damage per second against one unarmored target (shown in the HUD)."""
        st = stats or self.stats
        if self.kind == "aura":
            return 0.0
        dmg = float(st.get("damage", 0.0))
        if stats is None:
            dmg *= 1.0 + self.buff_damage
        if self.kind == "beam":
            return dmg * (1.0 + float(st["ramp"][0])) * 0.5
        cd = float(st.get("cooldown", 1.0)) if stats is not None else self.interval
        crit = st.get("crit")
        k = 1.0 + crit[0] * (crit[1] - 1.0) if crit else 1.0
        return dmg * k * int(st.get("multishot", 1)) / max(cd, 1e-3)

    def muzzle(self) -> tuple[float, float, float]:
        if self.view is not None:
            return self.view.muzzle()
        reach = REACH.get(self.type_id, 0.0)
        a = math.radians(self.aim_yaw)
        return self.x + math.sin(a) * reach, self.y + MUZZLE[self.type_id][self.level - 1], self.z + math.cos(a) * reach

    def in_range(self, c: "Creep") -> bool:
        if c.flying and not self.hits_air or not c.flying and not self.hits_ground:
            return False
        r = self.range + c.radius * 0.5
        return (c.x - self.x) ** 2 + (c.z - self.z) ** 2 <= r * r

    # ================================================================== lifecycle
    def begin_upgrade(self) -> None:
        self.invested += self.upgrade_cost()
        self.state = "upgrading"
        self.busy_total = self.busy_t = UPGRADE_TIME
        self.beam_target = None
        self.ramp = 1.0

    def _finish_busy(self) -> None:
        m = self.match
        if self.state == "upgrading":
            self.level += 1
            self.stats = self.defn["levels"][self.level - 1]
            if self.view is not None:
                self.view.build(self.level)
                self.view.pulse()
            m.fx.upgrade_done(self)
            events.emit("sound", name="upgrade", pos=(self.x, self.z))
        self.state = "ready"
        self.cooldown = 0.0
        m.towers_changed()

    def cycle_mode(self) -> str:
        self.mode = MODES[(MODES.index(self.mode) + 1) % len(MODES)]
        self.beam_target = None
        return self.mode

    # ================================================================== simulation
    def update(self, dt: float) -> None:
        if self.state != "ready":
            self.busy_t -= dt
            if self.busy_t <= 0:
                self._finish_busy()
            return
        if self.overcharge_t > 0:
            self.overcharge_t -= dt
        if self.firing > 0:
            self.firing -= dt
        if self.kind == "aura":
            return
        self.cooldown -= dt
        if self.kind == "beam":
            self._update_beam(dt)
            return
        if self.cooldown > 0:
            return
        self.acq_t -= dt
        if self.acq_t > 0:
            return
        targets = self._acquire(int(self.stats.get("multishot", 1)))
        if not targets:
            self.acq_t = 0.12
            return
        self.cooldown = max(self.cooldown, -dt) + self.interval
        first = targets[0]
        self.aim_yaw = yaw_to(first.x - self.x, first.z - self.z)
        self.firing = 0.25
        if self.kind == "projectile":
            self._fire_projectiles(targets)
        elif self.kind == "chain":
            self._fire_chain(first)
        elif self.kind == "cone":
            self._fire_cone(first)

    def _acquire(self, count: int = 1) -> list["Creep"]:
        """Pick targets in range according to the tower's targeting mode."""
        arr = self.match.combat.arr
        if arr.n == 0:
            return []
        d2 = (arr.x - self.x) ** 2 + (arr.z - self.z) ** 2
        mask = d2 <= (self.range + arr.rad * 0.5) ** 2
        if not self.hits_air:
            mask &= ~arr.fly
        if not self.hits_ground:
            mask &= arr.fly
        idx = np.flatnonzero(mask)
        if idx.size == 0:
            return []
        prefer = self.defn.get("prefer")
        if prefer == "unslowed":
            sub = idx[~arr.slowed[idx]]
            idx = sub if sub.size else idx
        elif prefer == "unpoisoned":
            sub = idx[~arr.poisoned[idx]]
            idx = sub if sub.size else idx
        mode = self.mode
        if mode == "first":
            key = arr.rem[idx]
        elif mode == "last":
            key = -arr.rem[idx]
        elif mode == "strong":
            key = -arr.hp[idx] + arr.rem[idx] * 1e-3
        elif mode == "weak":
            key = arr.hp[idx] + arr.rem[idx] * 1e-3
        else:
            key = d2[idx]
        out: list["Creep"] = []
        for i in idx[np.argsort(key, kind="stable")]:
            c = arr.creeps[i]
            if c.alive:
                out.append(c)
                if len(out) >= count:
                    break
        return out

    # ------------------------------------------------------------------ projectile towers
    def _fire_projectiles(self, targets: list["Creep"]) -> None:
        m = self.match
        sx, sy, sz = self.muzzle()
        for t in targets:
            m.combat.launch(self.defn.get("projectile", "arrow"), sx, sy, sz, t, self._impact)
        m.fx.tower_fired(self, sx, sy, sz)
        events.emit("sound", name=self.defn.get("sound", "bow"), pos=(self.x, self.z), volume=0.55)

    def _impact(self, target: "Creep | None", x: float, z: float) -> None:
        m = self.match
        st = self.stats
        dmg = self.damage
        crit = st.get("crit")
        is_crit = bool(crit) and random.random() < crit[0]
        if is_crit:
            dmg *= crit[1]
        splash = float(st.get("splash", 0.0))
        if splash > 0:
            edge = m.combat.splash_edge
            stun = st.get("stun")
            stunned = bool(stun) and random.random() < stun[0]
            for c in m.combat.arr.within(x, z, splash, ground=self.hits_ground, air=self.hits_air):
                d = math.hypot(c.x - x, c.z - z)
                self._hit(c, dmg * (1.0 - (1.0 - edge) * min(1.0, d / splash)))
                if stunned and c.alive:
                    c.apply_stun(stun[1])
            m.fx.splash(x, z, splash, self.type_id, stunned)
            return
        if target is None or not target.alive:
            return
        self._hit(target, dmg, is_crit)
        impale = st.get("impale")
        if impale:
            extra = [c for c in m.combat.arr.within(target.x, target.z, float(impale[1]), ground=self.hits_ground, air=self.hits_air)
                     if c is not target]
            extra.sort(key=lambda c: (c.x - target.x) ** 2 + (c.z - target.z) ** 2)
            for c in extra[: int(impale[0])]:
                self._hit(c, dmg * float(impale[2]))
                m.fx.impale(target, c)

    def _hit(self, c: "Creep", dmg: float, crit: bool = False) -> None:
        """Damage one creep and apply this tower's on-hit effects."""
        m = self.match
        st = self.stats
        if c.flying and st.get("air_bonus"):
            dmg *= 1.0 + float(st["air_bonus"])
        dealt = m.combat.damage(c, dmg, self.attack_type, self, float(st.get("armor_pierce", 0.0)))
        m.fx.creep_hit(c, self.type_id, dealt, crit)
        if not c.alive:
            return
        slow = st.get("slow")
        if slow:
            c.apply_slow(float(slow[0]), float(slow[1]))
        freeze = st.get("freeze")
        if freeze and random.random() < freeze[0] and c.apply_stun(float(freeze[1]), freeze=True):
            m.fx.frozen(c)
        self._apply_dots(c)

    def _apply_dots(self, c: "Creep") -> None:
        st = self.stats
        burn = st.get("burn")
        if burn:
            mult = self.match.combat.type_mult("elemental", c.armor_type)
            c.apply_burn(float(burn[0]) * (1.0 + self.buff_damage) * mult, float(burn[1]), self)
        poison = st.get("poison")
        if poison:
            c.apply_poison(float(poison[0]) * (1.0 + self.buff_damage), float(poison[1]), float(poison[2]), self,
                           float(st.get("spread", 0.0)))

    # ------------------------------------------------------------------ chain lightning
    def _fire_chain(self, target: "Creep") -> None:
        m = self.match
        count, radius, falloff = self.stats["chain"]
        dmg = self.damage
        sx, sy, sz = self.muzzle()
        pts = [(sx, sy, sz), (target.x, target.center_y(), target.z)]
        hit = [target]
        m.combat.damage(target, dmg, self.attack_type, self)
        cur = target
        for _ in range(int(count) - 1):
            cands = [c for c in m.combat.arr.within(cur.x, cur.z, float(radius), ground=self.hits_ground, air=self.hits_air)
                     if c not in hit]
            if not cands:
                break
            nxt = min(cands, key=lambda c: (c.x - cur.x) ** 2 + (c.z - cur.z) ** 2)
            dmg *= float(falloff)
            pts.append((nxt.x, nxt.center_y(), nxt.z))
            hit.append(nxt)
            m.combat.damage(nxt, dmg, self.attack_type, self)
            cur = nxt
        m.fx.lightning(pts)
        events.emit("sound", name=self.defn.get("sound", "zap"), pos=(self.x, self.z), volume=0.6)

    # ------------------------------------------------------------------ flame cone
    def _fire_cone(self, target: "Creep") -> None:
        m = self.match
        st = self.stats
        a = math.radians(self.aim_yaw)
        dirx, dirz = math.sin(a), math.cos(a)
        limit = math.cos(math.radians(float(st.get("cone", 60)) * 0.5))
        dmg = self.damage
        for c in m.combat.arr.within(self.x, self.z, self.range, ground=self.hits_ground, air=self.hits_air):
            dx, dz = c.x - self.x, c.z - self.z
            d = math.hypot(dx, dz)
            if d < 0.7 or (dx * dirx + dz * dirz) / d >= limit:
                m.combat.damage(c, dmg, self.attack_type, self, kind="dot")
                if c.alive:
                    self._apply_dots(c)
        m.fx.flame_jet(self, self.aim_yaw, self.range, float(st.get("cone", 60)))
        events.emit("sound", name=self.defn.get("sound", "flame"), pos=(self.x, self.z), volume=0.4)

    # ------------------------------------------------------------------ beam
    def _update_beam(self, dt: float) -> None:
        m = self.match
        st = self.stats
        t = self.beam_target
        if t is not None and (not t.alive or not self.in_range(t)):
            t = None
        if t is None:
            self.ramp = 1.0
            self.beam_target = None
            self.acq_t -= dt
            if self.acq_t > 0:
                self.cooldown = min(self.cooldown, 0.0)
                return
            self.acq_t = 0.12
            found = self._acquire(1)
            if not found:
                self.cooldown = 0.0
                return
            t = self.beam_target = found[0]
            self.cooldown = 0.0
            events.emit("sound", name=self.defn.get("sound", "beam"), pos=(self.x, self.z), volume=0.45)
        mx, gain = st["ramp"]
        self.ramp = min(float(mx), self.ramp + float(gain) * dt)
        self.aim_yaw = yaw_to(t.x - self.x, t.z - self.z)
        self.firing = 0.2
        tick = float(st.get("cooldown", 0.1))
        pierce = float(st.get("armor_pierce", 0.0))
        guard = 0
        while self.cooldown <= 0 and guard < 8:
            guard += 1
            self.cooldown += self.interval
            m.combat.damage(t, self.damage * self.ramp * tick, self.attack_type, self, pierce, kind="dot")
            if not t.alive:
                self.beam_target = None
                self.ramp = 1.0
                break

    def destroy(self) -> None:
        if self.view is not None:
            self.view.destroy()
            self.view = None
