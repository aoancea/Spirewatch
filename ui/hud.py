"""In-match HUD: lives / gold / wave bar, upcoming waves, selection panel, build card with powers, notices."""
from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING, Any

from ursina import Entity, Text, camera, color, window

from core import data
from core.events import events
from creeps.creep import Creep
from settings import SPEEDS
from towers.tower import MODE_HELP, MODE_NAMES, Tower
from ui.icons import icon, mode_icon, spell_icon
from ui.portraits import creep_portrait, render_portraits, tower_portrait
from ui.widgets import GOLD, PANEL_BORDER, TEXT, Bar, IconSlot, Tooltip, label, quad, rgb

if TYPE_CHECKING:
    from core.game import Game

PANEL_H = 0.212
DIM = color.rgb(0.7, 0.68, 0.62)
C_GOLD = (1.0, 0.85, 0.4)
C_WHITE = (0.95, 0.92, 0.85)
C_GREY = (0.7, 0.68, 0.62)
C_RED = (1.0, 0.42, 0.36)
C_GREEN = (0.55, 1.0, 0.6)
C_BLUE = (0.7, 0.88, 1.0)
TAG_COLORS = {"Boss": (1.0, 0.4, 0.35), "Flying": (0.6, 0.85, 1.0), "Fast": (1.0, 0.9, 0.45), "Armored": (0.8, 0.8, 0.86),
              "Regenerates": (0.5, 1.0, 0.55), "Healer": (0.5, 1.0, 0.55), "Splits": (0.7, 0.95, 0.45),
              "Ethereal": (0.75, 0.85, 1.0), "Warded": (0.85, 0.6, 1.0), "Swarm": (1.0, 0.7, 0.4)}
CARD_ORDER = ("arrow", "cannon", "frost", "ember", "storm", "venom", "prism", "ballista", "banner")


def wrap(text: str, width: int = 54, col: Any = C_WHITE) -> list[tuple[str, Any]]:
    return [(line, col) for line in textwrap.wrap(text, width)]


def versus(attack_type: str) -> tuple[list[str], list[str]]:
    """Armor types an attack type is strong / weak against."""
    c = data.combat()
    names = c["armor_names"]
    row = c["multipliers"].get(attack_type, {})
    return [names[a] for a, m in row.items() if m >= 1.2], [names[a] for a, m in row.items() if m <= 0.8]


def weak_to(armor_type: str) -> tuple[list[str], list[str]]:
    """Attack types that are strong / weak against an armor type."""
    c = data.combat()
    names = c["attack_names"]
    good = [names[a] for a, row in c["multipliers"].items() if a != "true" and row.get(armor_type, 1.0) >= 1.2]
    bad = [names[a] for a, row in c["multipliers"].items() if a != "true" and row.get(armor_type, 1.0) <= 0.8]
    return good, bad


def stat_lines(tdef: dict[str, Any], st: dict[str, Any]) -> list[str]:
    """Human-readable stats of one tower level."""
    kind = tdef["kind"]
    names = data.combat()["attack_names"]
    out = []
    if kind == "aura":
        a = st.get("aura", {})
        parts = [f"+{int(a['damage'] * 100)}% damage"] if a.get("damage") else []
        if a.get("rate"):
            parts.append(f"+{int(a['rate'] * 100)}% attack speed")
        if a.get("range"):
            parts.append(f"+{int(a['range'] * 100)}% range")
        out.append("Aura: " + ", ".join(parts))
        out.append(f"Aura radius {st['range']:g}")
        return out
    atk = names.get(tdef.get("attack_type", ""), "")
    if kind == "beam":
        out.append(f"Damage {st['damage']:g}/s, ramping to x{st['ramp'][0]:g} ({atk})")
    elif kind == "cone":
        out.append(f"Damage {st['damage'] / st['cooldown']:g}/s to all in the jet ({atk})")
    else:
        out.append(f"Damage {st['damage']:g} ({atk})   every {st['cooldown']:g}s")
    line = f"Range {st['range']:g}"
    if st.get("splash"):
        line += f"   Blast radius {st['splash']:g}"
    if st.get("chain"):
        line += f"   Jumps to {int(st['chain'][0])} creeps"
    if st.get("multishot"):
        line += f"   {int(st['multishot'])} targets"
    out.append(line)
    extra = []
    if st.get("slow"):
        extra.append(f"Slows {int(st['slow'][0] * 100)}% for {st['slow'][1]:g}s")
    if st.get("burn"):
        extra.append(f"Burn {st['burn'][0]:g}/s for {st['burn'][1]:g}s")
    if st.get("poison"):
        extra.append(f"Poison {st['poison'][0]:g}/s for {st['poison'][1]:g}s, armor -{st['poison'][2]:g}")
    if st.get("air_bonus"):
        extra.append(f"+{int(st['air_bonus'] * 100)}% vs flying")
    if extra:
        out.append(",  ".join(extra))
    return out


class HUD:
    """Created once; attached to each new match."""

    def __init__(self, game: "Game") -> None:
        self.game = game
        self.match = None
        self.root = Entity(parent=camera.ui, enabled=False)
        self.built = False
        self.notices: list[list[Any]] = []
        self._sel_sig: tuple = ()
        self._t = 0.0
        self._card_sig: tuple = ()
        self._wave_sig: tuple = ()
        self.banner_t = 0.0
        self.flash_a = 0.0
        events.on("notice", self._on_notice)
        events.on("wave_started", self._on_wave_started)
        events.on("wave_cleared", self._on_wave_cleared)
        events.on("creep_leaked", self._on_leak)
        events.on("selection_changed", lambda **_: self._force())
        events.on("mode_changed", lambda **_: self._force())

    # ================================================================== layout
    def _build(self) -> None:
        ar = window.aspect_ratio
        r = self.root
        L, R = -ar / 2, ar / 2
        bottom = -0.5
        cy = bottom + PANEL_H / 2
        # screen flash when a creep leaks (drawn under everything else)
        self.flash = quad(r, (0, 0), (ar, 1.0), color.rgba(0.9, 0.1, 0.05, 0.0), z=0.9)
        # bottom panel
        self.panel = quad(r, (0, cy), (ar, PANEL_H), color.rgba(0.07, 0.055, 0.045, 1.0), z=0.02)
        self.panel.collider = "box"
        quad(r, (0, bottom + PANEL_H + 0.002), (ar, 0.004), PANEL_BORDER, z=0.015)
        # top bar
        self.top = quad(r, (0, 0.5 - 0.02), (ar, 0.04), color.rgba(0.07, 0.055, 0.045, 0.92), z=0.02)
        self.top.collider = "box"
        quad(r, (0, 0.5 - 0.041), (ar, 0.003), PANEL_BORDER, z=0.015)
        ty = 0.48
        x = L + 0.03
        Entity(parent=r, model="quad", texture=icon("heart", (0.3, 0.12, 0.06)), scale=0.03, position=(x, ty, -0.01))
        self.lives_text = label(r, "20", (x + 0.024, ty + 0.013), 1.15, color.rgb(1.0, 0.72, 0.4))
        x += 0.16
        Entity(parent=r, model="quad", texture=icon("gold", (0.25, 0.2, 0.08)), scale=0.03, position=(x, ty, -0.01))
        self.gold_text = label(r, "0", (x + 0.024, ty + 0.013), 1.15, GOLD)
        self.wave_text = label(r, "", (0, ty + 0.014), 1.15, TEXT, origin=(0, 0.5))
        self.map_text = label(r, "", (L + 0.36, ty + 0.011), 0.85, DIM)
        # speed buttons + menu
        self.speed_btns: list[Entity] = []
        sx = R - 0.36
        for i, sp in enumerate(SPEEDS):
            b = Entity(parent=r, model="quad", scale=(0.048, 0.028), position=(sx + i * 0.054, ty, -0.01),
                       color=color.rgba(0.2, 0.14, 0.09, 1), collider="box")
            Text(parent=b, text=f"{sp:g}x", origin=(0, 0), scale=(19.0, 33.0), color=GOLD, z=-0.01)
            b.on_click = lambda i=i: self.game.set_speed(i)
            self.speed_btns.append(b)
        self.menu_btn = Entity(parent=r, model="quad", scale=(0.13, 0.028), position=(R - 0.08, ty, -0.01),
                               color=color.rgba(0.32, 0.18, 0.08, 1), collider="box")
        Text(parent=self.menu_btn, text="Menu (F10)", origin=(0, 0), scale=(7.0, 33.0), color=GOLD, z=-0.01)
        self.menu_btn.on_click = self._open_menu

        # ---- left: upcoming waves
        wx0 = L + 0.016
        self.wave_w = 0.5
        label(r, "INCOMING", (wx0, bottom + PANEL_H - 0.012), 0.8, DIM)
        self.send_btn = Entity(parent=r, model="quad", scale=(0.25, 0.03), position=(wx0 + self.wave_w - 0.125, bottom + PANEL_H - 0.024, -0.01),
                               color=color.rgba(0.45, 0.25, 0.06, 1), collider="box")
        self.send_text = Text(parent=self.send_btn, text="", origin=(0, 0), scale=(3.6, 30.0), color=GOLD, z=-0.01)
        self.send_btn.on_click = self._send
        self.wave_rows: list[dict[str, Any]] = []
        for i in range(3):
            y = bottom + PANEL_H - 0.07 - i * 0.049
            slot = IconSlot(r, (wx0 + 0.024, y), 0.044, tooltip=self._wave_tip)
            slot.data = i
            row = {"slot": slot, "title": label(r, "", (wx0 + 0.056, y + 0.02), 0.9, TEXT),
                   "tags": label(r, "", (wx0 + 0.056, y - 0.003), 0.74, DIM), "number": 0}
            self.wave_rows.append(row)

        # ---- right: build card (towers + powers)
        size, gap = 0.056, 0.006
        step = size + gap
        cx = R - 0.02 - step * 1.5 - size / 2
        self.card: list[IconSlot] = []
        for i in range(12):
            rr, cc = divmod(i, 4)
            s = IconSlot(r, (cx - step * 1.5 + cc * step, cy + step - rr * step - 0.002), size, on_click=self._card_click,
                         tooltip=self._card_tip)
            s.data = i
            s.count_text.color = GOLD
            if i < len(CARD_ORDER):
                quad(s, (0, -0.35), (0.9, 0.22), color.rgba(0, 0, 0, 0.6), z=-0.015)
            self.card.append(s)
        self.card_ids: list[tuple[str, str] | None] = [("tower", t) for t in CARD_ORDER] + [("spell", s) for s in data.spells()]
        self.card_ids += [None] * (12 - len(self.card_ids))
        self.card_x0 = cx - step * 2 - 0.02

        # ---- centre: selection
        sx0 = wx0 + self.wave_w + 0.03
        self.sel_x0 = sx0
        quad(r, (sx0 - 0.014, cy), (0.003, PANEL_H - 0.03), color.rgba(0.3, 0.24, 0.16, 1), z=0.01)
        quad(r, (self.card_x0, cy), (0.003, PANEL_H - 0.03), color.rgba(0.3, 0.24, 0.16, 1), z=0.01)
        self.sel_root = Entity(parent=r)
        self.portrait = Entity(parent=self.sel_root, model="quad", scale=0.13, position=(sx0 + 0.07, cy + 0.012, -0.01))
        self.portrait_frame = quad(self.sel_root, (sx0 + 0.07, cy + 0.012), (0.136, 0.136), PANEL_BORDER, z=0.0)
        tx = sx0 + 0.15
        self.name_text = label(self.sel_root, "", (tx, cy + 0.094), 1.3, GOLD)
        self.sub_text = label(self.sel_root, "", (tx, cy + 0.062), 0.82, DIM)
        self.stats_text = label(self.sel_root, "", (tx, cy + 0.036), 0.8, TEXT, line_height=1.3)
        self.extra_text = label(self.sel_root, "", (tx + 0.43, cy + 0.094), 0.74, color.rgb(0.78, 0.95, 0.78), line_height=1.3)
        self.hp_bar = Bar(self.sel_root, (tx, cy + 0.028), (0.3, 0.018), color.rgb(0.25, 0.75, 0.25), text_scale=0.75)
        self.level_text = label(self.sel_root, "", (sx0 + 0.07, cy - 0.066), 0.85, GOLD, origin=(0, 0.5))
        # tower actions
        ax = self.card_x0 - 0.05
        self.actions: list[IconSlot] = []
        for i, (key, fn) in enumerate((("U", self._act_upgrade), ("T", self._act_mode), ("X", self._act_sell))):
            s = IconSlot(self.sel_root, (ax, cy + 0.062 - i * 0.062), 0.054, on_click=fn, tooltip=self._action_tip, key_label=key)
            s.data = i
            s.count_text.color = GOLD
            if i != 1:
                quad(s, (0, -0.35), (0.9, 0.22), color.rgba(0, 0, 0, 0.6), z=-0.015)
            self.actions.append(s)
        self.hint_text = label(r, "", (sx0, cy + 0.09), 0.85, DIM, line_height=1.3)

        # ---- overlays
        self.notice_root = Entity(parent=r, position=(0, 0.36, -0.5))
        self.banner = Text(parent=r, text="", position=(0, 0.26, -0.5), origin=(0, 0), scale=2.6, color=GOLD)
        self.banner_sub = Text(parent=r, text="", position=(0, 0.205, -0.5), origin=(0, 0), scale=1.15, color=TEXT)
        self.mode_text = label(r, "", (0, bottom + PANEL_H + 0.04), 1.0, color.rgb(0.85, 1.0, 0.85), origin=(0, 0), z=-0.5)
        self.built = True

    # ================================================================== match lifecycle
    def attach(self, match) -> None:
        self.match = match
        render_portraits(self.game.base)
        if not self.built:
            self._build()
        self.root.enabled = True
        self._sel_sig = self._card_sig = self._wave_sig = ()
        self.banner.text = self.banner_sub.text = ""
        self.banner_t = 0.0
        self.flash_a = 0.0
        d = match.diff
        self.map_text.text = f"{match.level.name}  -  {d['name']}" + ("  -  Endless" if match.config.endless else "")
        self.set_visible(not match.config.demo)

    def detach(self) -> None:
        self.match = None
        self.root.enabled = False
        Tooltip.get().hide()
        for n in self.notices:
            n[0].disable()
        self.notices.clear()

    def set_visible(self, visible: bool) -> None:
        self.root.enabled = visible
        if not visible:
            Tooltip.get().hide()

    def _open_menu(self) -> None:
        if self.game.menus is not None:
            self.game.menus.show_pause()

    def _force(self) -> None:
        self._sel_sig = ()
        self._card_sig = ()

    # ================================================================== events
    def _notice(self, text: str, col: Any = C_GOLD, life: float = 2.6) -> None:
        if not self.built or not self.root.enabled:
            return
        for n in self.notices:
            n[0].y -= 0.032
        t = label(self.notice_root, text, (0, 0), 1.05, rgb(col), origin=(0, 0))
        self.notices.append([t, life, col])
        if len(self.notices) > 4:
            old = self.notices.pop(0)
            old[0].disable()

    def _on_notice(self, text: str = "", color: Any = C_GOLD, **_: Any) -> None:
        if self.match is not None:
            self._notice(text, color)

    def _on_wave_started(self, number: int = 0, info: dict | None = None, **_: Any) -> None:
        m = self.match
        if m is None or not self.built or m.config.demo:
            return
        info = info or {}
        boss = info.get("boss")
        total = "" if m.config.endless else f" / {m.waves.total}"
        self.banner.text = f"Wave {number}{total}"
        self.banner.color = color.rgb(1.0, 0.4, 0.35) if boss else GOLD
        sub = info.get("name", "")
        tags = ", ".join(info.get("tags", []))
        self.banner_sub.text = sub + (f"   ({tags})" if tags else "")
        self.banner_t = 3.6 if boss else 2.8
        events.emit("sound", name="boss_warning" if boss else "wave_start", volume=0.8)

    def _on_wave_cleared(self, number: int = 0, bonus: int = 0, **_: Any) -> None:
        m = self.match
        if m is None or m.config.demo or m.over:
            return
        self._notice(f"Wave {number} cleared:  +{bonus} gold", C_GREEN, 2.4)
        events.emit("sound", name="wave_clear", volume=0.7)

    def _on_leak(self, creep=None, lives: int = 0, **_: Any) -> None:
        if self.match is not None and not self.match.config.demo:
            self.flash_a = 0.35

    # ================================================================== input
    def input(self, key: str) -> bool:
        if self.match is None or not self.built or not self.root.enabled:
            return False
        if key == "f10":
            self._open_menu()
            return True
        if key in ("left mouse down", "right mouse down") and (self.panel.hovered or self.top.hovered):
            return True
        if key == "escape":
            ctl = self.game.controls
            if ctl is not None and (ctl.mode is not None or self.match.selected is not None):
                return False             # the controls cancel the mode / selection
            self._open_menu()
            return True
        return False

    def _send(self) -> None:
        if self.game.controls is not None:
            self.game.controls.send_wave()

    # ------------------------------------------------------------------ build card
    def _card_click(self, slot: IconSlot) -> None:
        item = self.card_ids[slot.data]
        ctl = self.game.controls
        if item is None or ctl is None:
            return
        if item[0] == "tower":
            ctl.begin_build(item[1])
        else:
            ctl.begin_spell(item[1])

    def _card_tip(self, slot: IconSlot) -> list | None:
        item = self.card_ids[slot.data]
        m = self.match
        if item is None or m is None:
            return None
        if item[0] == "spell":
            s = data.spells()[item[1]]
            left, total = m.spells.cooldown(item[1])
            lines = [(f"{s['name']}  [{s['hotkey'].upper()}]", C_GOLD)] + wrap(s["description"])
            if item[1] == "meteor":
                lines.append((f"Damage {int(m.spells.meteor_damage())} (Blast, grows each wave)   Radius {s['radius']:g}", C_WHITE))
            else:
                lines.append((f"Radius {s['radius']:g}", C_WHITE))
            lines.append((f"Cooldown {int(total)}s" + (f"   -   ready in {int(left + 0.99)}s" if left > 0 else "   -   ready"),
                          C_RED if left > 0 else C_GREEN))
            return lines
        t = data.towers()[item[1]]
        st = t["levels"][0]
        cost = int(st["cost"])
        lines = [(f"{t['name']}  [{t['hotkey'].upper()}]", C_GOLD), (f"Cost: {cost} gold", C_WHITE if m.gold >= cost else C_RED)]
        lines += wrap(t["description"])
        lines += [(ln, C_WHITE) for ln in stat_lines(t, st)]
        if t["kind"] != "aura":
            lines.append(("Hits: " + " and ".join(t["targets"]), C_GREY))
            strong, weak = versus(t["attack_type"])
            if strong or weak:
                lines.append((("Strong vs " + ", ".join(strong) if strong else "") + ("   " if strong and weak else "")
                              + ("Weak vs " + ", ".join(weak) if weak else ""), C_BLUE))
        ups = t["levels"][1:]
        lines.append(("Upgrades: " + "  >  ".join(f"{u['name']} ({u['cost']})" for u in ups), C_GREY))
        return lines

    def _refresh_card(self) -> None:
        m = self.match
        ctl = self.game.controls
        mode = (ctl.mode, ctl.mode_arg) if ctl is not None else (None, None)
        sig = (m.gold, mode, tuple(int(m.spells.cd[s] > 0) for s in m.spells.order))
        if sig != self._card_sig:
            self._card_sig = sig
            for slot, item in zip(self.card, self.card_ids):
                if item is None:
                    slot.set_icon(None)
                    slot.key_text.text = ""
                    continue
                if item[0] == "tower":
                    t = data.towers()[item[1]]
                    cost = int(t["levels"][0]["cost"])
                    active = mode == ("build", item[1])
                    slot.set_icon(tower_portrait(item[1], 1), border=(1.0, 0.85, 0.3) if active else None, count=cost)
                    slot.count_text.color = GOLD if m.gold >= cost else color.rgb(1.0, 0.4, 0.35)
                    slot.key_text.text = t["hotkey"].upper()
                    slot.set_dim(m.gold < cost)
                else:
                    s = data.spells()[item[1]]
                    active = mode == ("spell", item[1])
                    slot.set_icon(spell_icon(item[1]), border=(1.0, 0.85, 0.3) if active else (0.5, 0.4, 0.7))
                    slot.key_text.text = s["hotkey"].upper()
                    slot.set_dim(False)
        for slot, item in zip(self.card, self.card_ids):
            if item is not None and item[0] == "spell":
                left, total = m.spells.cooldown(item[1])
                slot.set_cooldown(left, total)

    # ------------------------------------------------------------------ waves
    def _wave_tip(self, slot: IconSlot) -> list | None:
        m = self.match
        row = self.wave_rows[slot.data]
        if m is None or not row["number"]:
            return None
        info = m.waves.info(row["number"])
        if info is None:
            return None
        creeps = data.creeps()
        names = data.combat()["armor_names"]
        lines: list[tuple[str, Any]] = [(f"Wave {info['number']}:  {info['name']}", C_GOLD)]
        for cid, count in info["groups"]:
            c = creeps[cid]
            lines.append((f"{count} x {c['name']}", C_RED if c.get("boss") else C_WHITE))
            lines.append((f"   Health {int(info['hp'][cid])}   Speed {c['speed']:g}   Armor {c.get('armor', 0):g} ({names[c['armor_type']]})",
                          C_GREY))
            good, bad = weak_to(c["armor_type"])
            if good or bad:
                lines.append(("   " + ("Weak to " + ", ".join(good) if good else "") + ("   " if good and bad else "")
                              + ("Resists " + ", ".join(bad) if bad else ""), C_BLUE))
            lines += wrap("   " + c.get("description", ""), 58, C_GREY)
        lines.append((f"Kill gold about {int(m.waves.kill_gold(info['number']))}, clear bonus {m.waves.clear_gold(info['number'])}", C_GOLD))
        return lines

    def _refresh_waves(self) -> None:
        m = self.match
        w = m.waves
        first = w.number if w.in_progress and w.number > 0 else w.number + 1
        alive = sum(max(0, v) for v in w.alive.values())
        sig = (first, w.number, alive if first == w.number else -1)
        if sig == self._wave_sig:
            return
        self._wave_sig = sig
        for i, row in enumerate(self.wave_rows):
            n = first + i
            info = w.info(n)
            slot: IconSlot = row["slot"]
            if slot.enabled != (info is not None):
                slot.enabled = info is not None
            if info is None:
                row["number"] = 0
                slot.set_icon(None)
                row["title"].text = "" if i else "No more waves"
                row["tags"].text = ""
                continue
            row["number"] = n
            slot.set_icon(creep_portrait(info["lead"]), border=(1.0, 0.4, 0.35) if info["boss"] else None)
            now = n == w.number
            head = "Now" if now else f"Wave {n}"
            row["title"].text = f"{head}:  {info['name'] if info['name'] != info['detail'] else info['detail']}" + (f"   ({alive} left)" if now else "")
            row["title"].color = color.rgb(1.0, 0.5, 0.45) if info["boss"] else (GOLD if now else TEXT)
            row["title"].scale = 0.9 if len(row["title"].text) <= 34 else 0.9 * 34 / len(row["title"].text)
            tags = info["tags"]
            row["tags"].text = "  ".join(f"<rgb({TAG_COLORS.get(t, C_GREY)[0]},{TAG_COLORS.get(t, C_GREY)[1]},{TAG_COLORS.get(t, C_GREY)[2]})>{t}"
                                         for t in tags) if tags else (info["detail"] if info["name"] != info["detail"] else "")

    # ------------------------------------------------------------------ selection actions
    def _sel_tower(self) -> Tower | None:
        m = self.match
        t = m.selected if m is not None else None
        return t if isinstance(t, Tower) else None

    def _act_upgrade(self, slot: IconSlot) -> None:
        self.game.controls.upgrade_selected()
        self._force()

    def _act_mode(self, slot: IconSlot) -> None:
        self.game.controls.cycle_mode_selected()
        self._force()

    def _act_sell(self, slot: IconSlot) -> None:
        self.game.controls.sell_selected()
        self._force()

    def _action_tip(self, slot: IconSlot) -> list | None:
        t = self._sel_tower()
        m = self.match
        if t is None:
            return None
        if slot.data == 0:
            nxt = t.next_stats()
            if nxt is None:
                return [("Fully upgraded", C_GOLD)]
            cost = t.upgrade_cost()
            lines = [(f"Upgrade to {nxt['name']}  [U]", C_GOLD), (f"Cost: {cost} gold", C_WHITE if m.gold >= cost else C_RED)]
            old = stat_lines(t.defn, t.stats)
            new = stat_lines(t.defn, nxt)
            for a, b in zip(old, new):
                lines.append((b, C_GREEN if a != b else C_WHITE))
            for b in new[len(old):]:
                lines.append((b, C_GREEN))
            if nxt.get("perk"):
                lines += wrap(nxt["perk"], 54, C_GOLD)
            if t.state != "ready":
                lines.append(("Busy...", C_RED))
            return lines
        if slot.data == 1:
            if t.kind == "aura":
                return [("Banners do not attack", C_GREY)]
            return [(f"Targeting: {MODE_NAMES[t.mode]}  [T]", C_GOLD), (f"Attacks {MODE_HELP[t.mode]}.", C_WHITE),
                    ("Click to switch: First, Last, Strongest, Weakest, Closest.", C_GREY)]
        value = t.sell_value()
        full = value >= t.invested
        return [(f"Sell for {value} gold  [X]", C_GOLD),
                ("Full refund: this tower was only just placed." if full else f"75% of the {t.invested} gold invested.", C_WHITE)]

    def _refresh_selection(self) -> None:
        m = self.match
        sel = m.selected
        if isinstance(sel, Creep) and not sel.alive:
            sel = m.selected = None
        tower = sel if isinstance(sel, Tower) else None
        creep = sel if isinstance(sel, Creep) else None
        show = sel is not None
        if self.sel_root.enabled != show:
            self.sel_root.enabled = show
        if self.hint_text.enabled == show:
            self.hint_text.enabled = not show
        if not show:
            sig = ("none", m.stats["kills"], m.stats["leaks"])
            if sig != self._sel_sig:
                self._sel_sig = sig
                self.hint_text.text = ("Q W E R / A S D F / Z   build a tower        1 2 3   powers\n"
                                       "Space   call the next wave early        Tab   game speed\n"
                                       "Click a tower to upgrade (U), retarget (T) or sell (X)\n"
                                       "Alt   show ranges and the creeps' route        G   keep the route visible\n\n"
                                       f"Kills {m.stats['kills']}      Leaks {m.stats['leaks']}      Score {m.score()}")
            return
        if tower is not None:
            sig = ("tower", id(tower), tower.level, tower.state, tower.mode, tower.kills, int(tower.damage_dealt), m.gold,
                   round(tower.buff_damage, 2), round(tower.buff_rate, 2), tower.overcharge_t > 0, tower.sell_value())
            if sig == self._sel_sig:
                return
            self._sel_sig = sig
            self.hp_bar.enabled = False
            self.portrait.texture = tower_portrait(tower.type_id, tower.level)
            self.name_text.text = tower.name
            atk = data.combat()["attack_names"].get(tower.attack_type, "")
            state = {"building": "  -  under construction", "upgrading": "  -  upgrading"}.get(tower.state, "")
            self.sub_text.text = (f"{atk} damage" if tower.kind != "aura" else "Support") + state
            self.level_text.text = "Level " + "I" * tower.level
            lines = stat_lines(tower.defn, tower.stats)
            if tower.kind != "aura":
                lines.append(f"Kills {tower.kills}     Damage dealt {int(tower.damage_dealt):,}     about {tower.dps():.0f} dps")
            self.stats_text.text = "\n".join(lines)
            extra = []
            if tower.buff_damage or tower.buff_rate or tower.buff_range:
                b = [f"+{int(tower.buff_damage * 100)}% damage"] if tower.buff_damage else []
                if tower.buff_rate:
                    b.append(f"+{int(tower.buff_rate * 100)}% speed")
                if tower.buff_range:
                    b.append(f"+{int(tower.buff_range * 100)}% range")
                extra += textwrap.wrap("Banner: " + ", ".join(b), 30)
            if tower.overcharge_t > 0:
                extra.append("OVERCHARGED")
            if tower.stats.get("perk"):
                extra += textwrap.wrap(tower.stats["perk"], 30)
            self.extra_text.text = "\n".join(extra)
            for s in self.actions:
                if not s.enabled:
                    s.enabled = True
            up, mode, sell = self.actions
            nxt = tower.next_stats()
            if nxt is None:
                up.set_icon(icon("upgrade", (0.2, 0.2, 0.2)))
                up.set_dim(True)
            else:
                cost = tower.upgrade_cost()
                up.set_icon(tower_portrait(tower.type_id, tower.level + 1), border=(0.5, 1.0, 0.55), count=cost)
                up.count_text.color = GOLD if m.gold >= cost else color.rgb(1.0, 0.4, 0.35)
                up.set_dim(m.gold < cost or tower.state != "ready")
            if tower.kind == "aura":
                mode.set_icon(mode_icon("first"))
                mode.set_dim(True)
            else:
                mode.set_icon(mode_icon(tower.mode))
                mode.set_dim(False)
            sell.set_icon(icon("sell", (0.3, 0.24, 0.08)), count=tower.sell_value())
            sell.set_dim(False)
            return
        # a creep
        c = creep
        sig = ("creep", id(c), int(c.hp), int(c.armor * 10), c.slow_t > 0, c.poison_t > 0, c.burn_t > 0, c.stun_t > 0)
        if sig == self._sel_sig:
            return
        self._sel_sig = sig
        for s in self.actions:
            if s.enabled:
                s.enabled = False
        self.portrait.texture = creep_portrait(c.type_id)
        self.name_text.text = c.name
        names = data.combat()["armor_names"]
        self.sub_text.text = f"Wave {c.wave}   -   {names[c.armor_type]} armor"
        self.level_text.text = "BOSS" if c.boss else ""
        self.hp_bar.enabled = True
        self.hp_bar.set(c.hp, c.max_hp, f"{int(c.hp)} / {int(c.max_hp)}")
        f = c.hp_frac
        self.hp_bar.set_color(color.rgb(min(1, 2 - 2 * f) * 0.85, min(1, 2 * f) * 0.75, 0.15))
        good, bad = weak_to(c.armor_type)
        status = [s for s, on in (("FROZEN" if c.frozen else "STUNNED", c.stun_t > 0), ("slowed", c.slow_t > 0 and c.stun_t <= 0),
                                  ("poisoned", c.poison_t > 0), ("burning", c.burn_t > 0)) if on]
        lines = ["", f"Armor {c.armor:g}     Speed {c.base_speed:g}     Bounty {c.bounty}",
                 f"A leak costs {c.lives if c.lives < 90 else 'every'} {'life' if c.lives == 1 else 'lives'}",
                 ("Weak to " + ", ".join(good) if good else "") + ("     Resists " + ", ".join(bad) if bad else "")]
        if status:
            lines.append("Status: " + ", ".join(status))
        self.stats_text.text = "\n".join(lines)
        self.extra_text.text = "\n".join(textwrap.wrap(c.defn.get("description", ""), 30))

    # ================================================================== update
    def update(self, dt: float) -> None:
        m = self.match
        if m is None or not self.built or not self.root.enabled:
            return
        self.lives_text.text = str(max(0, m.lives))
        self.gold_text.text = str(m.gold)
        w = m.waves
        total = "" if m.config.endless else f" / {w.total}"
        if w.number == 0:
            self.wave_text.text = f"First wave in {int(w.countdown + 0.99)}s"
        elif w.in_progress:
            self.wave_text.text = f"Wave {w.number}{total}"
        elif w.has_next:
            self.wave_text.text = f"Wave {w.number}{total} cleared   -   next in {int(w.countdown + 0.99)}s"
        else:
            self.wave_text.text = f"Wave {w.number}{total}"
        # send button
        if w.can_send():
            bonus = w.early_bonus()
            txt = "Start first wave  (Space)" if w.number == 0 else "Call wave  (Space)" + (f"   +{bonus} gold" if bonus else "")
            col = color.rgba(0.45, 0.25, 0.06, 1)
        else:
            txt = "Wave arriving..." if w.has_next else "Final wave"
            col = color.rgba(0.2, 0.15, 0.1, 1)
        if self.send_text.text != txt:
            self.send_text.text = txt
            self.send_btn.color = col
        for i, b in enumerate(self.speed_btns):
            want = color.rgba(0.55, 0.32, 0.1, 1) if i == self.game.speed_index else color.rgba(0.2, 0.14, 0.09, 1)
            if b.color != want:
                b.color = want
        # notices, banner, leak flash
        keep = []
        for n in self.notices:
            n[1] -= dt
            if n[1] <= 0:
                n[0].disable()
            else:
                n[0].alpha = min(1.0, n[1] / 0.6)
                keep.append(n)
        self.notices = keep
        if self.banner_t > 0:
            self.banner_t -= dt
            a = min(1.0, self.banner_t / 0.7)
            self.banner.alpha = a
            self.banner_sub.alpha = a
            if self.banner_t <= 0:
                self.banner.text = self.banner_sub.text = ""
        if self.flash_a > 0 or self.flash.color[3] > 0:
            self.flash_a = max(0.0, self.flash_a - dt * 0.9)
            self.flash.color = color.rgba(0.9, 0.1, 0.05, self.flash_a)
        ctl = self.game.controls
        if ctl is not None and ctl.mode == "build":
            name = data.towers()[ctl.mode_arg]["name"]
            hint = f"Placing {name}  -  click a highlighted tile  (Shift: keep placing, right-click: cancel)"
            if ctl.hover_cell >= 0 and not ctl.hover_ok and ctl.hover_reason:
                hint = ctl.hover_reason
            want = hint
        elif ctl is not None and ctl.mode == "spell":
            want = f"{data.spells()[ctl.mode_arg]['name']}  -  click the target area  (right-click: cancel)"
        else:
            want = ""
        if self.mode_text.text != want:
            self.mode_text.text = want
        self._t -= dt
        if self._t <= 0:
            self._t = 0.1
            self._refresh_card()
            self._refresh_waves()
            self._refresh_selection()
