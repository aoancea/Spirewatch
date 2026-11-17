"""One game of tower defense: the map, every creep and tower, gold and lives, and the per-frame simulation.

A match can run with graphics (the normal game) or headless (``visual=False``) for the balance simulator;
all rules live here and in the logic classes, while everything visual sits behind ``if self.visual``.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import numpy as np

from combat.combat import Combat
from combat.spells import Spells
from core import data
from core.events import events
from creeps.creep import CORPSE_TIME, Creep
from creeps.waves import WaveManager
from settings import CELL, DAY_LENGTH_SECONDS, PLAYER_COLOR, user_settings
from towers.tower import Tower
from world.level import Level

if TYPE_CHECKING:
    from core.game import Game


@dataclass
class MatchConfig:
    map_id: str = "emberfall_road"
    difficulty: str = "normal"
    endless: bool = False
    visual: bool = True
    demo: bool = False           # menu backdrop: a bot plays and nothing is at stake
    seed: int = 1


class Match:
    """Owns and simulates everything in a running game."""

    def __init__(self, game: "Game | None", config: MatchConfig) -> None:
        self.game = game
        self.config = config
        self.visual = bool(config.visual and game is not None)
        self.time = 0.0
        self.rng = random.Random(config.seed)
        random.seed(config.seed)
        self.diff: dict[str, Any] = data.difficulty(config.difficulty)
        self.level = Level(config.map_id)
        self.grid = self.level.grid
        self.gold = int(self.diff["gold"])
        self.lives = self.max_lives = int(self.diff["lives"])
        self.creeps: list[Creep] = []
        self.towers: list[Tower] = []
        self.corpses: list[Creep] = []
        self._born: list[Creep] = []
        self._dirty = False
        self._gate_k = [0] * len(self.grid.gates)
        self.over = False
        self.won = False
        self.auto_send = bool(user_settings.auto_send) and not config.demo
        self.stats: dict[str, Any] = {"kills": 0, "leaks": 0, "lives_lost": 0, "gold_earned": 0, "gold_spent": 0,
                                      "towers_built": 0, "towers_sold": 0, "upgrades": 0, "spells": 0, "bosses": 0}
        self.selected: Any = None
        self.combat = Combat(self)
        self.waves = WaveManager(self, endless=config.endless)
        self.spells = Spells(self)
        # presentation state driven by the controls
        self.show_all_bars = False
        self.show_paths = False
        self.build_preview: dict[str, Any] | None = None
        self.spell_preview: tuple[float, float, float, tuple] | None = None
        self.preview_routes: list[list[tuple[float, float]]] | None = None
        self._routes_ver = -1
        self._routes: list[tuple[np.ndarray, np.ndarray]] = []
        self._cells_ver = -1
        self._cells_n = -1
        self.last_trial: tuple[list[float], list[int]] | None = None
        self.cam = None
        self.env = None
        if self.visual:
            self._init_visuals()
        else:
            from combat.effects import NullFX
            self.fx = NullFX()

    # ================================================================== visuals setup
    def _init_visuals(self) -> None:
        from ursina import camera as ucam
        from combat.effects import EffectsManager
        from core import shaders
        from core.camera import TDCamera
        from core.textures import cell_texture, radial_texture, ring_texture
        from ui.overlays import QuadBatch
        from world.sky import Environment
        self.base = self.game.base
        render = self.base.render
        self.root = render.attachNewNode("match")
        self.map_root = self.root.attachNewNode("map")
        self.tower_root = self.root.attachNewNode("towers")
        self.creep_root = self.root.attachNewNode("creeps")
        self.fx_root = self.root.attachNewNode("fx")
        self.env = Environment(render, 420.0)
        self.env.hour = float(self.level.defn.get("start_hour", 10.0))
        self.env.time_scale = DAY_LENGTH_SECONDS / float(self.level.defn.get("day_length", DAY_LENGTH_SECONDS))
        render.setShader(shaders.world_shader())
        self.level.build(self.map_root, self.env)
        self.cam = TDCamera(self.base, self.level)
        self.fx = EffectsManager(self)

        def batch(cap: int, tex, name: str, order: int, emissive: float = 1.0) -> QuadBatch:
            q = QuadBatch(self.fx_root, cap, tex, name)
            q.node.setShaderInput("u_emissive", emissive)
            q.node.setBin("transparent", order)
            return q

        self.cells = batch(self.grid.n, cell_texture(64, 0.08, 0.2), "cells", 1)
        self.cells.node.hide()
        self.shadows = batch(500, radial_texture(64, 0.0, 1.0, (0, 0, 0), power=1.3), "shadows", 2)
        self.dots = batch(700, radial_texture(32, 0.25, 1.0, (255, 255, 255), power=1.0), "dots", 3)
        self.fills = batch(8, radial_texture(64, 0.92, 1.0, (255, 255, 255), power=0.6), "fills", 4)
        self.ranges = batch(24, ring_texture(256, 0.035), "ranges", 5)
        self.rings = batch(16, ring_texture(128, 0.16), "rings", 6)
        self.cursor = batch(4, cell_texture(64, 0.14, 0.4), "cursor", 7)
        self.bars = QuadBatch(ucam.ui, 900, None, "bars", flat3d=False)
        self.bars.node.setZ(0.5)
        self.bars.node.setDepthTest(False)
        self._frame = 0

    def _attach_view(self, t: Tower) -> None:
        from towers.models import TowerView
        t.view = TowerView(t, self.tower_root, PLAYER_COLOR)
        t.light = None
        self._sync_light(t)

    def _sync_light(self, t: Tower) -> None:
        """Glowing towers light the ground around them (it shows at dusk and at night)."""
        from world.sky import PointLight
        if self.env is None or t.view is None:
            return
        if getattr(t, "light", None) is not None:
            self.env.remove_light(t.light)
            t.light = None
        col = t.view.parts.light
        if col is not None:
            t.light = self.env.add_light(PointLight((t.x, t.y + t.view.parts.pivot, t.z), 5.5 + t.level, col,
                                                    0.14 + 0.04 * t.level, flicker=0.12, night_boost=2.2))

    # ================================================================== economy
    def add_gold(self, amount: int, reason: str = "") -> None:
        if amount <= 0:
            return
        self.gold += amount
        self.stats["gold_earned"] += amount
        if reason == "early":
            events.emit("notice", text=f"Wave called early: +{amount} gold", color=(1.0, 0.85, 0.4))

    def spend(self, amount: int) -> bool:
        if amount > self.gold:
            return False
        self.gold -= amount
        self.stats["gold_spent"] += amount
        return True

    # ================================================================== creeps
    def spawn_creep(self, type_id: str, unit_hp: float, bounty: int, gate: int = 0, wave: int = 0,
                    at: tuple[float, float] | None = None) -> Creep:
        g = self.grid
        info = self.level.gate_info[gate % len(self.level.gate_info)]
        yaw = math.degrees(math.atan2(info["dir"][0], info["dir"][1]))
        if at is None:
            cells = g.gates[gate % len(g.gates)]
            k = self._gate_k[gate % len(g.gates)]
            self._gate_k[gate % len(g.gates)] = k + 1
            x, z = g.center(cells[k % len(cells)])
        else:
            x, z = at
        c = Creep(self, type_id, unit_hp, bounty, x, z, wave, yaw)
        if at is None and not c.flying:
            c.x -= info["dir"][1] * c.lane       # spread the column across the width of the road
            c.z += info["dir"][0] * c.lane
        self._born.append(c)
        self.waves.creep_added(wave)
        if at is None:
            self.fx.spawn_puff(x, c.y, z)
        return c

    def on_creep_killed(self, c: Creep, source: Any) -> None:
        self._dirty = True
        self.stats["kills"] += 1
        if c.boss:
            self.stats["bosses"] += 1
        if isinstance(source, Tower):
            source.kills += 1
        self.add_gold(c.bounty)
        self.fx.creep_died(c, c.bounty)
        events.emit("sound", name="boss_death" if c.boss else "squish", pos=(c.x, c.z), volume=0.7 if c.boss else 0.35)
        if c.poison_t > 0 and c.poison_spread > 0:
            self.fx.poison_spread(c, c.poison_spread)
            for o in self.combat.arr.within(c.x, c.z, c.poison_spread):
                if o is not c:
                    o.apply_poison(c.poison_dps, 3.0, c.poison_shred, c.poison_src, c.poison_spread)
        if c.split:
            child, count = c.split[0], int(c.split[1])
            for k in range(count):
                a = (k / count) * math.tau + 0.6
                x, z = c.x + math.cos(a) * 0.35, c.z + math.sin(a) * 0.35
                if not c.flying and not self.grid.walkable_at(x, z):
                    x, z = c.x, c.z
                kid = self.spawn_creep(child, c.unit_hp, max(1, int(round(c.bounty * 0.15))), 0, c.wave, at=(x, z))
                kid.face = kid.yaw = c.yaw
        self.waves.creep_removed(c.wave)
        if self.selected is c:
            self.selected = None
        if c.node is not None:
            c.die_visual()
            self.corpses.append(c)
        events.emit("creep_killed", creep=c)

    def on_leak(self, c: Creep) -> None:
        self._dirty = True
        self.stats["leaks"] += 1
        self.waves.creep_removed(c.wave)
        if self.selected is c:
            self.selected = None
        if not self.config.demo:
            lost = min(self.lives, c.lives)
            self.lives -= lost
            self.stats["lives_lost"] += lost
        self.fx.creep_leaked(c)
        events.emit("creep_leaked", creep=c, lives=self.lives)
        events.emit("sound", name="leak")
        c.destroy()
        if self.lives <= 0 and not self.over:
            self._finish(False)

    def heal_pulse(self, healer: Creep, frac: float, radius: float) -> None:
        healed = []
        for c in self.combat.arr.within(healer.x, healer.z, radius):
            if c is not healer and c.hp < c.max_hp and self.time - c.mended_at >= 1.8 and c.heal_by(c.max_hp * frac) > 0:
                c.mended_at = self.time
                healed.append(c)
        if healed:
            self.fx.heal_pulse(healer, radius, healed)

    def ground_creeps_in_cell(self, cell: int) -> bool:
        g = self.grid
        cx, cz = g.center(cell)
        half = CELL * 0.5
        for c in self.creeps:
            if c.alive and not c.flying and abs(c.x - cx) < half + c.radius * 0.6 and abs(c.z - cz) < half + c.radius * 0.6:
                return True
        return False

    # ================================================================== towers
    def can_place(self, type_id: str, cell: int, check_gold: bool = True) -> tuple[bool, str]:
        """Is building ``type_id`` on ``cell`` allowed right now? Returns (ok, reason when not)."""
        g = self.grid
        if cell < 0 or not g.buildable[cell]:
            return False, "Cannot build there"
        if g.tower[cell] is not None:
            return False, "There is already a tower there"
        if check_gold and self.gold < int(data.tower_level(type_id, 1)["cost"]):
            return False, "Not enough gold"
        self.last_trial = None
        if g.base_walk[cell]:
            if self.ground_creeps_in_cell(cell):
                return False, "A creep is in the way"
            occupied = {g.cell_of(c.x, c.z) for c in self.creeps if c.alive and not c.flying}
            blocked, dist, nxt = g.try_block([cell], occupied)
            if blocked:
                return False, "That would block the path"
            self.last_trial = (dist, nxt)       # the flow field as it would be: used to preview the new route
        return True, ""

    def build_tower(self, type_id: str, cell: int, free: bool = False, instant: bool = False) -> Tower | None:
        ok, _ = self.can_place(type_id, cell, check_gold=not free)
        if not ok:
            return None
        cost = int(data.tower_level(type_id, 1)["cost"])
        if not free and not self.spend(cost):
            return None
        t = Tower(self, type_id, cell, instant=instant)
        self.towers.append(t)
        self.grid.place(cell, t)
        self.stats["towers_built"] += 1
        if self.visual:
            self._attach_view(t)
        self.fx.tower_built(t)
        events.emit("sound", name="build", pos=(t.x, t.z))
        self.towers_changed()
        events.emit("tower_built", tower=t)
        return t

    def upgrade_tower(self, t: Tower, free: bool = False) -> str | None:
        """Start upgrading. Returns an error message, or None on success."""
        if t.level >= len(t.defn["levels"]):
            return "Already at the highest level"
        if t.state != "ready":
            return "The tower is busy"
        cost = t.upgrade_cost()
        if not free and not self.spend(cost):
            return "Not enough gold"
        t.begin_upgrade()
        self.stats["upgrades"] += 1
        events.emit("sound", name="hammer", pos=(t.x, t.z))
        return None

    def sell_tower(self, t: Tower) -> int:
        if t not in self.towers:
            return 0
        value = t.sell_value()
        self.gold += value
        self.towers.remove(t)
        self.grid.remove(t.cell)
        self.stats["towers_sold"] += 1
        if self.selected is t:
            self.selected = None
        self.fx.tower_sold(t.x, t.y, t.z, value)
        if self.env is not None and getattr(t, "light", None) is not None:
            self.env.remove_light(t.light)
        t.destroy()
        events.emit("sound", name="coin", pos=(t.x, t.z))
        self.towers_changed()
        events.emit("tower_sold", tower=t)
        return value

    def towers_changed(self) -> None:
        """Recompute banner auras (and refresh lights) after any build, upgrade or sale."""
        banners = [t for t in self.towers if t.kind == "aura" and t.state != "building"]
        for t in self.towers:
            dmg = rate = rng = 0.0
            if t.kind != "aura":
                for b in banners:
                    if (b.x - t.x) ** 2 + (b.z - t.z) ** 2 <= b.range ** 2:
                        aura = b.stats.get("aura", {})
                        dmg = max(dmg, float(aura.get("damage", 0.0)))
                        rate = max(rate, float(aura.get("rate", 0.0)))
                        rng = max(rng, float(aura.get("range", 0.0)))
            t.buff_damage, t.buff_rate, t.buff_range = dmg, rate, rng
            if self.visual and t.view is not None:
                self._sync_light(t)
        self._cells_ver = -1

    def tower_at(self, cell: int) -> Tower | None:
        return self.grid.tower[cell] if cell >= 0 else None

    # ================================================================== wave events
    def on_wave_cleared(self, number: int) -> None:
        bonus = self.waves.clear_gold(number)
        self.add_gold(bonus)
        events.emit("wave_cleared", number=number, bonus=bonus)

    def on_all_waves_cleared(self) -> None:
        if not self.over:
            self._finish(True)

    def _finish(self, won: bool) -> None:
        self.over = True
        self.won = won
        events.emit("match_over", match=self, won=won)

    @property
    def wave_reached(self) -> int:
        return self.waves.number

    def score(self) -> int:
        cleared = len(self.waves.cleared)
        base = cleared * 100 + self.stats["kills"] * 2 + (max(0, self.lives) * 150 + 5000 if self.won else 0)
        return int(base * float(self.diff.get("score", 1.0)))

    # ================================================================== simulation
    def update(self, dt: float) -> None:
        if dt <= 0 or self.over:
            return
        self.time += dt
        self.waves.update(dt)
        for c in self.creeps:
            if c.alive:
                c.update(dt)
        if self._born:
            self.creeps.extend(self._born)
            self._born = []
        self.combat.arr.rebuild(self.creeps)
        for t in self.towers:
            t.update(dt)
        self.combat.update(dt)
        self.spells.update(dt)
        if self._born:
            self.creeps.extend(self._born)
            self._born = []
        if self._dirty:
            self._dirty = False
            self.creeps = [c for c in self.creeps if c.alive]

    # ================================================================== presentation
    def present(self, dt: float, real_dt: float) -> None:
        """Per-frame visual sync: nodes, animation, overlays, effects, lighting."""
        if not self.visual:
            return
        cam = self.cam
        base = self.base
        self.env.focus = (cam.fx, cam.fz)
        self.env.update(dt, base.cam.getPos(base.render))
        self.level.update(real_dt, self.lives / max(1, self.max_lives))
        self._frame += 1
        creeps = self.creeps
        n = len(creeps)
        if n:
            pts = np.empty((n, 3), np.float32)
            pts[:, 0] = np.fromiter((c.x for c in creeps), np.float32, n)
            pts[:, 1] = np.fromiter((c.y + c.height * 0.5 for c in creeps), np.float32, n)
            pts[:, 2] = np.fromiter((c.z for c in creeps), np.float32, n)
            ui, front = cam.project(pts)
            from ursina import window
            ax = window.aspect_ratio * 0.5 + 0.15
            onscr = front & (np.abs(ui[:, 0]) < ax) & (np.abs(ui[:, 1]) < 0.65)
            n_on = int(onscr.sum())
            stride = 1 if n_on < 70 else 2 if n_on < 160 else 3
            for i, c in enumerate(creeps):
                c.sync_node(dt)
                c.on_screen = bool(onscr[i])
                if c.on_screen:
                    c.anim_dt += dt
                    if (i + self._frame) % stride == 0:
                        c.animate(c.anim_dt)
                        c.anim_dt = 0.0
        if self.corpses:
            keep = []
            for c in self.corpses:
                c.corpse_t += dt
                if c.corpse_t >= CORPSE_TIME:
                    c.destroy()
                else:
                    c.sync_node(dt)
                    c.animate(dt)
                    keep.append(c)
            self.corpses = keep
        for t in self.towers:
            if t.view is not None:
                t.view.update(dt)
            if t.kind == "beam" and t.beam_target is not None and t.beam_target.alive and t.ready:
                tg = t.beam_target
                mx = float(t.stats["ramp"][0])
                self.fx.beam(t.muzzle(), (tg.x, tg.center_y(), tg.z), (t.ramp - 1.0) / max(mx - 1.0, 1e-3))
        self._draw_overlays()
        q = base.cam.getQuat(base.render)
        right, up = q.getRight(), q.getUp()
        cp = base.cam.getPos(base.render)
        self.fx.update(dt, np.array([right.x, right.y, right.z], np.float32), np.array([up.x, up.y, up.z], np.float32),
                       np.array([cp.x, cp.y, cp.z], np.float32))
        self.fx.update_text(real_dt)

    def _draw_overlays(self) -> None:
        lv = self.level
        # blob shadows
        sh_r, sh_c = [], []
        for c in self.creeps:
            if c.on_screen:
                gy = lv.height(c.x, c.z) if c.flying else c.y
                sh_r.append((c.x, gy + 0.05, c.z, c.radius * (1.5 if c.flying else 1.25)))
                sh_c.append((1, 1, 1, 0.38 if c.flying else 0.55))
        for c in self.corpses:
            sh_r.append((c.x, lv.height(c.x, c.z) + 0.05, c.z, c.radius * 1.2))
            sh_c.append((1, 1, 1, 0.5 * max(0.0, 1.0 - c.corpse_t / CORPSE_TIME)))
        self.shadows.set(np.asarray(sh_r, np.float32).reshape(-1, 4), np.asarray(sh_c, np.float32).reshape(-1, 4))
        # selection + range circles
        rg_r, rg_c, rn_r, rn_c, fl_r, fl_c = [], [], [], [], [], []
        sel = self.selected
        if isinstance(sel, Tower) and sel in self.towers:
            rg_r.append((sel.x, sel.y + 0.1, sel.z, 1.3))
            rg_c.append((0.35, 1.0, 0.4, 0.95))
            col = (1.0, 0.85, 0.35) if sel.kind == "aura" else (1.0, 1.0, 1.0)
            rn_r.append((sel.x, sel.y + 0.12, sel.z, sel.range))
            rn_c.append(col + (0.85,))
            fl_r.append((sel.x, sel.y + 0.1, sel.z, sel.range))
            fl_c.append(col + (0.08,))
            nxt = sel.next_stats()
            if nxt is not None and float(nxt.get("range", 0)) > float(sel.stats.get("range", 0)) and self.show_all_bars:
                rn_r.append((sel.x, sel.y + 0.12, sel.z, float(nxt["range"])))
                rn_c.append((0.5, 1.0, 0.6, 0.5))
        elif isinstance(sel, Creep) and sel.alive:
            rg_r.append((sel.x, lv.height(sel.x, sel.z) + 0.1, sel.z, sel.radius * 1.4 + 0.2))
            rg_c.append((1.0, 0.3, 0.25, 0.95))
        bp = self.build_preview
        cur_r, cur_c = [], []
        if bp is not None and bp.get("cell", -1) >= 0:
            x, z = self.grid.center(bp["cell"])
            y = lv.cell_y[bp["cell"]]
            ok = bp.get("ok", False)
            col = (0.4, 1.0, 0.5) if ok else (1.0, 0.3, 0.25)
            cur_r.append((x, y + 0.09, z, CELL * 0.5))
            cur_c.append(col + (0.9,))
            if bp.get("range"):
                rn_r.append((x, y + 0.12, z, bp["range"]))
                rn_c.append(col + (0.9,))
                fl_r.append((x, y + 0.1, z, bp["range"]))
                fl_c.append(col + (0.09,))
        sp = self.spell_preview
        if sp is not None:
            x, z, r, col = sp
            y = lv.height(x, z)
            rn_r.append((x, y + 0.14, z, r))
            rn_c.append(tuple(col) + (0.95,))
            fl_r.append((x, y + 0.12, z, r))
            fl_c.append(tuple(col) + (0.16,))
        if self.show_all_bars and bp is None:
            for t in self.towers[:20]:
                if t is not sel and t.kind != "aura":
                    rn_r.append((t.x, t.y + 0.12, t.z, t.range))
                    rn_c.append((1.0, 1.0, 1.0, 0.22))
        f32 = np.float32
        self.rings.set(np.asarray(rg_r, f32).reshape(-1, 4), np.asarray(rg_c, f32).reshape(-1, 4))
        self.ranges.set(np.asarray(rn_r, f32).reshape(-1, 4), np.asarray(rn_c, f32).reshape(-1, 4))
        self.fills.set(np.asarray(fl_r, f32).reshape(-1, 4), np.asarray(fl_c, f32).reshape(-1, 4))
        self.cursor.set(np.asarray(cur_r, f32).reshape(-1, 4), np.asarray(cur_c, f32).reshape(-1, 4))
        self._draw_build_grid(bp is not None)
        self._draw_routes(bp is not None or self.show_paths or self.show_all_bars)
        self._draw_bars()

    def _draw_build_grid(self, show: bool) -> None:
        """Tint every free buildable cell while a tower is being placed."""
        if not show:
            if not self.cells.node.isHidden():
                self.cells.node.hide()
            return
        if self.cells.node.isHidden():
            self.cells.node.show()
        g = self.grid
        if self._cells_ver == g.version and self._cells_n == len(self.towers):
            return
        self._cells_ver, self._cells_n = g.version, len(self.towers)
        rects, cols = [], []
        for c in range(g.n):
            if g.buildable[c] and g.tower[c] is None:
                x, z = g.center(c)
                rects.append((x, self.level.cell_y[c] + 0.07, z, CELL * 0.5))
                cols.append((0.55, 1.0, 0.6, 0.5))
        self.cells.set(np.asarray(rects, np.float32).reshape(-1, 4), np.asarray(cols, np.float32).reshape(-1, 4))

    def _route_arrays(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """Polyline points and cumulative lengths for every ground route (cached per grid version)."""
        g = self.grid
        src = self.preview_routes
        if src is None and self._routes_ver == g.version:
            return self._routes
        lines = src if src is not None else [g.route(i) for i in range(len(g.gates))]
        out = []
        for pts in lines:
            if len(pts) < 2:
                continue
            p = np.asarray(pts, np.float32)
            seg = np.linalg.norm(np.diff(p, axis=0), axis=1)
            out.append((p, np.concatenate([[0.0], np.cumsum(seg)]).astype(np.float32)))
        if src is None:
            self._routes, self._routes_ver = out, g.version
        return out

    def _draw_routes(self, show: bool) -> None:
        """Marching dots along the creeps' route (and the straight line flyers take)."""
        if not show:
            if self.dots.count:
                self.dots.clear()
            return
        lv = self.level
        spacing = 1.25
        phase = (self.game.time * 2.2) % spacing if self.game is not None else 0.0
        rects, cols = [], []
        col = (1.0, 0.72, 0.3, 0.9) if self.preview_routes is not None else (1.0, 0.92, 0.55, 0.8)
        for p, cum in self._route_arrays():
            total = float(cum[-1])
            s = np.arange(phase, total, spacing, dtype=np.float32)
            xs = np.interp(s, cum, p[:, 0])
            zs = np.interp(s, cum, p[:, 1])
            for x, z in zip(xs.tolist(), zs.tolist()):
                rects.append((x, lv.height(x, z) + 0.1, z, 0.17))
                cols.append(col)
        ex, ez = lv.exit_pos
        for gi in lv.gate_info:
            gx, gz = gi["pos"]
            total = math.hypot(ex - gx, ez - gz)
            k = int((total - phase) / (spacing * 1.6)) + 1
            for j in range(k):
                f = (phase + j * spacing * 1.6) / total
                x, z = gx + (ex - gx) * f, gz + (ez - gz) * f
                rects.append((x, max(lv.height(x, z), 0.0) + 0.14, z, 0.11))
                cols.append((0.6, 0.85, 1.0, 0.55))
        self.dots.set(np.asarray(rects, np.float32).reshape(-1, 4), np.asarray(cols, np.float32).reshape(-1, 4))

    def _draw_bars(self) -> None:
        """2D health bars above damaged creeps, bosses and the selected creep; progress bars on busy towers."""
        show_all = self.show_all_bars
        sel = self.selected
        items = [c for c in self.creeps if c.on_screen and (show_all or c.boss or c is sel or c.hp < c.max_hp)]
        busy = [t for t in self.towers if t.state != "ready"]
        if not items and not busy:
            if self.bars.count:
                self.bars.clear()
            return
        n = len(items) + len(busy)
        pts = np.empty((n, 3), np.float32)
        for i, c in enumerate(items):
            pts[i] = (c.x, c.y + c.height + 0.45, c.z)
        for j, t in enumerate(busy):
            pts[len(items) + j] = (t.x, t.y + 0.4, t.z)
        ui, front = self.cam.project(pts)
        zoom = 48.0 / max(self.cam.distance, 10.0)
        rects, cols = [], []
        for i in range(n):
            if not front[i]:
                continue
            x, y = float(ui[i, 0]), float(ui[i, 1])
            if y > 0.455 or y < -0.24:
                continue
            if i < len(items):
                c = items[i]
                w = min(0.11, max(0.018, 0.02 * zoom * (0.8 + c.radius))) * (1.6 if c.boss else 1.0)
                h = max(0.0035, 0.005 * zoom ** 0.5) * (1.5 if c.boss else 1.0)
                f = max(0.0, min(1.0, c.hp_frac))
                fill = (0.75, 0.3, 0.95, 1.0) if c.boss else (min(1.0, 2.0 - 2.0 * f), min(1.0, 2.0 * f) * 0.9, 0.12, 1.0)
            else:
                t = busy[i - len(items)]
                w, h = 0.034 * zoom, max(0.0035, 0.005 * zoom ** 0.5)
                f = 1.0 - max(0.0, t.busy_t) / max(t.busy_total, 1e-3)
                fill = (0.95, 0.85, 0.4, 1.0)
            x0, x1 = x - w / 2, x + w / 2
            rects.append((x0 - 0.0015, y - 0.0015, x1 + 0.0015, y + h + 0.0015))
            cols.append((0.02, 0.02, 0.02, 0.85))
            rects.append((x0, y, x0 + w * f, y + h))
            cols.append(fill)
        self.bars.set(np.asarray(rects, np.float32).reshape(-1, 4), np.asarray(cols, np.float32).reshape(-1, 4))

    # ================================================================== teardown
    def destroy(self) -> None:
        self.combat.clear()
        if not self.visual:
            return
        self.fx.clear()
        self.bars.destroy()
        for c in self.creeps + self.corpses + self._born:
            c.destroy()
        for t in self.towers:
            t.destroy()
        self.level.destroy()
        self.root.removeNode()
        self.env.sky.removeNode()
        self.base.render.clearShader()
        events.emit("match_destroyed", match=self)
