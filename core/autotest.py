"""Scripted smoke tests: drive the real game for a while, take screenshots, log checks, quit.

Run with ``python main.py --autotest t1`` (see SCRIPTS below). Screenshots go to screenshots/autotest.
"""
from __future__ import annotations

import json
import math
import traceback
from typing import Any

from panda3d.core import Filename
from ursina import application, held_keys

from core import data
from core.match import MatchConfig
from settings import SCREENSHOT_DIR

Step = tuple
MENU_SCRIPTS = {"menu"}         # scripts that begin at the main menu instead of inside a match

CONFIGS: dict[str, dict[str, Any]] = {
    "t1": {"map": "emberfall_road"}, "t2": {"map": "emberfall_road"}, "t3": {"map": "hollow_moor"},
    "t4": {"map": "twin_rifts"}, "ui": {"map": "emberfall_road"}, "perf": {"map": "emberfall_road"}, "tips": {"map": "emberfall_road"},
    "fx": {"map": "emberfall_road"},
    "show": {"map": "emberfall_road"}, "shots": {"map": "emberfall_road"}, "shots2": {"map": "twin_rifts"},
    "shots3": {"map": "hollow_moor"}, "bot": {"map": "emberfall_road"}, "bot2": {"map": "twin_rifts"},
    "bot3": {"map": "hollow_moor"},
}


def config_for(name: str) -> MatchConfig:
    """Match setup used by each test."""
    spec = CONFIGS.get(name, CONFIGS.get(name.split("_")[0], {}))
    return MatchConfig(map_id=spec.get("map", "emberfall_road"), difficulty=spec.get("difficulty", "normal"),
                       endless=spec.get("endless", False), seed=spec.get("seed", 1))


class AutoTest:
    """Executes a list of timed steps against the running game."""

    def __init__(self, game, name: str) -> None:
        self.game = game
        self.name = name
        self.steps: list[Step] = list(SCRIPTS[name](game))
        self.t = 0.0
        self.next_at = 0.0
        self.log: list[str] = []
        self.out = SCREENSHOT_DIR / "autotest"
        self.out.mkdir(parents=True, exist_ok=True)
        self.released: list[tuple[float, str]] = []
        self.failures = 0
        self._until_start: float | None = None
        if game.match is not None:
            game.match.cam.edge_scroll = False   # the OS cursor may rest on a window edge during tests

    def say(self, msg: str) -> None:
        print(f"[autotest] {msg}", flush=True)
        self.log.append(msg)

    def update(self, dt: float) -> None:
        self.t += dt
        for at, key in list(self.released):
            if self.t >= at:
                held_keys[key] = 0
                self.released.remove((at, key))
        while self.steps and self.t >= self.next_at:
            step = self.steps.pop(0)
            try:
                self._run(step)
            except Exception:
                self.failures += 1
                self.say("STEP FAILED " + repr(step[:1]) + "\n" + traceback.format_exc())

    def _run(self, step: Step) -> None:
        g = self.game
        m = g.match
        op = step[0]
        if op == "wait":
            self.next_at = self.t + step[1]
        elif op == "until":
            pred, timeout = step[1], step[2]
            if self._until_start is None:
                self._until_start = self.t
            if not pred(g) and self.t - self._until_start < timeout:
                self.steps.insert(0, step)
                self.next_at = self.t + 0.2
            else:
                waited = self.t - self._until_start
                self._until_start = None
                if len(step) > 3:
                    self.say(f"until '{step[3]}' after {waited:.1f}s")
        elif op == "time":
            m.env.hour = float(step[1])
        elif op == "cam":
            m.cam.look_at(step[1], step[2])
            if len(step) > 3:
                m.cam.distance = m.cam.target_distance = step[3]
            m.cam.pitch_override = step[4] if len(step) > 4 else None
            m.cam.apply()
        elif op == "shot":
            path = self.out / f"{step[1]}.png"
            application.base.win.saveScreenshot(Filename.fromOsSpecific(str(path)))
            self.say(f"screenshot {path.name}")
        elif op == "hold":
            held_keys[step[1]] = 1
            self.released.append((self.t + step[2], step[1]))
        elif op == "press":
            from ursina.main import keyboard_keys
            key = step[1]
            if len(key) == 1 and key in keyboard_keys:
                g.app.input(key, is_raw=True)
            else:
                g.app.input(key)
        elif op == "speed":
            g.fast_forward = float(step[1])
        elif op == "call":
            result = step[1](g)
            if result is not None:
                self.say(f"{step[2] if len(step) > 2 else 'call'}: {result}")
        elif op == "check":
            ok = bool(step[1](g))
            if not ok:
                self.failures += 1
            self.say(f"CHECK {'OK ' if ok else 'FAIL'} {step[2]}")
        elif op == "fps":
            self.say(f"avg fps: {g.average_fps():.1f}  sim+present ms: {g.average_sim_ms():.2f}  creeps: {len(m.creeps)}  towers: {len(m.towers)}")
        elif op == "quit":
            self.say(f"finished with {self.failures} failure(s)")
            (self.out / f"{self.name}_log.json").write_text(json.dumps(self.log, indent=1), encoding="utf-8")
            application.quit()
        else:
            raise ValueError(f"unknown step {op}")


# ====================================================================== helpers
def _hold_waves(g) -> None:
    """Stop the wave timer so a test controls exactly what is on the field."""
    g.match.waves.countdown = 1e9


def _spawn(g, type_id: str, n: int = 1, hp: float = 100.0, gate: int = 0, at: tuple[float, float] | None = None,
           spacing: float = 1.3, still: bool = False) -> list:
    m = g.match
    unit = hp / float(data.creeps()[type_id].get("hp", 1.0))
    out = []
    for k in range(n):
        pos = None if at is None else (at[0] + k * spacing, at[1])
        c = m.spawn_creep(type_id, unit, 1, gate, 0, at=pos)
        if still:
            c.base_speed = 0.0
            c.face = c.yaw = 180.0
        out.append(c)
    return out


def _cell(g, col: int, row: int) -> int:
    return g.match.grid.index(col, row)


def _build(g, type_id: str, col: int, row: int, level: int = 1, instant: bool = True):
    m = g.match
    t = m.build_tower(type_id, _cell(g, col, row), free=True, instant=instant)
    if t is None:
        return None
    for _ in range(level - 1):
        t.begin_upgrade()
        t.busy_t = 0.0
        t.update(0.0)
    return t


# ====================================================================== scripts
def _t1(g) -> list[Step]:
    """Map, terrain, pathing along the road, leaks, flyers."""
    st: dict[str, Any] = {}

    def walkers(g):
        st["walk"] = _spawn(g, "gloomling", 5, hp=1e6)
        st["rem0"] = st["walk"][0].remaining

    def runners(g):
        st["lives0"] = g.match.lives
        st["fast"] = _spawn(g, "stalker", 2, hp=1e6)
        for c in st["fast"]:
            c.base_speed = 60.0

    def flyer(g):
        st["bat"] = _spawn(g, "gloomwing", 1, hp=1e6)[0]
        st["bat"].base_speed = 40.0
        st["lives1"] = g.match.lives

    return [
        ("call", _hold_waves), ("wait", 1.0),
        ("call", lambda g: (g.match.grid.w, g.match.grid.h, len(g.match.grid.gates), round(g.match.grid.route_length(0), 1),
                            g.match.level.tree_count), "grid w / h / gates / road length / trees"),
        ("check", lambda g: abs(g.match.grid.route_length(0) - 144.0) < 1.0, "road from the rift to the Heartfire is 144 units"),
        ("shot", "t1_01_overview"),
        ("cam", -24, 12, 24), ("wait", 0.4), ("shot", "t1_02_rift_gate"),
        ("call", walkers), ("wait", 5.0),
        ("check", lambda g: st["walk"][0].remaining < st["rem0"] - 8, "creeps walk down the road"),
        ("check", lambda g: all(g.match.grid.walkable_at(c.x, c.z) for c in st["walk"]), "creeps stay on walkable cells"),
        ("shot", "t1_03_creeps_on_road"),
        ("call", runners),
        ("until", lambda g: not any(c.alive for c in st["fast"]), 20, "fast creeps reach the Heartfire"),
        ("check", lambda g: g.match.lives == st["lives0"] - 2, "each leak costs a life"),
        ("call", flyer),
        ("until", lambda g: not st["bat"].alive, 15, "flyer crosses the map"),
        ("check", lambda g: g.match.lives == st["lives1"] - 1, "flyers leak too"),
        ("cam", 26, -14, 26), ("wait", 0.5), ("shot", "t1_04_heartfire"),
        ("time", 19.6), ("wait", 0.5), ("shot", "t1_05_dusk"),
        ("time", 23.0), ("cam", 0, -5, 62), ("wait", 0.5), ("shot", "t1_06_night"),
        ("time", 10.0), ("cam", 0, -5, 62), ("wait", 1.0), ("fps",),
        ("quit",),
    ]


def _show(g) -> list[Step]:
    """Model sheets: every tower at every level, and every creep."""
    def clear(g):
        for t in list(g.match.towers):
            g.match.sell_tower(t)

    def line(g, level: int):
        """One tower of each kind along the terrace row below the road."""
        clear(g)
        return sum(1 for i, tid in enumerate(data.towers()) if _build(g, tid, 3 + i * 2, 14, level) is not None)

    def creep_sheet(g):
        clear(g)
        ids = list(data.creeps())
        for i, cid in enumerate(ids):
            row, k = divmod(i, 10)
            _spawn(g, cid, 1, hp=100, at=(-27.0 + k * 2.8, -11.6 - row * 3.6), still=True)
        return len(ids)

    return [
        ("call", _hold_waves), ("wait", 0.8),
        ("call", lambda g: line(g, 1), "level 1 towers placed"),
        ("cam", -9, -15, 24), ("wait", 1.0), ("shot", "show_01_towers_l1"),
        ("call", lambda g: line(g, 2), "level 2 towers placed"), ("wait", 1.0), ("shot", "show_02_towers_l2"),
        ("call", lambda g: line(g, 3), "level 3 towers placed"), ("wait", 1.0), ("shot", "show_03_towers_l3"),
        ("cam", -17, -14, 16), ("wait", 0.6), ("shot", "show_04_towers_close_a"),
        ("cam", -2, -14, 16), ("wait", 0.6), ("shot", "show_05_towers_close_b"),
        ("call", creep_sheet, "creeps placed"),
        ("cam", -14, -17, 24), ("wait", 1.2), ("shot", "show_06_creeps"),
        ("cam", -21, -16, 14), ("wait", 0.6), ("shot", "show_07_creeps_close_a"),
        ("cam", -8, -16, 14), ("wait", 0.6), ("shot", "show_08_creeps_close_b"),
        ("fps",), ("quit",),
    ]


def _xz(g, col: int, row: int) -> tuple[float, float]:
    return g.match.grid.center(_cell(g, col, row))


def _clear(g) -> None:
    """Remove every tower and creep so the next test case starts from an empty field."""
    m = g.match
    for t in list(m.towers):
        m.sell_tower(t)
    for c in list(m.creeps):
        if c.alive:
            c.take_damage(1e12)
    m.combat.clear()


def _t2(g) -> list[Step]:
    """Every tower kind, its special effect, upgrades, selling, creep traits and the three powers."""
    st: dict[str, Any] = {}

    def setup(g, tower: str, level: int = 1, creeps: str = "gloomling", n: int = 1, flyer: bool = False, spacing: float = 1.2):
        """One tower below the first stretch of road with ``n`` motionless target dummies in front of it."""
        _clear(g)
        x, z = _xz(g, 4, 2)
        st["dummies"] = _spawn(g, creeps, n, hp=1e6, at=(x - (n - 1) * spacing * 0.5, z), spacing=spacing, still=True)
        st["bat"] = _spawn(g, "gloomwing", 1, hp=1e6, at=(x, z + 0.5), still=True)[0] if flyer else None
        st["tower"] = _build(g, tower, 4, 3, level)
        return st["tower"].name

    def dmg(c) -> float:
        return c.max_hp - c.hp

    def per_bolt(g) -> float:
        """What one level-1 ballista bolt should do to a light, unarmored flyer, straight from the data files."""
        st1 = data.tower_level("ballista", 1)
        return float(st1["damage"]) * g.match.combat.type_mult("pierce", "light") * (1.0 + float(st1["air_bonus"]))

    def dummy(i: int = 0):
        return st["dummies"][i]

    def formula(g):
        m = g.match
        c = _spawn(g, "marauder", 1, hp=1000, at=_xz(g, 2, 2), still=True)[0]
        got = m.combat.expected(100.0, "pierce", c)
        want = 100.0 * 0.6 * (1 - 0.18 / 1.18)
        c.apply_poison(1.0, 5.0, 7.0)
        shredded = m.combat.expected(100.0, "pierce", c)       # armor 3 - 7 = -4
        want2 = 100.0 * 0.6 * (2 - 0.94 ** 4)
        st["formula"] = abs(got - want) < 0.01 and abs(shredded - want2) < 0.01
        c.take_damage(1e9)
        return {"pierce vs heavy 3": round(got, 2), "after -7 armor": round(shredded, 2)}

    def banner(g):
        _clear(g)
        st["a"] = _build(g, "arrow", 4, 3)
        st["far"] = _build(g, "arrow", 12, 9)
        st["ban"] = _build(g, "banner", 5, 3)
        return (st["a"].buff_damage, st["far"].buff_damage)

    def upgrade(g):
        _clear(g)
        m = g.match
        m.gold = 500
        t = st["up"] = m.build_tower("arrow", _cell(g, 4, 3))
        st["gold_after_build"] = m.gold
        return m.gold

    def do_upgrade(g):
        m = g.match
        st["err"] = m.upgrade_tower(st["up"])
        return {"error": st["err"], "gold": m.gold, "state": st["up"].state}

    def sell(g):
        m = g.match
        m.gold = 200
        fresh = m.build_tower("cannon", _cell(g, 6, 3))
        st["fresh_refund"] = m.sell_tower(fresh)
        old = st["up"]
        st["old_value"], st["old_invested"] = old.sell_value(), old.invested
        st["old_refund"] = m.sell_tower(old)
        return {"fresh": st["fresh_refund"], "old": st["old_refund"], "invested": st["old_invested"], "gold": m.gold}

    def traits(g):
        _clear(g)
        m = g.match
        st["ooze"] = _spawn(g, "ooze", 1, hp=50, at=_xz(g, 4, 2), still=True)[0]
        st["n0"] = len([c for c in m.creeps if c.alive]) + len(m._born)
        st["ooze"].take_damage(1e6)

    def healer(g):
        _clear(g)
        st["hex"] = _spawn(g, "hexer", 1, hp=500, at=_xz(g, 4, 2), still=True)[0]
        st["hurt"] = _spawn(g, "gloomling", 1, hp=1000, at=_xz(g, 5, 2), still=True)[0]
        st["hurt"].hp = 400
        st["regen"] = _spawn(g, "mire_crawler", 2, hp=1000, at=_xz(g, 0, 2), spacing=1.5, still=True)
        for c in st["regen"]:
            c.hp = 500
        st["regen"][1].apply_poison(0.001, 30.0, 0.0)

    def spells(g):
        _clear(g)
        m = g.match
        x, z = _xz(g, 4, 2)
        st["weak"] = _spawn(g, "gloomling", 4, hp=20, at=(x - 1.5, z), spacing=1.0, still=True)
        st["tank"] = _spawn(g, "ogre_warlord", 1, hp=1e6, at=(x + 2.0, z), still=True)[0]
        st["kills0"] = m.stats["kills"]
        return m.spells.cast("meteor", x, z)

    def nova(g):
        _clear(g)
        m = g.match
        x, z = _xz(g, 4, 2)
        st["cold"] = _spawn(g, "gloomling", 3, hp=1e6, at=(x - 1.0, z), spacing=1.0)
        st["boss"] = _spawn(g, "ogre_warlord", 1, hp=1e6, at=(x + 2.0, z))[0]
        st["ot"] = _build(g, "arrow", 4, 3)

    def cast_nova(g):
        x, z = _xz(g, 4, 2)
        return (g.match.spells.cast("frost_nova", x, z), g.match.spells.cast("overcharge", x, z))

    return [
        ("call", _hold_waves), ("wait", 0.8),
        ("call", formula, "damage formula"),
        ("check", lambda g: st["formula"], "armor and attack-type multipliers match combat.json"),
        ("cam", -24, 11, 20),
        # --- arrow
        ("call", lambda g: setup(g, "arrow"), "built"), ("wait", 2.2),
        ("check", lambda g: dmg(dummy()) > 0 and st["tower"].damage_dealt > 0, "arrow spire damages a ground creep"),
        ("shot", "t2_01_arrow"),
        ("call", lambda g: setup(g, "arrow", flyer=True), "built"), ("wait", 2.2),
        ("check", lambda g: dmg(st["bat"]) > 0, "arrow spire also hits flyers"),
        # --- cannon
        ("call", lambda g: setup(g, "cannon", n=2, flyer=True), "built"), ("wait", 3.4),
        ("check", lambda g: all(dmg(c) > 0 for c in st["dummies"]), "cannon shell splashes every creep in the blast"),
        ("check", lambda g: dmg(st["bat"]) == 0, "cannon cannot hit flyers"),
        ("shot", "t2_02_cannon"),
        # --- frost
        ("call", lambda g: setup(g, "frost", n=2), "built"), ("wait", 2.4),
        ("check", lambda g: all(c.slow_t > 0 and abs(c.slow_f - 0.30) < 0.01 for c in st["dummies"]), "frost obelisk slows by 30% in an area"),
        ("shot", "t2_03_frost"),
        # --- ember
        ("call", lambda g: setup(g, "ember", n=2, flyer=True, spacing=0.7), "built"), ("wait", 1.6),
        ("check", lambda g: all(c.burn_t > 0 for c in st["dummies"]), "ember brazier sets everything in its jet on fire"),
        ("check", lambda g: dmg(st["bat"]) == 0, "ember brazier cannot hit flyers"),
        ("shot", "t2_04_ember"),
        # --- storm
        ("call", lambda g: setup(g, "storm", n=3), "built"), ("wait", 2.0),
        ("check", lambda g: all(dmg(c) > 0 for c in st["dummies"]), "storm coil chains to three creeps"),
        ("shot", "t2_05_storm"),
        # --- venom
        ("call", lambda g: setup(g, "venom", creeps="marauder", n=2), "built"), ("wait", 2.6),
        ("check", lambda g: all(c.poison_t > 0 for c in st["dummies"]), "venom thorn spreads its poison over fresh targets"),
        ("check", lambda g: dummy().armor == 1.0, "poison strips 2 armor (3 -> 1)"),
        ("shot", "t2_06_venom"),
        # --- prism
        ("call", lambda g: setup(g, "prism"), "built"), ("wait", 2.5),
        ("check", lambda g: st["tower"].ramp > 2.0 and st["tower"].beam_target is dummy(), "sun prism holds one target and ramps up"),
        ("shot", "t2_07_prism"),
        # --- ballista
        ("call", lambda g: setup(g, "ballista", flyer=True), "built"), ("wait", 0.6),
        ("call", lambda g: (dummy().take_damage(1e9), None)[1]), ("wait", 3.0),
        ("check", lambda g: dmg(st["bat"]) > 0 and abs(dmg(st["bat"]) / per_bolt(g) - round(dmg(st["bat"]) / per_bolt(g))) < 0.01,
         "ballista deals double damage to flyers (damage x pierce-vs-light x 2)"),
        ("shot", "t2_08_ballista"),
        # --- banner aura
        ("call", banner, "banner buff near / far"),
        ("check", lambda g: abs(st["a"].buff_damage - 0.15) < 1e-6 and st["far"].buff_damage == 0, "war banner buffs only towers in its aura"),
        ("call", lambda g: g.controls.select(st["ban"], sound=False)), ("wait", 0.5), ("shot", "t2_09_banner"),
        # --- level 3 perks in action
        ("call", lambda g: setup(g, "storm", level=3, n=6), "built"), ("wait", 1.6), ("shot", "t2_10_tempest"),
        ("check", lambda g: all(dmg(c) > 0 for c in st["dummies"]), "tempest coil arcs through six creeps"),
        ("call", lambda g: setup(g, "arrow", level=3, n=2), "built"), ("wait", 1.5),
        ("check", lambda g: all(dmg(c) > 0 for c in st["dummies"]), "hawkeye spire shoots two targets at once"),
        # --- upgrade and sell
        ("call", upgrade, "gold after building"),
        ("check", lambda g: st["gold_after_build"] == 450 and st["up"].state == "building", "building costs gold and takes time"),
        ("wait", 1.4),
        ("check", lambda g: st["up"].state == "ready", "construction finishes"),
        ("call", do_upgrade, "upgrade"),
        ("check", lambda g: st["err"] is None and g.match.gold == 375 and st["up"].state == "upgrading", "upgrade is paid for and takes time"),
        ("wait", 1.8),
        ("check", lambda g: st["up"].level == 2 and st["up"].stats["damage"] == 20 and st["up"].name == "Longbow Spire", "tower reaches level 2"),
        ("wait", 3.0),
        ("call", sell, "sell"),
        ("check", lambda g: st["fresh_refund"] == 90, "a tower sold right after placing is fully refunded"),
        ("check", lambda g: st["old_invested"] == 125 and st["old_refund"] == 94, "an older tower sells for 75%"),
        # --- creep traits
        ("call", traits), ("wait", 0.3),
        ("check", lambda g: len([c for c in g.match.creeps if c.alive and c.type_id == "oozeling"]) == 2, "an ooze splits into two oozelings"),
        ("call", healer), ("wait", 2.6),
        ("check", lambda g: st["hurt"].hp > 400, "bog hexer heals nearby creeps"),
        ("check", lambda g: st["regen"][0].hp > 500 and st["regen"][1].hp <= 500, "mire crawlers regenerate unless poisoned"),
        # --- powers
        ("call", spells, "meteor cast error"), ("wait", 0.5), ("shot", "t2_11_meteor_falling"), ("wait", 0.7),
        ("check", lambda g: g.match.stats["kills"] - st["kills0"] == 4, "meteor kills the weak pack"),
        ("check", lambda g: st["tank"].burn_t > 0, "meteor survivors burn"),
        ("shot", "t2_12_meteor_impact"),
        ("check", lambda g: not g.match.spells.ready("meteor"), "meteor goes on cooldown"),
        ("call", nova), ("wait", 0.25), ("call", cast_nova, "nova / overcharge errors"), ("wait", 0.3),
        ("check", lambda g: all(c.stun_t > 0 and c.frozen for c in st["cold"]), "frost nova freezes creeps solid"),
        ("check", lambda g: st["boss"].stun_t <= 0 and st["boss"].slow_t > 0, "bosses are only slowed, and only half as much"),
        ("check", lambda g: st["ot"].overcharge_t > 0 and abs(st["ot"].interval - 0.4) < 1e-6, "overcharge doubles attack speed"),
        ("shot", "t2_13_nova"),
        ("fps",), ("quit",),
    ]


def _fx(g) -> list[Step]:
    """Close-up shots of every attack effect, fired on cue so the screenshot catches it."""
    st: dict[str, Any] = {}

    def stage(tower: str, level: int, cols: tuple[float, ...], creep: str = "marauder"):
        def f(g):
            _clear(g)
            z = _xz(g, 4, 2)[1]
            st["d"] = [_spawn(g, creep, 1, hp=1e6, at=(-32.0 + (c + 0.5) * 2.0, z), still=True)[0] for c in cols]
            st["t"] = _build(g, tower, 3, 3, level)
        return f

    def fire(g):
        t = st["t"]
        d = st["d"]
        t.aim_yaw = math.degrees(math.atan2(d[0].x - t.x, d[0].z - t.z))
        if t.view is not None:
            t.view.yaw = t.aim_yaw
        if t.kind == "projectile":
            t._fire_projectiles(d[: int(t.stats.get("multishot", 1))])
        elif t.kind == "chain":
            t._fire_chain(d[0])
        elif t.kind == "cone":
            for _ in range(6):
                t._fire_cone(d[0])

    steps: list[Step] = [("call", _hold_waves), ("wait", 0.6), ("cam", -21, 11, 17)]
    for i, (tower, level, cols, delay) in enumerate((
            ("arrow", 3, (6, 7), 0.06), ("cannon", 2, (6, 6.6), 0.22), ("frost", 2, (5, 5.6), 0.1), ("ember", 3, (4.2, 4.9), 0.2),
            ("storm", 3, (5, 6, 7, 6.5, 5.5, 4.5), 0.05), ("venom", 2, (6, 7), 0.12), ("prism", 3, (7,), 1.6),
            ("ballista", 3, (7, 7.7, 6.4), 0.06))):
        steps += [("call", stage(tower, level, cols)), ("wait", 0.5 if tower != "prism" else 0.1), ("call", fire), ("wait", delay),
                  ("shot", f"fx_{i + 1:02d}_{tower}")]
    return steps + [("quit",)]


def _tips(g) -> list[Step]:
    """Render the HUD tooltips (they normally follow the mouse) so their layout can be checked."""
    from ui.widgets import Tooltip

    def show(lines_fn, pos):
        def f(g):
            tt = Tooltip.get()
            tt.show(lines_fn(g), None)
            tt.follow = lambda: None            # keep it where the test puts it
            tt.position = (pos[0], pos[1], -5)
        return f

    def select_tower(g):
        t = _build(g, "frost", 4, 3, 2)
        g.match.gold = 500
        g.controls.select(t, sound=False)

    return [
        ("wait", 1.0),
        ("call", show(lambda g: g.hud._card_tip(g.hud.card[1]), (-0.2, 0.4))), ("wait", 0.2), ("shot", "tips_01_cannon"),
        ("call", show(lambda g: g.hud._card_tip(g.hud.card[6]), (-0.2, 0.4))), ("wait", 0.2), ("shot", "tips_02_prism"),
        ("call", show(lambda g: g.hud._card_tip(g.hud.card[9]), (-0.2, 0.4))), ("wait", 0.2), ("shot", "tips_03_meteor"),
        ("call", show(lambda g: g.hud._wave_tip(g.hud.wave_rows[2]["slot"]), (-0.5, 0.4))), ("wait", 0.2), ("shot", "tips_04_wave"),
        ("call", select_tower), ("wait", 0.4),
        ("call", show(lambda g: g.hud._action_tip(g.hud.actions[0]), (-0.2, 0.4))), ("wait", 0.2), ("shot", "tips_05_upgrade"),
        ("call", lambda g: g.controls.select(_spawn(g, "hexer", 1, hp=240, at=_xz(g, 4, 2), still=True)[0], sound=False)),
        ("call", lambda g: Tooltip.get().hide()), ("wait", 0.5), ("shot", "tips_06_creep_selected"),
        ("quit",),
    ]


def _perf(g) -> list[Step]:
    """A late-game stress scene: a field full of towers and a very large mixed wave."""
    def towers(g):
        from core.bot import Bot
        m = g.match
        m.gold = 14000
        bot = Bot(m)
        m.waves.number = 22                  # value the purchases against late waves
        for _ in range(60):
            bot.t = 0.0
            bot.update(0.5)
        m.waves.number = 0
        return {"towers": len(m.towers), "invested": sum(t.invested for t in m.towers)}

    def horde(g):
        m = g.match
        n = 0
        for cid, count, hp in (("skitterling", 70, 2500), ("marauder", 30, 9000), ("wraith", 25, 6000), ("gloomwing", 25, 4000),
                               ("ooze", 10, 5000)):
            for k in range(count):
                m.spawn_creep(cid, hp / float(data.creeps()[cid].get("hp", 1.0)), 1, 0, 0)
                n += 1
        # string them out along the first stretch of road instead of one stacked blob
        for i, c in enumerate(m._born):
            if not c.flying:
                c.x += min(14.0, (i % 40) * 0.36)
        return n

    return [
        ("call", _hold_waves), ("wait", 0.8),
        ("call", towers, "towers"), ("wait", 1.0),
        ("call", horde, "creeps spawned"), ("wait", 5.0), ("shot", "perf_01"), ("fps",),
        ("wait", 5.0), ("shot", "perf_02"), ("fps",),
        ("check", lambda g: g.average_fps() > 45, "a 160-creep wave against a full field stays above 45 fps"),
        ("speed", 3), ("wait", 4.0), ("fps",), ("speed", 1),
        ("quit",),
    ]


def _shots(g) -> list[Step]:
    """Stage good-looking screenshots for the README: the bot plays to mid-game, then the camera moves in."""
    st: dict[str, Any] = {}
    name = g.match.config.map_id.split("_")[0]

    def start(g):
        from core.bot import Bot
        g.bot = Bot(g.match, use_powers=True)
        g.match.auto_send = True
        g.match.waves.countdown = 2.0
        g.match.lives = g.match.max_lives = 999

    def hub(g):
        """Aim the camera at the busiest group of towers."""
        m = g.match
        towers = [t for t in m.towers if t.kind != "aura"]
        best = max(towers, key=lambda t: sum(1 for o in towers if (o.x - t.x) ** 2 + (o.z - t.z) ** 2 < 64.0))
        st["hub"] = (best.x, best.z)
        m.cam.look_at(best.x, best.z - 3.0)
        m.cam.distance = m.cam.target_distance = 33.0
        m.cam.apply()
        g.controls.select(None, sound=False)

    def busy(n: int):
        def f(g):
            m = g.match
            hx, hz = st["hub"]
            return sum(1 for c in m.creeps if c.alive and (c.x - hx) ** 2 + (c.z - hz) ** 2 < 150.0) >= n or m.over
        return f

    def wave(n: int):
        return lambda g: g.match.waves.number >= n or g.match.over

    def wide(g):
        m = g.match
        m.cam.look_at(0.0, -6.0)
        m.cam.distance = m.cam.target_distance = 66.0
        m.cam.apply()

    return [
        ("wait", 0.5), ("call", start), ("speed", 6),
        ("until", wave(13), 400, "wave 13"), ("speed", 2), ("call", hub),
        ("until", busy(6), 40, "creeps reach the kill zone"), ("speed", 1), ("wait", 0.6), ("shot", f"shots_{name}_01_battle"),
        ("call", wide), ("wait", 0.4), ("shot", f"shots_{name}_02_wide"),
        ("speed", 6), ("until", wave(22), 400, "wave 22"), ("speed", 2), ("call", hub),
        ("until", busy(7), 40, "creeps reach the kill zone"), ("speed", 1), ("time", 21.3), ("wait", 0.8),
        ("shot", f"shots_{name}_03_night"),
        ("time", 18.7), ("wait", 0.6), ("shot", f"shots_{name}_04_dusk"),
        ("call", wide), ("time", 11.0), ("wait", 0.5), ("shot", f"shots_{name}_05_wide_late"),
        ("call", lambda g: (g.controls.begin_build("storm") if g.match.gold >= 110 else g.match.__setattr__("gold", 500) or g.controls.begin_build("storm"))),
        ("call", lambda g: (setattr(g.controls, "cell_under_mouse", lambda: g.match.grid.cell_of(st["hub"][0] + 6.0, st["hub"][1] - 4.0)),
                            setattr(g.controls, "over_ui", lambda: False))[0]),
        ("wait", 0.6), ("shot", f"shots_{name}_06_placing"),
        ("quit",),
    ]


def _t3(g) -> list[Step]:
    """Maze map: towers reshape the route, the route can never be sealed, creeps re-path on the fly."""
    st: dict[str, Any] = {}

    def wall(g):
        m = g.match
        st["len0"] = m.grid.route_length(0)
        # a wall down column 5 that leaves a single gap at the top: the creeps have to climb to it and come back
        built = [m.build_tower("arrow", _cell(g, 5, r), free=True, instant=True) for r in range(3, 17)]
        st["len1"] = m.grid.route_length(0)
        return {"built": sum(1 for t in built if t), "route before": round(st["len0"], 1), "after": round(st["len1"], 1)}

    def seal(g):
        m = g.match
        cells = [c for c in range(m.grid.n) if m.grid.can_build(c) and m.grid.base_walk[c]]
        blocked = [c for c in cells if not m.can_place("arrow", c, check_gold=False)[0]]
        st["blocked"] = blocked
        reasons = {m.can_place("arrow", c, check_gold=False)[1] for c in blocked}
        return {"cells that would seal the maze": len(blocked), "reasons": sorted(reasons)}

    def runners(g):
        m = g.match
        st["run"] = _spawn(g, "gloomling", 4, hp=1e6)
        for c in st["run"]:
            c.base_speed = 9.0
        st["lives"] = m.lives

    def reroute(g):
        m = g.match
        lead = st["run"][0]
        # drop a tower a few cells ahead of the lead creep, on its current route
        path = m.grid.path_cells(m.grid.cell_of(lead.x, lead.z))
        for c in path[3:]:
            if m.can_place("arrow", c, check_gold=False)[0]:
                st["plug"] = m.build_tower("arrow", c, free=True, instant=True)
                break
        return st.get("plug") is not None

    def on_creep(g):
        m = g.match
        lead = next(c for c in st["run"] if c.alive)
        return m.can_place("arrow", m.grid.cell_of(lead.x, lead.z), check_gold=False)

    def bot_maze(g):
        from core.bot import Bot
        _clear(g)
        m = g.match
        m.gold = 6000
        bot = Bot(m)
        for _ in range(80):
            bot.t = 0.0
            bot.update(0.5)
        return {"towers": len(m.towers), "route": round(m.grid.route_length(0), 1)}

    return [
        ("call", _hold_waves), ("wait", 0.8), ("shot", "t3_01_moor"),
        ("check", lambda g: g.match.grid.is_maze, "the moor is a maze map"),
        ("call", wall, "wall"),
        ("check", lambda g: st["len1"] > st["len0"] + 6, "a wall of towers makes the route longer"),
        ("call", seal, "sealing attempts"),
        ("check", lambda g: len(st["blocked"]) > 0, "cells that would seal the maze are refused"),
        ("call", lambda g: g.match.__setattr__("show_paths", True)), ("wait", 0.6), ("shot", "t3_02_wall_and_route"),
        ("call", runners), ("wait", 1.5),
        ("call", on_creep, "placing on a creep"),
        ("call", reroute, "plugged the route ahead"),
        ("until", lambda g: not any(c.alive for c in st["run"]), 40, "creeps find the new way out"),
        ("check", lambda g: g.match.lives == st["lives"] - 4, "every creep re-routed and reached the exit"),
        ("call", bot_maze, "bot maze"), ("wait", 1.2), ("shot", "t3_03_bot_maze"),
        ("check", lambda g: g.match.grid.route_length(0) > 110, "the bot's maze more than doubles the route"),
        ("time", 20.2), ("wait", 0.5), ("shot", "t3_04_moor_dusk"),
        ("fps",), ("quit",),
    ]


def _ui(g) -> list[Step]:
    """Drive the game through the same input path the player uses: hotkeys, clicks, HUD and pause menu."""
    st: dict[str, Any] = {}

    def aim(col: int, row: int):
        """Pretend the mouse is over a grid cell (the real pointer is wherever the OS left it)."""
        def f(g):
            ctl = g.controls
            cell = _cell(g, col, row)
            x, z = g.match.grid.center(cell)
            ctl.cell_under_mouse = lambda: cell
            ctl.ground_point = lambda: (x, z)
            ctl.over_ui = lambda: False
            ctl.pick = lambda mx=None, my=None: g.match.tower_at(cell)
            if "hud_input" not in st:           # the real cursor may be resting on the HUD: do not let it eat test clicks
                st["hud_input"] = g.hud.input
                g.hud.input = lambda key: False if "mouse" in key else st["hud_input"](key)
        return f

    return [
        ("wait", 1.0),
        ("check", lambda g: g.hud.root.enabled and g.hud.gold_text.text == "120", "HUD shows the starting gold"),
        ("call", aim(4, 3)), ("press", "q"), ("wait", 0.3),
        ("check", lambda g: g.controls.mode == "build" and g.controls.ghost is not None, "Q starts placing an Arrow Spire"),
        ("check", lambda g: g.match.build_preview is not None and g.match.build_preview.get("ok"), "the ghost is green on a free terrace"),
        ("shot", "ui_01_placing"),
        ("press", "left mouse down"), ("wait", 0.2),
        ("check", lambda g: len(g.match.towers) == 1 and g.match.gold == 70 and g.controls.mode is None, "a click builds it and ends placement"),
        ("call", aim(4, 2)), ("press", "q"), ("wait", 0.3),
        ("check", lambda g: g.match.build_preview is not None and not g.match.build_preview.get("ok"), "the road itself cannot be built on"),
        ("press", "left mouse down"), ("wait", 0.2),
        ("check", lambda g: len(g.match.towers) == 1, "clicking the road builds nothing"),
        ("press", "right mouse down"), ("wait", 0.1),
        ("check", lambda g: g.controls.mode is None and g.controls.ghost is None, "right-click cancels placement"),
        ("press", "d"), ("wait", 0.1),
        ("check", lambda g: g.controls.mode is None, "an unaffordable tower cannot be picked"),
        # select, retarget, upgrade, sell
        ("wait", 1.3), ("call", aim(4, 3)), ("press", "left mouse down"), ("wait", 0.3),
        ("check", lambda g: g.match.selected is g.match.towers[0], "clicking a tower selects it"),
        ("shot", "ui_02_selected"),
        ("press", "t"), ("wait", 0.2),
        ("check", lambda g: g.match.towers[0].mode == "last", "T cycles the targeting mode"),
        ("call", lambda g: g.match.__setattr__("gold", 300)), ("press", "u"), ("wait", 0.2),
        ("check", lambda g: g.match.towers[0].state == "upgrading" and g.match.gold == 225, "U upgrades the selected tower"),
        ("wait", 1.8),
        ("check", lambda g: g.match.towers[0].level == 2, "the upgrade completes"),
        ("wait", 0.3), ("shot", "ui_03_upgraded"),
        ("press", "x"), ("wait", 0.2),
        ("check", lambda g: len(g.match.towers) == 0 and g.match.selected is None, "X sells the selected tower"),
        # powers, waves, speed
        ("call", aim(6, 2)), ("press", "1"), ("wait", 0.2),
        ("check", lambda g: g.controls.mode == "spell" and g.match.spell_preview is not None, "1 starts aiming a Meteor"),
        ("shot", "ui_04_meteor_aim"),
        ("press", "left mouse down"), ("wait", 0.2),
        ("check", lambda g: g.controls.mode is None and not g.match.spells.ready("meteor"), "a click casts it"),
        ("press", "space"), ("wait", 0.3),
        ("check", lambda g: g.match.waves.number == 1, "Space calls the first wave"),
        ("press", "tab"),
        ("check", lambda g: g.speed == 2.0, "Tab switches to 2x speed"),
        ("press", "tab"), ("press", "tab"),
        ("check", lambda g: g.speed == 1.0, "and cycles back to 1x"),
        ("wait", 2.5), ("shot", "ui_05_wave_running"),
        # pause menu
        ("press", "escape"), ("wait", 0.3),
        ("check", lambda g: g.paused and g.menus.current is g.menus.pause, "Esc opens the pause menu"),
        ("shot", "ui_06_pause"),
        ("call", lambda g: g.menus.show_help(g.menus.show_pause)), ("wait", 0.3), ("shot", "ui_07_help"),
        ("call", lambda g: g.menus.show_settings(g.menus.show_pause)), ("wait", 0.3), ("shot", "ui_08_settings"),
        ("press", "escape"), ("wait", 0.2),
        ("check", lambda g: g.menus.current is g.menus.pause, "Esc goes back from settings"),
        ("press", "escape"), ("wait", 0.2),
        ("check", lambda g: not g.paused and g.menus.current is None, "Esc resumes"),
        ("quit",),
    ]


def _menu(g) -> list[Step]:
    """Main menu with its bot-played backdrop, the setup screen, a defeat and the end-of-game summary."""
    def lose(g):
        m = g.match
        m.lives = 1
        m.waves.number = 7
        m.waves.cleared = set(range(1, 7))
        m.stats["kills"] = 93
        c = _spawn(g, "gloomling", 1, hp=1e6)[0]
        c.base_speed = 200.0

    def win(g):
        m = g.match
        m.waves.number = m.waves.total
        m.waves.cleared = set(range(1, m.waves.total + 1))
        m.stats["kills"] = 412
        m.on_all_waves_cleared()

    return [
        ("wait", 2.5),
        ("check", lambda g: g.menus.current is g.menus.main and g.match is not None and g.match.config.demo, "main menu over a bot-played game"),
        ("speed", 6), ("wait", 6.0), ("speed", 1), ("wait", 1.0),
        ("check", lambda g: len(g.match.towers) >= 5, "the backdrop bot is building towers"),
        ("shot", "menu_01_main"),
        ("call", lambda g: g.menus.show_setup()), ("wait", 0.4), ("shot", "menu_02_setup"),
        ("call", lambda g: (g.menus.cfg.update(map="hollow_moor", difficulty="hard"), g.menus._refresh_setup())[1]), ("wait", 0.3),
        ("shot", "menu_03_setup_moor"),
        ("call", lambda g: (g.menus.cfg.update(map="emberfall_road", difficulty="normal", mode="campaign"), g.menus.start_game())[1]),
        ("wait", 1.0),
        ("check", lambda g: g.state == "playing" and not g.match.config.demo and g.hud.root.enabled, "starting a game shows the HUD"),
        ("call", lose),
        ("until", lambda g: g.match.over, 10, "the last life is lost"),
        ("check", lambda g: g.match.over and not g.match.won and g.state == "ended", "defeat ends the game"),
        ("wait", 3.0),
        ("check", lambda g: g.menus.current is g.menus.end, "the summary screen appears"),
        ("shot", "menu_04_defeat"),
        ("call", lambda g: g.menus.restart()), ("wait", 0.8),
        ("check", lambda g: g.state == "playing" and g.match.lives == 20, "Play Again starts a fresh game"),
        ("call", win), ("wait", 3.0),
        ("check", lambda g: g.menus.current is g.menus.end and g.match.won, "victory shows the summary too"),
        ("shot", "menu_05_victory"),
        ("call", lambda g: g.menus._continue_endless()), ("wait", 0.5),
        ("check", lambda g: g.state == "playing" and g.match.waves.endless and not g.match.over, "Keep Going switches to endless"),
        ("call", lambda g: g.menus.quit_to_main()), ("wait", 1.0),
        ("check", lambda g: g.menus.current is g.menus.main and g.match.config.demo, "back at the main menu"),
        ("quit",),
    ]


def _bot(g) -> list[Step]:
    """Let the bot play a whole game on screen at high speed, with screenshots along the way."""
    def start(g):
        from core.bot import Bot
        g.bot = Bot(g.match)
        g.match.auto_send = True
        g.match.waves.countdown = 2.0

    def wave(n: int):
        return lambda g: g.match.waves.number >= n or g.match.over

    def pick_tower(g):
        m = g.match
        if m.towers:
            g.controls.select(max(m.towers, key=lambda t: t.damage_dealt), sound=False)

    def result(g):
        m = g.match
        return {"won": m.won, "over": m.over, "wave": m.waves.number, "lives": m.lives, "kills": m.stats["kills"],
                "towers": len(m.towers), "score": m.score()}

    name = g.match.config.map_id.split("_")[0]
    steps: list[Step] = [("wait", 0.5), ("call", start), ("speed", 5)]
    for i, n in enumerate((3, 6, 9, 12, 15, 18, 21, 24, 27, 30)):
        steps += [("until", wave(n), 240, f"wave {n}"), ("wait", 1.2), ("speed", 1), ("call", pick_tower), ("wait", 0.35),
                  ("shot", f"bot_{name}_{i + 1:02d}_wave{n}"), ("fps",), ("speed", 5)]
    steps += [("until", lambda g: g.match.over, 200, "game over"), ("speed", 1), ("wait", 3.2),
              ("shot", f"bot_{name}_end"), ("call", result, "result"), ("quit",)]
    return steps


SCRIPTS = {"t1": _t1, "t2": _t2, "t3": _t3, "ui": _ui, "menu": _menu, "show": _show, "fx": _fx, "tips": _tips, "perf": _perf, "shots": _shots, "shots2": _shots, "shots3": _shots, "bot": _bot, "bot2": _bot,
           "bot3": _bot}
