"""Mouse and keyboard control of a match: tower placement, selection, powers, hotkeys."""
from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

import numpy as np
from panda3d.core import TransparencyAttrib
from ursina import held_keys, mouse

from core import data
from core.events import events
from creeps.creep import Creep
from settings import PLAYER_COLOR
from towers.tower import MODE_NAMES, Tower

if TYPE_CHECKING:
    from core.game import Game

SPELL_COLORS = {"meteor": (1.0, 0.5, 0.2), "frost_nova": (0.6, 0.88, 1.0), "overcharge": (1.0, 0.9, 0.35)}


class Controls:
    """Translates player input into builds, upgrades, sales, spell casts and selections."""

    def __init__(self, game: "Game") -> None:
        self.game = game
        self.match = game.match
        self.mode: str | None = None          # None | "build" | "spell"
        self.mode_arg: Any = None
        self.ghost = None
        self.hover_cell = -1
        self.hover_ok = False
        self.hover_reason = ""
        self._trial_key: tuple | None = None
        self._trial_t = 0.0
        self.enabled = True
        self.tower_keys = {t["hotkey"]: tid for tid, t in data.towers().items()}
        self.spell_keys = {s["hotkey"]: sid for sid, s in data.spells().items()}

    # ================================================================== helpers
    def over_ui(self) -> bool:
        # world picking is done by hand, so anything Ursina reports as hovered is a UI element
        return mouse.hovered_entity is not None

    def notify(self, text: str, error: bool = True) -> None:
        events.emit("notice", text=text, color=(1.0, 0.45, 0.4) if error else (1.0, 0.85, 0.4))
        if error:
            events.emit("sound", name="error")

    def ground_point(self) -> tuple[float, float] | None:
        return self.match.cam.ground_under_mouse()

    def cell_under_mouse(self) -> int:
        gp = self.ground_point()
        return self.match.grid.cell_of(gp[0], gp[1]) if gp is not None else -1

    def select(self, obj: Any, sound: bool = True) -> None:
        m = self.match
        if m.selected is obj:
            return
        m.selected = obj
        events.emit("selection_changed", selected=obj)
        if obj is not None and sound:
            events.emit("sound", name="select")

    # ================================================================== picking
    def pick(self, mx: float | None = None, my: float | None = None) -> Any:
        """The creep or tower under the cursor (creeps win, then the tower drawn in front)."""
        m = self.match
        cam = m.cam
        if mx is None:
            mx, my = mouse.x, mouse.y
        q = self.game.base.cam.getQuat(self.game.base.render)
        r = q.getRight()
        right = np.array([r.x, r.y, r.z], np.float32)
        creeps = [c for c in m.creeps if c.alive and c.on_screen]
        if creeps:
            pts = np.array([(c.x, c.y + c.height * 0.5, c.z) for c in creeps], np.float32)
            radii = np.array([max(c.radius * 1.3, 0.6) for c in creeps], np.float32)
            ui, front = cam.project(pts)
            ui2, _ = cam.project(pts + right[None, :] * radii[:, None])
            rad = np.linalg.norm(ui2 - ui, axis=1)
            d = np.hypot(ui[:, 0] - mx, ui[:, 1] - my)
            ok = front & (d <= rad + 0.004)
            if ok.any():
                return creeps[int(np.argmin(np.where(ok, d / np.maximum(rad, 1e-3), np.inf)))]
        best, best_z = None, 1e9
        for t in m.towers:
            top = t.y + (t.view.parts.pivot if t.view is not None else 2.5) + 0.4
            a = cam.project_one(t.x, t.y + 0.2, t.z)
            b = cam.project_one(t.x, top, t.z)
            e = cam.project_one(t.x + right[0] * 0.95, t.y + 1.0 + right[1] * 0.95, t.z + right[2] * 0.95)
            c0 = cam.project_one(t.x, t.y + 1.0, t.z)
            if a is None or b is None or e is None or c0 is None:
                continue
            rad = math.hypot(e[0] - c0[0], e[1] - c0[1])
            # distance from the cursor to the tower's projected vertical axis
            vx, vy = b[0] - a[0], b[1] - a[1]
            ll = vx * vx + vy * vy
            k = 0.0 if ll < 1e-9 else max(0.0, min(1.0, ((mx - a[0]) * vx + (my - a[1]) * vy) / ll))
            if math.hypot(mx - (a[0] + vx * k), my - (a[1] + vy * k)) <= rad and t.z < best_z:
                best, best_z = t, t.z
        if best is not None:
            return best
        return m.tower_at(self.cell_under_mouse())

    # ================================================================== input
    def input(self, key: str) -> None:
        if not self.enabled or self.match is None or self.match.config.demo:
            return
        m = self.match
        if key == "left mouse down":
            if self.over_ui():
                return
            if self.mode == "build":
                self.try_place(bool(held_keys["shift"]))
            elif self.mode == "spell":
                self.try_cast()
            else:
                self.select(self.pick())
        elif key == "right mouse down":
            if self.mode is not None:
                self.cancel_mode()
            elif not self.over_ui():
                self.select(None)
        elif key == "middle mouse down":
            m.cam.begin_drag()
        elif key == "middle mouse up":
            m.cam.end_drag()
        elif key == "scroll up":
            if not self.over_ui():
                m.cam.zoom(1)
        elif key == "scroll down":
            if not self.over_ui():
                m.cam.zoom(-1)
        elif key == "escape":
            if self.mode is not None:
                self.cancel_mode()
            else:
                self.select(None)
        elif key in ("space", "n"):
            self.send_wave()
        elif key == "tab":
            self.game.cycle_speed()
        elif key == "g":
            m.show_paths = not m.show_paths
        elif key == "alt":
            m.show_all_bars = True
        elif key == "alt up":
            m.show_all_bars = False
        elif key in ("home", "backspace"):
            m.cam.look_at(0.0, -5.0)
        elif key == "u":
            self.upgrade_selected()
        elif key in ("x", "delete"):
            self.sell_selected()
        elif key == "t":
            self.cycle_mode_selected()
        elif key in self.tower_keys and not held_keys["control"]:
            self.begin_build(self.tower_keys[key])
        elif key in self.spell_keys:
            self.begin_spell(self.spell_keys[key])

    # ================================================================== actions
    def send_wave(self) -> None:
        m = self.match
        if m.waves.send_now():
            events.emit("sound", name="click")
        elif not m.waves.has_next:
            self.notify("That was the last wave", error=False)
        elif m.waves.running:
            self.notify("The current wave is still arriving")

    def upgrade_selected(self) -> None:
        t = self.match.selected
        if isinstance(t, Tower):
            err = self.match.upgrade_tower(t)
            if err:
                self.notify(err)

    def sell_selected(self) -> None:
        t = self.match.selected
        if isinstance(t, Tower):
            self.match.sell_tower(t)
            events.emit("selection_changed", selected=None)

    def cycle_mode_selected(self) -> None:
        t = self.match.selected
        if isinstance(t, Tower) and t.kind != "aura":
            mode = t.cycle_mode()
            events.emit("notice", text=f"Targeting: {MODE_NAMES[mode]}", color=(0.8, 0.9, 1.0))
            events.emit("sound", name="click")

    # ================================================================== modes
    def begin_build(self, type_id: str) -> None:
        m = self.match
        if self.mode == "build" and self.mode_arg == type_id:
            self.cancel_mode()
            return
        cost = int(data.tower_level(type_id, 1)["cost"])
        if m.gold < cost:
            self.notify(f"Not enough gold for {data.towers()[type_id]['name']} ({cost})")
            return
        self.cancel_mode()
        self.select(None, sound=False)
        self.mode, self.mode_arg = "build", type_id
        self._make_ghost(type_id)
        self._trial_key = None
        events.emit("mode_changed", mode="build", arg=type_id)
        events.emit("sound", name="click")

    def begin_spell(self, sid: str) -> None:
        m = self.match
        if self.mode == "spell" and self.mode_arg == sid:
            self.cancel_mode()
            return
        if not m.spells.ready(sid):
            left, _ = m.spells.cooldown(sid)
            self.notify(f"{data.spells()[sid]['name']} is ready in {int(left + 0.99)}s")
            return
        self.cancel_mode()
        self.mode, self.mode_arg = "spell", sid
        events.emit("mode_changed", mode="spell", arg=sid)
        events.emit("sound", name="click")

    def cancel_mode(self) -> None:
        m = self.match
        if self.mode is None and self.ghost is None:
            return
        self.mode = None
        self.mode_arg = None
        if self.ghost is not None:
            self.ghost.removeNode()
            self.ghost = None
        if m is not None:
            m.build_preview = None
            m.spell_preview = None
            m.preview_routes = None
        events.emit("mode_changed", mode=None, arg=None)

    def try_place(self, keep: bool) -> bool:
        m = self.match
        type_id = self.mode_arg
        cell = self.cell_under_mouse()
        ok, reason = m.can_place(type_id, cell)
        if not ok:
            self.notify(reason)
            return False
        t = m.build_tower(type_id, cell)
        if t is None:
            return False
        self._trial_key = None
        cost = int(data.tower_level(type_id, 1)["cost"])
        if not keep or m.gold < cost:
            self.cancel_mode()
        return True

    def try_cast(self) -> bool:
        m = self.match
        gp = self.ground_point()
        if gp is None:
            return False
        err = m.spells.cast(self.mode_arg, gp[0], gp[1])
        if err:
            self.notify(err)
            return False
        self.cancel_mode()
        return True

    # ================================================================== ghost
    def _make_ghost(self, type_id: str) -> None:
        from towers.models import tower_protos
        protos = tower_protos(type_id, 1, PLAYER_COLOR)
        node = self.match.fx_root.attachNewNode("ghost")
        protos["base"].copyTo(node)
        if protos["glow"] is not None:
            protos["glow"].copyTo(node)
        head = node.attachNewNode("head")
        head.setPos(0, protos["parts"].pivot, 0)
        head.setH(180)
        for name in ("head", "head_glow"):
            if protos[name] is not None:
                protos[name].copyTo(head)
        node.setTransparency(TransparencyAttrib.M_alpha)
        node.setDepthWrite(False)
        node.setBin("transparent", 40)
        node.setShaderInput("u_emissive", 0.55)
        node.hide()
        self.ghost = node

    def update(self, dt: float) -> None:
        m = self.match
        if m is None or m.config.demo:
            return
        if self.mode == "build":
            self._update_build(dt)
        elif self.mode == "spell":
            gp = None if self.over_ui() else self.ground_point()
            sid = self.mode_arg
            if gp is None:
                m.spell_preview = None
            else:
                m.spell_preview = (gp[0], gp[1], float(data.spells()[sid]["radius"]), SPELL_COLORS.get(sid, (1, 1, 1)))
            if not m.spells.ready(sid):
                self.cancel_mode()

    def _update_build(self, dt: float) -> None:
        m = self.match
        type_id = self.mode_arg
        cell = -1 if self.over_ui() else self.cell_under_mouse()
        self.hover_cell = cell
        if cell < 0:
            if self.ghost is not None:
                self.ghost.hide()
            m.build_preview = {"cell": -1}
            m.preview_routes = None
            return
        self._trial_t -= dt
        key = (cell, m.grid.version, type_id)
        if key != self._trial_key or self._trial_t <= 0:
            self._trial_key = key
            self._trial_t = 0.25
            self.hover_ok, self.hover_reason = m.can_place(type_id, cell)
            if self.hover_ok and m.last_trial is not None:
                dist, nxt = m.last_trial
                m.preview_routes = [m.grid.route(i, dist, nxt) for i in range(len(m.grid.gates))]
            else:
                m.preview_routes = None
        st = data.tower_level(type_id, 1)
        m.build_preview = {"cell": cell, "ok": self.hover_ok, "range": float(st.get("range", 0.0)), "type": type_id}
        if self.ghost is not None:
            x, z = m.grid.center(cell)
            self.ghost.show()
            self.ghost.setPos(x, m.level.cell_y[cell], z)
            if self.hover_ok:
                self.ghost.setColorScale(0.6, 1.35, 0.7, 0.62)
            else:
                self.ghost.setColorScale(1.6, 0.45, 0.4, 0.55)

    def destroy(self) -> None:
        self.cancel_mode()
