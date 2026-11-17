"""Procedural tower models: nine towers, three levels each.

Every builder fills a :class:`Parts` bundle: a static ``base``, an optional ``head`` that turns towards the
target (cannon barrel, ballista, crystal lens...), and emissive ``glow`` / ``head_glow`` meshes. All towers fit
inside one grid cell (about 1.8 units wide) and grow taller and richer with each level.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

from panda3d.core import NodePath

from core.meshgen import MeshBuilder, shade
from core.util import damp_angle

Col = tuple[float, float, float, float]

STONE = (0.72, 0.70, 0.66, 1)
STONE_D = (0.56, 0.54, 0.52, 1)
STONE_DD = (0.42, 0.41, 0.40, 1)
SLATE = (0.28, 0.36, 0.55, 1)
TIMBER = (0.46, 0.31, 0.18, 1)
TIMBER_D = (0.32, 0.21, 0.13, 1)
THATCH = (0.62, 0.50, 0.28, 1)
GOLDEN = (0.84, 0.64, 0.22, 1)
BRONZE = (0.52, 0.38, 0.20, 1)
IRON = (0.30, 0.30, 0.34, 1)
IRON_L = (0.46, 0.46, 0.50, 1)
COPPER = (0.74, 0.46, 0.24, 1)
MARBLE = (0.90, 0.88, 0.84, 1)
ICE = (0.62, 0.86, 1.0, 1)
ICE_CORE = (0.9, 0.97, 1.0, 1)
FROST_STONE = (0.55, 0.62, 0.72, 1)
FIRE = (1.0, 0.55, 0.12, 1)
FIRE_HOT = (1.0, 0.85, 0.4, 1)
EMBER = (1.0, 0.3, 0.05, 1)
STORM = (0.62, 0.72, 1.0, 1)
STORM_CORE = (0.92, 0.95, 1.0, 1)
VINE = (0.24, 0.34, 0.17, 1)
VINE_D = (0.17, 0.24, 0.13, 1)
VENOM = (0.55, 1.0, 0.3, 1)
PETAL = (0.62, 0.22, 0.55, 1)
SUN = (1.0, 0.9, 0.5, 1)
SUN_CORE = (1.0, 0.98, 0.85, 1)
DARK = (0.06, 0.05, 0.05, 1)

# height of the firing point above the tower's foot, per level (the logic launches projectiles from here)
MUZZLE: dict[str, tuple[float, float, float]] = {
    "arrow": (2.75, 3.25, 3.85), "cannon": (1.75, 1.95, 2.2), "frost": (3.0, 3.55, 4.15), "ember": (1.75, 1.95, 2.2),
    "storm": (2.9, 3.4, 3.95), "venom": (2.45, 2.95, 3.5), "prism": (3.1, 3.65, 4.25), "ballista": (1.85, 2.1, 2.4),
    "banner": (3.4, 4.0, 4.7),
}
# how far in front of the pivot the muzzle sits (for towers with a turning head)
REACH: dict[str, float] = {"cannon": 1.0, "ballista": 0.9, "ember": 0.75, "arrow": 0.4, "venom": 0.45, "prism": 0.3}


@dataclass
class Parts:
    base: MeshBuilder = field(default_factory=MeshBuilder)
    found: MeshBuilder = field(default_factory=MeshBuilder)     # the part buried in the ground
    glow: MeshBuilder = field(default_factory=MeshBuilder)
    head: MeshBuilder = field(default_factory=MeshBuilder)
    head_glow: MeshBuilder = field(default_factory=MeshBuilder)
    pivot: float = 2.0             # height of the head's pivot
    spin: float = 0.0              # degrees per second the head turns on its own (crystals, orbs)
    bob: float = 0.0               # vertical bobbing amplitude of the head
    aims: bool = False             # does the head turn to face the target?
    flame: bool = False            # should the glow mesh flicker like fire?
    sway: float = 0.0              # banner cloth sway in degrees
    light: tuple[float, float, float] | None = None


# ============================================================================ kit
def plinth(p: "Parts", level: int, col: Col = STONE_D, size: float = 1.7) -> None:
    """Common stone footing; gains studs at level 2 and gold caps at level 3."""
    mb = p.base
    mb.box((0, 0.14, 0), (size, 0.28, size), col, taper=0.96)
    p.found.box((0, -0.5, 0), (size * 0.96, 1.0, size * 0.96), shade(col, 0.8))  # foundation reaching into the ground
    if level >= 2:
        for sx in (-1, 1):
            for sz in (-1, 1):
                mb.box((sx * size * 0.42, 0.34, sz * size * 0.42), (0.26, 0.2, 0.26), GOLDEN if level >= 3 else shade(col, 1.15))


def banner_cloth(mb: MeshBuilder, x: float, y: float, z: float, team: Col, w: float = 0.5, h: float = 0.9,
                 facing: tuple = (0, 0, -1)) -> None:
    fx, fz = facing[0], facing[2]
    tx, tz = -fz, fx                    # tangent along the cloth
    ox, oz = fx * 0.04, fz * 0.04
    p0 = (x - tx * w / 2 + ox, y, z - tz * w / 2 + oz)
    p1 = (x + tx * w / 2 + ox, y, z + tz * w / 2 + oz)
    p2 = (x + tx * w / 2 + ox, y - h, z + tz * w / 2 + oz)
    p3 = (x - tx * w / 2 + ox, y - h * 0.82, z - tz * w / 2 + oz)
    mb.quad(p0, p1, p2, p3, team, facing=facing, double=True)
    mb.box((x + ox * 1.5, y - h * 0.42, z + oz * 1.5), (w * 0.32, w * 0.32, 0.03), GOLDEN, rot=(0, math.degrees(math.atan2(fx, -fz)), 45))


def crenels(mb: MeshBuilder, r: float, y: float, n: int, col: Col, size: float = 0.26) -> None:
    for k in range(n):
        if k % 2 == 0:
            a = k / n * math.tau
            mb.box((math.cos(a) * r, y, math.sin(a) * r), (size, size * 1.1, size), col, rot=(0, -math.degrees(a), 0))


def crystal(mb: MeshBuilder, pos: tuple, r: float, h: float, col: Col, segments: int = 4) -> None:
    """Double-pointed gem centred on ``pos``."""
    x, y, z = pos
    mb.cone((x, y, z), r, h * 0.6, col, segments=segments)
    mb.cone((x, y, z), r, h * 0.4, shade(col, 0.85), segments=segments, rot=(180, 0, 0))


# ============================================================================ arrow
def build_arrow(level: int, team: Col) -> Parts:
    p = Parts(aims=True)
    mb = p.base
    plinth(p, level)
    top = (2.2, 2.7, 3.3)[level - 1]
    if level == 1:
        for sx in (-1, 1):
            for sz in (-1, 1):
                mb.beam((sx * 0.72, 0.25, sz * 0.72), (sx * 0.5, top, sz * 0.5), 0.13, TIMBER)
        for sx, sz, ex, ez in ((-1, -1, 1, -1), (1, -1, 1, 1), (1, 1, -1, 1), (-1, 1, -1, -1)):
            mb.beam((sx * 0.66, 0.9, sz * 0.66), (ex * 0.56, 1.7, ez * 0.56), 0.06, TIMBER_D)
    else:
        mb.cylinder((0, 0.28, 0), 0.72, top - 0.28, STONE, radius_top=0.6, segments=8)
        mb.box((0, 1.0, -0.66), (0.16, 0.42, 0.08), DARK)
        if level == 3:
            mb.cylinder((0, top * 0.55, 0), 0.69, 0.14, GOLDEN, segments=8)
    # fighting platform and parapet
    mb.box((0, top + 0.06, 0), (1.5, 0.14, 1.5), TIMBER_D)
    for sx, sz, w, d in ((0, -0.72, 1.5, 0.08), (0, 0.72, 1.5, 0.08), (-0.72, 0, 0.08, 1.5), (0.72, 0, 0.08, 1.5)):
        mb.box((sx, top + 0.3, sz), (w, 0.36, d), TIMBER if level == 1 else STONE_D)
    roof_y = top + 1.15
    for sx in (-1, 1):
        for sz in (-1, 1):
            mb.beam((sx * 0.62, top + 0.1, sz * 0.62), (sx * 0.62, roof_y, sz * 0.62), 0.07, TIMBER)
    roof = THATCH if level == 1 else SLATE
    mb.cone((0, roof_y - 0.02, 0), 1.2, 0.95 + 0.15 * level, roof, segments=4, rot=(0, 45, 0), jitter=0.07)
    if level == 3:
        mb.cone((0, roof_y + 1.2, 0), 0.12, 0.55, GOLDEN, segments=4)
        mb.sphere((0, roof_y + 1.8, 0), 0.11, GOLDEN, detail=0)
    banner_cloth(mb, 0, top + 0.1, -0.78, team, w=0.55, h=0.9)
    if level >= 2:
        banner_cloth(mb, 0.78, top + 0.1, 0, team, w=0.45, h=0.75, facing=(1, 0, 0))
        banner_cloth(mb, -0.78, top + 0.1, 0, team, w=0.45, h=0.75, facing=(-1, 0, 0))
    # the turning bow under the roof
    h = p.head
    p.pivot = MUZZLE["arrow"][level - 1]
    s = 0.9 + 0.12 * level
    h.box((0, -0.28, 0), (0.14, 0.5, 0.14), TIMBER_D)
    h.beam((0, 0, -0.3 * s), (0, 0, 0.42 * s), 0.07, TIMBER)
    for sx in (-1, 1):
        h.beam((0, 0, 0.36 * s), (sx * 0.42 * s, 0, 0.14 * s), 0.045, TIMBER_D if level < 3 else GOLDEN)
        h.beam((sx * 0.42 * s, 0, 0.14 * s), (sx * 0.5 * s, 0, -0.1 * s), 0.035, TIMBER_D if level < 3 else GOLDEN)
        h.beam((sx * 0.5 * s, 0, -0.1 * s), (0, 0, -0.2 * s), 0.012, (0.9, 0.88, 0.8, 1))
    h.beam((0, 0.05, -0.2 * s), (0, 0.05, 0.6 * s), 0.02, (0.85, 0.8, 0.7, 1))
    h.cone((0, 0.05, 0.6 * s), 0.045, 0.14, IRON_L, rot=(90, 0, 0), segments=4)
    return p


# ============================================================================ cannon
def build_cannon(level: int, team: Col) -> Parts:
    p = Parts(aims=True)
    mb = p.base
    plinth(p, level)
    hgt = (1.3, 1.5, 1.72)[level - 1]
    mb.cylinder((0, 0.28, 0), 0.82, hgt - 0.28, STONE, radius_top=0.76, segments=10, jitter=0.06)
    mb.cylinder((0, hgt, 0), 0.86, 0.12, STONE_D, segments=10)
    crenels(mb, 0.78, hgt + 0.22, 12, STONE_D, 0.24)
    band = IRON if level < 3 else GOLDEN
    if level >= 2:
        mb.cylinder((0, 0.55, 0), 0.83, 0.12, band, segments=10)
        mb.cylinder((0, hgt - 0.3, 0), 0.8, 0.1, band, segments=10)
    mb.box((0, 0.7, -0.78), (0.3, 0.6, 0.1), DARK)
    banner_cloth(mb, 0.0, hgt - 0.05, 0.84, team, w=0.5, h=0.75, facing=(0, 0, 1))
    for bx, by, bz in ((0.68, 0.41, -0.74), (0.68, 0.41, -0.5), (0.68, 0.61, -0.62)):      # a little pile of shot
        mb.sphere((bx, by, bz), 0.13, IRON, detail=0)
    h = p.head
    p.pivot = MUZZLE["cannon"][level - 1]
    r = (0.2, 0.24, 0.3)[level - 1]
    ln = (1.1, 1.3, 1.5)[level - 1]
    h.box((0, -0.2, -0.05), (0.62, 0.22, 0.9), TIMBER_D)
    for sx in (-1, 1):
        h.box((sx * 0.3, -0.05, -0.05), (0.08, 0.36, 0.62), TIMBER)
        h.cylinder((sx * 0.36, -0.2, -0.3), 0.2, 0.08, IRON, segments=8, rot=(0, 0, 90))
    h.cylinder((0, 0.02, -0.45), r, ln, IRON, radius_top=r * 0.82, segments=8, rot=(90, 0, 0))
    h.cylinder((0, 0.02, ln - 0.55), r * 0.98, 0.14, band if level >= 2 else IRON_L, segments=8, rot=(90, 0, 0))
    h.cylinder((0, 0.02, -0.5), r * 1.12, 0.16, IRON_L, segments=8, rot=(90, 0, 0))
    h.sphere((0, 0.02, -0.52), r * 0.7, IRON, detail=0)
    if level == 3:
        h.cylinder((0, 0.02, 0.1), r * 1.08, 0.12, GOLDEN, segments=8, rot=(90, 0, 0))
        p.head_glow.cylinder((0, 0.02, ln - 0.47), r * 0.6, 0.04, EMBER, segments=6, rot=(90, 0, 0))
    return p


# ============================================================================ frost
def build_frost(level: int, team: Col) -> Parts:
    p = Parts(spin=40.0, bob=0.12, light=(0.5, 0.8, 1.0))
    mb = p.base
    plinth(p, level, FROST_STONE)
    mb.cylinder((0, 0.28, 0), 0.7, 0.22, shade(FROST_STONE, 0.85), segments=6)
    hgt = (2.2, 2.7, 3.25)[level - 1]
    mb.cylinder((0, 0.5, 0), 0.42, hgt - 0.5, FROST_STONE, radius_top=0.2, segments=4, rot=(0, 45, 0), jitter=0.05)
    mb.cylinder((0, hgt, 0), 0.3, 0.1, shade(FROST_STONE, 1.2), segments=4, rot=(0, 45, 0))
    # glowing rune strips on each face
    g = p.glow
    for k in range(4):
        a = k * math.pi / 2
        g.box((math.cos(a) * 0.27, 0.5 + (hgt - 0.5) * 0.45, math.sin(a) * 0.27), (0.07, (hgt - 0.5) * 0.5, 0.07), ICE,
              rot=(0, -math.degrees(a), 0))
    n_spikes = (0, 4, 6)[level - 1]
    for k in range(n_spikes):
        a = k / n_spikes * math.tau + 0.4
        mb.cone((math.cos(a) * 0.62, 0.3, math.sin(a) * 0.62), 0.16, 0.7 + 0.25 * (k % 2), ICE, segments=4,
                rot=(math.sin(a) * 16, 0, -math.cos(a) * 16))
    if level == 3:
        mb.cylinder((0, hgt * 0.62, 0), 0.36, 0.1, GOLDEN, segments=4, rot=(0, 45, 0))
    banner_cloth(mb, 0, 1.25, -0.4, team, w=0.36, h=0.6)
    p.pivot = MUZZLE["frost"][level - 1]
    hg = p.head_glow
    size = (0.3, 0.36, 0.44)[level - 1]
    crystal(hg, (0, -0.1, 0), size, size * 3.0, ICE)
    crystal(hg, (0, 0.0, 0), size * 0.55, size * 1.8, ICE_CORE)
    for k in range((0, 2, 4)[level - 1]):
        a = k / max(1, (0, 2, 4)[level - 1]) * math.tau
        crystal(hg, (math.cos(a) * 0.62, 0.0, math.sin(a) * 0.62), 0.1, 0.36, ICE_CORE, segments=3)
    return p


# ============================================================================ ember
def build_ember(level: int, team: Col) -> Parts:
    p = Parts(aims=True, flame=True, light=(1.0, 0.6, 0.25))
    mb = p.base
    plinth(p, level)
    hgt = (1.25, 1.42, 1.62)[level - 1]
    mb.cylinder((0, 0.28, 0), 0.55, hgt - 0.5, STONE_DD, radius_top=0.36, segments=7, jitter=0.06)
    for k in range(3):
        a = k / 3 * math.tau + 0.5
        mb.beam((math.cos(a) * 0.78, 0.25, math.sin(a) * 0.78), (math.cos(a) * 0.5, hgt - 0.05, math.sin(a) * 0.5), 0.07, IRON)
    rim = BRONZE if level == 3 else IRON_L
    r = (0.72, 0.8, 0.9)[level - 1]
    mb.cylinder((0, hgt - 0.25, 0), 0.4, 0.42, IRON, radius_top=r, segments=9)
    mb.cylinder((0, hgt + 0.17, 0), r, 0.09, rim, segments=9)
    if level >= 2:
        for k in range(6):
            a = k / 6 * math.tau
            mb.cone((math.cos(a) * r, hgt + 0.2, math.sin(a) * r), 0.09, 0.4, rim, segments=4,
                    rot=(math.sin(a) * 30, 0, -math.cos(a) * 30))
    banner_cloth(mb, 0, 1.0, 0.56, team, w=0.4, h=0.6, facing=(0, 0, 1))
    # coals and fire in the bowl (the glow mesh is modelled from the bowl's lip so it can flicker)
    g = p.glow
    s = 0.8 + 0.22 * level
    g.cylinder((0, hgt + 0.12, 0), r * 0.86, 0.1, EMBER, segments=8)
    g.cone((0, hgt + 0.15, 0), 0.42 * s, 1.1 * s, FIRE, segments=6)
    g.cone((0.12, hgt + 0.15, -0.08), 0.28 * s, 1.5 * s, FIRE_HOT, segments=5)
    g.cone((-0.16, hgt + 0.15, 0.12), 0.24 * s, 0.9 * s, EMBER, segments=5)
    # the spout that turns towards the creeps
    h = p.head
    p.pivot = MUZZLE["ember"][level - 1]
    h.cylinder((0, -0.12, 0.28), 0.16, 0.6, IRON, radius_top=0.2, segments=6, rot=(78, 0, 0))
    h.cylinder((0, 0.0, 0.82), 0.22, 0.1, rim, segments=6, rot=(78, 0, 0))
    for sx in (-1, 1):
        h.cone((sx * 0.2, 0.1, 0.5), 0.07, 0.34, rim, segments=4, rot=(-20, 0, sx * -30))
    p.head_glow.cone((0, 0.0, 0.84), 0.14, 0.36, FIRE_HOT, segments=5, rot=(80, 0, 0))
    return p


# ============================================================================ storm
def build_storm(level: int, team: Col) -> Parts:
    p = Parts(spin=90.0, light=(0.6, 0.7, 1.0))
    mb = p.base
    plinth(p, level)
    top = MUZZLE["storm"][level - 1]
    mb.cylinder((0, 0.28, 0), 0.5, 0.35, STONE_DD, radius_top=0.34, segments=8)
    mb.cylinder((0, 0.6, 0), 0.17, top - 1.0, COPPER, radius_top=0.11, segments=6)
    rings = (3, 4, 5)[level - 1]
    for k in range(rings):
        f = k / max(1, rings - 1)
        y = 0.95 + f * (top - 1.75)
        r = 0.62 - 0.32 * f
        mb.cylinder((0, y, 0), r, 0.09, COPPER if k % 2 == 0 else shade(COPPER, 0.75), segments=10)
        mb.cylinder((0, y + 0.09, 0), r * 0.8, 0.05, GOLDEN if (level == 3 and k == rings - 1) else IRON_L, segments=10)
    for k in range(4):
        a = k / 4 * math.tau + math.pi / 4
        mb.beam((math.cos(a) * 0.66, 0.28, math.sin(a) * 0.66), (math.cos(a) * 0.3, 1.0, math.sin(a) * 0.3), 0.05, IRON)
    banner_cloth(mb, 0, 1.05, -0.36, team, w=0.34, h=0.55)
    p.pivot = top
    hg = p.head_glow
    r = (0.24, 0.29, 0.35)[level - 1]
    hg.sphere((0, 0, 0), r, STORM, detail=1, jitter=0.1)
    hg.sphere((0, 0, 0), r * 0.6, STORM_CORE, detail=0)
    prongs = (0, 3, 4)[level - 1]
    for k in range(prongs):
        a = k / prongs * math.tau
        p.head.beam((math.cos(a) * 0.1, -0.35, math.sin(a) * 0.1), (math.cos(a) * 0.5, 0.0, math.sin(a) * 0.5), 0.035, COPPER, segments=4)
        hg.sphere((math.cos(a) * 0.52, 0.03, math.sin(a) * 0.52), 0.075, STORM_CORE, detail=0)
    return p


# ============================================================================ venom
def build_venom(level: int, team: Col) -> Parts:
    p = Parts(aims=True, light=(0.5, 1.0, 0.35))
    mb = p.base
    plinth(p, level, shade(STONE_D, 0.9))
    top = MUZZLE["venom"][level - 1]
    # twisting trunk made of leaning segments
    pts = [(0.0, 0.25, 0.0)]
    n = 5
    for k in range(1, n + 1):
        f = k / n
        a = f * 2.6
        pts.append((math.cos(a) * 0.2 * (1 - f * 0.6), 0.25 + f * (top - 0.5), math.sin(a) * 0.2 * (1 - f * 0.6)))
    for k, (a, b) in enumerate(zip(pts[:-1], pts[1:])):
        mb.beam(a, b, 0.3 - 0.035 * k, VINE if k % 2 == 0 else VINE_D, segments=6, radius_b=0.27 - 0.035 * k, jitter=0.08)
    thorns = 6 + 3 * level
    for k in range(thorns):
        f = (k + 0.5) / thorns
        a = k * 2.4
        y = 0.4 + f * (top - 0.9)
        mb.cone((math.cos(a) * 0.2, y, math.sin(a) * 0.2), 0.07, 0.4, (0.72, 0.68, 0.5, 1), segments=3,
                rot=(math.sin(a) * 65, 0, -math.cos(a) * 65))
    for k in range(3):
        a = k / 3 * math.tau + 0.4
        mb.beam((0, 0.3, 0), (math.cos(a) * 0.72, 0.18, math.sin(a) * 0.72), 0.12, VINE_D, segments=5, radius_b=0.04)
    g = p.glow
    sacs = (2, 3, 5)[level - 1]
    for k in range(sacs):
        a = k * 2.1 + 0.6
        y = 0.75 + k * (top - 1.4) / max(1, sacs - 1) * 0.8
        g.sphere((math.cos(a) * 0.3, y, math.sin(a) * 0.3), (0.17, 0.21, 0.17), VENOM, detail=0)
    banner_cloth(mb, 0, 1.0, -0.42, team, w=0.34, h=0.55)
    # the spitting pod
    h = p.head
    p.pivot = top
    s = (0.85, 1.0, 1.2)[level - 1]
    h.sphere((0, 0, 0.1 * s), (0.3 * s, 0.28 * s, 0.38 * s), VINE, detail=1, jitter=0.08)
    petal = PETAL if level == 3 else shade(VINE, 1.35)
    for k in range(5):
        a = k / 5 * math.tau
        h.cone((math.cos(a) * 0.2 * s, math.sin(a) * 0.2 * s, 0.32 * s), 0.12 * s, 0.5 * s, petal, segments=3,
               rot=(90 - math.sin(a) * 28, 0, -math.cos(a) * 28))
    p.head_glow.sphere((0, 0, 0.42 * s), 0.13 * s, VENOM, detail=0)
    if level >= 2:
        for sx in (-1, 1):
            h.beam((sx * 0.1, -0.1, 0), (sx * 0.5, -0.35, 0.1), 0.05, VINE_D, segments=4)
            p.head_glow.sphere((sx * 0.52, -0.38, 0.1), 0.1, VENOM, detail=0)
    return p


# ============================================================================ prism
def build_prism(level: int, team: Col) -> Parts:
    p = Parts(aims=True, bob=0.05, light=(1.0, 0.9, 0.55))
    mb = p.base
    plinth(p, level, MARBLE)
    top = MUZZLE["prism"][level - 1]
    mb.cylinder((0, 0.28, 0), 0.6, 0.2, STONE, segments=8)
    mb.cylinder((0, 0.48, 0), 0.34, top - 1.2, MARBLE, radius_top=0.2, segments=8, jitter=0.03)
    for f in ((0.3, 0.7) if level == 1 else (0.2, 0.5, 0.8)):
        y = 0.48 + f * (top - 1.2)
        mb.cylinder((0, y, 0), 0.36 - 0.14 * f, 0.09, GOLDEN, segments=8)
    # the claw that cradles the lens
    for k in range(3 if level < 3 else 4):
        n = 3 if level < 3 else 4
        a = k / n * math.tau + 0.3
        mb.beam((math.cos(a) * 0.16, top - 0.75, math.sin(a) * 0.16), (math.cos(a) * 0.5, top - 0.3, math.sin(a) * 0.5), 0.05, GOLDEN, segments=4)
        mb.cone((math.cos(a) * 0.5, top - 0.32, math.sin(a) * 0.5), 0.05, 0.38, GOLDEN, segments=4,
                rot=(-math.sin(a) * 22, 0, math.cos(a) * 22))
    if level >= 2:
        for sx in (-1, 1):
            mb.box((sx * 0.4, top * 0.45, 0), (0.06, top * 0.4, 0.4), MARBLE, taper=0.5)
    banner_cloth(mb, 0, 1.3, -0.36, team, w=0.34, h=0.6)
    p.pivot = top
    hg = p.head_glow
    s = (0.3, 0.36, 0.44)[level - 1]
    hg.sphere((0, 0, 0), (s, s, s * 0.42), SUN, detail=1, jitter=0.04)
    hg.sphere((0, 0, 0), (s * 0.55, s * 0.55, s * 0.5), SUN_CORE, detail=0)
    h = p.head
    for k in range(8):
        a = k / 8 * math.tau
        h.box((math.cos(a) * s * 1.08, math.sin(a) * s * 1.08, 0), (0.09, 0.09, 0.1), GOLDEN, rot=(0, 0, math.degrees(a)))
    if level == 3:
        for k in range(12):
            a = k / 12 * math.tau
            b = (k + 1) / 12 * math.tau
            h.beam((math.cos(a) * s * 1.7, math.sin(a) * s * 1.7, -0.02), (math.cos(b) * s * 1.7, math.sin(b) * s * 1.7, -0.02), 0.04, GOLDEN)
        for k in range(4):
            a = k / 4 * math.tau + math.pi / 4
            hg.cone((math.cos(a) * s * 1.7, math.sin(a) * s * 1.7, -0.02), 0.06, 0.26, SUN, segments=3, rot=(0, 0, math.degrees(a) - 90))
    return p


# ============================================================================ ballista
def build_ballista(level: int, team: Col) -> Parts:
    p = Parts(aims=True)
    mb = p.base
    plinth(p, level)
    hgt = (1.2, 1.4, 1.65)[level - 1]
    mb.box((0, 0.28 + (hgt - 0.28) / 2, 0), (1.35, hgt - 0.28, 1.35), STONE, taper=0.9, jitter=0.05)
    for sx in (-1, 1):
        for sz in (-1, 1):
            mb.beam((sx * 0.68, 0.25, sz * 0.68), (sx * 0.62, hgt + 0.05, sz * 0.62), 0.09, TIMBER)
    mb.box((0, hgt + 0.05, 0), (1.5, 0.12, 1.5), TIMBER_D)
    trim = IRON if level < 3 else GOLDEN
    if level >= 2:
        mb.box((0, hgt * 0.55, 0), (1.42, 0.1, 1.42), trim)
    mb.box((0, 0.75, -0.69), (0.26, 0.5, 0.06), DARK)
    banner_cloth(mb, 0, hgt - 0.02, 0.7, team, w=0.5, h=0.7, facing=(0, 0, 1))
    # quiver of spare bolts
    for k in range(3):
        mb.beam((0.52, hgt + 0.1, -0.5 + k * 0.08), (0.56, hgt + 0.95, -0.45 + k * 0.08), 0.025, (0.8, 0.75, 0.6, 1))
    h = p.head
    p.pivot = MUZZLE["ballista"][level - 1]
    s = (0.95, 1.1, 1.28)[level - 1]
    h.cylinder((0, -0.34, 0), 0.16, 0.3, IRON, segments=6)
    h.beam((0, 0, -0.75 * s), (0, 0, 0.85 * s), (0.16, 0.14), TIMBER)
    h.box((0, 0.04, -0.6 * s), (0.34, 0.14, 0.2), trim)
    limb = TIMBER_D if level < 3 else GOLDEN
    for sx in (-1, 1):
        h.beam((0, 0.02, 0.62 * s), (sx * 0.62 * s, 0.02, 0.44 * s), 0.07, limb)
        h.beam((sx * 0.62 * s, 0.02, 0.44 * s), (sx * 0.86 * s, 0.02, 0.06 * s), 0.05, limb)
        h.beam((sx * 0.86 * s, 0.02, 0.06 * s), (0, 0.06, -0.42 * s), 0.014, (0.92, 0.9, 0.82, 1))
        if level >= 2:
            h.box((sx * 0.3 * s, 0.02, 0.56 * s), (0.1, 0.16, 0.1), trim)
    h.beam((0, 0.1, -0.42 * s), (0, 0.1, 0.95 * s), 0.035, (0.82, 0.78, 0.66, 1), segments=4)
    h.cone((0, 0.1, 0.95 * s), 0.08, 0.3, IRON_L, rot=(90, 0, 0), segments=4)
    if level == 3:
        for sx in (-1, 1):
            h.beam((0, 0.2, 0.5 * s), (sx * 0.5 * s, 0.2, 0.36 * s), 0.05, GOLDEN)
    return p


# ============================================================================ banner
def build_banner(level: int, team: Col) -> Parts:
    p = Parts(sway=6.0, light=None)
    mb = p.base
    plinth(p, level)
    mb.cylinder((0, 0.28, 0), 0.5, 0.4, STONE, radius_top=0.38, segments=8)
    top = MUZZLE["banner"][level - 1]
    mb.cylinder((0, 0.6, 0), 0.085, top - 0.6, TIMBER, radius_top=0.065, segments=6)
    mb.beam((-0.62, top - 0.25, 0), (0.62, top - 0.25, 0), 0.05, TIMBER_D, segments=5)
    for sx in (-1, 1):
        mb.sphere((sx * 0.64, top - 0.25, 0), 0.07, GOLDEN, detail=0)
    if level == 1:
        mb.cone((0, top, 0), 0.09, 0.3, IRON_L, segments=4)
    elif level == 2:
        mb.cone((0, top, 0), 0.1, 0.36, GOLDEN, segments=4)
    else:
        mb.sphere((0, top + 0.12, 0), 0.14, GOLDEN, detail=0)
        for k in range(5):
            a = k / 5 * math.tau
            mb.cone((math.cos(a) * 0.14, top + 0.16, math.sin(a) * 0.14), 0.045, 0.28, GOLDEN, segments=3,
                    rot=(math.sin(a) * 20, 0, -math.cos(a) * 20))
    # shields and weapon rack around the foot
    for k in range((2, 3, 4)[level - 1]):
        a = k / 4 * math.tau + math.pi / 4
        x, z = math.cos(a) * 0.52, math.sin(a) * 0.52
        mb.cylinder((x, 0.62, z), 0.26, 0.06, team, segments=8, rot=(90 - 18, -math.degrees(a) + 90, 0))
        mb.sphere((x * 1.07, 0.64, z * 1.07), 0.07, GOLDEN, detail=0)
    if level >= 2:
        for sx in (-1, 1):
            mb.beam((sx * 0.3, 0.3, 0.6), (sx * 0.12, 1.5, 0.48), 0.03, IRON_L, segments=4)
    # the cloth is the "head": it sways gently
    h = p.head
    p.pivot = top - 0.25
    w, ln = (1.0, 1.1, 1.2)[level - 1], (1.7, 2.0, 2.4)[level - 1]
    h.quad((-w / 2, 0, 0.02), (w / 2, 0, 0.02), (w / 2, -ln, 0.02), (-w / 2, -ln, 0.02), team, facing=(0, 0, -1), double=True)
    h.triangle((-w / 2, -ln, 0.02), (w / 2, -ln, 0.02), (0, -ln - 0.4, 0.02), team, facing=(0, 0, -1), double=True)
    for z in (-0.012, 0.052):
        h.box((0, -ln * 0.42, z), (w * 0.42, w * 0.42, 0.02), GOLDEN, rot=(0, 0, 45))
        h.box((0, -ln * 0.42, z * 1.3), (w * 0.2, w * 0.2, 0.02), team, rot=(0, 0, 45))
        h.box((0, -0.06, z), (w, 0.1, 0.02), GOLDEN)
        h.box((0, -ln + 0.06, z), (w, 0.08, 0.02), GOLDEN)
    if level >= 2:
        for sx in (-1, 1):
            h.quad((sx * (w / 2 + 0.04), 0, 0.02), (sx * (w / 2 + 0.3), 0, 0.02), (sx * (w / 2 + 0.3), -ln * 0.5, 0.02),
                   (sx * (w / 2 + 0.04), -ln * 0.62, 0.02), shade(team, 0.8), facing=(0, 0, -1), double=True)
    if level == 3:
        p.glow.sphere((0, top + 0.12, 0), 0.09, SUN, detail=0)
    return p


BUILDERS: dict[str, Callable[[int, Col], Parts]] = {
    "arrow": build_arrow, "cannon": build_cannon, "frost": build_frost, "ember": build_ember, "storm": build_storm,
    "venom": build_venom, "prism": build_prism, "ballista": build_ballista, "banner": build_banner,
}

_cache: dict[tuple, dict] = {}


def tower_protos(type_id: str, level: int, team: tuple[float, float, float]) -> dict:
    """Cached prototype nodes (and metadata) for one tower type / level."""
    key = (type_id, level, tuple(round(c, 3) for c in team))
    hit = _cache.get(key)
    if hit is None:
        parts = BUILDERS[type_id](level, (team[0], team[1], team[2], 1.0))
        hit = {"parts": parts}
        for name in ("base", "found", "glow", "head", "head_glow"):
            mb: MeshBuilder = getattr(parts, name)
            hit[name] = None if mb.is_empty() else mb.build(f"{type_id}{level}_{name}")
        _cache[key] = hit
    return hit


class TowerView:
    """Scene nodes of one tower plus its small animations (aiming, recoil, spinning crystals, rising when built)."""

    def __init__(self, tower, parent: NodePath, team: tuple[float, float, float]) -> None:
        self.tower = tower
        self.team = team
        self.root = parent.attachNewNode(f"tower_{tower.type_id}")
        self.root.setPos(tower.x, tower.y, tower.z)
        self.model: NodePath | None = None
        self.head: NodePath | None = None
        self.parts: Parts | None = None
        self.yaw = 180.0
        self.recoil = 0.0
        self.flash = 0.0
        self.t = (tower.cell * 0.37) % 6.28
        self.light = None
        self.build(tower.level)

    def build(self, level: int) -> None:
        if self.model is not None:
            self.model.removeNode()
        protos = tower_protos(self.tower.type_id, level, self.team)
        parts: Parts = protos["parts"]
        self.parts = parts
        self.model = self.root.attachNewNode("model")
        protos["base"].copyTo(self.model)
        protos["found"].copyTo(self.model)
        if protos["glow"] is not None:
            g = protos["glow"].copyTo(self.model)
            g.setShaderInput("u_emissive", 1.12 if parts.flame else 1.5)
            if parts.flame:
                g.setShaderInput("u_flame", 1.0)
        self.head = self.model.attachNewNode("head")
        self.head.setPos(0, parts.pivot, 0)
        if protos["head"] is not None:
            protos["head"].copyTo(self.head)
        if protos["head_glow"] is not None:
            hg = protos["head_glow"].copyTo(self.head)
            hg.setShaderInput("u_emissive", 1.15 if parts.flame else 1.7)
        self.head.setH(-self.yaw)

    def muzzle(self) -> tuple[float, float, float]:
        """World position projectiles and beams leave from."""
        t = self.tower
        reach = REACH.get(t.type_id, 0.0)
        a = math.radians(self.yaw)
        return t.x + math.sin(a) * reach, t.y + self.parts.pivot, t.z + math.cos(a) * reach

    def kick(self, amount: float = 1.0) -> None:
        self.recoil = amount

    def pulse(self) -> None:
        self.flash = 1.0

    def update(self, dt: float) -> None:
        t = self.tower
        parts = self.parts
        self.t += dt
        # construction / upgrade: the tower rises out of the ground
        if t.state == "building":
            k = 1.0 - max(0.0, t.busy_t) / max(t.busy_total, 1e-3)
            self.model.setScale(0.7 + 0.3 * k, max(0.05, k), 0.7 + 0.3 * k)
        elif t.state == "upgrading":
            k = 1.0 - max(0.0, t.busy_t) / max(t.busy_total, 1e-3)
            w = 1.0 + 0.06 * math.sin(k * math.pi * 6)
            self.model.setScale(w, 1.0 + 0.04 * math.sin(k * math.pi * 6 + 1.0), w)
        elif self.model.getSy() != 1.0:
            self.model.setScale(1.0)
        if parts.aims:
            self.yaw = damp_angle(self.yaw, t.aim_yaw, 14.0, dt)
            self.head.setH(-self.yaw)
        elif parts.spin:
            fast = 3.0 if t.firing > 0 else 1.0
            self.yaw = (self.yaw + parts.spin * fast * dt) % 360.0
            self.head.setH(-self.yaw)
        elif parts.sway:
            self.head.setHpr(math.sin(self.t * 0.9) * parts.sway, math.sin(self.t * 1.7) * parts.sway * 0.6, 0)
        y = parts.pivot + (math.sin(self.t * 2.0) * parts.bob if parts.bob else 0.0)
        if self.recoil > 0:
            self.recoil = max(0.0, self.recoil - dt * 4.5)
            a = math.radians(self.yaw)
            k = self.recoil * 0.3
            self.head.setPos(-math.sin(a) * k, y, -math.cos(a) * k)
        else:
            self.head.setPos(0, y, 0)
        if self.flash > 0:
            self.flash = max(0.0, self.flash - dt * 2.2)
            k = 1.0 + 1.4 * self.flash
            self.root.setColorScale(k, k, k * 0.9, 1)
            if self.flash <= 0:
                self.root.clearColorScale()

    def destroy(self) -> None:
        self.root.removeNode()
