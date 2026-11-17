"""A simple computer player. It plays the menu backdrop and stands in for "a competent but unremarkable
player" in the balance simulator.

Like a player reading the wave preview, it values each possible purchase by how much it would hurt the next
few waves (attack type against armor type, whether it can reach flyers, area damage against swarms), divides
by the price and buys the best deal it can afford. Towers go where they cover the most road. With
`use_powers` it also drops meteors on packs, freezes creeps that get close to the Heartfire and overcharges
its busiest towers. It never calls a wave early.
"""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from combat.combat import armor_factor
from core import data
from settings import MAX_LEVEL

if TYPE_CHECKING:
    from core.match import Match
    from towers.tower import Tower

LOOKAHEAD = ((1, 1.0), (2, 0.6), (3, 0.35))       # (waves ahead, weight)


class Bot:
    """Greedy value-per-gold buyer."""

    def __init__(self, match: "Match", skill: float = 1.0, use_powers: bool = False) -> None:
        self.m = match
        self.skill = skill
        self.use_powers = use_powers
        self.t = 0.0
        self.maze = [match.grid.index(c, r) for c, r in match.level.defn.get("bot_maze", [])]
        self.k = float(data.combat().get("armor_k", 0.06))
        self.mult: dict[str, dict[str, float]] = data.combat()["multipliers"]

    # ------------------------------------------------------------------ road geometry
    def _routes(self) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
        g = self.m.grid
        route = [g.center(c) for c in g.all_route_cells()]
        air: list[tuple[float, float]] = []
        ex, ez = self.m.level.exit_pos
        for gi in self.m.level.gate_info:
            gx, gz = gi["pos"]
            n = max(2, int(math.hypot(ex - gx, ez - gz) / 2.0))
            air += [(gx + (ex - gx) * k / n, gz + (ez - gz) * k / n) for k in range(n + 1)]
        return route, air

    def _pressure(self, route: list[tuple[float, float]]) -> list[float]:
        """Firepower (in hundreds of gold) already covering each road cell."""
        out = [0.0] * len(route)
        for t in self.m.towers:
            if t.kind == "aura":
                continue
            r2 = t.range ** 2
            w = t.invested / 100.0
            for i, (px, pz) in enumerate(route):
                if (px - t.x) ** 2 + (pz - t.z) ** 2 <= r2:
                    out[i] += w
        return out

    def _coverage(self, type_id: str, cell: int, route: list[tuple[float, float]], air: list[tuple[float, float]],
                  pressure: list[float] | None = None, want_air: float = 0.3) -> float:
        """How much road a tower of this type would cover from ``cell``."""
        m = self.m
        st = data.tower_level(type_id, 1)
        x, z = m.grid.center(cell)
        r = float(st.get("range", 5.0))
        if type_id == "banner":
            score = 0.0
            for t in m.towers:
                d2 = (t.x - x) ** 2 + (t.z - z) ** 2
                if t.kind != "aura" and d2 <= r * r:
                    score += t.invested / 60.0
                elif t.kind == "aura" and d2 <= (r * 1.5) ** 2:
                    score -= 6.0            # do not stack banners on top of each other
            return score
        r2 = r * r
        score = 0.0
        for i, (px, pz) in enumerate(route):
            if (px - x) ** 2 + (pz - z) ** 2 <= r2:
                p = pressure[i] if pressure is not None else 0.0
                score += (0.6 + 0.4 * min(p, 3.0)) if type_id == "frost" else 1.0 / (1.0 + 0.12 * p)
        if "air" in data.towers()[type_id].get("targets", []):
            score += want_air * sum(1 for px, pz in air if (px - x) ** 2 + (pz - z) ** 2 <= r2)
        return score

    def _best_free_cell(self, type_id: str) -> int:
        """Best cell ignoring the maze plan (banners go next to the towers, not into the wall)."""
        g = self.m.grid
        route, air = self._routes()
        best, bs = -1, -1e9
        for c in range(g.n):                 # terraces first: they can never block the road
            if g.can_build(c) and not g.base_walk[c]:
                s = self._coverage(type_id, c, route, air)
                if s > bs:
                    best, bs = c, s
        if best < 0:
            for c in range(g.n):
                if g.can_build(c):
                    s = self._coverage(type_id, c, route, air)
                    if s > bs and self.m.can_place(type_id, c, check_gold=False)[0]:
                        best, bs = c, s
        return best

    def best_cell(self, type_id: str, want_air: float = 0.3) -> int:
        m = self.m
        g = m.grid
        # on maze maps the planned wall comes first: every early tower is also a piece of the maze
        while self.maze:
            c = self.maze[0]
            if c < 0 or g.tower[c] is not None:
                self.maze.pop(0)
                continue
            if m.can_place(type_id, c, check_gold=False)[0]:
                return c
            break
        route, air = self._routes()
        pressure = self._pressure(route)
        best, bs = -1, -1e9
        for c in range(g.n):
            if not g.can_build(c):
                continue
            s = self._coverage(type_id, c, route, air, pressure, want_air)
            if g.base_walk[c]:
                s -= 0.5                     # prefer not to reshape the maze by accident
            if s > bs:
                best, bs = c, s
        if best >= 0 and g.base_walk[best] and not m.can_place(type_id, best, check_gold=False)[0]:
            return -1
        return best

    # ------------------------------------------------------------------ valuing purchases
    def _threat(self) -> list[tuple[float, dict[str, Any], float]]:
        """(weight, creep definition, share of the wave's health) for the waves coming up."""
        w = self.m.waves
        creeps = data.creeps()
        out = []
        for ahead, weight in LOOKAHEAD:
            info = w.info(w.number + ahead)
            if info is None:
                continue
            total = sum(info["hp"][cid] * n for cid, n in info["groups"]) or 1.0
            swarm = sum(n for _, n in info["groups"]) >= 20
            for cid, n in info["groups"]:
                c = dict(creeps[cid])
                c["_swarm"] = swarm
                out.append((weight, c, info["hp"][cid] * n / total))
        return out

    def power(self, type_id: str, level: int, threat: list[tuple[float, dict[str, Any], float]]) -> float:
        """Rough killing power of one tower against the upcoming waves (arbitrary units)."""
        tdef = data.towers()[type_id]
        st = tdef["levels"][level - 1]
        kind = tdef["kind"]
        if kind == "aura":
            return 0.0
        dmg = float(st.get("damage", 0.0))
        cd = float(st.get("cooldown", 1.0))
        if kind == "beam":
            base = dmg * (1.0 + float(st["ramp"][0])) * 0.5
        else:
            crit = st.get("crit")
            base = dmg * (1.0 + crit[0] * (crit[1] - 1.0) if crit else 1.0) / cd
        targets = tdef.get("targets", [])
        atk = tdef.get("attack_type", "pierce")
        total = 0.0
        for weight, c, share in threat:
            flying = bool(c.get("flying"))
            if flying and "air" not in targets or not flying and "ground" not in targets:
                continue
            armor = float(c.get("armor", 0))
            p = base * float(self.mult[atk].get(c["armor_type"], 1.0)) * armor_factor(armor * (1.0 - float(st.get("armor_pierce", 0.0))), self.k)
            area = 1.0
            if st.get("splash"):
                area = 2.4 if c["_swarm"] else 1.25
            if st.get("chain"):
                area = 1.0 + 0.4 * (float(st["chain"][0]) - 1.0)
            if kind == "cone":
                area = 2.4 if c["_swarm"] else 1.7
                p += float(st["burn"][0]) * float(self.mult["elemental"].get(c["armor_type"], 1.0)) * 0.8
            if st.get("multishot"):
                area = max(area, float(st["multishot"]) * 0.85)
            if c.get("boss"):
                area = 1.0
            p *= area
            if st.get("poison"):
                p += float(st["poison"][0]) * min(float(st["poison"][1]) / cd, 3.0) + float(st["poison"][2]) * 1.2
            if st.get("slow"):
                p += 5.0 + 22.0 * float(st["slow"][0]) * (1.0 - float(c.get("slow_resist", 0.5 if c.get("boss") else 0.0)))
            if flying and st.get("air_bonus"):
                p *= 1.0 + float(st["air_bonus"])
            if c.get("boss") and kind == "beam":
                p *= 1.6
            total += weight * share * p
        return total * math.sqrt(float(st.get("range", 6.0)) / 6.5)

    def _options(self) -> list[tuple[float, int, str, Any]]:
        """Every purchase worth considering as (value per gold, cost, action, argument), best first."""
        m = self.m
        threat = self._threat()
        if not threat:
            return []
        counts: dict[str, int] = {}
        for t in m.towers:
            counts[t.type_id] = counts.get(t.type_id, 0) + 1
        n_towers = len(m.towers)
        fighters = n_towers - counts.get("banner", 0)
        out: list[tuple[float, int, str, Any]] = []
        crowded = 0.85 if n_towers >= 14 else 1.0
        route, air = self._routes()
        strength = {t: self.power(t.type_id, t.level, threat) * min(1.4, max(0.5, self._coverage(t.type_id, t.cell, route, air) / 9.0))
                    for t in m.towers if t.kind != "aura"}

        def boosted(x: float, z: float, radius: float, bonus: float) -> float:
            """Firepower a banner at (x, z) would add: only what its aura improves counts."""
            return sum(p * max(0.0, bonus - t.buff_damage) for t, p in strength.items()
                       if (t.x - x) ** 2 + (t.z - z) ** 2 <= radius * radius)

        for tid, tdef in data.towers().items():
            cost = int(tdef["levels"][0]["cost"])
            have = counts.get(tid, 0)
            if tid == "banner":
                if fighters >= 5:
                    cell = self._best_free_cell(tid)
                    if cell >= 0:
                        x, z = m.grid.center(cell)
                        aura = tdef["levels"][0]["aura"]
                        out.append((boosted(x, z, float(tdef["levels"][0]["range"]), float(aura.get("damage", 0.0))) / cost, cost, "build", tid))
                continue
            v = self.power(tid, 1, threat) / cost
            v *= crowded / (1.0 + 0.18 * have)                 # variety: each extra copy is worth a little less
            if tid == "frost" and have >= max(1, fighters // 5):
                v *= 0.35
            if tid == "ember" and have >= 2 + fighters // 6:
                v *= 0.4                                        # only so many good corners for a short-range jet
            out.append((v, cost, "build", tid))
        for t in m.towers:
            if t.level >= MAX_LEVEL or t.state != "ready":
                continue
            cost = t.upgrade_cost()
            if t.kind == "aura":
                nxt = t.defn["levels"][t.level]
                now, new = t.stats.get("aura", {}), nxt.get("aura", {})
                gain = sum(p * ((float(new.get("damage", 0)) + float(new.get("rate", 0))) - (float(now.get("damage", 0)) + float(now.get("rate", 0))))
                           for o, p in strength.items() if (o.x - t.x) ** 2 + (o.z - t.z) ** 2 <= float(nxt["range"]) ** 2)
                out.append((gain / cost, cost, "upgrade", t))
                continue
            gain = self.power(t.type_id, t.level + 1, threat) - self.power(t.type_id, t.level, threat)
            cov = self._coverage(t.type_id, t.cell, route, air) / 9.0
            out.append((gain / cost * min(1.4, max(0.5, cov)) * 1.12, cost, "upgrade", t))
        out.sort(key=lambda o: -o[0])
        w = m.waves
        self._air_next = any(info is not None and info["air"] for info in (w.info(w.number + 1), w.info(w.number + 2)))
        return out

    # ------------------------------------------------------------------ decisions
    def update(self, dt: float) -> None:
        self.t -= dt
        if self.t > 0:
            return
        self.t = 0.4
        m = self.m
        if m.over:
            return
        for _ in range(4):                   # a few purchases per think so it keeps up at high game speed
            if not self._act():
                break
        if self.use_powers:
            self._cast()

    def _cast(self) -> None:
        """Use whichever power is ready and clearly worth it right now."""
        m = self.m
        sp = m.spells
        arr = m.combat.arr
        if arr.n == 0:
            return
        creeps = [c for c in arr.creeps if c.alive]
        if not creeps:
            return
        sample = creeps[:: max(1, len(creeps) // 14)]
        if sp.ready("meteor"):
            dmg = sp.meteor_damage()
            radius = float(sp.defs["meteor"]["radius"])
            best, score = None, 0.0
            for c in sample:
                s = sum(min(o.hp, dmg) for o in arr.within(c.x, c.z, radius))
                if s > score:
                    best, score = c, s
            if best is not None and score >= dmg * 2.5:
                sp.cast("meteor", best.x, best.z)
        if sp.ready("frost_nova"):
            radius = float(sp.defs["frost_nova"]["radius"])
            lead = min(creeps, key=lambda c: c.remaining)
            if lead.remaining < 26.0:
                near = arr.within(lead.x, lead.z, radius)
                if len(near) >= 3 or lead.boss or lead.hp > 0.5 * lead.max_hp and m.lives <= lead.lives:
                    sp.cast("frost_nova", lead.x, lead.z)
        if sp.ready("overcharge") and m.towers:
            radius = float(sp.defs["overcharge"]["radius"])
            busy = [t for t in m.towers if t.firing > 0 and t.kind != "aura"]
            if len(busy) >= 4 and (len(creeps) >= 8 or any(c.boss for c in creeps)):
                hub = max(busy, key=lambda t: sum(1 for o in busy if (o.x - t.x) ** 2 + (o.z - t.z) ** 2 <= radius * radius))
                sp.cast("overcharge", hub.x, hub.z)

    def _act(self) -> bool:
        m = self.m
        options = self._options()
        if not options:
            return False
        best_value, best_cost = options[0][0], options[0][1]
        saving = best_cost > m.gold and m.gold >= 0.6 * best_cost       # nearly there: hold on for the best deal
        for value, cost, action, arg in options:
            if cost > m.gold:
                continue
            if value < best_value * (0.8 if saving else 0.4) * self.skill:
                return False
            if action == "upgrade":
                if m.upgrade_tower(arg) is None:
                    return True
                continue
            if arg == "banner":
                cell = self._best_free_cell(arg)
            else:
                # with flyers due, anything that can shoot up goes where it covers their straight flight line
                cell = self.best_cell(arg, want_air=(2.2 if arg == "ballista" else 1.5) if self._air_next else 0.3)
            if cell >= 0 and m.build_tower(arg, cell) is not None:
                return True
        return False
