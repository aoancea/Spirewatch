"""Procedural scenery: trees, boulders, the rift gates creeps pour out of and the Heartfire shrine they march on."""
from __future__ import annotations

import math
import random

from core.meshgen import MeshBuilder, shade

Col = tuple[float, float, float, float]

TREE_PALETTES: dict[str, dict[str, list[Col]]] = {
    "vale": {"bark": [(0.36, 0.25, 0.16, 1)], "pine": [(0.14, 0.33, 0.17, 1), (0.18, 0.39, 0.19, 1), (0.12, 0.29, 0.16, 1)],
             "leaf": [(0.25, 0.45, 0.19, 1), (0.32, 0.50, 0.20, 1), (0.36, 0.44, 0.17, 1)]},
    "dusk": {"bark": [(0.33, 0.23, 0.16, 1)], "pine": [(0.20, 0.30, 0.16, 1), (0.24, 0.33, 0.16, 1), (0.17, 0.26, 0.15, 1)],
             "leaf": [(0.72, 0.42, 0.14, 1), (0.78, 0.30, 0.12, 1), (0.70, 0.55, 0.16, 1)]},
    "moor": {"bark": [(0.24, 0.20, 0.20, 1)], "pine": [(0.14, 0.26, 0.24, 1), (0.17, 0.30, 0.26, 1), (0.12, 0.22, 0.22, 1)],
             "leaf": [(0.30, 0.30, 0.26, 1), (0.34, 0.26, 0.36, 1), (0.26, 0.32, 0.28, 1)]},
}


def tree_mesh(palette: str, kind: int) -> MeshBuilder:
    """Tree prototype; kinds 0-2 are pines, 3-5 broadleaf (gnarled on the moor)."""
    pal = TREE_PALETTES.get(palette, TREE_PALETTES["vale"])
    bark = pal["bark"][0]
    variant = kind % 3
    mb = MeshBuilder()
    if kind < 3:
        g = pal["pine"][variant]
        mb.cylinder((0, 0, 0), 0.2, 1.3, bark, radius_top=0.14, segments=5)
        for i, (y, r, h) in enumerate(((0.9, 1.2, 1.7), (1.8, 0.95, 1.5), (2.6, 0.65, 1.4))):
            mb.cone((0, y, 0), r, h, shade(g, 1.0 + 0.08 * i), segments=7, jitter=0.06)
    elif palette == "moor":
        rng = random.Random(variant)
        mb.cylinder((0, 0, 0), 0.26, 2.1, bark, radius_top=0.17, segments=6)
        for k in range(4):
            a = k * 1.57 + rng.random() * 0.6
            y0 = 1.2 + k * 0.28
            tip = (math.cos(a) * 1.15, y0 + 1.0 + rng.random() * 0.5, math.sin(a) * 1.15)
            mb.beam((0, y0, 0), tip, 0.09, bark, segments=4, radius_b=0.035)
            mb.sphere(tip, (0.5, 0.38, 0.5), pal["leaf"][(variant + k) % 3], detail=0, noise=0.2, seed=variant + k)
        mb.sphere((0, 2.7, 0), (0.8, 0.55, 0.8), pal["leaf"][variant], detail=0, noise=0.25, seed=variant + 11)
    else:
        leaves = pal["leaf"][variant]
        mb.cylinder((0, 0, 0), 0.24, 1.7, bark, radius_top=0.16, segments=5)
        mb.beam((0, 1.35, 0), (0.55, 2.1, 0.2), 0.08, bark, segments=4)
        mb.sphere((0, 2.35, 0), (1.2, 1.0, 1.15), leaves, detail=1, noise=0.18, seed=variant, jitter=0.07)
        mb.sphere((0.5, 2.85, 0.22), (0.72, 0.62, 0.72), shade(leaves, 1.12), detail=0, noise=0.15, seed=variant + 5)
        mb.sphere((-0.45, 2.05, -0.3), (0.76, 0.62, 0.72), shade(leaves, 0.9), detail=0, noise=0.15, seed=variant + 9)
    return mb


def boulder_mesh(color: Col, seed: int) -> MeshBuilder:
    rng = random.Random(seed)
    mb = MeshBuilder()
    mb.sphere((0, 0.25, 0), (1.0, 0.75, 0.9), shade(color, 0.9 + rng.random() * 0.2), detail=1, noise=0.28, seed=seed)
    if rng.random() < 0.6:
        mb.sphere((0.75, 0.1, 0.35), (0.5, 0.4, 0.45), shade(color, 0.85), detail=0, noise=0.3, seed=seed + 3)
    return mb


# ============================================================================ rift gate
OBSIDIAN = (0.16, 0.13, 0.20, 1)
OBSIDIAN_L = (0.26, 0.21, 0.32, 1)
RIFT = (0.72, 0.28, 0.95, 1)
RIFT_CORE = (1.0, 0.75, 1.0, 1)


def rift_gate_meshes(width: float = 1.0) -> tuple[MeshBuilder, MeshBuilder, MeshBuilder]:
    """(solid frame, glowing portal, spinning shards). The gate faces +z; the portal plane is z = 0."""
    solid, glow, shards = MeshBuilder(), MeshBuilder(), MeshBuilder()
    half = 1.25 * width
    rng = random.Random(4)
    # cracked footing
    solid.box((0, 0.06, 0.2), (half * 2 + 1.4, 0.16, 2.4), shade(OBSIDIAN, 1.15), jitter=0.06)
    for sx in (-1, 1):
        solid.cylinder((sx * half, 0, 0), 0.52, 3.6, OBSIDIAN, radius_top=0.26, segments=5, rot=(0, 0, sx * 7), jitter=0.08)
        solid.cylinder((sx * (half + 0.55), 0, 0.35), 0.36, 2.0, OBSIDIAN_L, radius_top=0.12, segments=4, rot=(8, 0, sx * -14))
        solid.cylinder((sx * (half + 0.2), 0, -0.5), 0.3, 1.4, OBSIDIAN, radius_top=0.08, segments=4, rot=(-12, 0, sx * -6))
        solid.cone((sx * (half - 0.42), 3.5, 0), 0.24, 1.1, OBSIDIAN_L, segments=4, rot=(0, 0, sx * 30))
        for k in range(3):
            glow.box((sx * (half - 0.2 - 0.05 * k), 0.9 + k * 0.85, 0.36), (0.07, 0.34, 0.05), RIFT, rot=(0, 0, rng.uniform(-25, 25)))
    # broken lintel
    solid.beam((-half * 0.95, 3.55, 0), (-half * 0.2, 3.95, 0), (0.42, 0.4), OBSIDIAN)
    solid.beam((half * 0.95, 3.55, 0), (half * 0.3, 3.9, 0), (0.42, 0.4), OBSIDIAN)
    # portal membrane
    glow.sphere((0, 1.95, 0), (half * 0.86, 1.75, 0.3), RIFT, detail=2, jitter=0.12)
    glow.sphere((0, 1.95, 0.0), (half * 0.5, 1.15, 0.36), RIFT_CORE, detail=1, jitter=0.05)
    # shards orbit the portal centre (modelled around the origin, spun in code)
    for k in range(7):
        a = k / 7 * math.tau
        r = half * 0.95 + rng.uniform(-0.1, 0.25)
        shards.cone((math.cos(a) * r, math.sin(a) * r * 1.35, 0), 0.09, 0.42, RIFT_CORE if k % 2 else RIFT, segments=3,
                    rot=(0, 0, math.degrees(a) - 90))
    return solid, glow, shards


# ============================================================================ heartfire
STONE = (0.72, 0.70, 0.66, 1)
STONE_D = (0.58, 0.56, 0.53, 1)
GOLDEN = (0.84, 0.64, 0.22, 1)
FIRE = (1.0, 0.55, 0.12, 1)
FIRE_HOT = (1.0, 0.82, 0.35, 1)
EMBER = (1.0, 0.3, 0.05, 1)
TIMBER = (0.42, 0.29, 0.17, 1)


def heartfire_meshes(team: Col) -> tuple[MeshBuilder, MeshBuilder]:
    """(solid shrine, flame). The flame is modelled with its base at the origin so it can be scaled in code."""
    mb, flame = MeshBuilder(), MeshBuilder()
    mb.cylinder((0, 0, 0), 2.5, 0.3, STONE_D, segments=10)
    mb.cylinder((0, 0.3, 0), 2.0, 0.3, STONE, segments=10)
    mb.cylinder((0, 0.6, 0), 0.75, 0.9, STONE_D, radius_top=0.55, segments=8)
    mb.cylinder((0, 1.5, 0), 0.6, 0.35, (0.36, 0.26, 0.22, 1), radius_top=1.0, segments=8)
    mb.cylinder((0, 1.85, 0), 1.0, 0.08, GOLDEN, segments=8)
    for k in range(4):
        a = k / 4 * math.tau + math.pi / 4
        x, z = math.cos(a) * 1.75, math.sin(a) * 1.75
        mb.cylinder((x, 0.6, z), 0.2, 2.6, STONE, segments=6)
        mb.box((x, 3.25, z), (0.55, 0.16, 0.55), STONE_D)
        mb.cone((x, 3.33, z), 0.22, 0.5, GOLDEN, segments=4)
        # banners hang on the outer face of each pillar
        ox, oz = math.cos(a) * 0.22, math.sin(a) * 0.22
        tx, tz = -math.sin(a) * 0.3, math.cos(a) * 0.3
        mb.quad((x + ox - tx, 3.0, z + oz - tz), (x + ox + tx, 3.0, z + oz + tz), (x + ox + tx, 1.5, z + oz + tz),
                (x + ox - tx, 1.75, z + oz - tz), team, facing=(math.cos(a), 0, math.sin(a)), double=True)
    for k in range(8):
        a = k / 8 * math.tau
        mb.box((math.cos(a) * 2.25, 0.36, math.sin(a) * 2.25), (0.3, 0.14, 0.3), GOLDEN, rot=(0, -math.degrees(a), 0))
    flame.cone((0, 0, 0), 0.8, 2.1, FIRE, segments=7)
    flame.cone((0.16, 0, -0.1), 0.52, 2.8, FIRE_HOT, segments=6)
    flame.cone((-0.22, 0, 0.16), 0.46, 1.7, EMBER, segments=5)
    flame.cone((0.05, 0, 0.25), 0.3, 1.3, (1.0, 0.95, 0.7, 1), segments=5)
    return mb, flame
