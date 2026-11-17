"""Creep models. ``build_creep_rig(model)`` turns a ``model`` entry from creeps.json into an animated rig.

Humanoids, riders, ogres and drakes live in :mod:`creeps.rigs_humanoid`; four-legged beasts, scorpions, crabs
and the behemoth in :mod:`creeps.rigs_beasts`. The rigs unique to this game (bat, slime, wraith) are below.
"""
from __future__ import annotations

import math
from typing import Any

from panda3d.core import NodePath

from core.meshgen import MeshBuilder, shade
from core.rig import Rig, copy_part
from core.util import set_rot
from creeps.rigs_beasts import BehemothRig, CrabRig, QuadrupedRig, ScorpionRig
from creeps.rigs_humanoid import FlyerRig, OgreRig, RiderRig, UnitHumanoidRig

DARK = (0.06, 0.05, 0.07, 1.0)
IVORY = (0.92, 0.87, 0.72, 1.0)


def _c(v: Any, a: float = 1.0) -> tuple[float, float, float, float]:
    return (float(v[0]), float(v[1]), float(v[2]), a)


def _ck(col: Any) -> tuple:
    return tuple(round(float(c), 3) for c in col)


# ============================================================================ bat
class BatRig(Rig):
    """A leathery flier: round furred body, big ears and two membrane wings."""

    def __init__(self, color: Any, accent: Any = None) -> None:
        super().__init__()
        self.col = _c(color)
        self.acc = _c(accent) if accent is not None else shade(self.col, 1.5)
        self.height = 1.1
        self.radius = 0.8
        key = ("bat", _ck(self.col), _ck(self.acc))
        self.torso = self.body.attachNewNode("torso")
        copy_part(key + ("body",), self._body, self.torso)
        self.wings: list[NodePath] = []
        for side in (-1, 1):
            w = self.torso.attachNewNode("wing")
            w.setPos(side * 0.22, 0.12, 0.0)
            copy_part(key + ("wing", side), lambda side=side: self._wing(side), w)
            self.wings.append(w)

    def _body(self) -> MeshBuilder:
        mb = MeshBuilder()
        c = self.col
        mb.sphere((0, 0, 0), (0.34, 0.32, 0.5), c, detail=1)
        mb.sphere((0, 0.12, 0.5), (0.26, 0.24, 0.26), shade(c, 1.15), detail=1)
        mb.box((0, 0.04, 0.72), (0.14, 0.1, 0.12), shade(c, 0.7))
        for sx in (-1, 1):
            mb.cone((sx * 0.17, 0.24, 0.44), 0.11, 0.42, shade(c, 0.85), segments=4, rot=(-12, 0, sx * -20))
            mb.cone((sx * 0.17, 0.26, 0.46), 0.06, 0.3, self.acc, segments=3, rot=(-12, 0, sx * -20))
            mb.box((sx * 0.11, 0.18, 0.7), (0.07, 0.06, 0.04), (1.0, 0.35, 0.7, 1))
            mb.cone((sx * 0.06, -0.02, 0.76), 0.025, 0.12, IVORY, rot=(180, 0, 0), segments=3)
            mb.beam((sx * 0.1, -0.25, -0.1), (sx * 0.14, -0.5, -0.02), 0.03, shade(c, 0.6), segments=3)
        mb.cone((0, 0.0, -0.45), 0.16, 0.4, shade(c, 0.8), segments=4, rot=(-100, 0, 0))
        return mb

    def _wing(self, side: int) -> MeshBuilder:
        mb = MeshBuilder()
        mem = shade(self.acc, 0.55)
        bone = shade(self.col, 0.7)
        pts = [(side * 0.9, 0.1, 0.35), (side * 1.7, 0.02, 0.05), (side * 1.45, 0.0, -0.55), (side * 0.75, 0.0, -0.4)]
        mb.quad((0, 0, 0.25), pts[0], pts[3], (0, 0, -0.3), mem, facing=(0, 1, 0), double=True)
        mb.quad(pts[0], pts[1], pts[2], pts[3], shade(mem, 0.85), facing=(0, 1, 0), double=True)
        mb.beam((0, 0.02, 0.25), pts[0], 0.035, bone, segments=3)
        mb.beam(pts[0], pts[1], 0.028, bone, segments=3)
        mb.beam(pts[0], pts[2], 0.02, bone, segments=3)
        return mb

    def _pose(self, dt: float, speed: float, grounded: bool, swimming: bool) -> None:
        t = self.idle_t
        flap = math.sin(t * 13.0) * 42
        for j, w in enumerate(self.wings):
            side = -1 if j == 0 else 1
            set_rot(w, 0, 0, side * (flap + 8))
        self.torso.setY(math.sin(t * 13.0 + 1.2) * 0.09)
        set_rot(self.torso, 10 + math.sin(t * 2.1) * 4, 0, math.sin(t * 1.3) * 6)

    def _pose_death(self, t: float) -> None:
        e = 1 - (1 - t) ** 3
        set_rot(self.body, 40 * e, 0, 150 * e)
        for j, w in enumerate(self.wings):
            set_rot(w, 0, 0, (-1 if j == 0 else 1) * -55 * e)


# ============================================================================ slime
class SlimeRig(Rig):
    """A hopping blob that squashes and stretches; flattens into a puddle when it dies."""

    def __init__(self, color: Any) -> None:
        super().__init__()
        self.col = _c(color)
        self.height = 1.15
        self.radius = 0.62
        key = ("slime", _ck(self.col))
        self.blob = self.body.attachNewNode("blob")
        copy_part(key + ("blob",), self._blob, self.blob)
        self.cap = self.blob.attachNewNode("cap")
        self.cap.setPos(0, 0.72, 0)
        copy_part(key + ("cap",), self._cap, self.cap)

    def _blob(self) -> MeshBuilder:
        mb = MeshBuilder()
        c = self.col
        mb.sphere((0, 0.42, 0), (0.62, 0.46, 0.62), c, detail=2, noise=0.07, seed=2, jitter=0.08)
        mb.sphere((0, 0.12, 0), (0.7, 0.16, 0.7), shade(c, 0.8), detail=1, noise=0.1, seed=5)
        dark = shade(c, 0.35)
        for sx in (-1, 1):
            mb.sphere((sx * 0.2, 0.55, 0.5), (0.11, 0.14, 0.07), (0.95, 0.98, 0.9, 1), detail=0)
            mb.box((sx * 0.2, 0.54, 0.56), (0.07, 0.09, 0.04), DARK)
        mb.box((0, 0.36, 0.59), (0.2, 0.04, 0.04), dark)
        for k, (x, y, z, r) in enumerate(((0.38, 0.5, -0.2, 0.12), (-0.3, 0.3, -0.4, 0.1), (0.1, 0.6, -0.42, 0.08))):
            mb.sphere((x, y, z), r, shade(c, 1.35), detail=0, seed=k)
        return mb

    def _cap(self) -> MeshBuilder:
        mb = MeshBuilder()
        mb.sphere((0, 0.05, 0), (0.34, 0.2, 0.34), shade(self.col, 1.25), detail=1, noise=0.08, seed=9)
        mb.sphere((0.08, 0.2, 0.05), 0.09, shade(self.col, 1.5), detail=0)
        return mb

    def _pose(self, dt: float, speed: float, grounded: bool, swimming: bool) -> None:
        ph = self.phase * 1.4
        hop = max(0.0, math.sin(ph))
        k = self.move_blend
        stretch = 1.0 + (0.26 * hop - 0.1 * max(0.0, -math.sin(ph))) * k + math.sin(self.idle_t * 2.4) * 0.03
        wide = 1.0 / math.sqrt(max(stretch, 0.3))
        self.blob.setScale(wide, stretch, wide)
        self.blob.setY(hop * 0.3 * k)
        self.cap.setPos(math.sin(ph * 1.0 + 0.8) * 0.05 * k, 0.72 + math.sin(ph - 0.9) * 0.06 * k, 0)
        if self.action == "hit":
            j = math.sin(self.action_progress() * math.pi)
            self.blob.setScale(wide * (1 + 0.15 * j), stretch * (1 - 0.2 * j), wide * (1 + 0.15 * j))

    def _pose_death(self, t: float) -> None:
        e = 1 - (1 - t) ** 3
        self.blob.setScale(1.0 + 0.9 * e, max(0.06, 1.0 - 0.94 * e), 1.0 + 0.9 * e)
        self.blob.setY(0)


# ============================================================================ wraith
class WraithRig(Rig):
    """A drifting hooded spectre with a ragged robe and grasping arms. Drawn translucent."""

    translucent = True

    def __init__(self, color: Any, accent: Any = None) -> None:
        super().__init__()
        self.col = _c(color)
        self.acc = _c(accent) if accent is not None else (0.5, 0.9, 1.0, 1.0)
        self.height = 2.3
        self.radius = 0.55
        key = ("wraith", _ck(self.col), _ck(self.acc))
        self.torso = self.body.attachNewNode("torso")
        self.torso.setY(1.25)
        copy_part(key + ("torso",), self._torso, self.torso)
        self.tail = self.torso.attachNewNode("tail")
        self.tail.setPos(0, -0.35, -0.05)
        copy_part(key + ("tail",), self._tail, self.tail)
        self.arms: list[NodePath] = []
        for side in (-1, 1):
            arm = self.torso.attachNewNode("arm")
            arm.setPos(side * 0.36, 0.42, 0.05)
            copy_part(key + ("arm", side), lambda side=side: self._arm(side), arm)
            self.arms.append(arm)

    def _torso(self) -> MeshBuilder:
        mb = MeshBuilder()
        c = self.col
        mb.box((0, 0.1, 0), (0.72, 0.9, 0.42), c, taper=1.25)
        mb.box((0, 0.5, -0.02), (0.92, 0.2, 0.5), shade(c, 0.9))
        # hood with a dark hollow and two cold eyes
        mb.box((0, 0.86, 0.0), (0.5, 0.56, 0.52), shade(c, 0.85), taper=0.8)
        mb.cone((0, 1.05, -0.2), 0.16, 0.42, shade(c, 0.8), segments=4, rot=(-55, 0, 0))
        mb.box((0, 0.82, 0.2), (0.34, 0.36, 0.14), DARK)
        for sx in (-1, 1):
            mb.box((sx * 0.085, 0.86, 0.28), (0.07, 0.045, 0.03), self.acc)
        mb.box((0, 0.3, 0.22), (0.1, 0.5, 0.03), shade(self.acc, 0.6))
        return mb

    def _tail(self) -> MeshBuilder:
        mb = MeshBuilder()
        c = self.col
        mb.cylinder((0, 0, 0), 0.4, 0.75, shade(c, 0.92), radius_top=0.12, segments=6, rot=(180, 0, 0), caps=False)
        for k in range(5):
            a = k / 5 * math.tau + 0.3
            mb.cone((math.cos(a) * 0.2, -0.55, math.sin(a) * 0.2), 0.11, 0.5, shade(c, 0.75 + 0.06 * k), segments=3,
                    rot=(180 + math.sin(a) * 14, 0, math.cos(a) * 14))
        return mb

    def _arm(self, side: int) -> MeshBuilder:
        mb = MeshBuilder()
        c = shade(self.col, 0.92)
        mb.beam((0, 0, 0), (side * 0.12, -0.42, 0.22), (0.2, 0.18), c)
        mb.beam((side * 0.12, -0.42, 0.22), (side * 0.1, -0.5, 0.66), 0.045, shade(c, 0.7), segments=4)
        for k in range(3):
            mb.cone((side * 0.1 + (k - 1) * 0.06, -0.5, 0.66), 0.022, 0.2, DARK, segments=3, rot=(80, (k - 1) * 18, 0))
        return mb

    def _pose(self, dt: float, speed: float, grounded: bool, swimming: bool) -> None:
        t = self.idle_t
        self.torso.setY(1.25 + math.sin(t * 2.2) * 0.12)
        set_rot(self.torso, 12 * self.move_blend + math.sin(t * 1.3) * 3, math.sin(t * 0.9) * 5, 0)
        set_rot(self.tail, -18 * self.move_blend + math.sin(t * 3.1) * 8, 0, math.sin(t * 2.3) * 9)
        for j, arm in enumerate(self.arms):
            side = -1 if j == 0 else 1
            set_rot(arm, -35 + math.sin(t * 1.9 + j * 2.0) * 16, 0, side * (6 + math.sin(t * 1.4 + j) * 6))

    def _pose_death(self, t: float) -> None:
        e = 1 - (1 - t) ** 3
        self.torso.setY(1.25 - 0.9 * e)
        self.body.setScale(1.0 - 0.5 * e, max(0.05, 1.0 - e), 1.0 - 0.5 * e)


# ============================================================================ factory
def build_creep_rig(model: dict[str, Any]) -> Rig:
    """Create the rig described by a creeps.json ``model`` entry."""
    kind = model.get("kind", "humanoid")
    if kind == "humanoid":
        rig: Rig = UnitHumanoidRig(dict(model.get("look", {})))
    elif kind == "quadruped":
        rig = QuadrupedRig(model.get("style", "boar"), model.get("color", (0.5, 0.4, 0.3)), model.get("accent"))
    elif kind == "scorpion":
        rig = ScorpionRig(model.get("color", (0.4, 0.3, 0.2)), model.get("accent"))
    elif kind == "crab":
        rig = CrabRig(model.get("color", (0.7, 0.3, 0.2)))
    elif kind == "bat":
        rig = BatRig(model.get("color", (0.3, 0.25, 0.35)), model.get("accent"))
    elif kind == "slime":
        rig = SlimeRig(model.get("color", (0.3, 0.6, 0.3)))
    elif kind == "wraith":
        rig = WraithRig(model.get("color", (0.8, 0.85, 0.95)), model.get("accent"))
    elif kind == "rider":
        rig = RiderRig(model.get("mount", "hound"), model.get("mount_color", (0.3, 0.25, 0.3)), model.get("barding"),
                       dict(model.get("look", {})))
    elif kind == "ogre":
        rig = OgreRig(model.get("skin", (0.5, 0.47, 0.42)), model.get("accent"))
    elif kind == "drake":
        rig = FlyerRig("drake", model.get("color", (0.3, 0.25, 0.35)), model.get("accent"))
    elif kind == "behemoth":
        rig = BehemothRig(model.get("skin", (0.52, 0.46, 0.38)))
    else:
        raise ValueError(f"unknown creep model kind '{kind}'")
    sc = float(model.get("scale", 1.0))
    if sc != 1.0:
        rig.body.setScale(sc)
        rig.height *= sc
        rig.radius *= sc
        rig.scale = sc
    return rig
