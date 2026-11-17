"""Wave schedule: what spawns when, how tough it is, what it pays, and when the next wave is due."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from core import data
from core.events import events

if TYPE_CHECKING:
    from core.match import Match


class _Spawner:
    """Releases the creeps of one wave over time."""

    def __init__(self, mgr: "WaveManager", number: int, wdef: dict[str, Any]) -> None:
        self.mgr = mgr
        self.number = number
        self.unit_hp = mgr.unit_hp(number)
        self.groups = [{"creep": g["creep"], "left": int(g["count"]), "interval": float(g.get("interval", 1.0)),
                        "t": float(g.get("delay", 0.0))} for g in wdef["groups"]]
        self.bounty = mgr.bounties(number, wdef)
        self.k = 0

    @property
    def done(self) -> bool:
        return all(g["left"] <= 0 for g in self.groups)

    def update(self, dt: float) -> None:
        m = self.mgr.match
        gates = len(m.grid.gates)
        for g in self.groups:
            if g["left"] <= 0:
                continue
            g["t"] -= dt
            while g["t"] <= 0 and g["left"] > 0:
                g["t"] += g["interval"]
                g["left"] -= 1
                cdef = data.creeps()[g["creep"]]
                gate = 0 if cdef.get("boss") else self.k % gates
                self.k += 1
                m.spawn_creep(g["creep"], self.unit_hp, self.bounty.get(g["creep"], 1), gate, self.number)


class WaveManager:
    """Owns the wave counter, the countdown between waves and the per-wave bookkeeping."""

    def __init__(self, match: "Match", endless: bool = False) -> None:
        cfg = data.waves()
        self.match = match
        self.cfg = cfg
        self.defs: list[dict[str, Any]] = cfg["waves"]
        self.curve: dict[str, list[float]] = cfg["curve"]
        self.total = len(self.defs)
        self.endless = endless
        self.number = 0                      # waves started so far
        self.countdown = float(cfg.get("first_delay", 20))
        self.countdown_full = self.countdown
        self.running: list[_Spawner] = []
        self.alive: dict[int, int] = {}      # wave number -> creeps of that wave still on the field
        self.cleared: set[int] = set()
        self.finished = False

    # ------------------------------------------------------------------ definitions
    def wave_def(self, number: int) -> dict[str, Any]:
        if number <= self.total:
            return self.defs[number - 1]
        cycle = self.cfg["endless"]["cycle"]
        return cycle[(number - self.total - 1) % len(cycle)]

    def base_hp(self, number: int) -> float:
        a, b, c = self.curve["hp"]
        n = number - 1
        hp = a * (1.0 + b * n + c * n * n)
        if number > self.total:
            hp *= float(self.cfg["endless"].get("growth", 1.1)) ** (number - self.total)
        return hp

    def unit_hp(self, number: int) -> float:
        m = self.match
        diff = float(m.diff["hp"]) + float(m.diff.get("hp_ramp", 0.0)) * (number - 1)
        return self.base_hp(number) * diff * m.level.hp_mult

    def kill_gold(self, number: int) -> float:
        b0, b1 = self.curve["bounty"]
        return (b0 + b1 * min(number, self.total + 10)) * float(self.match.diff["bounty"])

    def clear_gold(self, number: int) -> int:
        c0, c1 = self.curve["clear"]
        return int(round((c0 + c1 * min(number, self.total + 10)) * float(self.match.diff["bounty"])))

    def bounties(self, number: int, wdef: dict[str, Any]) -> dict[str, int]:
        """Gold per kill for each creep type of the wave (the wave's kill gold shared by health weight)."""
        creeps = data.creeps()
        weights = {}
        total = 0.0
        for g in wdef["groups"]:
            c = creeps[g["creep"]]
            w = float(c.get("bounty", c.get("hp", 1.0)))
            weights[g["creep"]] = w
            total += w * int(g["count"])
        pool = self.kill_gold(number)
        return {cid: max(1, int(round(pool * w / total))) for cid, w in weights.items()} if total > 0 else {}

    def info(self, number: int) -> dict[str, Any] | None:
        """Summary of a wave for the HUD preview, or None past the end of a campaign."""
        if number < 1 or (number > self.total and not self.endless):
            return None
        wdef = self.wave_def(number)
        creeps = data.creeps()
        unit = self.unit_hp(number)
        groups = [(g["creep"], int(g["count"])) for g in wdef["groups"]]
        lead = max(groups, key=lambda g: (bool(creeps[g[0]].get("boss")), creeps[g[0]].get("hp", 1.0) * g[1]))[0]
        tags: list[str] = []
        for cid, _ in groups:
            for t in data.creep_tags(cid):
                if t not in tags:
                    tags.append(t)
        if sum(n for _, n in groups) >= 24 and "Swarm" not in tags:
            tags.append("Swarm")
        names = " + ".join(f"{n} {creeps[cid]['name']}" for cid, n in groups)
        return {"number": number, "name": wdef.get("name") or names, "groups": groups, "lead": lead, "tags": tags,
                "boss": any(creeps[cid].get("boss") for cid, _ in groups),
                "air": any(creeps[cid].get("flying") for cid, _ in groups),
                "hp": {cid: unit * float(creeps[cid].get("hp", 1.0)) for cid, _ in groups},
                "detail": names}

    # ------------------------------------------------------------------ state
    @property
    def has_next(self) -> bool:
        return self.endless or self.number < self.total

    @property
    def in_progress(self) -> bool:
        return bool(self.running) or any(v > 0 for v in self.alive.values())

    def creep_added(self, wave: int) -> None:
        self.alive[wave] = self.alive.get(wave, 0) + 1

    def creep_removed(self, wave: int) -> None:
        self.alive[wave] = self.alive.get(wave, 0) - 1

    def can_send(self) -> bool:
        return self.has_next and not self.running and not self.match.over

    def early_bonus(self) -> int:
        """Gold for calling the next wave now instead of waiting out the countdown."""
        if not self.can_send() or self.number == 0:
            return 0
        return int(math.ceil(max(0.0, self.countdown) * float(self.cfg.get("early_bonus", 0.5))))

    def send_now(self) -> bool:
        if not self.can_send():
            return False
        bonus = self.early_bonus()
        if bonus:
            self.match.add_gold(bonus, reason="early")
        self.start_next()
        return True

    def start_next(self) -> None:
        self.number += 1
        n = self.number
        wdef = self.wave_def(n)
        self.running.append(_Spawner(self, n, wdef))
        self.alive.setdefault(n, 0)
        self.countdown = self.countdown_full = float(self.match.diff["countdown"])
        info = self.info(n)
        events.emit("wave_started", number=n, info=info)

    def update(self, dt: float) -> None:
        m = self.match
        if m.over:
            return
        for sp in self.running:
            sp.update(dt)
        if self.running:
            self.running = [sp for sp in self.running if not sp.done]
        spawning = {sp.number for sp in self.running}
        for n in range(1, self.number + 1):
            if n not in self.cleared and n not in spawning and self.alive.get(n, 0) <= 0:
                self.cleared.add(n)
                m.on_wave_cleared(n)
        if self.has_next:
            if not self.in_progress:
                if m.auto_send and self.number > 0 and self.countdown > 3.0:
                    self.countdown = 3.0
                self.countdown -= dt
                if self.countdown <= 0:
                    self.start_next()
        elif not self.finished and not self.in_progress and len(self.cleared) >= self.number:
            self.finished = True
            m.on_all_waves_cleared()
