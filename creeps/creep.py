"""A single creep: walks the flow field (or flies straight), carries status effects, dies or leaks."""
from __future__ import annotations

import math
import random
from typing import TYPE_CHECKING, Any

from core import data
from core.util import damp_angle, yaw_to
from settings import CELL, FLY_HEIGHT
from world.grid import INF

if TYPE_CHECKING:
    from core.match import Match

LOOKAHEAD = 7              # how many flow-field cells a creep may cut across in a straight line
LOS_MARGIN = 0.3 * CELL    # clearance kept from walls when cutting corners
CORPSE_TIME = 1.5

_dims: dict[str, tuple[float, float]] = {}


def creep_dims(type_id: str) -> tuple[float, float]:
    """(radius, height) of a creep type, measured once from its rig."""
    d = _dims.get(type_id)
    if d is None:
        from creeps.models import build_creep_rig
        rig = build_creep_rig(data.creeps()[type_id]["model"])
        d = (rig.radius, rig.height)
        rig.destroy()
        _dims[type_id] = d
    return d


class Creep:
    """One enemy on the field."""

    def __init__(self, match: "Match", type_id: str, unit_hp: float, bounty: int, x: float, z: float,
                 wave: int = 0, yaw: float = 90.0) -> None:
        defn = data.creeps()[type_id]
        self.match = match
        self.type_id = type_id
        self.defn = defn
        self.name: str = defn["name"]
        self.wave = wave
        self.unit_hp = unit_hp
        self.max_hp = max(1.0, unit_hp * float(defn.get("hp", 1.0)))
        self.hp = self.max_hp
        self.base_armor = float(defn.get("armor", 0))
        self.armor_type: str = defn.get("armor_type", "unarmored")
        self.base_speed = float(defn.get("speed", 2.5))
        self.flying = bool(defn.get("flying", False))
        self.boss = bool(defn.get("boss", False))
        self.lives = int(defn.get("lives", 1))
        self.bounty = bounty
        self.regen = float(defn.get("regen", 0.0))
        self.heal: list[float] | None = defn.get("heal")
        self.heal_t = random.uniform(0.5, 1.5) if self.heal else 0.0
        self.mended_at = -99.0                 # match time of the last heal received (healers do not stack)
        self.split: list[Any] | None = defn.get("split")
        resist = float(defn.get("slow_resist", 0.0))
        if self.boss:
            resist = max(resist, float(data.combat().get("boss_slow_resist", 0.5)))
        self.slow_resist = resist
        self.radius, self.height = creep_dims(type_id)
        self.x = x
        self.z = z
        self.y = match.level.height(x, z)
        self.yaw = yaw
        self.face = yaw
        self.alive = True
        self.leaked = False
        self.selected = False
        # status effects
        self.slow_f = 0.0
        self.slow_t = 0.0
        self.stun_t = 0.0
        self.frozen = False
        self.burn_dps = 0.0
        self.burn_t = 0.0
        self.burn_src: Any = None
        self.poison_dps = 0.0
        self.poison_t = 0.0
        self.poison_shred = 0.0
        self.poison_src: Any = None
        self.poison_spread = 0.0
        # movement
        self.wp: tuple[float, float] | None = None
        self.wp_cell = -1
        self.wp_final = False
        self.grid_ver = -1
        self.lane = random.uniform(-0.45, 0.45)
        self.remaining = 1e9
        self.speed_now = 0.0
        # visuals
        self.rig = None
        self.skin = None
        self.node = None
        self.on_screen = True
        self.anim_dt = 0.0
        self.hit_flash = 0.0
        self.corpse_t = 0.0
        self.bob = random.uniform(0, 6.28)
        self._tint: tuple | None = None
        self._fx_t = random.uniform(0, 0.4)
        if match.visual:
            self._build_visual()
        self._update_remaining()

    # ================================================================== stats
    @property
    def armor(self) -> float:
        return self.base_armor - (self.poison_shred if self.poison_t > 0 else 0.0)

    @property
    def hp_frac(self) -> float:
        return self.hp / self.max_hp if self.max_hp > 0 else 0.0

    @property
    def slowed(self) -> bool:
        return self.slow_t > 0 or self.stun_t > 0

    @property
    def poisoned(self) -> bool:
        return self.poison_t > 0

    def center_y(self) -> float:
        return self.y + min(self.height * 0.55, 1.6)

    # ================================================================== status effects
    def apply_slow(self, frac: float, duration: float) -> None:
        eff = frac * (1.0 - self.slow_resist)
        if eff <= 0:
            return
        if self.slow_t <= 0:
            self.slow_f, self.slow_t = eff, duration
        elif eff >= self.slow_f:                 # a weaker slow never overrides or extends a stronger one
            self.slow_f = eff
            self.slow_t = max(self.slow_t, duration)

    def apply_stun(self, duration: float, freeze: bool = False) -> bool:
        """Stop the creep for ``duration`` seconds. Bosses are immune. Returns True if it took hold."""
        if self.boss:
            return False
        self.stun_t = max(self.stun_t, duration)
        self.frozen = self.frozen or freeze
        return True

    def apply_burn(self, dps: float, duration: float, source: Any = None) -> None:
        if dps >= self.burn_dps or self.burn_t <= 0:
            self.burn_dps = dps
            self.burn_src = source
            self.burn_t = max(self.burn_t, duration)

    def apply_poison(self, dps: float, duration: float, shred: float, source: Any = None, spread: float = 0.0) -> None:
        if dps >= self.poison_dps or self.poison_t <= 0:
            self.poison_dps = dps
            self.poison_shred = shred
            self.poison_src = source
            self.poison_spread = spread
            self.poison_t = max(self.poison_t, duration)

    # ================================================================== damage
    def take_damage(self, amount: float, source: Any = None, kind: str = "hit") -> float:
        """Apply already-mitigated damage. Returns the health actually removed."""
        if not self.alive or amount <= 0:
            return 0.0
        dealt = min(self.hp, amount)
        self.hp -= amount
        if source is not None:
            source.damage_dealt += dealt
        if kind == "hit":
            self.hit_flash = 0.12
        if self.hp <= 0:
            self.hp = 0.0
            self.alive = False
            self.match.on_creep_killed(self, source)
        return dealt

    def heal_by(self, amount: float) -> float:
        before = self.hp
        self.hp = min(self.max_hp, self.hp + amount)
        return self.hp - before

    # ================================================================== simulation
    def update(self, dt: float) -> None:
        if not self.alive:
            return
        m = self.match
        if self.poison_t > 0:
            self.poison_t -= dt
            self.take_damage(self.poison_dps * dt, self.poison_src, "dot")
            if not self.alive:
                return
            if self.poison_t <= 0:
                self.poison_dps = self.poison_shred = self.poison_spread = 0.0
        if self.burn_t > 0:
            self.burn_t -= dt
            self.take_damage(self.burn_dps * dt, self.burn_src, "dot")
            if not self.alive:
                return
            if self.burn_t <= 0:
                self.burn_dps = 0.0
        if self.regen and self.poison_t <= 0 and self.hp < self.max_hp:
            self.hp = min(self.max_hp, self.hp + self.regen * self.max_hp * dt)
        if self.heal:
            self.heal_t -= dt
            if self.heal_t <= 0:
                self.heal_t = float(self.heal[1])
                m.heal_pulse(self, float(self.heal[0]), float(self.heal[2]))
        if self.hit_flash > 0:
            self.hit_flash -= dt
        speed = self.base_speed
        if self.slow_t > 0:
            self.slow_t -= dt
            speed *= 1.0 - self.slow_f
            if self.slow_t <= 0:
                self.slow_f = 0.0
        if self.stun_t > 0:
            self.stun_t -= dt
            speed = 0.0
            if self.stun_t <= 0:
                self.frozen = False
        self.speed_now = speed
        if speed > 0:
            if self.flying:
                self._fly(speed * dt)
            else:
                self._walk(speed * dt)
        self._update_remaining()

    def _fly(self, step: float) -> None:
        ex, ez = self.match.level.exit_pos
        dx, dz = ex - self.x, ez - self.z
        d = math.hypot(dx, dz)
        if d <= step + 0.3:
            self.x, self.z = ex, ez
            self._leak()
            return
        self.x += dx / d * step
        self.z += dz / d * step
        self.face = yaw_to(dx, dz)

    def _walk(self, step: float) -> None:
        g = self.match.grid
        for _ in range(4):
            if step <= 1e-6:
                return
            if self.wp is None or self.grid_ver != g.version:
                self._pick_waypoint()
                if self.wp is None:
                    return
            wx, wz = self.wp
            dx, dz = wx - self.x, wz - self.z
            d = math.hypot(dx, dz)
            if d <= step:
                self.x, self.z = wx, wz
                step -= d
                if self.wp_final:
                    self._leak()
                    return
                self.wp = None
            else:
                self.x += dx / d * step
                self.z += dz / d * step
                self.face = yaw_to(dx, dz)
                return

    def _pick_waypoint(self) -> None:
        g = self.match.grid
        self.grid_ver = g.version
        c = g.cell_of(self.x, self.z)
        if c < 0 or g.dist[c] == INF:
            # pushed somewhere odd (should not happen): head for the closest cell that still has a route
            best, bd = -1, INF
            for k in range(g.n):
                if g.dist[k] != INF:
                    cx, cz = g.center(k)
                    d = (cx - self.x) ** 2 + (cz - self.z) ** 2
                    if d < bd:
                        best, bd = k, d
            if best < 0:
                self.wp = None
                return
            self.wp, self.wp_cell, self.wp_final = g.center(best), best, g.dist[best] == 0
            return
        if g.dist[c] == 0:
            self.wp, self.wp_cell, self.wp_final = g.center(c), c, True
            return
        best = g.next[c]
        k = best
        for _ in range(LOOKAHEAD - 1):
            nk = g.next[k]
            if nk == k:
                break
            cx, cz = g.center(nk)
            if not g.line_clear(self.x, self.z, cx, cz, LOS_MARGIN):
                break
            best = k = nk
        tx, tz = g.center(best)
        final = g.dist[best] == 0
        if not final and self.lane:
            dx, dz = tx - self.x, tz - self.z
            d = math.hypot(dx, dz)
            if d > 1e-3:
                ox, oz = tx - dz / d * self.lane, tz + dx / d * self.lane
                if g.cell_of(ox, oz) == best and g.line_clear(self.x, self.z, ox, oz, 0.0):
                    tx, tz = ox, oz
        self.wp, self.wp_cell, self.wp_final = (tx, tz), best, final

    def _update_remaining(self) -> None:
        if self.flying:
            ex, ez = self.match.level.exit_pos
            self.remaining = math.hypot(ex - self.x, ez - self.z)
        elif self.wp is not None and self.wp_cell >= 0:
            d = self.match.grid.dist[self.wp_cell]
            self.remaining = (d * CELL if d != INF else 999.0) + math.hypot(self.wp[0] - self.x, self.wp[1] - self.z)
        else:
            c = self.match.grid.cell_of(self.x, self.z)
            d = self.match.grid.dist[c] if c >= 0 else INF
            self.remaining = d * CELL if d != INF else 999.0

    def _leak(self) -> None:
        self.alive = False
        self.leaked = True
        self.match.on_leak(self)

    # ================================================================== visuals
    def _build_visual(self) -> None:
        from panda3d.core import BoundingSphere, Point3, TransparencyAttrib
        from creeps.models import build_creep_rig
        from creeps.skinning import Skin
        m = self.match
        self.rig = build_creep_rig(self.defn["model"])
        self.node = m.creep_root.attachNewNode(f"creep_{self.type_id}")
        self.rig.root.reparentTo(self.node)
        try:
            self.skin = Skin(self.rig, (self.type_id,))
        except ValueError:
            self.skin = None
        r = max(self.rig.height, self.rig.radius * 2.0) * 0.8 + 0.6
        self.node.node().setBounds(BoundingSphere(Point3(0, self.rig.height * 0.5, 0), r))
        self.node.node().setFinal(True)
        self.alpha = 1.0
        if getattr(self.rig, "translucent", False):
            self.alpha = 0.72
            self.node.setTransparency(TransparencyAttrib.M_alpha)
            self.node.setBin("transparent", 8)
        self.sync_node(0.0)

    def sync_node(self, dt: float) -> None:
        """Move the scene node to the simulated position (with smooth turning and flight bobbing)."""
        node = self.node
        if node is None:
            return
        m = self.match
        ground = m.level.height(self.x, self.z)
        if self.alive:
            self.yaw = damp_angle(self.yaw, self.face, 9.0, dt) if dt > 0 else self.face
            if self.flying:
                self.y = ground + FLY_HEIGHT + math.sin(m.time * 2.2 + self.bob) * 0.25
            else:
                self.y = ground
        elif self.flying:
            self.y = max(ground, self.y - dt * 9.0)       # shot down: drop out of the sky
        else:
            self.y = ground - max(0.0, self.corpse_t - (CORPSE_TIME - 0.6)) * 1.2
        node.setPos(self.x, self.y, self.z)
        node.setH(-self.yaw)
        if not self.alive:
            tint = (0.7, 0.68, 0.72)                      # a corpse: no status glow, no hit flash
        elif self.frozen:
            tint = (0.6, 0.85, 1.55)
        elif self.slow_t > 0:
            tint = (0.72, 0.88, 1.3)
        elif self.poison_t > 0:
            tint = (0.72, 1.22, 0.62)
        elif self.burn_t > 0:
            tint = (1.3, 0.86, 0.62)
        else:
            tint = (1.0, 1.0, 1.0)
        if self.hit_flash > 0 and self.alive:
            k = 1.0 + 1.6 * self.hit_flash / 0.12
            tint = (tint[0] * k, tint[1] * k, tint[2] * k)
        if tint != self._tint:
            self._tint = tint
            node.setColorScale(tint[0], tint[1], tint[2], self.alpha)

    def animate(self, dt: float) -> None:
        if self.rig is None:
            return
        moving = self.speed_now if self.alive else 0.0
        if self.frozen and self.alive:
            return                                         # frozen solid: hold the pose
        self.rig.update(dt, moving * 1.6 if moving > 0.2 else 0.0)
        if self.skin is not None:
            self.skin.update()

    def die_visual(self) -> None:
        if self.rig is not None:
            self.rig.die()
        self.corpse_t = 0.0

    def destroy(self) -> None:
        if self.rig is not None:
            self.rig.destroy()
            self.rig = None
        if self.node is not None:
            self.node.removeNode()
            self.node = None
