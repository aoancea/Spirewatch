"""The player's three powers: Meteor, Frost Nova and Overcharge. Free to cast, limited by cooldown."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from core import data
from core.events import events

if TYPE_CHECKING:
    from core.match import Match


class Spells:
    """Cooldowns and effects of the player powers."""

    def __init__(self, match: "Match") -> None:
        self.match = match
        self.defs: dict[str, dict[str, Any]] = data.spells()
        self.order = list(self.defs)
        self.cd: dict[str, float] = {sid: 0.0 for sid in self.defs}
        self.pending: list[tuple[float, float, float]] = []      # meteors in flight: (time left, x, z)
        self.casts = 0

    def ready(self, sid: str) -> bool:
        return self.cd.get(sid, 1.0) <= 0.0 and not self.match.over

    def cooldown(self, sid: str) -> tuple[float, float]:
        return max(0.0, self.cd[sid]), float(self.defs[sid]["cooldown"])

    def meteor_damage(self) -> float:
        a, b, c = self.defs["meteor"]["damage"]
        w = max(1, self.match.waves.number)
        return a + b * w + c * w * w

    def cast(self, sid: str, x: float, z: float) -> str | None:
        """Cast at a ground point. Returns an error message, or None on success."""
        m = self.match
        if sid not in self.defs:
            return "Unknown power"
        if not self.ready(sid):
            return f"{self.defs[sid]['name']} is not ready"
        d = self.defs[sid]
        self.cd[sid] = float(d["cooldown"])
        self.casts += 1
        m.stats["spells"] += 1
        radius = float(d["radius"])
        if sid == "meteor":
            self.pending.append((float(d["delay"]), x, z))
            m.fx.meteor_called(x, z, radius, float(d["delay"]))
            events.emit("sound", name="meteor", pos=(x, z))
        elif sid == "frost_nova":
            freeze = float(d["freeze"])
            frac, dur = d["slow"]
            for c in m.combat.arr.within(x, z, radius):
                c.apply_stun(freeze, freeze=True)
                c.apply_slow(float(frac), freeze + float(dur))
            m.fx.nova(x, z, radius)
            events.emit("sound", name="nova", pos=(x, z))
        elif sid == "overcharge":
            hit = [t for t in m.towers if math.hypot(t.x - x, t.z - z) <= radius]
            for t in hit:
                t.overcharge_t = float(d["duration"])
            m.fx.overcharge(x, z, radius, hit)
            events.emit("sound", name="overcharge", pos=(x, z))
        events.emit("spell_cast", spell=sid, x=x, z=z)
        return None

    def update(self, dt: float) -> None:
        for sid in self.cd:
            if self.cd[sid] > 0:
                self.cd[sid] -= dt
        if not self.pending:
            return
        m = self.match
        d = self.defs["meteor"]
        keep = []
        for t, x, z in self.pending:
            t -= dt
            if t > 0:
                keep.append((t, x, z))
                continue
            dmg = self.meteor_damage()
            radius = float(d["radius"])
            edge = m.combat.splash_edge
            for c in m.combat.arr.within(x, z, radius):
                dist = math.hypot(c.x - x, c.z - z)
                m.combat.damage(c, dmg * (1.0 - (1.0 - edge) * min(1.0, dist / radius)), d.get("attack_type", "blast"))
                if c.alive:
                    c.apply_burn(dmg * float(d.get("burn_frac", 0.1)), float(d.get("burn_time", 3.0)))
            m.fx.meteor_impact(x, z, radius)
            events.emit("sound", name="explosion", pos=(x, z))
        self.pending = keep
